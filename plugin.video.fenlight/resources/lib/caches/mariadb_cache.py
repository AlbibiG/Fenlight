import pymysql
import xbmc
from modules.settings import watch_history_server_ip, watch_history_port, watch_history_username, watch_history_password, watch_history_database_name

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
    }


def connect():
    config = get_connection_config()
    try:
        return pymysql.connect(**config)
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
                    last_played text,
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
                    last_played text,
                    resume_id integer,
                    title text,
                    profile text,
                    unique (db_type, media_id, season, episode, last_played, profile)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS watched (
                    db_type text not null,
                    media_id text not null,
                    season integer,
                    episode integer,
                    last_played text,
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
                    contents text,
                    total integer,
                    created text,
                    sort_order integer,
                    profile text,
                    unique(name, profile)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            ''')

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
                'last_played': 'text', 'resume_id': 'integer',
                'title': 'text', 'profile': 'text'
            },
            'unique': ['db_type', 'media_id', 'season', 'episode', 'profile']
        },
        'historical': {
            'columns': {
                'db_type': 'text not null', 'media_id': 'text not null',
                'season': 'integer', 'episode': 'integer',
                'resume_point': 'text', 'curr_time': 'text',
                'last_played': 'text', 'resume_id': 'integer',
                'title': 'text', 'profile': 'text'
            },
            'unique': ['db_type', 'media_id', 'season', 'episode', 'profile', 'last_played']
        },
        'watched': {
            'columns': {
                'db_type': 'text not null', 'media_id': 'text not null',
                'season': 'integer', 'episode': 'integer',
                'last_played': 'text', 'title': 'text', 'profile': 'text'
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
                'name': 'text', 'contents': 'text', 'total': 'integer',
                'created': 'text', 'sort_order': 'integer', 'profile': 'text'
            },
            'unique': ['name', 'profile']
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