# -*- coding: utf-8 -*-
import os
import time
import pymysql

try:
    import xbmc
except Exception:
    xbmc = None

try:
    from caches.settings_cache import get_setting
except Exception:
    def get_setting(setting_id, fallback=''):
        return os.environ.get(setting_id.replace('fenlight.', 'FENLIGHT_').upper().replace('.', '_'), fallback)


TABLE_NAME = 'progress'
HISTORY_TABLE_NAME = 'watch_history'


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
        'host': get_setting('fenlight.watch_history.server_ip', '127.0.0.1'),
        'port': int(get_setting('fenlight.watch_history.port', '3306')),
        'user': get_setting('fenlight.watch_history.username', 'root'),
        'password': get_setting('fenlight.watch_history.password', ''),
        'database': get_setting('fenlight.watch_history.database_name', 'fenlight'),
        'charset': 'utf8mb4',
        'autocommit': True,
    }


def connect():
    config = get_connection_config()
    try:
        return pymysql.connect(**config)
    except Exception as exc:
        _log_error('Watch History DB connection failed: %s' % exc)
        raise


def _get_column_names(cursor):
    try:
        cursor.execute('SHOW COLUMNS FROM %s' % TABLE_NAME)
        rows = cursor.fetchall() or []
    except Exception:
        return set()

    column_names = []
    for row in rows:
        if isinstance(row, dict):
            column_name = row.get('Field') or row.get('field') or row.get('name')
        else:
            column_name = row[0] if row else None
        if column_name:
            column_names.append(column_name)
    return set(column_names)


def _get_column_type(cursor, column_name):
    try:
        cursor.execute("SHOW COLUMNS FROM %s LIKE %%s" % TABLE_NAME, (column_name,))
        row = cursor.fetchone()
    except Exception:
        return None

    if not row:
        return None
    if isinstance(row, dict):
        return row.get('Type') or row.get('type')
    return row[1] if len(row) > 1 else None


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
                    tmdb_id VARCHAR(255) NOT NULL,
                    media_type VARCHAR(64),
                    title VARCHAR(255),
                    start_time DATETIME,
                    end_time DATETIME,
                    progress_seconds INT,
                    total_length_seconds INT,
                    is_finished BOOL,
                    profile_name VARCHAR(255),
                    season_number INT,
                    episode_number INT,
                    show_title VARCHAR(255),
                    show_tmdb_id VARCHAR(255),
                    PRIMARY KEY (tmdb_id, profile_name)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS watch_history (
                    history_id INT NOT NULL AUTO_INCREMENT,
                    tmdb_id VARCHAR(255) NOT NULL,
                    media_type VARCHAR(64),
                    title VARCHAR(255),
                    start_time DATETIME,
                    end_time DATETIME,
                    progress_seconds INT,
                    total_length_seconds INT,
                    is_finished BOOL,
                    profile_name VARCHAR(255),
                    season_number INT,
                    episode_number INT,
                    show_title VARCHAR(255),
                    show_tmdb_id VARCHAR(255),
                    PRIMARY KEY (history_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

            columns = _get_column_names(cursor)

            column_definitions = {
                'start_time': 'DATETIME',
                'end_time': 'DATETIME',
                'progress_seconds': 'INT',
                'total_length_seconds': 'INT',
                'is_finished': 'BOOL',
                'profile_name': 'VARCHAR(255)',
                'season_number': 'INT',
                'episode_number': 'INT',
                'show_title': 'VARCHAR(255)',
                'show_tmdb_id': 'VARCHAR(255)',
            }
            for column_name, column_type in column_definitions.items():
                if column_name not in columns:
                    try:
                        cursor.execute('ALTER TABLE %s ADD COLUMN %s %s' % (TABLE_NAME, column_name, column_type))
                    except Exception as exc:
                        _log_error('Watch History add column failed for %s: %s' % (column_name, exc))

            if 'progress_seconds' in columns and 'total_length_seconds' in columns:
                try:
                    progress_type = _get_column_type(cursor, 'progress_seconds')
                    if progress_type and 'int' not in str(progress_type).lower():
                        cursor.execute('ALTER TABLE %s MODIFY progress_seconds INT' % TABLE_NAME)
                except Exception as exc:
                    _log_error('Watch History progress column repair failed: %s' % exc)

                try:
                    total_length_type = _get_column_type(cursor, 'total_length_seconds')
                    if total_length_type and 'int' not in str(total_length_type).lower():
                        cursor.execute('ALTER TABLE %s MODIFY total_length_seconds INT' % TABLE_NAME)
                except Exception as exc:
                    _log_error('Watch History total_length column repair failed: %s' % exc)

            if 'is_finished' in columns:
                try:
                    is_finished_type = _get_column_type(cursor, 'is_finished')
                    if is_finished_type and 'bool' not in str(is_finished_type).lower():
                        cursor.execute('ALTER TABLE %s MODIFY is_finished BOOL' % TABLE_NAME)
                except Exception as exc:
                    _log_error('Watch History is_finished column repair failed: %s' % exc)

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
    return initialize_history_database()


def is_enabled():
    return str(get_setting('fenlight.watch_history.enabled', 'true')).lower() == 'true'


def get_profile_name(profile_name=None):
    if profile_name:
        return profile_name
    configured_profile = get_setting('fenlight.watch_history.profile_name', 'default')
    return configured_profile or 'default'


def _normalize_value(value):
    if value in (None, ''):
        return None
    return value


def _to_datetime_string(value):
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if hasattr(value, 'strftime'):
        return value.strftime('%Y-%m-%d %H:%M:%S')
    if isinstance(value, (int, float)):
        return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(value)))
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())


