import os
import sys
from unittest.mock import MagicMock, patch

from pymysql.err import OperationalError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'plugin.video.fenlight', 'resources', 'lib'))

from modules import watch_history


def test_history_record_round_trip():
    connection = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = {
        'tmdb_id': '123',
        'media_type': 'movie',
        'title': 'Example Movie',
        'start_time': 10,
        'end_time': 120,
        'progress_seconds': 20,
        'total_length_seconds': 120,
        'is_finished': 0,
        'profile_name': 'default',
        'season_number': None,
        'episode_number': None,
        'show_title': None,
        'show_tmdb_id': None,
    }
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = None
    connection.cursor.return_value = cursor
    connection.commit.return_value = None
    connection.close.return_value = None

    with patch('modules.watch_history.get_setting', side_effect=lambda key, fallback='': {
        'fenlight.watch_history.enabled': 'true',
        'fenlight.watch_history.server_ip': '127.0.0.1',
        'fenlight.watch_history.port': '3306',
        'fenlight.watch_history.username': 'root',
        'fenlight.watch_history.password': '',
        'fenlight.watch_history.database_name': 'fenlight',
        'fenlight.watch_history.profile_name': 'default',
    }.get(key, fallback)):
        with patch('modules.watch_history.pymysql.connect', return_value=connection):
            watch_history.initialize_history_database()
            saved = watch_history.save_watch_history_entry(
                tmdb_id='123',
                title='Example Movie',
                start_time=10,
                end_time=120,
                progress_seconds=20,
                total_length_seconds=120,
                is_finished=False,
                profile='default',
                season_number=None,
                episode_number=None,
                show_title=None,
                show_tmdb_id=None,
            )
            resumed = watch_history.get_resume_state('123', 'default')

    assert saved is True
    assert resumed is not None
    assert resumed['progress_seconds'] == 20
    assert resumed['is_finished'] is False
    executed_sql = ' '.join(call.args[0] for call in cursor.execute.call_args_list if call.args)
    assert 'watch_history' not in executed_sql
    assert 'progress' in executed_sql


def test_auth_failure_does_not_raise():
    with patch('modules.watch_history.get_setting', side_effect=lambda key, fallback='': {
        'fenlight.watch_history.enabled': 'true',
        'fenlight.watch_history.server_ip': '127.0.0.1',
        'fenlight.watch_history.port': '3306',
        'fenlight.watch_history.username': 'Kodi',
        'fenlight.watch_history.password': 'wrong',
        'fenlight.watch_history.database_name': 'fenlight',
        'fenlight.watch_history.profile_name': 'default',
    }.get(key, fallback)):
        with patch('modules.watch_history.pymysql.connect', side_effect=OperationalError(1698, 'Access denied')):
            assert watch_history.initialize_history_database() is False
            assert watch_history.reconfigure_history_database() is False
            assert watch_history.save_watch_history_entry('123', 'Example Movie') is False
            assert watch_history.get_resume_state('123', 'default') is None


def test_tuple_based_column_rows_are_supported():
    class FakeCursor(object):
        def __init__(self):
            self._rows = [('tmdb_id',), ('media_type',)]

        def execute(self, *args, **kwargs):
            return None

        def fetchall(self):
            return self._rows

        def fetchone(self):
            return ('varchar(255)',) if self._rows else None

    cursor = FakeCursor()
    assert watch_history._get_column_names(cursor) == {'tmdb_id', 'media_type'}
    assert watch_history._get_column_type(cursor, 'tmdb_id') == 'varchar(255)'


def test_create_watch_history_entry_inserts_history_row():
    connection = MagicMock()
    cursor = MagicMock()
    cursor.lastrowid = 7
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = None
    connection.cursor.return_value = cursor
    connection.commit.return_value = None
    connection.close.return_value = None

    with patch('modules.watch_history.get_setting', side_effect=lambda key, fallback='': {
        'fenlight.watch_history.enabled': 'true',
        'fenlight.watch_history.server_ip': '127.0.0.1',
        'fenlight.watch_history.port': '3306',
        'fenlight.watch_history.username': 'root',
        'fenlight.watch_history.password': '',
        'fenlight.watch_history.database_name': 'fenlight',
        'fenlight.watch_history.profile_name': 'default',
    }.get(key, fallback)):
        with patch('modules.watch_history.pymysql.connect', return_value=connection):
            history_id = watch_history.create_watch_history_entry(
                tmdb_id='123',
                media_type='movie',
                title='Example Movie',
                start_time=10,
            )

    assert history_id == 7
    executed_sql = ' '.join(call.args[0] for call in cursor.execute.call_args_list if call.args)
    assert 'INSERT INTO watch_history' in executed_sql


def test_update_watch_history_entry_updates_existing_row():
    connection = MagicMock()
    cursor = MagicMock()
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = None
    connection.cursor.return_value = cursor
    connection.commit.return_value = None
    connection.close.return_value = None

    with patch('modules.watch_history.get_setting', side_effect=lambda key, fallback='': {
        'fenlight.watch_history.enabled': 'true',
        'fenlight.watch_history.server_ip': '127.0.0.1',
        'fenlight.watch_history.port': '3306',
        'fenlight.watch_history.username': 'root',
        'fenlight.watch_history.password': '',
        'fenlight.watch_history.database_name': 'fenlight',
    }.get(key, fallback)):
        with patch('modules.watch_history.pymysql.connect', return_value=connection):
            updated = watch_history.update_watch_history_entry(
                history_id=7,
                end_time=120,
                progress_seconds=118,
                total_length_seconds=120,
                is_finished=True,
            )

    assert updated is True
    executed_sql = ' '.join(call.args[0] for call in cursor.execute.call_args_list if call.args)
    assert 'UPDATE watch_history' in executed_sql


def test_get_resume_state_supports_tuple_rows():
    connection = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.return_value = ('123', 'default', 120, 20, 120, 0)
    cursor.__enter__.return_value = cursor
    cursor.__exit__.return_value = None
    connection.cursor.return_value = cursor
    connection.commit.return_value = None
    connection.close.return_value = None

    with patch('modules.watch_history.get_setting', side_effect=lambda key, fallback='': {
        'fenlight.watch_history.enabled': 'true',
        'fenlight.watch_history.server_ip': '127.0.0.1',
        'fenlight.watch_history.port': '3306',
        'fenlight.watch_history.username': 'root',
        'fenlight.watch_history.password': '',
        'fenlight.watch_history.database_name': 'fenlight',
        'fenlight.watch_history.profile_name': 'default',
    }.get(key, fallback)):
        with patch('modules.watch_history.pymysql.connect', return_value=connection):
            state = watch_history.get_resume_state('123', 'default')

    assert state is not None
    assert state['tmdb_id'] == '123'
    assert state['profile_name'] == 'default'
    assert state['progress_seconds'] == 20
    assert state['is_finished'] is False
