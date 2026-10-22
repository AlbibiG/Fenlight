from fastapi import APIRouter, Depends, HTTPException, Query, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional, Any, Union
import mysql.connector
from mysql.connector import errorcode
import os
import json
import ast
import logging
import threading
from datetime import datetime, timedelta, timezone
from auth import verify_client_api_key

router = APIRouter()
logger = logging.getLogger(__name__)
_initialized_databases = set()
_database_initialization_lock = threading.Lock()

DB_HOST = os.environ["DB_HOST"]
DB_PORT = int(os.environ.get("DB_PORT", "3306"))
BACKEND_DB_USER = os.environ["FENLIGHT_DB_USER"]
BACKEND_DB_PASS = os.environ["FENLIGHT_DB_PASS"]

TIMESTAMP_PATTERN = '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$'

# Same schema as Fen Light's direct MariaDB mode (caches/mariadb_cache.py)
TABLE_DEFINITIONS = (
    '''
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
    ''',
    '''
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
    ''',
    '''
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
    ''',
    '''
    CREATE TABLE IF NOT EXISTS watched_status (
        db_type varchar(32) not null,
        media_id varchar(64) not null,
        status text,
        profile varchar(191),
        unique (db_type, media_id, profile)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    ''',
    '''
    CREATE TABLE IF NOT EXISTS personal_lists (
        name varchar(191),
        created timestamp NULL DEFAULT NULL,
        utc_offset varchar(6),
        sort_order integer,
        profile varchar(191),
        unique(name, profile)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    ''',
    '''
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
    '''
)

TIMESTAMP_COLUMNS = (
    ('progress', 'last_played'),
    ('historical', 'started'),
    ('historical', 'ended'),
    ('watched', 'last_played'),
    ('personal_lists', 'created'),
    ('personal_list_items', 'date_added')
)

# Tables created before the key columns were VARCHAR have them as TEXT, which MariaDB can only make unique with a
# hash that no query can use, so every lookup scanned the whole table. (table, [(column, length, not null)], unique key)
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

# utc_offset records the client's timezone each timestamp was written in, placed after the table's last timestamp column
UTC_OFFSET_COLUMNS = (
    ('progress', 'last_played'),
    ('historical', 'ended'),
    ('watched', 'last_played'),
    ('personal_lists', 'created'),
    ('personal_list_items', 'date_added')
)

# All times come from the Fenlight client: it sends 'YYYY-MM-DD HH:MM:SS' in its local time plus that
# time's utc_offset ('+HH:MM'). The API never uses its own or the database server's timezone.

def _parse_datetime(value):
    if isinstance(value, datetime):
        return value
    value = str(value).strip()
    if value.isdigit():
        # epoch seconds (older personal list items)
        return datetime.fromtimestamp(int(value), timezone.utc)
    return datetime.fromisoformat(value.replace('Z', '+00:00'))

def _offset_label(tzinfo, dt):
    offset = dt.replace(tzinfo=tzinfo).strftime('%z')
    return '%s:%s' % (offset[:3], offset[3:])

def parse_utc_offset(label):
    """'+HH:MM' / '-HH:MM' from the Fenlight client to a timezone"""
    try:
        sign = -1 if label[0] == '-' else 1
        hours, minutes = label[1:].split(':')
        if label[0] not in '+-' or len(hours) != 2 or len(minutes) != 2: raise ValueError
        return timezone(sign * timedelta(hours=int(hours), minutes=int(minutes)))
    except (TypeError, ValueError, IndexError):
        raise HTTPException(status_code=422, detail="utc_offset must be formatted as +HH:MM or -HH:MM, got %r" % (label,))

def to_sql_timestamp(value, utc_offset):
    """Validate a Fenlight client time for the TIMESTAMP columns as 'YYYY-MM-DD HH:MM:SS'.
    Fenlight sends its local time with no timezone, which is stored as-is. A value that includes a timezone is
    converted to the client's utc_offset. Returns None for empty values."""
    if value in (None, ''):
        return None
    try:
        dt = _parse_datetime(value)
    except (ValueError, OverflowError, OSError):
        raise HTTPException(status_code=422, detail="Invalid time value %r" % (value,))
    if dt.tzinfo is not None:
        dt = dt.astimezone(parse_utc_offset(utc_offset)).replace(tzinfo=None)
    return dt.strftime('%Y-%m-%d %H:%M:%S')

def require(value, name):
    if value in (None, ''):
        raise HTTPException(status_code=422, detail="%s is required (sent by the Fenlight client in its local time)" % name)
    return value

def require_utc_offset(utc_offset):
    require(utc_offset, 'utc_offset')
    parse_utc_offset(utc_offset)
    return utc_offset

def migrate_timestamp(value):
    """Convert an existing stored value without using any server timezone. Returns (timestamp, utc_offset):
    values with an offset keep their own local time and offset, epoch values become UTC with '+00:00',
    plain values are kept with an unknown (None) offset, and invalid values become (None, None)."""
    if value in (None, ''):
        return None, None
    try:
        dt = _parse_datetime(value)
    except (ValueError, OverflowError, OSError):
        return None, None
    offset = _offset_label(dt.tzinfo, dt) if dt.tzinfo is not None else None
    return dt.replace(tzinfo=None).strftime('%Y-%m-%d %H:%M:%S'), offset

def _text_row(row):
    # older mysql.connector versions return SHOW COLUMNS/SHOW INDEX values as bytes
    return tuple(value.decode() if isinstance(value, (bytes, bytearray)) else value for value in row)

def _columns(cursor, table_name):
    cursor.execute('SHOW COLUMNS FROM %s' % table_name)
    return {row[0].lower(): row for row in map(_text_row, cursor.fetchall())}

def _unique_indexes(cursor, table_name):
    cursor.execute('SHOW INDEX FROM %s WHERE Non_unique = 0' % table_name)
    return {row[2] for row in map(_text_row, cursor.fetchall()) if row[2] != 'PRIMARY'}