def create_watch_history_entry(
    tmdb_id,
    media_type,
    title,
    start_time=None,
    end_time=None,
    progress_seconds=0,
    total_length_seconds=0,
    is_finished=False,
    profile=None,
    season_number=None,
    episode_number=None,
    show_title=None,
    show_tmdb_id=None,
):
    if not is_enabled():
        return None
    try:
        initialize_history_database()
        conn = connect()
        profile_name = get_profile_name(profile)
        if start_time is None:
            start_time = time.time()
        with conn.cursor() as cursor:
            cursor.execute(
                '''
                INSERT INTO watch_history (
                    tmdb_id,
                    media_type,
                    title,
                    start_time,
                    end_time,
                    progress_seconds,
                    total_length_seconds,
                    is_finished,
                    profile_name,
                    season_number,
                    episode_number,
                    show_title,
                    show_tmdb_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''',
                (
                    str(tmdb_id),
                    media_type,
                    title,
                    _to_datetime_string(start_time),
                    _to_datetime_string(end_time),
                    int(progress_seconds),
                    int(total_length_seconds),
                    bool(is_finished),
                    profile_name,
                    _normalize_value(season_number),
                    _normalize_value(episode_number),
                    _normalize_value(show_title),
                    _normalize_value(show_tmdb_id),
                ),
            )
            history_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return history_id
    except Exception as exc:
        _log_error('Watch History create failed: %s' % exc)
        return None


def update_watch_history_entry(history_id, end_time=None, progress_seconds=0, total_length_seconds=0, is_finished=False):
    if not is_enabled() or not history_id:
        return False
    try:
        initialize_history_database()
        conn = connect()
        if end_time is None:
            end_time = time.time()
        with conn.cursor() as cursor:
            cursor.execute(
                '''
                UPDATE watch_history
                SET end_time = %s,
                    progress_seconds = %s,
                    total_length_seconds = %s,
                    is_finished = %s
                WHERE history_id = %s
                ''',
                (
                    _to_datetime_string(end_time),
                    int(progress_seconds),
                    int(total_length_seconds),
                    bool(is_finished),
                    int(history_id),
                ),
            )
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        _log_error('Watch History update failed: %s' % exc)
        return False


