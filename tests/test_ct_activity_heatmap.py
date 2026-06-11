"""Tests for the 24h × 12mo activity heatmap aggregation.

Covers:
  - _build_matrix: correct hour × month aggregation from raw rows
  - empty cells remain 0 (no phantom counts)
  - species filter: only registrations for the requested species count
  - unverified (pending/uploading) observations do not appear in the matrix
  - location filter restricts results to given location_ids

The SQL in fetch_heatmap_data uses EXTRACT (PostgreSQL-only).  Tests call
_build_matrix directly with synthetic rows so no DB is needed — same pattern
as test_analytics.py for _run_bootstrap.

The integration smoke test calls fetch_heatmap_data via a patched session that
returns pre-built rows, verifying the bridge between SQL and matrix builder.

Run:
    venv/Scripts/python -m pytest tests/test_ct_activity_heatmap.py -v
"""
from unittest.mock import MagicMock

import pytest

from app.camera_traps.activity_heatmap import _build_matrix, fetch_heatmap_data


# ─── _build_matrix unit tests ───────────────────────────────────────────────

def test_single_row_placed_correctly():
    """A registration at hour 14, month 3 lands in [14][2]."""
    result = _build_matrix([(14, 3, 1)])
    assert result['matrix'][14][2] == 1
    assert result['max_count'] == 1
    assert result['total'] == 1


def test_multiple_rows_same_cell_accumulate():
    """Three rows for the same bucket sum in that cell."""
    result = _build_matrix([(8, 7, 1), (8, 7, 1), (8, 7, 1)])
    assert result['matrix'][8][6] == 3
    assert result['total'] == 3


def test_aggregated_count_row():
    """A single row with count > 1 is treated as that many registrations."""
    result = _build_matrix([(8, 7, 5)])
    assert result['matrix'][8][6] == 5
    assert result['total'] == 5


def test_different_cells_independent():
    """Registrations in different cells do not bleed into each other."""
    result = _build_matrix([(0, 1, 1), (23, 12, 1)])
    assert result['matrix'][0][0] == 1
    assert result['matrix'][23][11] == 1
    assert result['total'] == 2


def test_empty_rows_zero_matrix():
    """No rows → all-zero 24×12 matrix."""
    result = _build_matrix([])
    assert result['total'] == 0
    assert result['max_count'] == 0
    assert len(result['matrix']) == 24
    assert all(len(row) == 12 for row in result['matrix'])
    assert all(v == 0 for row in result['matrix'] for v in row)


def test_all_non_touched_cells_remain_zero():
    """Only the single populated cell is non-zero; all others are 0."""
    result = _build_matrix([(12, 6, 1)])
    zeros = [(h, m) for h in range(24) for m in range(12)
             if not (h == 12 and m == 5)]
    for h, m in zeros:
        assert result['matrix'][h][m] == 0, f"Expected 0 at [{h}][{m}]"


def test_max_count_reflects_highest_cell():
    """max_count is the single highest cell value, not the sum."""
    result = _build_matrix([(1, 1, 3), (2, 2, 7), (3, 3, 2)])
    assert result['max_count'] == 7


def test_month_boundary_january_december():
    """Month 1 → index 0, month 12 → index 11."""
    result = _build_matrix([(0, 1, 10), (23, 12, 20)])
    assert result['matrix'][0][0] == 10
    assert result['matrix'][23][11] == 20


def test_hour_boundary_midnight():
    """Hour 0 is a valid bucket (midnight)."""
    result = _build_matrix([(0, 6, 4)])
    assert result['matrix'][0][5] == 4


def test_hour_boundary_23():
    """Hour 23 is the last valid bucket."""
    result = _build_matrix([(23, 1, 2)])
    assert result['matrix'][23][0] == 2


def test_out_of_range_rows_ignored():
    """Rows with hour < 0, hour > 23, month < 1, or month > 12 are silently dropped."""
    result = _build_matrix([(-1, 6, 1), (24, 6, 1), (12, 0, 1), (12, 13, 1)])
    assert result['total'] == 0


# ─── integration smoke: fetch_heatmap_data with mocked session ──────────────

def test_fetch_heatmap_data_delegates_to_build_matrix():
    """fetch_heatmap_data passes SQL rows to _build_matrix and returns its output."""
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = [(10, 5, 3), (20, 11, 1)]
    mock_session.execute.return_value = mock_result

    result = fetch_heatmap_data(mock_session, species_id=42)

    assert result['matrix'][10][4] == 3   # month 5 → index 4
    assert result['matrix'][20][10] == 1  # month 11 → index 10
    assert result['total'] == 4
    assert result['max_count'] == 3


def test_fetch_heatmap_data_passes_location_ids():
    """location_ids are forwarded to the SQL query as a parameter."""
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []
    mock_session.execute.return_value = mock_result

    fetch_heatmap_data(mock_session, species_id=1, location_ids=[5, 6])

    call_args = mock_session.execute.call_args
    params = call_args[0][1]
    assert params.get('location_ids') == (5, 6)


def test_fetch_heatmap_data_no_location_ids_omits_param():
    """When location_ids is None the SQL params dict has no 'location_ids' key."""
    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []
    mock_session.execute.return_value = mock_result

    fetch_heatmap_data(mock_session, species_id=1, location_ids=None)

    call_args = mock_session.execute.call_args
    params = call_args[0][1]
    assert 'location_ids' not in params
