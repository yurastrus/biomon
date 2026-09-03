"""Per-module institution access, phase 3: the read paths respect the split.

Phase 1 stored the four flags, phase 2 let the admin form set them; this pins
that the modules actually *read* their own flag, and that a submodule keeps
working in a host whose User predates the split.

What is pinned:
  * `User.allowed_institution_ids` / `module_institutions` /
    `export_institution_ids` answer per module;
  * `app.camera_traps.access` and `app.pam.access` ask the host for their own
    module, and fall back to the legacy meaning when the host cannot answer;
  * approving a verification request grants only the approved module.

Run:
    venv/Scripts/python -m pytest tests/test_module_access_reads.py -v
"""
import pytest

from app.camera_traps import access as ct_access
from app.pam import access as pam_access


@pytest.fixture
def two_parks(db_session):
    from app.models import Institution

    a = Institution(name_uk='НПП Перший', name_en='First NNP', code='ONE')
    b = Institution(name_uk='НПП Другий', name_en='Second NNP', code='TWO')
    db_session.add_all([a, b])
    db_session.commit()
    return a, b


@pytest.fixture
def split_user(db_session, make_user, two_parks):
    """Photos in park A, sounds in park B — the case the split exists for."""
    from app.models import UserInstitution

    ct_park, pam_park = two_parks
    user = make_user(username='split', roles=('ct_verifier', 'pam_verifier'))
    user.institution_links.append(UserInstitution(
        institution_id=ct_park.id,
        can_view_ct=True, can_export_ct=True,
        can_view_pam=False, can_export_pam=False))
    user.institution_links.append(UserInstitution(
        institution_id=pam_park.id,
        can_view_ct=False, can_export_ct=False,
        can_view_pam=True, can_export_pam=False))
    db_session.commit()
    return user


# ── the model helpers ──────────────────────────────────────────────────────

def test_allowed_ids_are_per_module(split_user, two_parks):
    ct_park, pam_park = two_parks
    assert split_user.allowed_institution_ids('ct') == [ct_park.id]
    assert split_user.allowed_institution_ids('pam') == [pam_park.id]


def test_module_institutions_are_per_module(split_user, two_parks):
    ct_park, pam_park = two_parks
    assert [i.code for i in split_user.module_institutions('ct')] == ['ONE']
    assert [i.code for i in split_user.module_institutions('pam')] == ['TWO']


def test_export_is_per_module(split_user, two_parks):
    ct_park, _ = two_parks
    assert split_user.export_institution_ids('ct') == [ct_park.id]
    assert split_user.export_institution_ids('pam') == []


def test_the_module_blind_property_means_either_module(split_user, two_parks):
    """`export_institutions` (no module) is what shared-ct / shared-pam ask a
    host that has no per-module methods, so it must stay meaningful."""
    assert [i.code for i in split_user.export_institutions] == ['ONE']


def test_a_row_that_grants_nothing_grants_nothing(db_session, make_user, two_parks):
    """Since phase 4 the flags are the only truth: a bare row (all four false by
    default) opens neither module, whatever roles the person holds."""
    from app.models import UserInstitution

    park, _ = two_parks
    user = make_user(username='bare_row', roles=('ct_verifier', 'pam_verifier'))
    user.institution_links.append(UserInstitution(institution_id=park.id))
    db_session.commit()

    assert user.allowed_institution_ids('ct') == []
    assert user.allowed_institution_ids('pam') == []
    assert user.export_institution_ids('ct') == []
    assert user.export_institutions == []


# ── the submodule helpers ──────────────────────────────────────────────────

def test_each_module_asks_for_its_own_flag(split_user, two_parks):
    ct_park, pam_park = two_parks
    assert ct_access.allowed_institution_ids(split_user) == [ct_park.id]
    assert pam_access.allowed_institution_ids(split_user) == [pam_park.id]
    assert [i.code for i in ct_access.allowed_institutions(split_user)] == ['ONE']
    assert [i.code for i in pam_access.allowed_institutions(split_user)] == ['TWO']
    assert ct_access.export_institution_ids(split_user) == [ct_park.id]
    assert pam_access.export_institution_ids(split_user) == []


def test_has_module_access_is_per_module(split_user):
    assert ct_access.has_module_access(split_user) is True
    assert pam_access.has_module_access(split_user) is True


def test_pam_only_person_has_no_camera_trap_access(db_session, make_user, two_parks):
    from app.models import UserInstitution

    park, _ = two_parks
    user = make_user(username='listener', roles=('pam_verifier',))
    user.institution_links.append(UserInstitution(
        institution_id=park.id,
        can_view_ct=False, can_export_ct=False,
        can_view_pam=True, can_export_pam=False))
    db_session.commit()

    assert ct_access.allowed_institution_ids(user) == []
    assert ct_access.has_module_access(user) is False
    assert pam_access.allowed_institution_ids(user) == [park.id]


