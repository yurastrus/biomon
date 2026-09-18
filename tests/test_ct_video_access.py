"""Who may reach the video upload page.

Admin-only while the reader is still being taught new camera families. A
mis-read capture time is invisible once it is in the database -- it misgroups
series, shifts activity-by-hour and distorts phenology, and nobody notices for a
year. Until every camera family in the archives reads cleanly, the people who can
recognise a wrong reading are the only ones who should be producing them.

These tests exist so that the gate cannot be relaxed by accident: widening it
should take an edit here too, and therefore a moment's thought.
"""

import pytest

VIDEO_ENDPOINTS = (
    ('get', '/uk/camera-traps/upload-video'),
    ('post', '/uk/camera-traps/api/video/calibrate'),
    ('post', '/uk/camera-traps/api/video/read-clip'),
    ('post', '/uk/camera-traps/api/video/process-frame'),
)


def _call(client, method, url):
    return client.get(url) if method == 'get' else client.post(url)


@pytest.mark.parametrize('method,url', VIDEO_ENDPOINTS)
def test_a_manager_is_turned_away(auth_client, method, url):
    # A manager may upload photos; video is a narrower door for now.
    response = _call(auth_client(role='manager'), method, url)
    assert response.status_code in (302, 403)


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

def test_the_hub_hides_the_page_from_a_manager(auth_client):
    page = auth_client(role='manager').get('/uk/camera-traps/')

    assert b'upload-video' not in page.data
    # The photo upload stays exactly where it was for managers.
    assert b'upload-fast' in page.data


def test_the_hub_offers_the_page_to_an_admin(auth_client):
    page = auth_client(role='admin').get('/uk/camera-traps/')

    assert b'upload-video' in page.data
