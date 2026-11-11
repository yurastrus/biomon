"""Ecoregion scope on the PAM overview and detailed dashboards."""

from unittest.mock import patch

import pytest
from werkzeug.datastructures import MultiDict

from app.models import Institution, User, UserInstitution


@pytest.fixture
def institutions(db_session):
    rows = [
        Institution(code='POL1', name_uk='Парк 1', ecoregion_uk='Полісся',
                    ecoregion_en='Polissia'),
        Institution(code='POL2', name_uk='Парк 2', ecoregion_uk='Полісся'),
        Institution(code='CAR', name_uk='Парк 3', ecoregion_uk='Карпати',
                    ecoregion_en='Carpathians'),
        Institution(code='OTH', name_uk='Парк 4', ecoregion_uk='Степ'),
    ]
    db_session.add_all(rows)
    db_session.flush()
    return rows


@pytest.mark.parametrize(('endpoint', 'query_function'), [
    ('get-plot-data', 'get_filtered_detections'),
    ('get-barchart-data', 'get_daily_detection_counts'),
    ('get-time-scatter-data', 'get_time_scatter_data'),
    ('get-species-summary', 'get_species_summary'),
    ('get-unique-points', 'get_unique_detection_points'),
    ('get-species-ranking', 'get_species_ranking'),
    ('get-overview-stats', 'get_overview_statistics'),
    ('get-locations-map', 'get_locations_for_map'),
])
def test_dashboard_endpoints_use_the_same_union(client, institutions, endpoint, query_function):
    polissia, second, carpathians, other = institutions
    query = MultiDict([
        ('species', 'Parus major'),
        ('institution_id', f'{polissia.id},{other.id}'),
        ('ecoregion', 'Полісся'),
        ('ecoregion', 'Карпати'),
    ])
    with (
        patch('app.pam.utils.get_available_species', return_value=[{'value': 'Parus major'}]),
        patch(f'app.pam.utils.{query_function}', return_value={}) as query_data,
    ):
        response = client.get(f'/uk/api/pam/{endpoint}', query_string=query)

    assert response.status_code == 200
    assert query_data.call_args.kwargs['institution_id'] == sorted(
        [polissia.id, second.id, carpathians.id, other.id]
    )


@pytest.mark.parametrize('endpoint', ['get-filters-data', 'get-trends-filters'])
def test_filter_options_offer_only_regions_with_visible_pam_recordings(
    client, institutions, mock_pam_conn, endpoint
):
    polissia, second, _, steppe = institutions
    visible_rows = [
        {'id': inst.id, 'name_uk': inst.name_uk, 'name_en': None}
        for inst in (polissia, second, steppe)
    ]
    with mock_pam_conn() as conn:
        result = conn.execute.return_value
        result.mappings.return_value.fetchall.side_effect = [
            visible_rows, *([[]] * 12)
        ]
        response = client.get(f'/en/api/pam/{endpoint}', query_string={'ecoregion': 'Полісся'})

    assert response.status_code == 200
    assert response.json['ecoregions'] == [
        {'id': 'Полісся', 'text': 'Polissia'},
        {'id': 'Степ', 'text': 'Степ'},
    ]
    # The trends API also loads global taxonomy options after years/biotopes/locations.
    scope_query_count = 3 if endpoint == 'get-trends-filters' else 2
    dependent_queries = conn.execute.call_args_list[1:1 + scope_query_count]
    assert len(dependent_queries) == scope_query_count
    for call in dependent_queries:
        sql, params = call.args
        assert params['selected_inst_id'] == sorted([polissia.id, second.id])
        assert 'l.visibility_level = 0' in str(sql)
        assert 'li_sel.institution_id = ANY(:selected_inst_id)' in str(sql)


def test_unavailable_ecoregion_does_not_reset_to_all(client, mock_pam_conn):
    with mock_pam_conn() as conn:
        response = client.get(
            '/uk/api/pam/get-filters-data', query_string={'ecoregion': 'Невідомий регіон'}
        )

    assert response.status_code == 200
    assert all(call.args[1]['selected_inst_id'] == [-1]
               for call in conn.execute.call_args_list[1:])


def test_ecoregion_selection_keeps_pam_access_separate_from_ct(
    auth_client, db_session, institutions, mock_pam_conn
):
    client = auth_client(role='viewer', username='region_viewer')
    user = User.query.filter_by(username='region_viewer').one()
    polissia, second = institutions[:2]
    user.institution_links.extend([
        UserInstitution(institution_id=polissia.id, can_view_pam=True, can_view_ct=False),
        UserInstitution(institution_id=second.id, can_view_pam=False, can_view_ct=True),
    ])
    db_session.commit()

    with mock_pam_conn() as conn:
        response = client.get('/uk/api/pam/get-filters-data', query_string={'ecoregion': 'Полісся'})

    assert response.status_code == 200
    sql, params = conn.execute.call_args_list[-1].args
    assert params['user_inst_ids'] == [polissia.id]
    assert params['selected_inst_id'] == [polissia.id, second.id]
    assert 'li_perm.institution_id = ANY(:user_inst_ids)' in str(sql)
    assert 'AND EXISTS' in str(sql)


def test_weather_centroid_uses_the_same_ecoregion(client, institutions, mock_pam_conn):
    with (
        mock_pam_conn() as conn,
        patch('app.pam.utils.get_weather_data', return_value=[]) as get_weather,
    ):
        conn.execute.return_value.fetchone.return_value = (48.2, 24.6)
        response = client.get('/uk/api/pam/get-weather-overlay', query_string={
            'start_date': '2026-01-01', 'end_date': '2026-02-01',
            'species': 'Parus major', 'ecoregion': 'Карпати',
        })

    assert response.status_code == 200
    sql, params = conn.execute.call_args.args
    assert 'l.visibility_level = 0' in str(sql)
    assert params['selected_inst_id'] == [institutions[2].id]
    get_weather.assert_called_once_with('2026-01-01', '2026-02-01', 48.2, 24.6)


def test_detailed_xlsx_export_uses_the_selected_region(auth_client, institutions):
    client = auth_client(role='admin')
    with (
        patch('app.pam.utils.get_unique_detection_points', return_value=[]) as points,
        patch('app.pam.utils.get_daily_detection_counts',
              return_value={'dates': [], 'counts': []}) as daily,
        patch('app.pam.utils.get_time_scatter_data', return_value={}) as scatter,
        patch('app.pam.utils.get_weather_data', return_value=[]),
    ):
        response = client.get('/uk/api/pam/export-detailed-data', query_string={
            'species': 'Parus major', 'ecoregion': 'Полісся',
        })

    assert response.status_code == 200
    assert response.mimetype.endswith('spreadsheetml.sheet')
    for query in (points, daily, scatter):
        assert query.call_args.kwargs['institution_id'] == [inst.id for inst in institutions[:2]]
