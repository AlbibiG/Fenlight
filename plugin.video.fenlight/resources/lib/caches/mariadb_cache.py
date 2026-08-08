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
    return initialize_history_database()