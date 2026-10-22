import json
import urllib.request
import urllib.parse
from modules.settings import (
    watch_history_api_address,
    watch_history_api_key,
    watch_history_database_name,
    watch_history_profile_name
)
from modules.kodi_utils import logger
from modules.utils import format_timestamp, to_sql_timestamp, utc_offset_label

class WatchHistoryClient:

    def __init__(self):
        self.base_url = f"{watch_history_api_address()}/api"
        self.profile = watch_history_profile_name()

    def _request(self, endpoint, method="GET", data=None):
        url = f"{self.base_url}/{endpoint}"
        headers = {
            "Content-Type": "application/json",
            "X-API-Key": watch_history_api_key(),
            "X-Database-Name": watch_history_database_name(),
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        body = json.dumps(data).encode("utf-8") if data else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                if response.status == 200:
                    return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                logger('_request', error_message=f"API Unauthorized [{endpoint}], check your API key.", severity = 'high')
            else:
                logger('_request', error_message=f"API Request Failed [{endpoint}]: {exc}", severity = 'high')
        except Exception as exc:
            logger('_request', error_message=f"API Request Failed [{endpoint}]: {exc}", severity = 'high')
        return None

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
        return self._request("history/record", method="POST", data=payload)

    # --- Watched Info ---
    def get_watched_movies(self):
        params = urllib.parse.urlencode({"profile": self.profile})
        return self._request(f"watched/movies?{params}")

    def get_watched_info_tvshow(self):
        params = urllib.parse.urlencode({"profile": self.profile})
        return self._request(f"watched/tvshow/info?{params}")

    def get_watched_info_season(self, media_id):
        params = urllib.parse.urlencode({"media_id": media_id, "profile": self.profile})
        return self._request(f"watched/season/info?{params}")

    def get_watched_info_episode(self, media_id):
        params = urllib.parse.urlencode({"media_id": media_id, "profile": self.profile})
        return self._request(f"watched/episode/info?{params}")

    def get_recently_watched(self, media_type, short_list=1):
        params = urllib.parse.urlencode({"media_type": media_type, "short_list": short_list, "profile": self.profile})
        return self._request(f"watched/recently?{params}")

    # --- Hidden Progress ---
    def get_hidden_progress_items(self):
        params = urllib.parse.urlencode({"profile": self.profile})
        return self._request(f"hidden/progress?{params}")

    def update_hidden_progress_items(self, media_id, action):
        payload = {
            "media_id": media_id,
            "action": action,
            "profile": self.profile
        }
        return self._request("hidden/progress/update", method="POST", data=payload)

    # --- Bookmarks & Progress ---
    def get_bookmarks_movie(self):
        params = urllib.parse.urlencode({"profile": self.profile})
        return self._request(f"bookmarks/movies/info?{params}")

    def get_bookmarks_episode(self, media_id, season):
        params = urllib.parse.urlencode({"media_id": media_id, "season": season, "profile": self.profile})
        return self._request(f"bookmarks/episode/info?{params}")

    def get_in_progress_movies(self):
        params = urllib.parse.urlencode({"profile": self.profile})
        return self._request(f"progress/movies?{params}")

    def get_in_progress_episodes(self):
        params = urllib.parse.urlencode({"profile": self.profile})
        return self._request(f"progress/episodes?{params}")

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
        return self._request("bookmarks/set", method="POST", data=payload)

    def erase_bookmark(self, media_type, media_id, season=None, episode=None, refresh="false"):
        payload = {
            "media_type": media_type,
            "media_id": media_id,
            "season": season,
            "episode": episode,
            "refresh": refresh,
            "profile": self.profile
        }
        return self._request("bookmarks/erase", method="POST", data=payload)

    def batch_erase_bookmark(self, insert_list, action):
        payload = {
            "insert_list": insert_list,
            "action": action,
            "profile": self.profile
        }
        return self._request("bookmarks/batch-erase", method="POST", data=payload)

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
        return self._request("watched/status", method="POST", data=payload)

    def batch_watched_status_mark(self, insert_list, action):
        # mark_as_watched rows are (media_type, media_id, season, episode, last_played, title)
        if action == "mark_as_watched": insert_list = [i[:4] + (to_sql_timestamp(i[4]),) + tuple(i[5:]) for i in insert_list]
        payload = {
            "insert_list": insert_list,
            "action": action,
            "utc_offset": utc_offset_label(),
            "profile": self.profile
        }
        return self._request("watched/batch-status", method="POST", data=payload)

    # --- TV Shows Next Episodes ---
    def get_next_episodes(self, nextep_content=0):
        params = urllib.parse.urlencode({"nextep_content": nextep_content, "profile": self.profile})
        return self._request(f"tvshows/next-episodes?{params}")

    def make_list(self, list_name, sort_order):
        payload = {
            "list_name": list_name,
            "sort_order": sort_order,
            "created": to_sql_timestamp(format_timestamp()),
            "utc_offset": utc_offset_label(),
            "profile": self.profile
        }
        return self._request("lists/make", method="POST", data=payload)

    def delete_list(self, list_name):
        payload = {
            "list_name": list_name,
            "profile": self.profile
        }
        return self._request("lists/delete", method="POST", data=payload)

    def clear_list_contents(self, list_name):
        payload = {
            "list_name": list_name,
            "profile": self.profile
        }
        return self._request("lists/clear-contents", method="POST", data=payload)

    def update_list_details(self, list_name, sort_order, original_name):
        payload = {
            "original_name": original_name,
            "new_list_name": list_name,
            "sort_order": sort_order,
            "profile": self.profile
        }
        return self._request("lists/update-details", method="POST", data=payload)

    def get_lists(self):
        params = urllib.parse.urlencode({"profile": self.profile})
        return self._request(f"lists?{params}")

    def get_list(self, list_name):
        params = urllib.parse.urlencode({"list_name": list_name, "profile": self.profile})
        return self._request(f"lists/get?{params}")

    def add_list_item(self, list_name, item):
        payload = {
            "list_name": list_name,
            "item": self._list_item(item),
            "utc_offset": utc_offset_label(),
            "profile": self.profile
        }
        return self._list_changed(self._request("lists/add-item", method="POST", data=payload))

    def remove_list_item(self, list_name, media_id):
        payload = {
            "list_name": list_name,
            "media_id": str(media_id),
            "profile": self.profile
        }
        return self._list_changed(self._request("lists/remove-item", method="POST", data=payload))

    def add_many_list_items(self, list_name, items):
        payload = {
            "list_name": list_name,
            "items": [self._list_item(item) for item in items],
            "utc_offset": utc_offset_label(),
            "profile": self.profile
        }
        return self._request("lists/add-many-items", method="POST", data=payload)

    @staticmethod
    def _list_item(item):
        # the API stores times exactly as sent, so always send one in Kodi's time
        return dict(item, date_added=to_sql_timestamp(item.get("date_added") or format_timestamp()))

    @staticmethod
    def _list_changed(result):
        if result is None: raise Exception('Watch History API list request failed')
        return bool(result.get("changed"))
