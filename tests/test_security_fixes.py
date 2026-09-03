"""
Regression tests for the 2026-08 security review fixes.

Covered:
  F7  — admin/services: manager cannot assign a role above its allow-list
        (create_user / update_user), while an admin still can.
  F1  — camera_traps.utils.can_view_photo_file: media endpoints honour
        Location.visibility_level (public open, restricted gated).
  F8  — camera_traps.utils.is_allowed_upload_extension: only image extensions.
  F6  — utils.is_safe_url: rejects backslash / control-char open-redirect bypass.
  F4  — routes.main._dummy_password_hash: valid, cached (anti-enumeration timing).

Run:
    venv/Scripts/python -m pytest tests/test_security_fixes.py -v
"""
import pytest


# ── F8: upload extension allow-list ─────────────────────────────────────────
@pytest.mark.parametrize('name,ok', [
    ('IMG_0001.JPG', True),
    ('photo.jpg', True),
    ('photo.jpeg', True),
    ('evil.svg', False),
    ('evil.html', False),
    ('shell.php', False),
    ('noext', False),
    ('trick.jpg.svg', False),
    ('', False),
])
def test_is_allowed_upload_extension(name, ok):
    from app.camera_traps.utils import is_allowed_upload_extension
    assert is_allowed_upload_extension(name) is ok


def test_is_allowed_upload_extension_uses_config():
    from app.camera_traps.utils import is_allowed_upload_extension
    cfg = {'ALLOWED_EXTENSIONS': {'png'}}
    assert is_allowed_upload_extension('a.png', cfg) is True
    assert is_allowed_upload_extension('a.jpg', cfg) is False


# ── F6: open-redirect hardening ─────────────────────────────────────────────
def test_is_safe_url(app):
    from app.utils.utils import is_safe_url
    with app.test_request_context('/', base_url='https://biomon.app'):
        assert is_safe_url('/uk/dashboard') is True
        assert is_safe_url('https://biomon.app/uk/x') is True
        # bypass attempts
        assert is_safe_url('/\\evil.com') is False        # backslash → //evil.com
        assert is_safe_url('//evil.com') is False
        assert is_safe_url('https://evil.com') is False
        assert is_safe_url('/redir\r\nSet-Cookie: x') is False  # control chars
        assert is_safe_url('') is False
        assert is_safe_url(None) is False


# ── F1: media visibility enforcement ────────────────────────────────────────
def test_can_view_photo_file_visibility_matrix(
        ct_session, make_ct_location, make_ct_observation, make_ct_photo):
    from app.camera_traps.utils import can_view_photo_file
    from app.camera_traps.models import location_institutions

    pub_loc = make_ct_location(name='public', visibility_level=0)
    res_loc = make_ct_location(name='restricted', visibility_level=1)
    pub_photo = make_ct_photo(observation=make_ct_observation(location=pub_loc),
                              system_filename='pub_sys.jpg')
    res_photo = make_ct_photo(observation=make_ct_observation(location=res_loc),
                              system_filename='res_sys.jpg')
    # restricted location belongs to institution 42
    ct_session.execute(location_institutions.insert().values(
        location_id=res_loc.id, institution_id=42))
    ct_session.commit()

    # Public location → anyone, even anonymous (keeps the public gallery working)
    assert can_view_photo_file(ct_session, 'pub_sys.jpg') is True
    assert can_view_photo_file(ct_session, 'pub_sys.jpg',
                               is_authenticated=True, user_inst_ids=[1]) is True

    # Restricted location:
    assert can_view_photo_file(ct_session, 'res_sys.jpg') is False               # anon
    assert can_view_photo_file(ct_session, 'res_sys.jpg',
                               is_authenticated=True, is_admin=True) is True      # admin
    assert can_view_photo_file(ct_session, 'res_sys.jpg',
                               is_authenticated=True, user_inst_ids=[42]) is True # member
    assert can_view_photo_file(ct_session, 'res_sys.jpg',
                               is_authenticated=True, user_inst_ids=[7]) is False # other inst
    assert can_view_photo_file(ct_session, 'res_sys.jpg',
                               is_authenticated=True, user_inst_ids=[]) is False  # no inst

    # Unknown filename → fail closed
    assert can_view_photo_file(ct_session, 'does_not_exist.jpg') is False


def test_can_view_photo_file_resolves_location_via_batch(
        ct_session, make_ct_location):
    """A photo not yet grouped (observation_id NULL) resolves its location via the
    upload batch — a public batch stays viewable, a restricted one does not."""
    from app.camera_traps.utils import can_view_photo_file
    from app.camera_traps.models import UploadBatch, Photo
    from datetime import datetime

    res_loc = make_ct_location(name='restricted', visibility_level=1)
    batch = UploadBatch(id='b-1', location_id=res_loc.id, uploaded_by_id=1)
    ct_session.add(batch)
    ct_session.add(Photo(observation_id=None, upload_batch_id='b-1',
                         original_filename='x.jpg', system_filename='batch_sys.jpg',
                         captured_at=datetime(2025, 1, 1, 12, 0)))
    ct_session.commit()

    assert can_view_photo_file(ct_session, 'batch_sys.jpg') is False
    assert can_view_photo_file(ct_session, 'batch_sys.jpg',
                               is_authenticated=True, is_admin=True) is True