def _migrate_key_columns(cursor):
    """Turn TEXT key columns into VARCHAR and rebuild the unique key as an ordinary index that lookups can use"""
    for table_name, key_columns, unique_key in KEY_COLUMNS:
        columns = _columns(cursor, table_name)
        text_columns = [i for i in key_columns if i[0] in columns and columns[i[0]][1].lower() == 'text']
        if not text_columns:
            continue
        too_long = []
        for column_name, length, _ in text_columns:
            cursor.execute('SELECT COALESCE(MAX(CHAR_LENGTH(%s)), 0) FROM %s' % (column_name, table_name))
            if cursor.fetchone()[0] > length:
                too_long.append(column_name)
        if too_long:
            logger.warning("Leaving %s as it is: some %s values are too long for an indexed column", table_name, ', '.join(too_long))
            continue
        changes = ['DROP INDEX `%s`' % index_name for index_name in _unique_indexes(cursor, table_name)]
        changes += ['MODIFY COLUMN %s varchar(%d)%s' % (name, length, ' not null' if not_null else '') for name, length, not_null in text_columns]
        changes.append('ADD UNIQUE (%s)' % ', '.join(unique_key))
        logger.info("Converting the key columns of %s to VARCHAR so lookups can use its index", table_name)
        try:
            cursor.execute('ALTER TABLE %s %s' % (table_name, ', '.join(changes)))
        except mysql.connector.Error as e:
            # ALTER TABLE is all or nothing, so the table keeps working as before, just without a usable index
            logger.warning("Could not convert the key columns of %s: %s", table_name, e)

def _parse_list_item(raw_item):
    try:
        item = json.loads(raw_item)
    except (json.JSONDecodeError, TypeError):
        item = ast.literal_eval(raw_item)
    if not isinstance(item, dict):
        raise ValueError("Personal list item must be a dict")
    return item

def _migrate_old_personal_list_items(cursor):
    """Convert the old personal_list_items layout (item_id + JSON item column) to one column per field"""
    columns = _columns(cursor, 'personal_list_items')
    if 'item' not in columns:
        return
    logger.info("Migrating personal_list_items from JSON items to columns")
    for column_name, column_type in (('media_type', 'varchar(16)'), ('title', 'text'), ('date_added', 'text'),
                                     ('utc_offset', 'varchar(6)'), ('release_date', 'text')):
        if column_name not in columns:
            cursor.execute('ALTER TABLE personal_list_items ADD COLUMN %s %s' % (column_name, column_type))
    cursor.execute('SELECT profile, list_name, media_id, item FROM personal_list_items')
    updates = []
    for profile, list_name, media_id, raw_item in cursor.fetchall():
        try:
            item = _parse_list_item(raw_item)
        except Exception:
            logger.exception("Failed to parse personal list item %r in list %r for profile %r", media_id, list_name, profile)
            continue
        date_added, utc_offset = migrate_timestamp(item.get('date_added'))
        updates.append((item.get('media_type') or item.get('type'), item.get('title'),
                        date_added, utc_offset, item.get('release_date'), profile, list_name, media_id))
    if updates:
        cursor.executemany(
            'UPDATE personal_list_items SET media_type=%s, title=%s, date_added=%s, utc_offset=%s, release_date=%s '
            'WHERE profile=%s AND list_name=%s AND media_id=%s',
            updates
        )
    cursor.execute('DELETE FROM personal_list_items WHERE media_type IS NULL')
    if cursor.rowcount:
        logger.warning("Removed %d personal list items with no media type", cursor.rowcount)
    # replace the old (profile, list_name, media_id) unique key with the one used by Fen Light
    for index_name in _unique_indexes(cursor, 'personal_list_items'):
        cursor.execute('ALTER TABLE personal_list_items DROP INDEX `%s`' % index_name)
    cursor.execute('ALTER TABLE personal_list_items DROP COLUMN item')
    if 'item_id' in columns:
        cursor.execute('ALTER TABLE personal_list_items DROP COLUMN item_id')
    cursor.execute('ALTER TABLE personal_list_items MODIFY COLUMN media_type varchar(16) not null')
    cursor.execute('ALTER TABLE personal_list_items ADD UNIQUE (profile, list_name, media_id, media_type)')

def _migrate_timestamp_columns(cursor):
    for table_name, column_name in TIMESTAMP_COLUMNS:
        column = _columns(cursor, table_name).get(column_name)
        if not column:
            continue
        column_type, nullable, extra = column[1].lower(), column[2], (column[5] or '').lower()
        if column_type == 'timestamp' and nullable == 'YES' and 'on update' not in extra:
            continue
        if not column_type.startswith('timestamp'):
            # convert existing text values before changing the column type, invalid values become NULL.
            # a value's own offset is recorded in utc_offset when the row doesn't have one yet
            cursor.execute('SELECT DISTINCT %s FROM %s WHERE %s IS NOT NULL' % (column_name, table_name, column_name))
            updates = [migrate_timestamp(row[0]) + (row[0],) for row in cursor.fetchall()]
            updates = [i for i in updates if i[0] != i[2] or i[1]]
            if updates:
                cursor.executemany(
                    'UPDATE IGNORE %s SET %s = %%s, utc_offset = COALESCE(utc_offset, %%s) WHERE %s = %%s' % (table_name, column_name, column_name),
                    updates
                )
            cursor.execute("UPDATE %s SET %s = NULL WHERE %s NOT REGEXP '%s'" % (table_name, column_name, column_name, TIMESTAMP_PATTERN))
        cursor.execute('ALTER TABLE %s MODIFY COLUMN %s timestamp NULL DEFAULT NULL' % (table_name, column_name))

def _add_utc_offset_columns(cursor):
    for table_name, after_column in UTC_OFFSET_COLUMNS:
        if 'utc_offset' not in _columns(cursor, table_name):
            cursor.execute('ALTER TABLE %s ADD COLUMN utc_offset varchar(6) AFTER %s' % (table_name, after_column))