def save_watch_history_entry(
    tmdb_id,
    media_type,
    title,
    start_time=None,
    end_time=None,
    progress_seconds=0,
    total_length_seconds=0,
    is_finished=False,
    profile=None,
    season_number=None,
    episode_number=None,
    show_title=None,
    show_tmdb_id=None,
):
    if not is_enabled():
        return False
    try:
        initialize_history_database()
        conn = connect()
        profile_name = get_profile_name(profile)
        if start_time is None:
            start_time = time.time()
        if end_time is None:
            end_time = time.time()
        with conn.cursor() as cursor:
            cursor.execute(
                'DELETE FROM %s WHERE tmdb_id = %%s AND media_type = %%s AND COALESCE(profile_name, "") = %%s' % TABLE_NAME,
                (str(tmdb_id), media_type, profile_name),
            )
            cursor.execute(
                '''
                INSERT INTO progress (
                    tmdb_id,
                    media_type,
                    title,
                    start_time,
                    end_time,
                    progress_seconds,
                    total_length_seconds,
                    is_finished,
                    profile_name,
                    season_number,
                    episode_number,
                    show_title,
                    show_tmdb_id
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ''',
                (
                    str(tmdb_id),
                    media_type,
                    title,
                    _to_datetime_string(start_time),
                    _to_datetime_string(end_time),
                    int(progress_seconds),
                    int(total_length_seconds),
                    bool(is_finished),
                    profile_name,
                    _normalize_value(season_number),
                    _normalize_value(episode_number),
                    _normalize_value(show_title),
                    _normalize_value(show_tmdb_id),
                ),
            )
        conn.commit()
        conn.close()
        return True
    except Exception as exc:
        _log_error('Watch History save failed: %s' % exc)
        return False


def get_resume_state(tmdb_id, media_type, profile_name=None):
    if not is_enabled():
        return None
    try:
        initialize_history_database()
        conn = connect()
        profile_name = get_profile_name(profile_name)
        with conn.cursor() as cursor:
            cursor.execute(
                '''
                SELECT tmdb_id, media_type, title, start_time, end_time, progress_seconds, total_length_seconds, is_finished, profile_name,
                       season_number, episode_number, show_title, show_tmdb_id
                FROM progress
                WHERE tmdb_id = %s AND media_type = %s AND COALESCE(profile_name, "") = %s
                ORDER BY end_time DESC
                LIMIT 1
                ''',
                (str(tmdb_id), media_type, profile_name),
            )
            row = cursor.fetchone()
        conn.close()
        if not row:
            return None
        return {
            'tmdb_id': row['tmdb_id'],
            'media_type': row['media_type'],
            'title': row['title'],
            'start_time': row['start_time'],
            'end_time': row['end_time'],
            'progress_seconds': row['progress_seconds'] or 0,
            'total_length_seconds': row['total_length_seconds'] or 0,
            'is_finished': bool(row['is_finished']),
            'profile_name': row['profile_name'],
            'season_number': row['season_number'],
            'episode_number': row['episode_number'],
            'show_title': row['show_title'],
            'show_tmdb_id': row['show_tmdb_id'],
        }
    except Exception as exc:
        _log_error('Watch History resume lookup failed: %s' % exc)
        return None


def get_resume_percent(tmdb_id, media_type, profile_name=None):
    state = get_resume_state(tmdb_id, media_type, profile_name)
    if not state:
        return 0.0
    if state['is_finished']:
        return 100.0
    total = state.get('total_length_seconds') or 0
    if not total:
        return 0.0
    progress = state.get('progress_seconds') or 0
    if progress <= 0:
        return 0.0
    percent = round(min(99.0, (float(progress) / float(total)) * 100.0), 1)
    return percent


def mark_as_watched(tmdb_id, media_type, title, profile=None, season_number=None, episode_number=None, show_title=None, show_tmdb_id=None, total_length_seconds=0):
    return save_watch_history_entry(
        tmdb_id=tmdb_id,
        media_type=media_type,
        title=title,
        start_time=time.time(),
        end_time=time.time(),
        progress_seconds=int(total_length_seconds) if total_length_seconds else 0,
        total_length_seconds=int(total_length_seconds) if total_length_seconds else 0,
        is_finished=True,
        profile=profile,
        season_number=season_number,
        episode_number=episode_number,
        show_title=show_title,
        show_tmdb_id=show_tmdb_id,
    )
