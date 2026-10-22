import threading
from ast import literal_eval
from collections import deque

import pymysql
import xbmc
from caches.base_cache import SQLDatabaseWrapper
from modules.settings import watch_history_server_ip, watch_history_port, watch_history_username, watch_history_password, watch_history_database_name, watch_history_profile_name
from modules.utils import format_timestamp, to_sql_timestamp, utc_offset_label

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


# Tables created before the key columns were VARCHAR have them as TEXT, which MariaDB can only make unique with a
# hash that no query can use, so every lookup scanned the whole table. (table, [(column, length, not null)], unique key)
# The Fen Light API (api/app/fenlight.py) converts the same tables the same way.
KEY_COLUMNS = (
    ('progress', (('db_type', 32, True), ('media_id', 64, True), ('profile', 191, False)),
     ('db_type', 'media_id', 'season', 'episode', 'profile')),
    ('historical', (('db_type', 32, True), ('media_id', 64, True), ('play_event', 32, False), ('profile', 191, False)),
     ('db_type', 'media_id', 'season', 'episode', 'started', 'profile', 'play_event')),
    ('watched', (('db_type', 32, True), ('media_id', 64, True), ('profile', 191, False)),
     ('db_type', 'media_id', 'season', 'episode', 'profile')),
    ('watched_status', (('db_type', 32, True), ('media_id', 64, True), ('profile', 191, False)),
     ('db_type', 'media_id', 'profile')),
    ('personal_lists', (('name', 191, False), ('profile', 191, False)),
     ('name', 'profile'))
)