@pytest.mark.parametrize('module', [ct_access, pam_access])
def test_anonymous_visitors_get_nothing(module):
    class Anon:
        is_authenticated = False
        institutions = []

    assert module.allowed_institution_ids(Anon()) == []
    assert module.allowed_institutions(Anon()) == []
    assert module.export_institution_ids(Anon()) == []
    assert module.has_module_access(Anon()) is False
    assert module.allowed_institution_ids(None) == []


@pytest.mark.parametrize('module', [ct_access, pam_access])
def test_a_host_without_the_split_falls_back_to_the_old_meaning(module):
    """shared-ct also runs in /var/www/myproject, whose User has no per-module
    methods. There the row must keep meaning "access", as it always did."""
    class Park:
        def __init__(self, id):
            self.id = id

    class OldHostUser:
        is_authenticated = True
        institutions = [Park(7), Park(8)]
        export_institutions = [Park(7)]

    user = OldHostUser()
    assert module.allowed_institution_ids(user) == [7, 8]
    assert [p.id for p in module.allowed_institutions(user)] == [7, 8]
    assert module.export_institution_ids(user) == [7]
    assert module.has_module_access(user) is True


# ── approving a request grants one module ──────────────────────────────────

def test_approval_grants_only_the_approved_module(db_session, make_user, two_parks):
    from app.admin.services import VerificationRequestService as Service
    from app.models import VerificationRequest, VerificationRequestInstitution
    from datetime import datetime

    park, _ = two_parks
    admin = make_user(username='root_for_grant', roles=('admin',))
    applicant = make_user(username='newcomer', roles=('viewer',))
    applicant.email_confirmed_at = datetime.utcnow()

    req = VerificationRequest(user_id=applicant.id, module='ct')
    db_session.add(req)
    db_session.flush()
    db_session.add(VerificationRequestInstitution(request_id=req.id,
                                                  institution_id=park.id))
    db_session.commit()

    ok, error, outcome = Service.decide(req, admin, True,
                                        institution_ids=[str(park.id)])
    assert ok, error
    db_session.commit()
    db_session.expire_all()

    link = applicant.institution_links[0]
    assert link.can_view_ct is True
    assert link.can_view_pam is False, 'a photo request may not open the sounds'
    assert link.can_export_ct is False and link.can_export_pam is False, \
        'export stays an explicit decision in the user form'
    assert applicant.allowed_institution_ids('ct') == [park.id]
    assert applicant.allowed_institution_ids('pam') == []


def test_approving_the_second_module_extends_the_same_row(db_session, make_user,
                                                          two_parks):
    from app.admin.services import VerificationRequestService as Service
    from app.models import VerificationRequest, VerificationRequestInstitution
    from datetime import datetime

    park, _ = two_parks
    admin = make_user(username='root_for_grant2', roles=('admin',))
    applicant = make_user(username='newcomer2', roles=('viewer',))
    applicant.email_confirmed_at = datetime.utcnow()

    requests = []
    for module in ('ct', 'pam'):
        req = VerificationRequest(user_id=applicant.id, module=module)
        db_session.add(req)
        db_session.flush()
        db_session.add(VerificationRequestInstitution(request_id=req.id,
                                                      institution_id=park.id))
        requests.append(req)
    db_session.commit()

    for req in requests:
        ok, error, _ = Service.decide(req, admin, True,
                                      institution_ids=[str(park.id)])
        assert ok, error
        db_session.commit()

    db_session.expire_all()
    assert len(applicant.institution_links) == 1, 'one row per park, extended'
    link = applicant.institution_links[0]
    assert (link.can_view_ct, link.can_view_pam) == (True, True)


def test_a_manager_decides_only_the_module_they_hold(db_session, make_user, two_parks):
    """A manager whose park is granted for photos only may answer the photo
    request and not the sound one: opening what you do not have yourself is not
    a decision you get to make."""
    from app.admin.services import VerificationRequestService as Service
    from app.models import (UserInstitution, VerificationRequest,
                            VerificationRequestInstitution)
    from datetime import datetime

    park, _ = two_parks
    manager = make_user(username='ct_only_manager', roles=('manager',))
    manager.institution_links.append(UserInstitution(
        institution_id=park.id,
        can_view_ct=True, can_export_ct=False,
        can_view_pam=False, can_export_pam=False))

    applicant = make_user(username='asks_both', roles=('viewer',))
    applicant.email_confirmed_at = datetime.utcnow()
    requests = {}
    for module in ('ct', 'pam'):
        req = VerificationRequest(user_id=applicant.id, module=module)
        db_session.add(req)
        db_session.flush()
        db_session.add(VerificationRequestInstitution(request_id=req.id,
                                                      institution_id=park.id))
        requests[module] = req
    db_session.commit()

    assert Service.can_decide_request(requests['ct'], manager) is True
    assert Service.can_decide_request(requests['pam'], manager) is False

    ok, error, _ = Service.decide(requests['pam'], manager, True,
                                  institution_ids=[str(park.id)])
    assert ok is False and error
    db_session.expire_all()
    assert requests['pam'].status == 'pending'
    assert applicant.allowed_institution_ids('pam') == []