def _migrate_personal_list_contents(conn, cursor):
    """Move items still stored in the old personal_lists.contents JSON column into personal_list_items"""
    if 'contents' not in _columns(cursor, 'personal_lists'):
        return
    cursor.execute(
        "SELECT name, contents, profile FROM personal_lists "
        "WHERE contents IS NOT NULL AND contents NOT IN (%s, %s)",
        ("", "[]")
    )
    for list_name, contents, profile in cursor.fetchall():
        try:
            try:
                items = json.loads(contents)
            except (json.JSONDecodeError, TypeError):
                items = ast.literal_eval(contents)
            if not isinstance(items, list):
                raise ValueError("Personal list contents must be a list")
            cursor.executemany(
                'INSERT IGNORE INTO personal_list_items '
                '(profile, list_name, media_id, media_type, title, date_added, utc_offset, release_date) '
                'VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
                [
                    (profile, list_name, str(item["media_id"]), item.get('media_type') or item.get('type'),
                     item.get('title')) + migrate_timestamp(item.get('date_added')) + (item.get('release_date'),)
                    for item in items if item.get('media_type') or item.get('type')
                ]
            )
            cursor.execute(
                "UPDATE personal_lists SET contents='[]' WHERE name=%s AND profile=%s",
                (list_name, profile)
            )
            conn.commit()
        except Exception:
            logger.exception("Failed to migrate personal list %r for profile %r", list_name, profile)
            try:
                conn.rollback()
            except mysql.connector.Error:
                logger.exception("Failed to roll back personal list migration")

def initialize_database(conn, target_database: str):
    with _database_initialization_lock:
        if target_database in _initialized_databases:
            return

        cursor = conn.cursor()
        try:
            for table_definition in TABLE_DEFINITIONS:
                cursor.execute(table_definition)
            _migrate_old_personal_list_items(cursor)
            _add_utc_offset_columns(cursor)
            _migrate_timestamp_columns(cursor)
            _migrate_key_columns(cursor)
            conn.commit()
            _migrate_personal_list_contents(conn, cursor)
            _initialized_databases.add(target_database)
        finally:
            cursor.close()

def _connection_error(error, target_database):
    """Explain a failed MariaDB login in terms the Fen Light user can act on, and log it to the journal"""
    if error.errno == errorcode.ER_BAD_DB_ERROR:
        status_code, detail = 404, "Database %r does not exist on the MariaDB server. Create it, or fix the Database setting in Fen Light" % target_database
    elif error.errno == errorcode.ER_DBACCESS_DENIED_ERROR:
        status_code, detail = 404, "MariaDB user %r has no access to database %r. Check the Database setting in Fen Light, or grant the user access" % (BACKEND_DB_USER, target_database)
    else:
        status_code, detail = 503, "Cannot log in to MariaDB at %s:%s: %s" % (DB_HOST, DB_PORT, error)
    logger.error(detail)
    return HTTPException(status_code=status_code, detail=detail)

# Logging in to MariaDB costs several round trips, so each database keeps a few connections open between requests
POOL_IDLE_MAX = 8
_idle_connections = {}
_idle_lock = threading.Lock()

def _close_quietly(conn):
    try:
        conn.close()
    except mysql.connector.Error:
        pass

def _take_idle_connection(target_database):
    """An idle connection to target_database that still answers, or None"""
    while True:
        with _idle_lock:
            idle = _idle_connections.get(target_database)
            conn = idle.pop() if idle else None
        if conn is None or conn.is_connected():
            return conn
        _close_quietly(conn)

def release_connection(target_database, conn):
    """End the request's transaction and keep the connection for the next request"""
    try:
        # an open transaction would show the next request an old snapshot of the data
        conn.rollback()
    except mysql.connector.Error:
        return _close_quietly(conn)
    with _idle_lock:
        idle = _idle_connections.setdefault(target_database, [])
        if len(idle) < POOL_IDLE_MAX:
            idle.append(conn)
            return
    _close_quietly(conn)

def get_db_connection(target_database: str):
    conn = _take_idle_connection(target_database)
    if conn is not None:
        return conn
    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            port=DB_PORT,
            user=BACKEND_DB_USER,
            password=BACKEND_DB_PASS,
            database=target_database,
            # store TIMESTAMP values exactly as the Fenlight client sent them, without MariaDB shifting them
            # by its own timezone (the same as Fen Light's direct MariaDB mode); reapplied on every reconnect
            time_zone='+00:00'
        )
    except mysql.connector.Error as e:
        raise _connection_error(e, target_database) from e
    try:
        initialize_database(conn, target_database)
    except mysql.connector.Error as e:
        _close_quietly(conn)
        detail = "Could not set up the Fen Light tables in database %r: %s" % (target_database, e)
        logger.error(detail)
        raise HTTPException(status_code=500, detail=detail) from e
    except Exception:
        _close_quietly(conn)
        raise
    return conn

def get_db(
    request: Request,
    authorized: bool = Depends(verify_client_api_key),
    target_database: str = Header(..., alias="X-Database-Name")
    ):
    conn = get_db_connection(target_database)
    cursor = conn.cursor(dictionary=False)
    try:
        yield conn, cursor
    except mysql.connector.Error as e:
        logger.error("%s %s failed in database %r: %s", request.method, request.url.path, target_database, e)
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    finally:
        try:
            cursor.close()
        except mysql.connector.Error:
            pass
        release_connection(target_database, conn)

def _rows(cursor):
    # FastAPI's generic encoder is several times slower than plain JSON on long lists of rows
    return JSONResponse(cursor.fetchall())

@router.get("/health", include_in_schema=False)
def health():
    """Unauthenticated check for the installer and uptime monitors: is the API up, and can it log in to MariaDB?"""
    try:
        mysql.connector.connect(host=DB_HOST, port=DB_PORT, user=BACKEND_DB_USER, password=BACKEND_DB_PASS,
                                connection_timeout=5).close()
    except mysql.connector.Error as e:
        logger.error("Health check cannot log in to MariaDB at %s:%s: %s", DB_HOST, DB_PORT, e)
        return JSONResponse(status_code=503, content={"status": "error", "database": "unreachable"})
    return {"status": "ok", "database": "ok"}