def _migrate_key_columns(cursor):
    """Turn TEXT key columns into VARCHAR and rebuild the unique key as an ordinary index that lookups can use"""
    for table_name, key_columns, unique_key in KEY_COLUMNS:
        cursor.execute('SHOW COLUMNS FROM %s' % table_name)
        types = {row[0].lower(): row[1].lower() for row in cursor.fetchall()}
        text_columns = [i for i in key_columns if types.get(i[0]) == 'text']
        if not text_columns:
            continue
        too_long = []
        for column_name, length, _ in text_columns:
            cursor.execute('SELECT COALESCE(MAX(CHAR_LENGTH(%s)), 0) FROM %s' % (column_name, table_name))
            if cursor.fetchone()[0] > length:
                too_long.append(column_name)
        if too_long:
            _log_error('Leaving %s as it is: some %s values are too long for an indexed column' % (table_name, ', '.join(too_long)))
            continue
        cursor.execute('SHOW INDEX FROM %s WHERE Non_unique = 0' % table_name)
        changes = ['DROP INDEX `%s`' % name for name in {row[2] for row in cursor.fetchall() if row[2] != 'PRIMARY'}]
        changes += ['MODIFY COLUMN %s varchar(%d)%s' % (name, length, ' not null' if not_null else '') for name, length, not_null in text_columns]
        changes.append('ADD UNIQUE (%s)' % ', '.join(unique_key))
        try:
            cursor.execute('ALTER TABLE %s %s' % (table_name, ', '.join(changes)))
        except Exception as exc:
            # ALTER TABLE is all or nothing, so the table keeps working as before, just without a usable index
            _log_error('Could not convert the key columns of %s: %s' % (table_name, exc))


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
                    db_type varchar(32) not null,
                    media_id varchar(64) not null,
                    season integer,
                    episode integer,
                    resume_point text,
                    curr_time text,
                    last_played timestamp NULL DEFAULT NULL,
                    utc_offset varchar(6),
                    resume_id integer,
                    title text,
                    profile varchar(191),
                    unique (db_type, media_id, season, episode, profile)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS historical (
                    db_type varchar(32) not null,
                    media_id varchar(64) not null,
                    season integer,
                    episode integer,
                    resume_point text,
                    curr_time text,
                    started timestamp NULL DEFAULT NULL,
                    ended timestamp NULL DEFAULT NULL,
                    utc_offset varchar(6),
                    resume_id integer,
                    play_event varchar(32),
                    title text,
                    profile varchar(191),
                    unique (db_type, media_id, season, episode, started, profile, play_event)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS watched (
                    db_type varchar(32) not null,
                    media_id varchar(64) not null,
                    season integer,
                    episode integer,
                    last_played timestamp NULL DEFAULT NULL,
                    utc_offset varchar(6),
                    title text,
                    profile varchar(191),
                    unique (db_type, media_id, season, episode, profile)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS watched_status (
                    db_type varchar(32) not null,
                    media_id varchar(64) not null,
                    status text,
                    profile varchar(191),
                    unique (db_type, media_id, profile)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS personal_lists (
                    name varchar(191),
                    created timestamp NULL DEFAULT NULL,
                    utc_offset varchar(6),
                    sort_order integer,
                    profile varchar(191),
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
            _migrate_key_columns(cursor)
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
                'db_type': 'varchar(32) not null', 'media_id': 'varchar(64) not null',
                'season': 'integer', 'episode': 'integer',
                'resume_point': 'text', 'curr_time': 'text',
                'last_played': 'timestamp NULL DEFAULT NULL', 'utc_offset': 'varchar(6)', 'resume_id': 'integer',
                'title': 'text', 'profile': 'varchar(191)'
            },
            'unique': ['db_type', 'media_id', 'season', 'episode', 'profile']
        },
        'historical': {
            'columns': {
                'db_type': 'varchar(32) not null', 'media_id': 'varchar(64) not null',
                'season': 'integer', 'episode': 'integer',
                'resume_point': 'text', 'curr_time': 'text',
                'started': 'timestamp NULL DEFAULT NULL', 'ended': 'timestamp NULL DEFAULT NULL', 'utc_offset': 'varchar(6)', 'resume_id': 'integer',
                'play_event': 'varchar(32)', 'title': 'text', 'profile': 'varchar(191)'
            },
            'unique': ['db_type', 'media_id', 'season', 'episode', 'profile', 'started', 'play_event']
        },
        'watched': {
            'columns': {
                'db_type': 'varchar(32) not null', 'media_id': 'varchar(64) not null',
                'season': 'integer', 'episode': 'integer',
                'last_played': 'timestamp NULL DEFAULT NULL', 'utc_offset': 'varchar(6)', 'title': 'text', 'profile': 'varchar(191)'
            },
            'unique': ['db_type', 'media_id', 'season', 'episode', 'profile']
        },
        'watched_status': {
            'columns': {
                'db_type': 'varchar(32) not null', 'media_id': 'varchar(64) not null',
                'status': 'text', 'profile': 'varchar(191)'
            },
            'unique': ['db_type', 'media_id', 'profile']
        },
        'personal_lists': {
            'columns': {
                'name': 'varchar(191)', 'created': 'timestamp NULL DEFAULT NULL', 'utc_offset': 'varchar(6)', 'sort_order': 'integer', 'profile': 'varchar(191)'
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
    _episodes = None    # watched episodes and bookmarks per show, filled by prefetch_episodes()

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

    def prefetch_episodes(self, media_ids):
        """Fetch the watched episodes and bookmarks of many shows in two queries; the per-show methods below are then
        answered from them instead of querying once or twice for every show in an episode list"""
        media_ids = list(dict.fromkeys(str(i) for i in media_ids if i))[:1000]
        if not media_ids: return
        profile, in_list = watch_history_profile_name(), ', '.join(['?'] * len(media_ids))
        watched, bookmarks = {i: [] for i in media_ids}, {i: [] for i in media_ids}
        for media_id, season, episode in self.execute('SELECT media_id, season, episode FROM watched WHERE db_type = ? AND profile = ? AND media_id IN (%s)' % in_list,
                ('episode', profile, *media_ids)).fetchall():
            watched.setdefault(media_id, []).append((season, episode))
        for media_id, season, resume_point, curr_time, resume_id, episode in self.execute(
                'SELECT media_id, season, resume_point, curr_time, resume_id, episode FROM progress WHERE db_type = ? AND profile = ? AND media_id IN (%s)' % in_list,
                ('episode', profile, *media_ids)).fetchall():
            bookmarks.setdefault(media_id, []).append((season, resume_point, curr_time, resume_id, episode))
        self._episodes = {'watched': watched, 'bookmarks': bookmarks}

    def get_watched_info_episode(self, media_id):
        cached = self._episodes and self._episodes['watched'].get(str(media_id))
        if cached is not None: return cached
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

    def hide_progress_items(self, media_ids):
        """Hide many shows from the progress lists at once; returns how many weren't hidden yet"""
        try: current_items = literal_eval(self.get_hidden_progress_items()) or []
        except: current_items = []
        new_items = [int(i) for i in dict.fromkeys(media_ids) if int(i) not in current_items]
        if new_items:
            self.execute('REPLACE INTO watched_status VALUES (?, ?, ?, ?)', ('hidden_progress_items', 'hidden', repr(current_items + new_items), watch_history_profile_name()))
        return len(new_items)

    def get_bookmarks_movie(self):
        return self.execute('SELECT media_id, resume_point, curr_time, resume_id FROM progress WHERE db_type = ? and profile = ?',
            ('movie', watch_history_profile_name())).fetchall()

    def get_bookmarks_episode(self, media_id, season):
        cached = self._episodes and self._episodes['bookmarks'].get(str(media_id))
        if cached is not None:
            # cached rows are (season, resume_point, curr_time, resume_id, episode)
            return [row[1:] for row in cached if row[0] == int(season)]
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
        # a movie's empty season and episode never match in the unique key, so remove its old row first
        self.execute('DELETE FROM progress WHERE db_type = ? and media_id = ? and season IS ? and episode IS ? and profile = ?',
            (media_type, media_id, season, episode, watch_history_profile_name()))
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

    def clear_all_bookmarks(self, media_type):
        self.execute('DELETE FROM progress WHERE db_type = ? AND profile = ?', (media_type, watch_history_profile_name()))

    def watched_status_mark(self, media_type, media_id, action, season=None, episode=None, title='', last_played=None):
        profile_name = watch_history_profile_name()
        if action == 'mark_as_watched':
            last_played = to_sql_timestamp(last_played or format_timestamp())
            # a movie's empty season and episode never match in the unique key, so remove its old row first
            self.execute('DELETE FROM watched WHERE (db_type = ? and media_id = ? and season IS ? and episode IS ? and profile = ?)', (media_type, media_id, season, episode, profile_name))
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

    def get_profiles(self):
        """[{'profile', 'watched', 'in_progress', 'lists'}, ...]: the profiles that already have data in this database"""
        rows = self.execute('''SELECT profile, SUM(watched), SUM(in_progress), SUM(lists) FROM (
                SELECT profile, COUNT(*) AS watched, 0 AS in_progress, 0 AS lists FROM watched GROUP BY profile
                UNION ALL SELECT profile, 0, COUNT(*), 0 FROM progress GROUP BY profile
                UNION ALL SELECT profile, 0, 0, COUNT(*) FROM personal_lists GROUP BY profile
            ) counts WHERE profile IS NOT NULL AND profile <> '' GROUP BY profile ORDER BY profile''').fetchall()
        return [{'profile': p, 'watched': int(w), 'in_progress': int(i), 'lists': int(l)} for p, w, i, l in rows]

    def _insert_missing(self, table_name, columns, rows):
        """Insert the rows the table doesn't have yet. Rows with a season and episode are caught by the unique key (one
        batched INSERT IGNORE); movies, whose empty season and episode the unique key doesn't match, are checked one by one.
        Each row starts with db_type, media_id, season, episode and ends with profile."""
        keyed = [row for row in rows if row[2] is not None and row[3] is not None]
        keyless = [row for row in rows if row[2] is None or row[3] is None]
        placeholders = ', '.join(['?'] * len(columns))
        if keyed:
            self.executemany('INSERT IGNORE INTO %s (%s) VALUES (%s)' % (table_name, ', '.join(columns), placeholders), keyed)
        if keyless:
            self.executemany('INSERT INTO %s (%s) SELECT %s FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM %s WHERE db_type = ? AND media_id = ? '
                'AND season IS ? AND episode IS ? AND profile = ?)' % (table_name, ', '.join(columns), placeholders, table_name),
                [row + row[:4] + row[-1:] for row in keyless])

    def import_history(self, watched=(), progress=(), hidden=(), lists=(), callback=None):
        """Copy history from another source into this profile, keeping entries it already has.
        watched rows: (media_type, media_id, season, episode, last_played, title)
        progress rows: (media_type, media_id, season, episode, resume_point, curr_time, last_played, resume_id, title)
        lists: (name, sort_order, items). Returns what was added."""
        profile, offset = watch_history_profile_name(), utc_offset_label()
        def count(table_name):
            return self.execute('SELECT COUNT(*) FROM %s WHERE profile = ?' % table_name, (profile,)).fetchone()[0]
        before = {'watched': count('watched'), 'progress': count('progress'), 'list_items': count('personal_list_items')}
        watched_columns = ('db_type', 'media_id', 'season', 'episode', 'last_played', 'utc_offset', 'title', 'profile')
        progress_columns = ('db_type', 'media_id', 'season', 'episode', 'resume_point', 'curr_time', 'last_played', 'utc_offset', 'resume_id', 'title', 'profile')
        watched = [(i[0], str(i[1]), i[2], i[3], to_sql_timestamp(i[4]), offset, i[5], profile) for i in watched]
        progress = [(i[0], str(i[1]), i[2], i[3], str(i[4]), str(i[5]), to_sql_timestamp(i[6]), offset, i[7] or 0, i[8], profile) for i in progress]
        existing = {row[0] for row in self.get_lists()} if lists else set()
        jobs = [(self._insert_missing, ('watched', watched_columns, watched[n:n + 1000])) for n in range(0, len(watched), 1000)]
        jobs += [(self._insert_missing, ('progress', progress_columns, progress[n:n + 1000])) for n in range(0, len(progress), 1000)]
        for name, sort_order, items in lists:
            if name not in existing: jobs.append((self.make_list, (name, sort_order)))
            jobs += [(self.add_many_list_items, (name, items[n:n + 1000])) for n in range(0, len(items), 1000)]
        added_hidden = self.hide_progress_items(hidden) if hidden else 0
        for done, (function, args) in enumerate(jobs, 1):
            function(*args)
            if callback: callback(done, len(jobs))
        return {'watched': count('watched') - before['watched'], 'progress': count('progress') - before['progress'], 'hidden': added_hidden,
                'lists': len([i for i in lists if i[0] not in existing]), 'list_items': count('personal_list_items') - before['list_items']}


def test_connection():
    """(ok, message) about whether Fen Light can use the MariaDB database with the current settings"""
    try:
        conn = pymysql.connect(**dict(get_connection_config(), connect_timeout=5))
        conn.close()
    except Exception as exc:
        return False, "Can't log in to MariaDB at [B]%s:%s[/B] as [B]%s[/B].[CR][CR]%s" % (
            watch_history_server_ip(), watch_history_port(), watch_history_username(), exc)
    profile = watch_history_profile_name()
    try: known = [i['profile'] for i in MariaDBWatchedDatabase(connect).get_profiles()]
    except Exception: known = []    # the tables don't exist yet: Fen Light creates them when it starts
    detail = 'has history' if profile in known else 'has no history yet'
    return True, 'Connected to database [B]%s[/B]. Profile [B]%s[/B] %s.' % (watch_history_database_name(), profile, detail)
