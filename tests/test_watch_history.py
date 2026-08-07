import os
import sys
from unittest.mock import MagicMock, patch

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
                media_type='movie',
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
            resumed = watch_history.get_resume_state('123', 'movie', 'default')

    assert saved is True
    assert resumed is not None
    assert resumed['progress_seconds'] == 20
    assert resumed['is_finished'] is False
