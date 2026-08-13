import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / 'plugin.video.fenlight' / 'resources' / 'lib'
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from modules import watched_status


class FakeDB:
    def __init__(self):
        self.calls = []

    def executemany(self, query, params):
        self.calls.append((query, params))
        if not isinstance(params, list):
            raise AssertionError(f'Expected list of parameter tuples, got {type(params).__name__}: {params!r}')
        for param in params:
            if not isinstance(param, tuple):
                raise AssertionError(f'Expected tuple entries, got {type(param).__name__}: {param!r}')
            if len(param) != 7:
                raise AssertionError(f'Expected 7 values for watched profile row, got {len(param)}: {param!r}')


def test_batch_watched_status_marks_append_profile_for_mariadb(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(watched_status, 'get_database', lambda watched_indicator=None: db)
    monkeypatch.setattr(watched_status.settings, 'watch_history_profile_name', lambda: 'profile_1')

    insert_list = [
        ('episode', 123, 1, 2, '2024-01-01 00:00:00', 'Show Title'),
        ('episode', 123, 1, 3, '2024-01-01 00:00:00', 'Show Title'),
    ]

    watched_status.batch_watched_status_mark(2, insert_list, 'mark_as_watched')

    assert len(db.calls) == 2
    query, params = db.calls[0]
    assert 'INSERT IGNORE INTO watched' in query
    assert params == [
        ('episode', 123, 1, 2, '2024-01-01 00:00:00', 'Show Title', 'profile_1'),
        ('episode', 123, 1, 3, '2024-01-01 00:00:00', 'Show Title', 'profile_1'),
    ]