# ── F7: role-assignment privilege escalation ────────────────────────────────
def _seed_roles_and_manager(db_session):
    """admin role restricted to admins; viewer assignable by managers; a manager."""
    from app.extensions import db, bcrypt
    from app.models import User, Role

    role_admin = Role(name='admin', assignable_by='admin')
    role_manager = Role(name='manager', assignable_by='admin')
    role_viewer = Role(name='viewer', assignable_by='manager')
    db.session.add_all([role_admin, role_manager, role_viewer])
    db.session.flush()

    mgr = User(username='mgr', password_hash=bcrypt.generate_password_hash('x').decode())
    mgr.roles.append(role_manager)
    db.session.add(mgr)
    db.session.commit()
    return role_admin, role_manager, role_viewer, mgr


def test_manager_cannot_create_admin(db_session):
    from app.admin.services import UserService
    role_admin, _, role_viewer, mgr = _seed_roles_and_manager(db_session)

    new_user = UserService.create_user(
        creator=mgr, username='victim', password='StrongPass1',
        email=None, phone=None, first_name=None, last_name=None,
        # crafted request tries to grant admin id directly:
        selected_role_ids=[str(role_admin.id), str(role_viewer.id)],
    )
    db_session.commit()
    names = {r.name for r in new_user.roles}
    assert 'admin' not in names          # escalation blocked
    assert 'viewer' in names             # allowed role still assigned


def test_manager_cannot_promote_to_admin_via_update(db_session):
    from app.extensions import db, bcrypt
    from app.models import User
    from app.admin.services import UserService
    role_admin, _, role_viewer, mgr = _seed_roles_and_manager(db_session)

    target = User(username='target',
                  password_hash=bcrypt.generate_password_hash('x').decode())
    target.roles.append(role_viewer)
    db.session.add(target)
    db.session.commit()

    available = UserService.get_available_roles(mgr)   # excludes admin
    UserService.update_user(
        user=target, available_roles=available, username='target',
        email=None, phone=None, first_name=None, last_name=None, new_password=None,
        selected_role_ids=[str(role_admin.id), str(role_viewer.id)],
    )
    db.session.commit()
    assert 'admin' not in {r.name for r in target.roles}


def test_admin_can_still_assign_admin(db_session):
    from app.extensions import db, bcrypt
    from app.models import User
    from app.admin.services import UserService
    role_admin, _, _, _ = _seed_roles_and_manager(db_session)

    admin_user = User(username='root',
                      password_hash=bcrypt.generate_password_hash('x').decode())
    admin_user.roles.append(role_admin)
    db.session.add(admin_user)
    db.session.commit()

    new_user = UserService.create_user(
        creator=admin_user, username='new_admin', password='StrongPass1',
        email=None, phone=None, first_name=None, last_name=None,
        selected_role_ids=[str(role_admin.id)],
    )
    db.session.commit()
    assert 'admin' in {r.name for r in new_user.roles}


# ── F2: ProxyFix trusts only XFF/Proto, not client-supplied Host ────────────
def test_proxyfix_trusts_xff_not_forwarded_host(monkeypatch):
    monkeypatch.setenv('TRUSTED_PROXY_COUNT', '1')
    from app import create_app
    a = create_app('testing')

    @a.route('/__whoami')
    def _whoami():
        from flask import request, jsonify
        return jsonify(addr=request.remote_addr, host=request.host)

    c = a.test_client()
    r = c.get('/__whoami', base_url='http://real.host',
              headers={'X-Forwarded-For': '9.9.9.9', 'X-Forwarded-Host': 'evil.com'})
    data = r.get_json()
    assert data['addr'] == '9.9.9.9'          # X-Forwarded-For IS honoured
    assert 'evil.com' not in data['host']     # X-Forwarded-Host is NOT trusted


def test_proxyfix_off_by_default(monkeypatch):
    monkeypatch.delenv('TRUSTED_PROXY_COUNT', raising=False)
    from app import create_app
    a = create_app('testing')

    @a.route('/__whoami2')
    def _whoami2():
        from flask import request, jsonify
        return jsonify(addr=request.remote_addr)

    c = a.test_client()
    r = c.get('/__whoami2', headers={'X-Forwarded-For': '9.9.9.9'})
    # Without TRUSTED_PROXY_COUNT, XFF must be ignored (no spoofing).
    assert r.get_json()['addr'] != '9.9.9.9'


# ── F4: login-timing dummy hash ─────────────────────────────────────────────
def test_dummy_password_hash_is_valid_and_cached(app):
    from app.routes.main import _dummy_password_hash
    from app.extensions import bcrypt
    with app.app_context():
        h1 = _dummy_password_hash()
        h2 = _dummy_password_hash()
        assert h1 == h2                                   # cached
        assert bcrypt.check_password_hash(h1, 'not-a-real-account') is True
        assert bcrypt.check_password_hash(h1, 'wrong') is False
