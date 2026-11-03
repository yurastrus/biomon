"""Who may reach the video upload page.

Admin-only until 2026-11-03, while the only source of a clip's time was the
overlay reader. Now most clips get their time from the file header or from the
operator reading the first frame, both shown before anything is written, so the
page is open to managers like the photo upload. Frames are stored only on
locations the user manages.

These tests exist so that the gate cannot be changed by accident.
"""

import pytest

VIDEO_ENDPOINTS = (
    ('get', '/uk/camera-traps/upload-video'),
    ('post', '/uk/camera-traps/api/video/calibrate'),
    ('post', '/uk/camera-traps/api/video/read-clip'),
    ('post', '/uk/camera-traps/api/video/process-frame'),
    ('post', '/uk/camera-traps/api/video/metadata'),
    ('post', '/uk/camera-traps/api/video/sign-clip'),
)


def _call(client, method, url):
    return client.get(url) if method == 'get' else client.post(url)


@pytest.mark.parametrize('method,url', VIDEO_ENDPOINTS)
def test_a_manager_gets_through_the_gate(auth_client, method, url):
    response = _call(auth_client(role='manager', manages=True), method, url)
    assert response.status_code not in (302, 401, 403)


@pytest.mark.parametrize('method,url', VIDEO_ENDPOINTS)
def test_a_verifier_is_turned_away(auth_client, method, url):
    response = _call(auth_client(role='ct_verifier'), method, url)
    assert response.status_code in (302, 403)


@pytest.mark.parametrize('method,url', VIDEO_ENDPOINTS)
def test_an_anonymous_visitor_is_turned_away(client, method, url):
    response = _call(client, method, url)
    assert response.status_code in (302, 401, 403)


@pytest.mark.parametrize('method,url', VIDEO_ENDPOINTS)
def test_an_admin_gets_through_the_gate(auth_client, method, url):
    """Past the gate, not necessarily to a happy answer.

    The POST endpoints reject an empty body with 400, which is the point: the
    request was let through on role and then judged on its contents.
    """
    response = _call(auth_client(role='admin'), method, url)
    assert response.status_code not in (302, 401, 403)


# One logged-in client per test, deliberately. Building two in a single test
# does not work: the second client keeps acting as the first user, so a test
# that compares two roles side by side silently compares one role with itself
# and passes for the wrong reason. That is a quirk of the shared auth_client
# fixture, not of this feature.

def test_the_hub_offers_the_page_to_a_manager(auth_client):
    page = auth_client(role='manager', manages=True).get('/uk/camera-traps/')

    assert b'upload-video' in page.data
    assert b'upload-fast' in page.data


def test_the_hub_hides_the_page_from_a_verifier(auth_client):
    page = auth_client(role='ct_verifier').get('/uk/camera-traps/')

    assert b'upload-video' not in page.data


def test_a_frame_for_a_batch_the_manager_does_not_manage_is_refused(auth_client, app):
    """A valid token is not enough: the batch must sit on a managed location."""
    from io import BytesIO
    from unittest.mock import patch
    client = auth_client(role='manager', manages=True)
    with patch('app.camera_traps.routes._batch_access_error',
               return_value=({'error': 'no'}, 403)) as gate:
        response = client.post('/uk/camera-traps/api/video/process-frame', data={
            'location_id': '1', 'batch_id': 'b1', 'token': 't', 'filename': 'x.avi',
            'file': (BytesIO(b'jpeg'), 'x_000000.jpg')}, content_type='multipart/form-data')
    assert response.status_code == 403
    gate.assert_called_once_with('b1', '1')


def test_the_hub_offers_the_page_to_an_admin(auth_client):
    page = auth_client(role='admin').get('/uk/camera-traps/')

    assert b'upload-video' in page.data
