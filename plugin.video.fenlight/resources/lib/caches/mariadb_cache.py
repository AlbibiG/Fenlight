import threading
from ast import literal_eval
from collections import deque
from datetime import datetime, timedelta, timezone

import pymysql
import xbmc
from caches.base_cache import SQLDatabaseWrapper
from modules.settings import watch_history_server_ip, watch_history_port, watch_history_username, watch_history_password, watch_history_database_name, watch_history_profile_name, utc_offset
from modules.utils import format_timestamp

_pool = None
_pool_lock = threading.Lock()


class _PooledConnection:
    def __init__(self, pool, connection):
        self._pool = pool
        self._connection = connection

    def __getattr__(self, name):
        return getattr(self._connection, name)

    def close(self):
        if self._pool is None:
            return self._connection.close()
        try:
            self._connection.ping(reconnect=True)
        except Exception:
            try:
                self._connection.close()
            except Exception:
                pass
            return
        self._pool.release(self._connection)

    def cursor(self, *args, **kwargs):
        return self._connection.cursor(*args, **kwargs)

    def commit(self):
        return self._connection.commit()

    def rollback(self):
        return self._connection.rollback()


class ConnectionPool:
    def __init__(self, max_connections=5):
        self.max_connections = max_connections
        self._available = deque()

    def get(self):
        with _pool_lock:
            while self._available:
                connection = self._available.popleft()
                try:
                    connection.ping(reconnect=True)
                except Exception:
                    try: connection.close()
                    except Exception: pass
                    continue
                return _PooledConnection(self, connection)
            return _PooledConnection(self, pymysql.connect(**get_connection_config()))

    def release(self, connection):
        with _pool_lock:
            if len(self._available) < self.max_connections:
                self._available.append(connection)
            else:
                try:
                    connection.close()
                except Exception:
                    pass


def get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ConnectionPool()
    return _pool

def _log_error(message):
    try:
        if xbmc is not None:
            xbmc.log('###Fen Light###: %s' % message, 2)
        else:
            print(message)
    except Exception:
        pass


def to_sql_timestamp(value):
    """Convert an ISO string, epoch value or datetime to a 'YYYY-MM-DD HH:MM:SS' string in Kodi's local time for TIMESTAMP columns.
    Values without timezone info are already local. Connections use time_zone '+00:00' so MariaDB stores them unshifted."""
    if value in (None, ''):
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        value = str(value).strip()
        try:
            if value.isdigit():
                dt = datetime.fromtimestamp(int(value), timezone.utc)
            else:
                dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except (ValueError, OverflowError, OSError):
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone(timedelta(hours=utc_offset()))).replace(tzinfo=None)
    return dt.strftime('%Y-%m-%d %H:%M:%S')


