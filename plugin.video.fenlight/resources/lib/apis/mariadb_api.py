import json
import os
import sqlite3
import threading
import time
import requests
from requests.adapters import HTTPAdapter
from modules.settings import (
    watch_history_api_address,
    watch_history_api_key,
    watch_history_database_name,
    watch_history_profile_name
)
from modules.kodi_utils import logger, notification, get_property, set_property, clear_property, addon_profile, addon_version
from modules.utils import format_timestamp, to_sql_timestamp, utc_offset_label

TIMEOUT = (3.05, 15)             # seconds to connect, seconds to wait for the answer
IMPORT_TIMEOUT = (3.05, 120)
OFFLINE_SECONDS = 30             # after the API can't be reached, skip it this long instead of making every request wait
NOTICE_SECONDS = 300             # show the same problem on screen at most this often
MAX_QUEUED = 5000
IMPORT_CHUNK = 1000
QUEUED = {'queued': True}        # what a change returns when it was saved to send later

_OFFLINE_PROP = 'fenlight.watch_history_api.offline_until'
_NOTICE_PROP = 'fenlight.watch_history_api.notice'
_FLUSH_PROP = 'fenlight.watch_history_api.flushing'

# Kodi keeps Fen Light's Python running between calls (reuselanguageinvoker), so one shared session keeps the
# connections to the API open across requests and list loads. requests also decompresses gzip answers.
_session = requests.Session()
_session.mount('http://', HTTPAdapter(pool_maxsize=16))
_session.mount('https://', HTTPAdapter(pool_maxsize=16))
_session.headers['User-Agent'] = 'FenLight/%s' % addon_version()

_batch_supported = True          # False once the API turns out to be older than /api/episodes/info
_flush_lock = threading.Lock()
_queue_lock = threading.Lock()
_queue_db = None