class PlayRecordRequest(BaseModel):
    media_type: str
    media_id: int
    season: Optional[int] = None
    episode: Optional[int] = None
    title: str = ""
    started: Optional[str] = None
    ended: Optional[str] = None
    utc_offset: Optional[str] = None
    play_event: str = "watched"
    resume_point: Any = ""
    curr_time: Any = ""
    resume_id: int = 0
    profile: Optional[str] = None

class HideProgressRequest(BaseModel):
    media_id: int
    action: str
    profile: Optional[str] = None

class EraseBookmarkRequest(BaseModel):
    media_type: str
    media_id: int
    season: Optional[int] = None
    episode: Optional[int] = None
    refresh: str = "false"
    profile: Optional[str] = None

class BatchEraseBookmarkRequest(BaseModel):
    insert_list: list
    action: Optional[str] = None
    profile: Optional[str] = None

class SetBookmarkRequest(BaseModel):
    media_type: str
    media_id: int
    curr_time: float
    total_time: float
    title: str = ""
    season: Optional[int] = None
    episode: Optional[int] = None
    last_played: Optional[str] = None
    utc_offset: Optional[str] = None
    profile: Optional[str] = None

class WatchStatusRequest(BaseModel):
    media_type: str
    media_id: str
    action: str
    season: Optional[int] = None
    episode: Optional[int] = None
    title: Optional[str] = ""
    last_played: Optional[str] = None
    utc_offset: Optional[str] = None
    profile: Optional[str] = None

class BatchWatchStatusRequest(BaseModel):
    insert_list: list
    action: str
    utc_offset: Optional[str] = None
    profile: Optional[str] = None

