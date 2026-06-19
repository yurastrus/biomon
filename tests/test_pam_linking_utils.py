"""
Smoke tests for `app.pam.pam_linking_utils`.

No real PAM DB: `get_pam_db_connection` is replaced with a mock.
"""
import pytest
from unittest.mock import MagicMock, patch

from app.pam import pam_linking_utils
from app.pam.pam_linking_utils import link_verifications_to_detections


def test_module_imports():
    assert hasattr(pam_linking_utils, 'link_verifications_to_detections')


def _empty_conn():
    """Mock conn: any .execute -> empty fetchall."""
    conn = MagicMock()
    result = MagicMock()
    result.fetchall.return_value = []
    conn.execute.return_value = result
    # begin() as a context manager
    conn.begin.return_value.__enter__ = lambda self: self
    conn.begin.return_value.__exit__ = lambda self, *a: None
    return conn


def test_link_returns_message_when_no_segments(app):
    conn = _empty_conn()
    with app.app_context():
        with patch('app.pam.pam_linking_utils.get_pam_db_connection',
                   return_value=conn):
            result = link_verifications_to_detections(full_resync=False)
    assert isinstance(result, str)
    assert 'No new segments' in result


def test_link_full_resync_truncates(app):
    """With full_resync=True, TRUNCATE must be called before the query."""
    conn = _empty_conn()
    with app.app_context():
        with patch('app.pam.pam_linking_utils.get_pam_db_connection',
                   return_value=conn):
            link_verifications_to_detections(full_resync=True)
    executed_sql = [
        str(call.args[0]) if call.args else ''
        for call in conn.execute.call_args_list
    ]
    assert any('TRUNCATE' in sql for sql in executed_sql), \
        f'TRUNCATE not in: {executed_sql}'