def _call(method, endpoint, data=None, params=None, database=None, timeout=TIMEOUT):
    """One request to the API: (HTTP status, decoded answer), with status None when the API couldn't be reached"""
    url = '%s/api/%s' % (watch_history_api_address().rstrip('/'), endpoint)
    headers = {'X-API-Key': watch_history_api_key(), 'X-Database-Name': database or watch_history_database_name()}
    try:
        response = _session.request(method, url, json=data, params=params, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        return None, str(exc)
    try:
        body = response.json()
    except ValueError:
        body = response.text
    return response.status_code, body

def _detail(body):
    detail = body.get('detail') if isinstance(body, dict) else body
    return detail if isinstance(detail, str) else json.dumps(detail)

def _route_missing(status, body):
    # FastAPI's answer for an endpoint that this version of the API doesn't have
    return status == 404 and _detail(body) == 'Not Found'

def _unreachable(status):
    """The API, or the MariaDB behind it, is down rather than refusing this request"""
    return status is None or status in (502, 503, 504)

def _keep_queued(status, body):
    """Worth sending again later: the API is down, or a setting (API key, database) has to be fixed first"""
    return _unreachable(status) or status == 401 or (status == 404 and not _route_missing(status, body))

def explain(status, body):
    """What went wrong, in words the user can act on"""
    if status is None: return "Can't reach the watch history API at %s" % watch_history_api_address()
    if status == 401: return "The watch history API rejected the API Key in Fen Light's settings"
    if _route_missing(status, body): return 'The watch history API is older than this Fen Light. Update it by running its installer again'
    return 'Watch history API: %s' % _detail(body)

def _notify_once(message):
    """Show a problem on screen, but the same one at most every few minutes: one list can make many requests"""
    now = time.time()
    last_time, _, last_message = (get_property(_NOTICE_PROP) or '0|').partition('|')
    try: recent = now - float(last_time) < NOTICE_SECONDS
    except ValueError: recent = False
    if recent and last_message == message: return
    set_property(_NOTICE_PROP, '%s|%s' % (now, message))
    notification(message, 6000)

def _report(endpoint, status, body):
    logger('WatchHistoryClient', severity='high', notify=False,
           error_message='%s [%s]: %s' % (explain(status, body), endpoint, body if status is None else _detail(body)))
    _notify_once(explain(status, body))

def _offline():
    try: return float(get_property(_OFFLINE_PROP) or 0) > time.time()
    except ValueError: return False

def _note_result(status):
    if _unreachable(status): set_property(_OFFLINE_PROP, str(time.time() + OFFLINE_SECONDS))
    elif get_property(_OFFLINE_PROP): clear_property(_OFFLINE_PROP)

# --- Changes saved while the API was unreachable ---
def _queue():
    global _queue_db
    if _queue_db is None:
        _queue_db = sqlite3.connect(os.path.join(addon_profile(), 'databases', 'api_queue.db'), timeout=20, isolation_level=None, check_same_thread=False)
        _queue_db.execute('CREATE TABLE IF NOT EXISTS queue (id INTEGER PRIMARY KEY AUTOINCREMENT, database_name TEXT, endpoint TEXT, payload TEXT, created INTEGER)')
    return _queue_db

def queued_count():
    with _queue_lock:
        return _queue().execute('SELECT COUNT(*) FROM queue').fetchone()[0]

def _enqueue(endpoint, data):
    with _queue_lock:
        queue = _queue()
        if queue.execute('SELECT COUNT(*) FROM queue').fetchone()[0] >= MAX_QUEUED: return False
        queue.execute('INSERT INTO queue (database_name, endpoint, payload, created) VALUES (?, ?, ?, ?)',
                      (watch_history_database_name(), endpoint, json.dumps(data), int(time.time())))
    return True

def _claim_flush():
    try: claimed = float(get_property(_FLUSH_PROP) or 0)
    except ValueError: claimed = 0
    if time.time() - claimed < 60: return False      # another Fen Light process (the service, a widget) is sending them
    set_property(_FLUSH_PROP, str(time.time()))
    return True

def _send_queued():
    sent = 0
    while True:
        with _queue_lock:
            row = _queue().execute('SELECT id, database_name, endpoint, payload FROM queue ORDER BY id LIMIT 1').fetchone()
        if not row: return sent
        row_id, database, endpoint, payload = row
        status, body = _call('POST', endpoint, json.loads(payload), database=database)
        _note_result(status)
        if status != 200:
            if _keep_queued(status, body): return sent   # still can't be sent: keep it, and everything after it, for later
            _report(endpoint, status, body)               # can never succeed as it is, so drop it
        else: sent += 1
        with _queue_lock:
            _queue().execute('DELETE FROM queue WHERE id = ?', (row_id,))

def flush_offline_queue():
    """Send the changes saved while the API was unreachable, oldest first. Returns how many were sent."""
    if _offline() or not queued_count(): return 0
    if not _flush_lock.acquire(blocking=False): return 0   # another thread is already sending them
    try:
        if not _claim_flush(): return 0
        try: sent = _send_queued()
        finally: clear_property(_FLUSH_PROP)
    finally:
        _flush_lock.release()
    if sent: notification('Sent %d watch history change%s saved while the API was unreachable' % (sent, '' if sent == 1 else 's'), 5000)
    return sent

def test_connection():
    """(ok, message) about whether Fen Light can use the API with the current settings"""
    address = watch_history_api_address().rstrip('/')
    try:
        health = _session.get('%s/health' % address, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return False, "Can't reach the API at [B]%s[/B].[CR]Check External Host, and that the API is running.[CR][CR]%s" % (address, exc)
    if health.status_code == 503:
        return False, "The API at [B]%s[/B] is running but can't log in to MariaDB.[CR]Its log has the reason: journalctl -u fenlight-api" % address
    status, body = _call('GET', 'profiles')
    _note_result(status)
    if _route_missing(status, body):
        # an API from before the profiles endpoint: any authenticated read proves the key and database work
        status, body = _call('GET', 'lists', params={'profile': watch_history_profile_name()})
        if status == 200: return True, 'Connected, but the API is out of date. Run its installer again to update it.'
    if status != 200: return False, explain(status, body)
    profile = watch_history_profile_name()
    known = [i['profile'] for i in body]
    detail = 'has history' if profile in known else 'has no history yet'
    sent = flush_offline_queue()
    pending = queued_count()
    lines = ['Connected to database [B]%s[/B]. Profile [B]%s[/B] %s.' % (watch_history_database_name(), profile, detail)]
    if sent: lines.append('Sent %d change%s saved while the API was unreachable.' % (sent, '' if sent == 1 else 's'))
    if pending: lines.append('%d saved change%s still waiting to be sent.' % (pending, '' if pending == 1 else 's'))
    return True, '[CR]'.join(lines)

class WatchHistoryClient:
    """Watch history kept by the Fen Light API. Provides the same methods as the SQLite and MariaDB databases."""
    def __init__(self):
        self.profile = watch_history_profile_name()
        self._episodes = None    # watched episodes and bookmarks per show, filled by prefetch_episodes()

    def _request(self, endpoint, params=None):
        """A read: the decoded answer, or None if it failed (logged, and shown on screen now and then)"""
        flush_offline_queue()    # so the answer includes changes saved while the API was down
        if _offline(): return None
        status, body = _call('GET', endpoint, params=params)
        _note_result(status)
        if status == 200: return body
        _report(endpoint, status, body)
        return None

    def _write(self, endpoint, data):
        """A change: sent now, or saved and sent later if the API can't take it right now"""
        flush_offline_queue()    # older saved changes have to reach the API first
        status, body = None, 'saved changes are still waiting to be sent'
        if not _offline() and not queued_count():
            status, body = _call('POST', endpoint, data)
            _note_result(status)
            if status == 200: return body
            if not _keep_queued(status, body):
                _report(endpoint, status, body)
                return None
        if not _enqueue(endpoint, data):
            _report(endpoint, status, body)
            return None
        logger('WatchHistoryClient', severity='medium', notify=False, error_message='Saved %s to send later: %s' % (endpoint, body))
        if status is not None or _offline(): _notify_once('%s. Changes are saved and will be sent later' % explain(status, body))
        return QUEUED

    # --- Historical Play / Records ---
    def record_historical_play(self, media_type, media_id, season=None, episode=None, title="", started="", ended="", play_event="watched", resume_point="", curr_time="", resume_id=0):
        payload = {
            "media_type": media_type,
            "media_id": media_id,
            "season": season,
            "episode": episode,
            "title": title,
            "started": to_sql_timestamp(started),
            "ended": to_sql_timestamp(ended),
            "utc_offset": utc_offset_label(),
            "play_event": play_event,
            "resume_point": resume_point,
            "curr_time": curr_time,
            "resume_id": resume_id,
            "profile": self.profile
        }
        return self._write("history/record", payload)

    # --- Watched Info ---
    def get_watched_movies(self):
        return self._request("watched/movies", {"profile": self.profile})

    def get_watched_info_tvshow(self):
        return self._request("watched/tvshow/info", {"profile": self.profile})

    def get_watched_info_season(self, media_id):
        return self._request("watched/season/info", {"media_id": media_id, "profile": self.profile})

    def prefetch_episodes(self, media_ids):
        """Fetch the watched episodes and bookmarks of many shows in one request; the per-show methods below are then
        answered from it. Without it an episode list or widget makes two requests for every show on it."""
        global _batch_supported
        media_ids = list(dict.fromkeys(str(i) for i in media_ids if i))
        if not media_ids or not _batch_supported or _offline(): return
        status, body = _call("POST", "episodes/info", {"media_ids": media_ids[:1000], "profile": self.profile})
        _note_result(status)
        if _route_missing(status, body): _batch_supported = False    # an older API: keep asking show by show
        elif status == 200: self._episodes = body

    def get_watched_info_episode(self, media_id):
        cached = self._episodes and self._episodes["watched"].get(str(media_id))
        if cached is not None: return cached
        return self._request("watched/episode/info", {"media_id": media_id, "profile": self.profile})

    def get_recently_watched(self, media_type, short_list=1):
        params = {"media_type": media_type, "short_list": short_list, "profile": self.profile}
        # the full list is only used to find each show's latest episode, so let the API pick those
        if not short_list: params["per_show"] = 1
        return self._request("watched/recently", params)

    # --- Hidden Progress ---
    def get_hidden_progress_items(self):
        return self._request("hidden/progress", {"profile": self.profile})

    def update_hidden_progress_items(self, media_id, action):
        payload = {
            "media_id": media_id,
            "action": action,
            "profile": self.profile
        }
        return self._write("hidden/progress/update", payload)

    # --- Bookmarks & Progress ---
    def get_bookmarks_movie(self):
        return self._request("bookmarks/movies/info", {"profile": self.profile})

    def get_bookmarks_episode(self, media_id, season):
        cached = self._episodes and self._episodes["bookmarks"].get(str(media_id))
        if cached is not None:
            # cached rows are [season, resume_point, curr_time, resume_id, episode]
            return [row[1:] for row in cached if row[0] == int(season)]
        return self._request("bookmarks/episode/info", {"media_id": media_id, "season": season, "profile": self.profile})

    def get_in_progress_movies(self):
        return self._request("progress/movies", {"profile": self.profile})

    def get_in_progress_episodes(self):
        return self._request("progress/episodes", {"profile": self.profile})

    def set_bookmark(self, media_type, media_id, curr_time, total_time, title="", season=None, episode=None):
        payload = {
            "media_type": media_type,
            "media_id": media_id,
            "curr_time": curr_time,
            "total_time": total_time,
            "title": title,
            "season": season,
            "episode": episode,
            "last_played": to_sql_timestamp(format_timestamp()),
            "utc_offset": utc_offset_label(),
            "profile": self.profile
        }
        return self._write("bookmarks/set", payload)

    def erase_bookmark(self, media_type, media_id, season=None, episode=None, refresh="false"):
        payload = {
            "media_type": media_type,
            "media_id": media_id,
            "season": season,
            "episode": episode,
            "refresh": refresh,
            "profile": self.profile
        }
        return self._write("bookmarks/erase", payload)

    def batch_erase_bookmark(self, insert_list, action):
        payload = {
            "insert_list": insert_list,
            "action": action,
            "profile": self.profile
        }
        return self._write("bookmarks/batch-erase", payload)

    # --- Watch Status Management ---
    def watched_status_mark(self, media_type, media_id, action, season=None, episode=None, title="", last_played=None):
        if action == "mark_as_watched": last_played = to_sql_timestamp(last_played or format_timestamp())
        payload = {
            "media_type": media_type,
            "media_id": media_id,
            "action": action,
            "season": season,
            "episode": episode,
            "title": title,
            "last_played": last_played,
            "utc_offset": utc_offset_label(),
            "profile": self.profile
        }
        return self._write("watched/status", payload)

    def batch_watched_status_mark(self, insert_list, action):
        # mark_as_watched rows are (media_type, media_id, season, episode, last_played, title)
        if action == "mark_as_watched": insert_list = [i[:4] + (to_sql_timestamp(i[4]),) + tuple(i[5:]) for i in insert_list]
        payload = {
            "insert_list": insert_list,
            "action": action,
            "utc_offset": utc_offset_label(),
            "profile": self.profile
        }
        return self._write("watched/batch-status", payload)

    # --- TV Shows Next Episodes ---
    def get_next_episodes(self, nextep_content=0):
        return self._request("tvshows/next-episodes", {"nextep_content": nextep_content, "profile": self.profile})

    def make_list(self, list_name, sort_order):
        payload = {
            "list_name": list_name,
            "sort_order": sort_order,
            "created": to_sql_timestamp(format_timestamp()),
            "utc_offset": utc_offset_label(),
            "profile": self.profile
        }
        return self._write("lists/make", payload)

    def delete_list(self, list_name):
        payload = {
            "list_name": list_name,
            "profile": self.profile
        }
        return self._write("lists/delete", payload)

    def clear_list_contents(self, list_name):
        payload = {
            "list_name": list_name,
            "profile": self.profile
        }
        return self._write("lists/clear-contents", payload)

    def update_list_details(self, list_name, sort_order, original_name):
        payload = {
            "original_name": original_name,
            "new_list_name": list_name,
            "sort_order": sort_order,
            "profile": self.profile
        }
        return self._write("lists/update-details", payload)

    def get_lists(self):
        return self._request("lists", {"profile": self.profile})

    def get_list(self, list_name):
        return self._request("lists/get", {"list_name": list_name, "profile": self.profile})

    def add_list_item(self, list_name, item):
        payload = {
            "list_name": list_name,
            "item": self._list_item(item),
            "utc_offset": utc_offset_label(),
            "profile": self.profile
        }
        return self._list_changed(self._write("lists/add-item", payload))

    def remove_list_item(self, list_name, media_id):
        payload = {
            "list_name": list_name,
            "media_id": str(media_id),
            "profile": self.profile
        }
        return self._list_changed(self._write("lists/remove-item", payload))

    def add_many_list_items(self, list_name, items):
        payload = {
            "list_name": list_name,
            "items": [self._list_item(item) for item in items],
            "utc_offset": utc_offset_label(),
            "profile": self.profile
        }
        return self._write("lists/add-many-items", payload)

    # --- Profiles and importing ---
    def get_profiles(self):
        """[{'profile', 'watched', 'in_progress', 'lists'}, ...] for this database, or None if the API can't say"""
        status, body = _call("GET", "profiles")
        _note_result(status)
        return body if status == 200 else None

    def import_history(self, watched=(), progress=(), hidden=(), lists=(), callback=None):
        """Copy history from another source into this profile, keeping entries it already has.
        watched rows: (media_type, media_id, season, episode, last_played, title)
        progress rows: (media_type, media_id, season, episode, resume_point, curr_time, last_played, resume_id, title)
        lists: (name, sort_order, items). Returns what was added; raises with a readable message on failure."""
        def send(endpoint, data):
            status, body = _call("POST", endpoint, dict(data, profile=self.profile), timeout=IMPORT_TIMEOUT)
            _note_result(status)
            if status != 200: raise Exception(explain(status, body))
            return body
        watched = [list(i[:4]) + [to_sql_timestamp(i[4]), i[5]] for i in watched]
        progress = [list(i[:6]) + [to_sql_timestamp(i[6]), i[7], i[8]] for i in progress]
        offset = utc_offset_label()
        jobs = [("import", {"watched": watched[n:n + IMPORT_CHUNK], "utc_offset": offset}) for n in range(0, len(watched), IMPORT_CHUNK)]
        jobs += [("import", {"progress": progress[n:n + IMPORT_CHUNK], "utc_offset": offset}) for n in range(0, len(progress), IMPORT_CHUNK)]
        if hidden: jobs.append(("import", {"hidden": [int(i) for i in hidden], "utc_offset": offset}))
        # list names already in the database, with their item counts
        existing = {row[0]: row[1] for row in self._lists_or_fail()} if lists else {}
        for name, sort_order, items in lists:
            if name not in existing:
                jobs.append(("lists/make", {"list_name": name, "sort_order": sort_order, "created": to_sql_timestamp(format_timestamp()), "utc_offset": offset}))
            for n in range(0, len(items), IMPORT_CHUNK):
                jobs.append(("lists/add-many-items", {"list_name": name, "items": [self._list_item(i) for i in items[n:n + IMPORT_CHUNK]], "utc_offset": offset}))
        added = {"watched": 0, "progress": 0, "hidden": 0, "lists": len([i for i in lists if i[0] not in existing]), "list_items": 0}
        for done, (endpoint, data) in enumerate(jobs, 1):
            result = send(endpoint, data)
            if endpoint == "import":
                for key in ("watched", "progress", "hidden"): added[key] += result.get(key, 0)
            if callback: callback(done, len(jobs))
        if lists:
            counts = {row[0]: row[1] for row in self._lists_or_fail()}
            added["list_items"] = sum(counts.get(name, 0) - existing.get(name, 0) for name, _, _ in lists)
        return added

    def _lists_or_fail(self):
        status, body = _call("GET", "lists", params={"profile": self.profile}, timeout=IMPORT_TIMEOUT)
        if status != 200: raise Exception(explain(status, body))
        return body

    @staticmethod
    def _list_item(item):
        # the API stores times exactly as sent, so always send one in Kodi's time
        return dict(item, date_added=to_sql_timestamp(item.get("date_added") or format_timestamp()))

    @staticmethod
    def _list_changed(result):
        if result is None: raise Exception('Watch History API list request failed')
        return bool(result.get("changed", result.get("queued")))