@router.post("/api/history/record")
def record_historical_play(data: PlayRecordRequest, db = Depends(get_db)):
    conn, cursor = db
    utc_offset = require_utc_offset(data.utc_offset)
    started = to_sql_timestamp(require(data.started, 'started'), utc_offset)
    ended = to_sql_timestamp(data.ended, utc_offset)
    if data.play_event == 'started':
        cursor.execute("""
            INSERT IGNORE INTO historical
            (db_type, media_id, season, episode, resume_point, curr_time, started, ended, utc_offset, play_event, resume_id, title, profile)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (data.media_type, data.media_id, data.season, data.episode, str(data.resume_point), str(data.curr_time), started, ended, utc_offset, data.play_event, int(data.resume_id), data.title, data.profile))
    else:
        cursor.execute("""
            UPDATE historical
            SET resume_point = %s, curr_time = %s, ended = %s, utc_offset = %s, resume_id = %s, title = %s, play_event = %s
            WHERE db_type = %s AND media_id = %s AND season <=> %s AND episode <=> %s AND profile <=> %s AND started = %s
            ORDER BY started DESC LIMIT 1
        """, (str(data.resume_point), str(data.curr_time), ended, utc_offset, int(data.resume_id), data.title, data.play_event, data.media_type, data.media_id, data.season, data.episode, data.profile, started))

    conn.commit()
    return {"status": "success"}

@router.get("/api/watched/movies")
def get_watched_info_movie(profile: str = Query(..., description="The watch profile name"), db = Depends(get_db)):
    _, cursor = db
    cursor.execute(
        'SELECT media_id, title, CAST(last_played AS CHAR) FROM watched WHERE db_type = %s AND profile = %s',
        ('movie', profile)
    )
    return _rows(cursor)

@router.get("/api/hidden/progress")
def get_hidden_progress_items(profile: str = Query(..., description="The watch profile name"), db = Depends(get_db)):
    _, cursor = db
    cursor.execute(
        'SELECT status FROM watched_status WHERE db_type = %s AND profile = %s',
        ('hidden_progress_items', profile)
    )
    row = cursor.fetchone()
    # Fen Light expects the stored list string itself, e.g. "[1399, 60625]"
    return row[0] if row else None

def _hidden_items(cursor, profile):
    """The profile's list of show IDs hidden from progress lists"""
    cursor.execute(
        'SELECT status FROM watched_status WHERE db_type = %s AND profile = %s',
        ('hidden_progress_items', profile)
    )
    row = cursor.fetchone()
    if not row or not row[0]:
        return []
    try:
        return json.loads(row[0]) or []
    except (json.JSONDecodeError, TypeError):
        try:
            return ast.literal_eval(row[0]) or []
        except (ValueError, SyntaxError):
            return []

def _add_hidden_items(cursor, profile, media_ids):
    """Add shows to the profile's hidden list; returns how many weren't on it yet"""
    current_items = _hidden_items(cursor, profile)
    new_items = [i for i in dict.fromkeys(media_ids) if i not in current_items]
    if new_items:
        cursor.execute(
            'REPLACE INTO watched_status VALUES (%s, %s, %s, %s)',
            ('hidden_progress_items', 'hidden', json.dumps(current_items + new_items), profile)
        )
    return len(new_items)

class DropManyRequest(BaseModel):
    media_ids: list[int]
    profile: Optional[str] = None

@router.post("/api/hidden/progress/drop-many")
def drop_progress_items(data: DropManyRequest, db = Depends(get_db)):
    """Hide many shows from the progress lists at once, such as everything in In Progress TV Shows"""
    conn, cursor = db
    added = _add_hidden_items(cursor, data.profile, data.media_ids)
    conn.commit()
    return {"status": "success", "added": added}

@router.post("/api/hidden/progress/update")
def update_hidden_progress_items(data: HideProgressRequest, db = Depends(get_db)):
    conn, cursor = db
    current_items = _hidden_items(cursor, data.profile)

    if data.action == 'drop':
        if data.media_id not in current_items:
            current_items.append(data.media_id)
    else:
        if data.media_id in current_items:
            current_items.remove(data.media_id)

    cursor.execute(
        'REPLACE INTO watched_status VALUES (%s, %s, %s, %s)',
        ('hidden_progress_items', 'hidden', json.dumps(current_items), data.profile)
    )

    conn.commit()
    return {"status": "success", "hidden_items": current_items}

@router.get("/api/bookmarks/movies/info")
def get_bookmarks_movie(
    profile: str = Query(..., description="The watch profile name"),
    db = Depends(get_db)
):
    _, cursor = db
    cursor.execute(
        'SELECT media_id, resume_point, curr_time, resume_id FROM progress WHERE db_type = %s AND profile = %s',
        ('movie', profile)
    )
    return _rows(cursor)

@router.get("/api/watched/tvshow/info")
def get_watched_info_tvshow(
    profile: str = Query(..., description="The watch profile name"),
    db = Depends(get_db)
):
    _, cursor = db
    cursor.execute('''
        SELECT w.media_id, w.season, w.episode, w.title, CAST(w.last_played AS CHAR), c.total_played
        FROM watched w
        JOIN (
            SELECT media_id, MAX(last_played) AS max_lp, COUNT(*) AS total_played
            FROM watched
            WHERE db_type = %s AND profile = %s
            GROUP BY media_id
        ) c ON w.media_id = c.media_id AND w.last_played = c.max_lp
        WHERE w.db_type = %s AND w.profile = %s
        GROUP BY w.media_id
    ''', ('episode', profile, 'episode', profile))
    return _rows(cursor)

@router.get("/api/watched/season/info")
def get_watched_info_season(
    media_id: int = Query(..., description="The media ID of the TV show"),
    profile: str = Query(..., description="The watch profile name"),
    db = Depends(get_db)
):
    _, cursor = db
    cursor.execute(
        'SELECT season, COUNT(*) AS counter FROM watched WHERE db_type = %s AND media_id = %s AND profile = %s GROUP BY media_id, season',
        ('episode', media_id, profile)
    )
    return _rows(cursor)

@router.get("/api/watched/episode/info")
def get_watched_info_episode(
    media_id: int = Query(..., description="The media ID of the TV show"),
    profile: str = Query(..., description="The watch profile name"),
    db = Depends(get_db)
):
    _, cursor = db
    cursor.execute(
        'SELECT season, episode FROM watched WHERE db_type = %s AND media_id = %s AND profile = %s',
        ('episode', media_id, profile)
    )
    return _rows(cursor)

@router.get("/api/bookmarks/episode/info")
def get_bookmarks_episode(
    media_id: int = Query(..., description="The media ID of the TV show"),
    season: int = Query(..., description="The season number"),
    profile: str = Query(..., description="The watch profile name"),
    db = Depends(get_db)
):
    _, cursor = db
    cursor.execute(
        'SELECT resume_point, curr_time, resume_id, episode FROM progress WHERE db_type = %s AND media_id = %s AND season = %s AND profile = %s',
        ('episode', media_id, int(season), profile)
    )
    return _rows(cursor)

@router.post("/api/bookmarks/erase")
def erase_bookmark(data: EraseBookmarkRequest, db = Depends(get_db)):
    conn, cursor = db
    cursor.execute(
        'DELETE FROM progress WHERE db_type = %s AND media_id = %s AND season <=> %s AND episode <=> %s AND profile <=> %s',
        (data.media_type, data.media_id, data.season, data.episode, data.profile)
    )
    conn.commit()
    return {"status": "success"}

@router.post("/api/bookmarks/batch-erase")
def batch_erase_bookmark(data: BatchEraseBookmarkRequest, db = Depends(get_db)):
    conn, cursor = db
    # JSON sends rows as lists, so convert them to tuples before adding the profile
    params = [tuple(item[:4]) + (data.profile,) for item in data.insert_list]
    if params:
        cursor.executemany(
            'DELETE FROM progress WHERE db_type = %s AND media_id = %s AND season = %s AND episode = %s AND profile = %s',
            params
        )
    conn.commit()
    return {"status": "success"}

class ClearBookmarksRequest(BaseModel):
    media_type: str
    profile: Optional[str] = None

@router.post("/api/bookmarks/clear")
def clear_bookmarks(data: ClearBookmarksRequest, db = Depends(get_db)):
    """Remove the resume point of every movie, or of every episode, in the profile"""
    conn, cursor = db
    cursor.execute('DELETE FROM progress WHERE db_type = %s AND profile <=> %s', (data.media_type, data.profile))
    cleared = cursor.rowcount
    conn.commit()
    return {"status": "success", "cleared": cleared}

@router.post("/api/bookmarks/set")
def set_bookmark(data: SetBookmarkRequest, db = Depends(get_db)):
    conn, cursor = db
    adjusted_current_time = float(data.curr_time) - 5
    resume_point = round(adjusted_current_time / float(data.total_time) * 100, 1)
    utc_offset = require_utc_offset(data.utc_offset)
    last_played = to_sql_timestamp(require(data.last_played, 'last_played'), utc_offset)

    cursor.execute(
        'DELETE FROM progress WHERE db_type = %s AND media_id = %s AND season <=> %s AND episode <=> %s AND profile <=> %s',
        (data.media_type, data.media_id, data.season, data.episode, data.profile)
    )

    cursor.execute(
        'REPLACE INTO progress (db_type, media_id, season, episode, resume_point, curr_time, last_played, utc_offset, resume_id, title, profile) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
        (data.media_type, data.media_id, data.season, data.episode, str(resume_point), str(data.curr_time), last_played, utc_offset, 0, data.title, data.profile)
    )

    conn.commit()
    return {"status": "success"}

@router.post("/api/watched/status")
def watched_status_mark(data: WatchStatusRequest, db = Depends(get_db)):
    conn, cursor = db

    if data.action == 'mark_as_watched':
        season = int(data.season) if data.season is not None else None
        episode = int(data.episode) if data.episode is not None else None
        profile = str(data.profile) if data.profile is not None else ""
        last_played = to_sql_timestamp(require(data.last_played, 'last_played'), require_utc_offset(data.utc_offset))
        # a movie's empty season and episode never match in the unique key, so remove its old row first
        cursor.execute(
            'DELETE FROM watched WHERE db_type = %s AND media_id = %s AND season <=> %s AND episode <=> %s AND profile <=> %s',
            (str(data.media_type), str(data.media_id), season, episode, profile)
        )
        cursor.execute(
            'REPLACE INTO watched (db_type, media_id, season, episode, last_played, utc_offset, title, profile) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
            (
                str(data.media_type),
                str(data.media_id),
                season,
                episode,
                last_played,
                data.utc_offset,
                str(data.title) if data.title is not None else "",
                profile
            )
        )
    else:
        cursor.execute(
            'DELETE FROM watched WHERE db_type = %s AND media_id = %s AND season <=> %s AND episode <=> %s AND profile <=> %s',
            (data.media_type, data.media_id, data.season, data.episode, data.profile)
        )

    cursor.execute(
        'DELETE FROM progress WHERE db_type = %s AND media_id = %s AND season <=> %s AND episode <=> %s AND profile <=> %s',
        (data.media_type, data.media_id, data.season, data.episode, data.profile)
    )

    conn.commit()
    return {"status": "success"}

@router.post("/api/watched/batch-status")
def batch_watched_status_mark(data: BatchWatchStatusRequest, db = Depends(get_db)):
    conn, cursor = db

    if data.action == 'mark_as_watched':
        utc_offset = require_utc_offset(data.utc_offset)
        # rows are (media_type, media_id, season, episode, last_played, title)
        rows = [(i[0], i[1], i[2], i[3], to_sql_timestamp(require(i[4], 'last_played'), utc_offset), i[5]) for i in data.insert_list]
        if rows:
            cursor.executemany(
                'INSERT IGNORE INTO watched (db_type, media_id, season, episode, last_played, utc_offset, title, profile) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
                [i[:5] + (utc_offset, i[5], data.profile) for i in rows]
            )
            cursor.executemany(
                'INSERT IGNORE INTO historical (db_type, media_id, season, episode, resume_point, curr_time, started, ended, utc_offset, play_event, resume_id, title, profile) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                [(i[0], i[1], i[2], i[3], '100', '100', i[4], i[4], utc_offset, 'watched', 0, i[5], data.profile) for i in rows]
            )
    elif data.action == 'mark_as_unwatched':
        params = [tuple(i[:4]) + (data.profile,) for i in data.insert_list]
        if params:
            cursor.executemany(
                'DELETE FROM watched WHERE db_type = %s AND media_id = %s AND season <=> %s AND episode <=> %s AND profile <=> %s',
                params
            )

    bookmark_params = [tuple(i[:4]) + (data.profile,) for i in data.insert_list]
    if bookmark_params:
        cursor.executemany(
            'DELETE FROM progress WHERE db_type = %s AND media_id = %s AND season = %s AND episode = %s AND profile = %s',
            bookmark_params
        )

    conn.commit()
    return {"status": "success"}

@router.get("/api/tvshows/next-episodes")
def get_next_episodes(
    nextep_content: int = Query(..., description="Next episode content setting"),
    profile: str = Query(..., description="The watch profile name"),
    db = Depends(get_db)
):
    _, cursor = db
    if nextep_content == 0:
        cursor.execute('''
            WITH cte AS (
                SELECT *, ROW_NUMBER() OVER (PARTITION BY media_id ORDER BY season DESC, episode DESC) AS rn
                FROM watched WHERE db_type = %s AND profile = %s
            )
            SELECT media_id, season, episode, title, CAST(last_played AS CHAR) FROM cte WHERE rn = 1
        ''', ('episode', profile))
    else:
        cursor.execute('''
            SELECT w.media_id, w.season, w.episode, w.title, CAST(c.last_played AS CHAR), c.COUNTER
            FROM watched w
            JOIN (
                SELECT media_id, MAX(last_played) AS last_played, COUNT(*) AS COUNTER
                FROM watched WHERE db_type = %s AND profile = %s GROUP BY media_id
            ) c ON w.media_id = c.media_id AND w.last_played = c.last_played
            WHERE w.db_type = %s AND w.profile = %s
            GROUP BY w.media_id
        ''', ('episode', profile, 'episode', profile))
    return _rows(cursor)

@router.get("/api/progress/movies")
def get_in_progress_movies(
    profile: str = Query(..., description="The watch profile name"),
    db = Depends(get_db)
):
    _, cursor = db
    cursor.execute('SELECT media_id, title, CAST(last_played AS CHAR) FROM progress WHERE db_type = %s AND profile = %s', ('movie', profile))
    return _rows(cursor)

@router.get("/api/progress/episodes")
def get_in_progress_episodes(
    profile: str = Query(..., description="The watch profile name"),
    db = Depends(get_db)
):
    _, cursor = db
    cursor.execute('SELECT media_id, season, episode, resume_point, CAST(last_played AS CHAR), title FROM progress WHERE db_type = %s AND profile = %s', ('episode', profile))
    return _rows(cursor)

@router.get("/api/watched/recently")
def get_recently_watched(
    media_type: str = Query(..., description="Media type (movie or episode)"),
    short_list: int = Query(1, description="Whether to return a short list"),
    per_show: int = Query(0, description="Only each show's most recently watched episode"),
    profile: str = Query(..., description="The watch profile name"),
    db = Depends(get_db)
):
    _, cursor = db
    if media_type != 'episode':
        return []
    if per_show:
        # what Fen Light's "Because You Watched" lists keep of the full history: one row per show, newest first
        cursor.execute('''
            WITH cte AS (
                SELECT media_id, season, episode, title, last_played,
                       ROW_NUMBER() OVER (PARTITION BY media_id ORDER BY last_played DESC, season DESC, episode DESC) AS rn
                FROM watched WHERE db_type = %s AND profile = %s
            )
            SELECT media_id, season, episode, title, CAST(last_played AS CHAR) FROM cte WHERE rn = 1
            ORDER BY last_played DESC
        ''', ('episode', profile))
    else:
        limit_clause = "LIMIT 20" if short_list else ""
        cursor.execute(f'''
            SELECT media_id, season, episode, title, CAST(last_played AS CHAR)
            FROM watched
            WHERE db_type = %s AND profile = %s
            ORDER BY last_played DESC
            {limit_clause}
        ''', ('episode', profile))
    return _rows(cursor)

class ListCreate(BaseModel):
    list_name: str
    profile: str
    sort_order: Optional[int] = 0
    created: Optional[str] = None
    utc_offset: Optional[str] = None

class ListDelete(BaseModel):
    list_name: str
    profile: str

class ListClear(BaseModel):
    list_name: str
    profile: str

class ListUpdate(BaseModel):
    original_name: str
    new_list_name: str
    sort_order: int
    profile: str

class ListAddItem(BaseModel):
    list_name: str
    item: dict[str, Any]
    utc_offset: Optional[str] = None
    profile: str

class ListRemoveItem(BaseModel):
    list_name: str
    media_id: Union[str, int]
    profile: str

class ListAddMany(BaseModel):
    list_name: str
    items: list[dict[str, Any]]
    utc_offset: Optional[str] = None
    profile: str

def _list_item_row(profile, list_name, item, utc_offset):
    media_type = item.get("media_type") or item.get("type")
    if "media_id" not in item or not media_type:
        raise HTTPException(status_code=422, detail="each item must include media_id and media_type or type")
    return (profile, list_name, str(item["media_id"]), media_type, item.get("title"),
            to_sql_timestamp(require(item.get("date_added"), 'date_added'), utc_offset), utc_offset, item.get("release_date"))

LIST_ITEM_INSERT = (
    'INSERT IGNORE INTO personal_list_items '
    '(profile, list_name, media_id, media_type, title, date_added, utc_offset, release_date) '
    'VALUES (%s, %s, %s, %s, %s, %s, %s, %s)'
)

@router.post("/api/lists/make")
def make_list(data: ListCreate, db = Depends(get_db)):
    conn, cursor = db
    cursor.execute(
        'INSERT INTO personal_lists (name, created, utc_offset, sort_order, profile) VALUES (%s, %s, %s, %s, %s)',
        (data.list_name, to_sql_timestamp(require(data.created, 'created'), require_utc_offset(data.utc_offset)), data.utc_offset, data.sort_order, data.profile)
    )
    conn.commit()
    return {"status": "success", "data": data.model_dump()}

@router.post("/api/lists/delete")
def delete_list(data: ListDelete, db = Depends(get_db)):
    conn, cursor = db
    cursor.execute(
        'DELETE FROM personal_list_items WHERE list_name=%s AND profile=%s',
        (data.list_name, data.profile)
    )
    cursor.execute('DELETE FROM personal_lists WHERE name=%s and profile=%s', (data.list_name, data.profile))
    conn.commit()
    return {"status": "success", "data": data.model_dump()}

@router.post("/api/lists/clear-contents")
def clear_list_contents(data: ListClear, db = Depends(get_db)):
    conn, cursor = db
    cursor.execute(
        'DELETE FROM personal_list_items WHERE list_name=%s AND profile=%s',
        (data.list_name, data.profile)
    )
    conn.commit()
    return {"status": "success", "data": data.model_dump()}

@router.post("/api/lists/update-details")
def update_list_details(data: ListUpdate, db = Depends(get_db)):
    conn, cursor = db
    cursor.execute(
        'UPDATE personal_lists SET name=%s, sort_order=%s WHERE name=%s AND profile=%s',
        (data.new_list_name, data.sort_order, data.original_name, data.profile)
    )
    if data.new_list_name != data.original_name:
        cursor.execute(
            'UPDATE personal_list_items SET list_name=%s WHERE list_name=%s AND profile=%s',
            (data.new_list_name, data.original_name, data.profile)
        )
    conn.commit()

    return {"status": "success", "data": data.model_dump()}

@router.get("/api/lists")
def get_lists(
    profile: str = Query(..., description="The watch profile name"),
    db = Depends(get_db)
):
    _, cursor = db
    cursor.execute(
        'SELECT l.name, '
        '(SELECT COUNT(*) FROM personal_list_items i '
        'WHERE i.list_name=l.name AND i.profile=l.profile), '
        'l.sort_order FROM personal_lists l WHERE l.profile=%s',
        (profile,)
    )
    return _rows(cursor)

@router.get("/api/lists/get")
def get_list(
    list_name: str = Query(..., description="The name of the list"),
    profile: str = Query(..., description="The watch profile name"),
    db = Depends(get_db)
):
    _, cursor = db
    cursor.execute(
        'SELECT media_id, media_type, title, CAST(date_added AS CHAR), release_date FROM personal_list_items '
        'WHERE list_name=%s AND profile=%s ORDER BY date_added',
        (list_name, profile)
    )
    return JSONResponse([
        {'media_id': row[0], 'media_type': row[1], 'type': row[1], 'title': row[2],
         'date_added': row[3], 'release_date': row[4]}
        for row in cursor.fetchall()
    ])

@router.post("/api/lists/add-item")
def add_list_item(data: ListAddItem, db = Depends(get_db)):
    conn, cursor = db
    cursor.execute(LIST_ITEM_INSERT, _list_item_row(data.profile, data.list_name, data.item, require_utc_offset(data.utc_offset)))
    conn.commit()
    return {"changed": cursor.rowcount > 0}

@router.post("/api/lists/remove-item")
def remove_list_item(data: ListRemoveItem, db = Depends(get_db)):
    conn, cursor = db
    cursor.execute(
        'DELETE FROM personal_list_items WHERE profile=%s AND list_name=%s AND media_id=%s',
        (data.profile, data.list_name, str(data.media_id))
    )
    conn.commit()
    return {"changed": cursor.rowcount > 0}

@router.post("/api/lists/add-many-items")
def add_many_list_items(data: ListAddMany, db = Depends(get_db)):
    conn, cursor = db
    utc_offset = require_utc_offset(data.utc_offset)
    rows = [_list_item_row(data.profile, data.list_name, item, utc_offset) for item in data.items]
    if rows:
        cursor.executemany(LIST_ITEM_INSERT, rows)
    conn.commit()
    return {"success": True}

BATCH_MAX_SHOWS = 1000
IMPORT_MAX_ROWS = 2000

class EpisodesInfoRequest(BaseModel):
    media_ids: list[Union[str, int]]
    profile: str

@router.post("/api/episodes/info")
def get_episodes_info(data: EpisodesInfoRequest, db = Depends(get_db)):
    """Watched episodes and episode bookmarks for many shows at once, so an episode list or widget
    needs one request instead of two per show"""
    _, cursor = db
    media_ids = list(dict.fromkeys(str(i) for i in data.media_ids))
    if len(media_ids) > BATCH_MAX_SHOWS:
        raise HTTPException(status_code=422, detail="Ask for at most %d shows at a time" % BATCH_MAX_SHOWS)
    watched, bookmarks = {i: [] for i in media_ids}, {i: [] for i in media_ids}
    if media_ids:
        in_list = ', '.join(['%s'] * len(media_ids))
        cursor.execute(
            'SELECT media_id, season, episode FROM watched WHERE db_type = %%s AND profile = %%s AND media_id IN (%s)' % in_list,
            ('episode', data.profile, *media_ids)
        )
        for media_id, season, episode in cursor.fetchall():
            watched.setdefault(media_id, []).append([season, episode])
        cursor.execute(
            'SELECT media_id, season, resume_point, curr_time, resume_id, episode FROM progress '
            'WHERE db_type = %%s AND profile = %%s AND media_id IN (%s)' % in_list,
            ('episode', data.profile, *media_ids)
        )
        for media_id, season, resume_point, curr_time, resume_id, episode in cursor.fetchall():
            bookmarks.setdefault(media_id, []).append([season, resume_point, curr_time, resume_id, episode])
    return JSONResponse({'watched': watched, 'bookmarks': bookmarks})

@router.get("/api/profiles")
def get_profiles(db = Depends(get_db)):
    """The profiles that already have data in this database, for Fen Light's profile picker"""
    _, cursor = db
    cursor.execute('''
        SELECT profile, SUM(watched), SUM(in_progress), SUM(lists) FROM (
            SELECT profile, COUNT(*) AS watched, 0 AS in_progress, 0 AS lists FROM watched GROUP BY profile
            UNION ALL SELECT profile, 0, COUNT(*), 0 FROM progress GROUP BY profile
            UNION ALL SELECT profile, 0, 0, COUNT(*) FROM personal_lists GROUP BY profile
        ) counts
        WHERE profile IS NOT NULL AND profile <> ''
        GROUP BY profile ORDER BY profile
    ''')
    return JSONResponse([
        {'profile': profile, 'watched': int(watched), 'in_progress': int(in_progress), 'lists': int(lists)}
        for profile, watched, in_progress, lists in cursor.fetchall()
    ])

class ImportRequest(BaseModel):
    profile: str
    utc_offset: Optional[str] = None
    # rows of [media_type, media_id, season, episode, last_played, title]
    watched: list[list[Any]] = []
    # rows of [media_type, media_id, season, episode, resume_point, curr_time, last_played, resume_id, title]
    progress: list[list[Any]] = []
    hidden: list[int] = []

def _optional_int(value):
    return int(value) if value not in (None, '') else None

def _import_timestamp(value, utc_offset):
    # one unreadable date in an old history shouldn't stop the rest of it from importing
    try:
        return to_sql_timestamp(value, utc_offset)
    except HTTPException:
        return None

def _insert_missing(cursor, table_name, columns, rows):
    """Insert the rows the table doesn't have yet and return how many were added. Rows with a season and episode
    are caught by the unique key (one batched INSERT IGNORE); movies, whose empty season and episode the unique key
    doesn't match, are checked one by one. Each row starts with db_type, media_id, season, episode and ends with profile."""
    added = 0
    keyed = [row for row in rows if row[2] is not None and row[3] is not None]
    keyless = [row for row in rows if row[2] is None or row[3] is None]
    placeholders = ', '.join(['%s'] * len(columns))
    if keyed:
        cursor.executemany('INSERT IGNORE INTO %s (%s) VALUES (%s)' % (table_name, ', '.join(columns), placeholders), keyed)
        added += cursor.rowcount
    if keyless:
        cursor.executemany(
            'INSERT INTO %s (%s) SELECT %s FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM %s WHERE db_type = %%s AND media_id = %%s '
            'AND season <=> %%s AND episode <=> %%s AND profile <=> %%s)' % (table_name, ', '.join(columns), placeholders, table_name),
            [row + row[:4] + row[-1:] for row in keyless]
        )
        added += cursor.rowcount
    return added

@router.post("/api/import")
def import_history(data: ImportRequest, db = Depends(get_db)):
    """Add watch history from another source, such as Fen Light's local database or its Trakt cache.
    Entries the profile already has are kept as they are."""
    conn, cursor = db
    if len(data.watched) + len(data.progress) > IMPORT_MAX_ROWS:
        raise HTTPException(status_code=422, detail="Send at most %d rows at a time" % IMPORT_MAX_ROWS)
    if any(len(row) < 6 for row in data.watched) or any(len(row) < 9 for row in data.progress):
        raise HTTPException(status_code=422, detail="watched rows need 6 values and progress rows 9")
    utc_offset = require_utc_offset(data.utc_offset)
    added = {'watched': 0, 'progress': 0, 'hidden': 0}
    if data.watched:
        added['watched'] = _insert_missing(
            cursor, 'watched', ('db_type', 'media_id', 'season', 'episode', 'last_played', 'utc_offset', 'title', 'profile'),
            [(str(media_type), str(media_id), _optional_int(season), _optional_int(episode),
              _import_timestamp(last_played, utc_offset), utc_offset, title, data.profile)
             for media_type, media_id, season, episode, last_played, title in (row[:6] for row in data.watched)]
        )
    if data.progress:
        added['progress'] = _insert_missing(
            cursor, 'progress', ('db_type', 'media_id', 'season', 'episode', 'resume_point', 'curr_time', 'last_played',
                                 'utc_offset', 'resume_id', 'title', 'profile'),
            [(str(media_type), str(media_id), _optional_int(season), _optional_int(episode), str(resume_point), str(curr_time),
              _import_timestamp(last_played, utc_offset), utc_offset, _optional_int(resume_id) or 0, title, data.profile)
             for media_type, media_id, season, episode, resume_point, curr_time, last_played, resume_id, title in (row[:9] for row in data.progress)]
        )
    if data.hidden:
        added['hidden'] = _add_hidden_items(cursor, data.profile, data.hidden)
    conn.commit()
    return added