def utc_offset_label():
    """Current Kodi UTC offset as '+HH:MM', stored alongside timestamps in the utc_offset columns"""
    minutes = round(utc_offset() * 60)
    return '%s%02d:%02d' % ('-' if minutes < 0 else '+', abs(minutes) // 60, abs(minutes) % 60)


def get_connection_config():
    return {
        'host': watch_history_server_ip(),
        'port': int(watch_history_port()),
        'user': watch_history_username(),
        'password': watch_history_password(),
        'database': watch_history_database_name(),
        'charset': 'utf8mb4',
        'autocommit': True,
        'init_command': "SET time_zone = '+00:00'",
    }


def connect():
    try:
        return get_pool().get()
    except Exception as exc:
        _log_error('Watch History DB connection failed: %s' % exc)
        raise


def initialize_history_database():
    try:
        conn = connect()
    except Exception as exc:
        _log_error('Watch History DB connection failed: %s' % exc)
        return False
    try:
        with conn.cursor() as cursor:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS progress (
                    db_type text not null,
                    media_id text not null,
                    season integer,
                    episode integer,
                    resume_point text,
                    curr_time text,
                    last_played timestamp NULL DEFAULT NULL,
                    utc_offset varchar(6),
                    resume_id integer,
                    title text,
                    profile text,
                    unique (db_type, media_id, season, episode, profile)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS historical (
                    db_type text not null,
                    media_id text not null,
                    season integer,
                    episode integer,
                    resume_point text,
                    curr_time text,
                    started timestamp NULL DEFAULT NULL,
                    ended timestamp NULL DEFAULT NULL,
                    utc_offset varchar(6),
                    resume_id integer,
                    play_event text,
                    title text,
                    profile text,
                    unique (db_type, media_id, season, episode, started, profile, play_event)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS watched (
                    db_type text not null,
                    media_id text not null,
                    season integer,
                    episode integer,
                    last_played timestamp NULL DEFAULT NULL,
                    utc_offset varchar(6),
                    title text,
                    profile text,
                    unique (db_type, media_id, season, episode, profile)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS watched_status (
                    db_type text not null,
                    media_id text not null,
                    status text,
                    profile text,
                    unique (db_type, media_id, profile)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS personal_lists (
                    name text,
                    created timestamp NULL DEFAULT NULL,
                    utc_offset varchar(6),
                    sort_order integer,
                    profile text,
                    unique(name, profile)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS personal_list_items (
                    profile varchar(191) not null,
                    list_name varchar(191) not null,
                    media_id varchar(64) not null,
                    media_type varchar(16) not null,
                    title text,
                    date_added timestamp NULL DEFAULT NULL,
                    utc_offset varchar(6),
                    release_date text,
                    unique (profile, list_name, media_id, media_type)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            timestamp_columns = (
                ('progress', 'last_played'),
                ('historical', 'started'),
                ('historical', 'ended'),
                ('watched', 'last_played'),
                ('personal_lists', 'created'),
                ('personal_list_items', 'date_added')
            )
            for table_name, column_name in timestamp_columns:
                cursor.execute('SHOW COLUMNS FROM %s' % table_name)
                columns = {row[0].lower(): row for row in cursor.fetchall()}
                column = columns.get(column_name)
                if not column:
                    continue
                column_type, nullable, extra = column[1].lower(), column[2], (column[5] or '').lower()
                if column_type == 'timestamp' and nullable == 'YES' and 'on update' not in extra:
                    continue
                if not column_type.startswith('timestamp'):
                    # convert existing text values before changing the column type, invalid values become NULL
                    cursor.execute('SELECT DISTINCT %s FROM %s WHERE %s IS NOT NULL' % (column_name, table_name, column_name))
                    updates = [(to_sql_timestamp(row[0]), row[0]) for row in cursor.fetchall()]
                    cursor.executemany('UPDATE IGNORE %s SET %s = %%s WHERE %s = %%s' % (table_name, column_name, column_name),
                        [i for i in updates if i[0] != i[1]])
                    cursor.execute("UPDATE %s SET %s = NULL WHERE %s NOT REGEXP '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$'"
                        % (table_name, column_name, column_name))
                cursor.execute('ALTER TABLE %s MODIFY COLUMN %s timestamp NULL DEFAULT NULL' % (table_name, column_name))
            # utc_offset records the Kodi timezone each timestamp was written in, placed after the table's last timestamp column
            utc_offset_columns = (
                ('progress', 'last_played'),
                ('historical', 'ended'),
                ('watched', 'last_played'),
                ('personal_lists', 'created'),
                ('personal_list_items', 'date_added')
            )
            for table_name, after_column in utc_offset_columns:
                cursor.execute('SHOW COLUMNS FROM %s' % table_name)
                if 'utc_offset' not in {row[0].lower() for row in cursor.fetchall()}:
                    cursor.execute('ALTER TABLE %s ADD COLUMN utc_offset varchar(6) AFTER %s' % (table_name, after_column))
        conn.commit()
        return True
    except Exception as exc:
        _log_error('Watch History DB initialization failed: %s' % exc)
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def reconfigure_history_database():
    if not initialize_history_database():
        return False

    tables_schema = {
        'progress': {
            'columns': {
                'db_type': 'text not null', 'media_id': 'text not null',
                'season': 'integer', 'episode': 'integer',
                'resume_point': 'text', 'curr_time': 'text',
                'last_played': 'timestamp NULL DEFAULT NULL', 'utc_offset': 'varchar(6)', 'resume_id': 'integer',
                'title': 'text', 'profile': 'text'
            },
            'unique': ['db_type', 'media_id', 'season', 'episode', 'profile']
        },
        'historical': {
            'columns': {
                'db_type': 'text not null', 'media_id': 'text not null',
                'season': 'integer', 'episode': 'integer',
                'resume_point': 'text', 'curr_time': 'text',
                'started': 'timestamp NULL DEFAULT NULL', 'ended': 'timestamp NULL DEFAULT NULL', 'utc_offset': 'varchar(6)', 'resume_id': 'integer',
                'play_event': 'text', 'title': 'text', 'profile': 'text'
            },
            'unique': ['db_type', 'media_id', 'season', 'episode', 'profile', 'started', 'play_event']
        },
        'watched': {
            'columns': {
                'db_type': 'text not null', 'media_id': 'text not null',
                'season': 'integer', 'episode': 'integer',
                'last_played': 'timestamp NULL DEFAULT NULL', 'utc_offset': 'varchar(6)', 'title': 'text', 'profile': 'text'
            },
            'unique': ['db_type', 'media_id', 'season', 'episode', 'profile']
        },
        'watched_status': {
            'columns': {
                'db_type': 'text not null', 'media_id': 'text not null',
                'status': 'text', 'profile': 'text'
            },
            'unique': ['db_type', 'media_id', 'profile']
        },
        'personal_lists': {
            'columns': {
                'name': 'text', 'created': 'timestamp NULL DEFAULT NULL', 'utc_offset': 'varchar(6)', 'sort_order': 'integer', 'profile': 'text'
            },
            'unique': ['name', 'profile']
        },
        'personal_list_items': {
            'columns': {
                'profile': 'varchar(191) not null', 'list_name': 'varchar(191) not null',
                'media_id': 'varchar(64) not null', 'media_type': 'varchar(16) not null',
                'title': 'text', 'date_added': 'timestamp NULL DEFAULT NULL', 'utc_offset': 'varchar(6)', 'release_date': 'text'
            },
            'unique': ['profile', 'list_name', 'media_id', 'media_type']
        }
    }

    try:
        conn = connect()
    except Exception as exc:
        _log_error('Watch History DB connection failed during reconfigure: %s' % exc)
        return False

    try:
        with conn.cursor() as cursor:
            for table_name, schema in tables_schema.items():
                
                # --- A. CHECK AND ADD MISSING COLUMNS ---
                cursor.execute(f"SHOW COLUMNS FROM {table_name}")
                existing_columns = {row[0].lower() for row in cursor.fetchall()}

                for col_name, col_type in schema['columns'].items():
                    if col_name.lower() not in existing_columns:
                        _log_error(f"Reconfigure: Adding column '{col_name}' to table '{table_name}'")
                        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}")

                # --- B. CHECK AND REBUILD UNIQUE CONSTRAINTS ---
                cursor.execute(f"SHOW INDEX FROM {table_name} WHERE Non_unique = 0")
                indexes = cursor.fetchall()

                # Group index columns by the index name
                index_map = {}
                for row in indexes:
                    idx_name, col_name = row[2], row[4].lower()
                    if idx_name == 'PRIMARY': 
                        continue
                    index_map.setdefault(idx_name, []).append(col_name)

                expected_unique = schema['unique']
                
                # Check if we already have an index matching our exact expected columns
                match_found = any(set(cols) == set(expected_unique) for cols in index_map.values())

                if not match_found:
                    _log_error(f"Reconfigure: Rebuilding unique constraints for table '{table_name}'")
                    
                    # 1. Drop the outdated unique constraints
                    for idx_name in index_map:
                        cursor.execute(f"ALTER TABLE {table_name} DROP INDEX {idx_name}")
                    
                    # 2. Add the new unique constraint matching the expected schema
                    cols_str = ', '.join(expected_unique)
                    cursor.execute(f"ALTER TABLE {table_name} ADD UNIQUE ({cols_str})")

        conn.commit()
        return True

    except Exception as exc:
        _log_error('Watch History DB reconfiguration failed: %s' % exc)
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


class MariaDBWatchedDatabase(SQLDatabaseWrapper):
    """Direct MariaDB watched database. Provides the same watched methods as the SQLite and API databases."""
    def record_historical_play(self, media_type, media_id, season=None, episode=None, title='', started='', ended='', play_event='watched', resume_point='', curr_time='', resume_id=0):
        profile = watch_history_profile_name()
        started, ended = to_sql_timestamp(started), to_sql_timestamp(ended)
        if play_event == 'started':
            self.execute('''INSERT IGNORE INTO historical
                        (db_type, media_id, season, episode, resume_point, curr_time, started, ended, utc_offset, play_event, resume_id, title, profile)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                        (media_type, media_id, season, episode, str(resume_point), str(curr_time), started, ended, utc_offset_label(), play_event, int(resume_id), title, profile))
        else:
            self.execute('''UPDATE historical
                        SET resume_point = ?, curr_time = ?, ended = ?, utc_offset = ?, resume_id = ?, title = ?, play_event = ?
                        WHERE db_type = ? AND media_id = ? AND season IS ? AND episode IS ? AND profile = ? AND started = ?
                        ORDER BY started DESC
                        LIMIT 1''',
                        (str(resume_point), str(curr_time), ended, utc_offset_label(), int(resume_id), title, play_event,
                        media_type, media_id, season, episode, profile, started))

    def get_watched_movies(self):
        return self.execute('SELECT media_id, title, CAST(last_played AS CHAR) FROM watched WHERE db_type = ? and profile = ?',
            ('movie', watch_history_profile_name())).fetchall()

    def get_watched_info_tvshow(self):
        return self.execute('SELECT media_id, season, episode, title, CAST(MAX(last_played) AS CHAR), COUNT(*) AS COUNTER FROM watched WHERE db_type = ? and profile = ? GROUP BY media_id',
            ('episode', watch_history_profile_name())).fetchall()

    def get_watched_info_season(self, media_id):
        return self.execute('SELECT season, COUNT(*) AS COUNTER FROM watched WHERE db_type = ? AND media_id = ? AND profile = ? GROUP BY media_id, season',
            ('episode', str(media_id), watch_history_profile_name())).fetchall()

    def get_watched_info_episode(self, media_id):
        return self.execute('SELECT season, episode FROM watched WHERE db_type = ? AND media_id = ? AND profile = ?',
            ('episode', str(media_id), watch_history_profile_name())).fetchall()

    def get_recently_watched(self, media_type, short_list=1):
        if short_list:
            return self.execute('SELECT media_id, season, episode, title, CAST(last_played AS CHAR) FROM watched WHERE db_type = ? and profile = ? ORDER BY last_played DESC',
                (media_type, watch_history_profile_name())).fetchall()
        return self.execute('SELECT media_id, season, episode, title, CAST(last_played AS CHAR) FROM watched WHERE db_type = ? and profile = ?',
            (media_type, watch_history_profile_name())).fetchall()

    def get_hidden_progress_items(self):
        return self.execute('SELECT status FROM watched_status WHERE db_type = ? and profile = ?',
            ('hidden_progress_items', watch_history_profile_name())).fetchone()[0]

    def update_hidden_progress_items(self, media_id, action):
        media_id = int(media_id)
        try: current_items = literal_eval(self.get_hidden_progress_items()) or []
        except: current_items = []
        if action == 'drop':
            if media_id not in current_items: current_items.append(media_id)
        elif media_id in current_items: current_items.remove(media_id)
        self.execute('REPLACE INTO watched_status VALUES (?, ?, ?, ?)', ('hidden_progress_items', 'hidden', repr(current_items), watch_history_profile_name()))

    def get_bookmarks_movie(self):
        return self.execute('SELECT media_id, resume_point, curr_time, resume_id FROM progress WHERE db_type = ? and profile = ?',
            ('movie', watch_history_profile_name())).fetchall()

    def get_bookmarks_episode(self, media_id, season):
        return self.execute('SELECT resume_point, curr_time, resume_id, episode FROM progress WHERE db_type = ? AND media_id = ? AND season = ? AND profile = ?',
            ('episode', str(media_id), int(season), watch_history_profile_name())).fetchall()

    def get_in_progress_movies(self):
        return self.execute('SELECT media_id, title, CAST(last_played AS CHAR) FROM progress WHERE db_type = ? and profile = ?',
            ('movie', watch_history_profile_name())).fetchall()

    def get_in_progress_episodes(self):
        return self.execute('SELECT media_id, season, episode, resume_point, CAST(last_played AS CHAR), title FROM progress WHERE db_type = ? and profile = ?',
            ('episode', watch_history_profile_name())).fetchall()

    def set_bookmark(self, media_type, media_id, curr_time, total_time, title='', season=None, episode=None):
        resume_point = round((float(curr_time) - 5) / float(total_time) * 100, 1)
        last_played = to_sql_timestamp(format_timestamp())
        self.execute('REPLACE INTO progress (db_type, media_id, season, episode, resume_point, curr_time, last_played, utc_offset, resume_id, title, profile) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (media_type, media_id, season, episode, str(resume_point), str(curr_time), last_played, utc_offset_label(), 0, title, watch_history_profile_name()))

    def erase_bookmark(self, media_type, media_id, season=None, episode=None, refresh='false'):
        self.execute('DELETE FROM progress where db_type = ? and media_id = ? and season IS ? and episode IS ? and profile = ?',
            (media_type, media_id, season, episode, watch_history_profile_name()))

    def batch_erase_bookmark(self, insert_list, action=None):
        profile_name = watch_history_profile_name()
        self.executemany('DELETE FROM progress where db_type = ? and media_id = ? and season = ? and episode = ? and profile = ?',
            [item + (profile_name,) for item in insert_list])

    def watched_status_mark(self, media_type, media_id, action, season=None, episode=None, title='', last_played=None):
        profile_name = watch_history_profile_name()
        if action == 'mark_as_watched':
            last_played = to_sql_timestamp(last_played or format_timestamp())
            self.execute('REPLACE INTO watched (db_type, media_id, season, episode, last_played, utc_offset, title, profile) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (media_type, media_id, season, episode, last_played, utc_offset_label(), title, profile_name))
        elif action == 'mark_as_unwatched':
            self.execute('DELETE FROM watched WHERE (db_type = ? and media_id = ? and season IS ? and episode IS ? and profile = ?)', (media_type, media_id, season, episode, profile_name))

    def batch_watched_status_mark(self, insert_list, action):
        profile_name = watch_history_profile_name()
        params = [item + (profile_name,) for item in insert_list]
        if action == 'mark_as_watched':
            offset = utc_offset_label()
            params = [i[:4] + (to_sql_timestamp(i[4]), offset) + i[5:] for i in params]
            self.executemany('INSERT IGNORE INTO watched (db_type, media_id, season, episode, last_played, utc_offset, title, profile) VALUES (?, ?, ?, ?, ?, ?, ?, ?)', params)
        elif action == 'mark_as_unwatched':
            self.executemany('DELETE FROM watched WHERE (db_type = ? and media_id = ? and season IS ? and episode IS ? and profile = ?)', params)

    def get_next_episodes(self, nextep_content=0):
        profile_name = watch_history_profile_name()
        if nextep_content == 0:
            return self.execute('''WITH cte AS (SELECT *, ROW_NUMBER() OVER (PARTITION BY media_id ORDER BY season DESC, episode DESC) rn FROM watched WHERE db_type = ? and profile = ?)
                                SELECT media_id, season, episode, title, CAST(last_played AS CHAR) FROM cte WHERE rn = 1''', ('episode', profile_name)).fetchall()
        return self.execute('SELECT media_id, season, episode, title, CAST(MAX(last_played) AS CHAR), COUNT(*) AS COUNTER FROM watched WHERE db_type = ? and profile = ? GROUP BY media_id',
            ('episode', profile_name)).fetchall()

    def make_list(self, list_name, sort_order):
        self.execute('INSERT INTO personal_lists (name, created, utc_offset, sort_order, profile) VALUES (?, ?, ?, ?, ?)',
            (list_name, to_sql_timestamp(format_timestamp()), utc_offset_label(), sort_order, watch_history_profile_name()))

    def delete_list(self, list_name):
        profile = watch_history_profile_name()
        self.execute('DELETE FROM personal_list_items WHERE list_name=? AND profile=?', (list_name, profile))
        self.execute('DELETE FROM personal_lists WHERE name=? AND profile=?', (list_name, profile))
        self.execute('OPTIMIZE TABLE personal_lists')

    def clear_list_contents(self, list_name):
        self.execute('DELETE FROM personal_list_items WHERE list_name=? AND profile=?',
            (list_name, watch_history_profile_name()))

    def update_list_details(self, list_name, sort_order, original_name):
        profile = watch_history_profile_name()
        self.execute('UPDATE personal_lists SET name=?, sort_order=? WHERE name=? and profile=?', (list_name, sort_order, original_name, profile))
        if list_name != original_name:
            self.execute('UPDATE personal_list_items SET list_name=? WHERE list_name=? and profile=?', (list_name, original_name, profile))

    def get_lists(self):
        return self.execute('SELECT l.name, (SELECT COUNT(*) FROM personal_list_items i WHERE i.list_name = l.name and i.profile = l.profile), l.sort_order FROM personal_lists l WHERE l.profile=?',
            (watch_history_profile_name(),)).fetchall()

    def get_list(self, list_name):
        rows = self.execute(
            'SELECT media_id, media_type, title, CAST(date_added AS CHAR), release_date FROM personal_list_items '
            'WHERE list_name=? AND profile=? ORDER BY date_added',
            (list_name, watch_history_profile_name())
        ).fetchall()
        return [
            {'media_id': row[0], 'media_type': row[1], 'type': row[1], 'title': row[2],
             'date_added': row[3], 'release_date': row[4]}
            for row in rows
        ]

    def add_list_item(self, list_name, item):
        media_type = item.get('media_type') or item.get('type')
        return self.execute(
            'INSERT IGNORE INTO personal_list_items '
            '(profile, list_name, media_id, media_type, title, date_added, utc_offset, release_date) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (watch_history_profile_name(), list_name, str(item['media_id']), media_type,
             item.get('title'), to_sql_timestamp(item.get('date_added')), utc_offset_label(), item.get('release_date'))
        ).rowcount > 0

    def remove_list_item(self, list_name, media_id):
        return self.execute(
            'DELETE FROM personal_list_items WHERE list_name=? AND media_id=? AND profile=?',
            (list_name, str(media_id), watch_history_profile_name())
        ).rowcount > 0

    def add_many_list_items(self, list_name, items):
        profile = watch_history_profile_name()
        if not items:
            return
        offset = utc_offset_label()
        self.executemany(
            'INSERT IGNORE INTO personal_list_items '
            '(profile, list_name, media_id, media_type, title, date_added, utc_offset, release_date) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            [
                (profile, list_name, str(item['media_id']), item.get('media_type') or item.get('type'),
                 item.get('title'), to_sql_timestamp(item.get('date_added')), offset, item.get('release_date'))
                for item in items
            ]
        )
