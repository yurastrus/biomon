"""Gap sampling and the manager-only preparation route."""

from unittest.mock import MagicMock, patch

from app.pam.pam_gap_sampling import prioritize_candidates


def _clip(detection, species, institutions, month, confidence, recording=None):
    return {
        'detection_id': detection, 'species_id': species, 'institution_ids': institutions,
        'recorded_month': month, 'confidence': confidence, 'recording_id': recording or detection,
        'is_redbook': False,
    }


def test_gap_plan_prioritizes_distinct_gaps_and_recordings():
    history = [_clip(1, 1, [10], 5, .92), _clip(2, 2, [20], 6, .94)]
    candidates = [
        _clip(3, 1, [10], 5, .99),
        _clip(4, 1, [10], 7, .90),
        _clip(5, 1, [10], 7, .89, recording=4),
        _clip(6, 1, [20], 5, .91),
        _clip(7, 3, [10, 20], 6, .88),
        _clip(8, 2, [20], 6, .99),
        _clip(9, 1, [10], 8, .75),
    ]
    plan = prioritize_candidates(candidates, history, 10, balance_species=True, balance_institutions=True)
    assert [r['detection_id'] for r in plan[:4]] == [7, 6, 4, 9]
    assert [r['novelty'] for r in plan] == [3, 2, 1, 1, 0, 0]
    assert 5 not in [r['detection_id'] for r in plan]


def test_gap_plan_balances_unfiltered_territories_on_a_tie():
    candidates = [_clip(1, 1, [10], 5, .96), _clip(2, 2, [10], 5, .95), _clip(3, 2, [20], 5, .93)]
    assert [r['detection_id'] for r in prioritize_candidates(
        candidates, [], 2, balance_species=True, balance_institutions=True,
    )] == [1, 3]


def test_gap_route_accepts_optional_filters_and_ignores_period(auth_client):
    client = auth_client(role='admin')
    connection = MagicMock()
    connection.__enter__.return_value = connection
    with (
        patch('app.pam.routes.get_pam_db_connection', return_value=connection),
        patch('app.pam.routes._resolve_model_choice', return_value=(4, False)),
        patch('app.pam.utils.get_model_conf_columns', return_value={4: 'conf_perch_v2'}),
        patch('app.pam.pam_gap_sampling.plan_gap_sample', return_value=([], False)) as prepare,
    ):
        response = client.post('/uk/api/pam/sample/prepare-gaps', json={
            'sample_size': 5, 'species_name': 'Parus major', 'institution_id': 12,
            'location_ids': [7], 'model_id': 4, 'date_from': '2026-01-01', 'month_start': 5,
            'class_name': 'Aves', 'order_name': 'Passeriformes',
            'family_name': 'Paridae', 'genus_name': 'Parus',
        })
    assert response.status_code == 200
    params = prepare.call_args.kwargs['params']
    assert params['species_name'] == 'Parus major'
    assert params['institution_ids'] == [12] and not params['all_institutions']
    assert params['location_ids'] == [7] and not params['all_locations']
    assert 'date_from' not in params and 'month_start' not in params
    assert prepare.call_args.kwargs['conf_column'] == 'conf_perch_v2'
    assert [params[field] for field in ('class_name', 'order_name', 'family_name', 'genus_name')] == [
        'Aves', 'Passeriformes', 'Paridae', 'Parus',
    ]


def test_sample_species_catalog_exposes_taxonomy(auth_client):
    client = auth_client(role='admin')
    conn = MagicMock()
    conn.execute.return_value.mappings.return_value.fetchall.return_value = [{
        'species_id': 1, 'scientific_name': 'Parus major', 'common_name_uk': 'Синиця велика',
        'common_name_en': 'Great tit', 'class_name': 'Aves', 'order_name': 'Passeriformes',
        'family_name': 'Paridae', 'genus_name': 'Parus',
    }]
    with patch('app.pam.routes.get_pam_db_connection', return_value=conn):
        response = client.get('/en/api/pam/sample/species?catalog=1')
    assert response.status_code == 200
    species = response.json['species'][0]
    assert species['text'] == 'Great tit (Parus major)'
    assert [species[field] for field in ('class_name', 'order_name', 'family_name', 'genus_name')] == [
        'Aves', 'Passeriformes', 'Paridae', 'Parus',
    ]


def test_gap_route_preserves_manager_scope_even_without_filters(auth_client):
    client = auth_client(role='manager', manages=True)
    connection = MagicMock()
    connection.__enter__.return_value = connection
    with (
        patch('app.pam.routes.get_pam_db_connection', return_value=connection),
        patch('app.pam.routes._resolve_model_choice', return_value=(1, True)),
        patch('app.pam.utils.get_model_conf_columns', return_value={1: 'confidence'}),
        patch('app.pam.pam_gap_sampling.plan_gap_sample', return_value=([], False)) as prepare,
    ):
        response = client.post('/en/api/pam/sample/prepare-gaps', json={'sample_size': 5})
        assert response.status_code == 200
        params = prepare.call_args.kwargs['params']
        assert not params['is_admin'] and len(params['managed_ids']) == 1
        assert params['all_institutions'] and params['all_locations']
        prepare.reset_mock()
        response = client.post('/en/api/pam/sample/prepare-gaps', json={
            'institution_ids': [params['managed_ids'][0], params['managed_ids'][0] + 1],
        })
        assert response.status_code == 403
        prepare.assert_not_called()
