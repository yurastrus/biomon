"""
Integration tests for the admin routes.

Checks:
  - Access (who can and can't reach a route)
  - CRUD operations: users, institutions, roles
  - Business rules: a manager sees only their own users, can't delete an admin, etc.
  - WTForms validation (duplicate username, too-short name, etc.)

Run:
    C:/Users/IuriiStrus/repositories/biomon/venv/Scripts/python.exe \
        -m unittest tests.test_admin -v
"""
import os
import unittest
from unittest.mock import patch, MagicMock

# ── helper to log into Flask-Login via the session ──────────────────────────
def _login(client, user_id):
    """Set the Flask-Login session for user_id without an HTTP request."""
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh']   = True


# ── base class with shared setUp ─────────────────────────────────────────────
class AdminTestBase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        """Create the app once for the whole class."""
        os.environ['DATABASE_URL'] = 'sqlite:///:memory:'
        cls._ct_patcher = patch(
            'app.camera_traps.database.create_engine',
            return_value=MagicMock()
        )
        cls._ct_patcher.start()

        from app import create_app
        cls.app = create_app('testing')

    @classmethod
    def tearDownClass(cls):
        cls._ct_patcher.stop()
        os.environ.pop('DATABASE_URL', None)

    def setUp(self):
        """Before each test - clean DB with the base data set."""
        self.ctx = self.app.app_context()
        self.ctx.push()

        from app.extensions import db
        db.create_all()
        self.db = db

        self._seed()
        self.client = self.app.test_client()

    def tearDown(self):
        from app.extensions import db
        db.session.remove()
        db.drop_all()
        self.ctx.pop()

    # ── helpers ──────────────────────────────────────────────────────────────

    def _seed(self):
        """Create a minimal set of roles, institutions and users."""
        from app.extensions import db, bcrypt
        from app.models import User, Role, Institution, UserInstitution

        # Roles
        self.role_admin   = Role(name='admin')
        self.role_manager = Role(name='manager')
        self.role_viewer  = Role(name='viewer')
        db.session.add_all([self.role_admin, self.role_manager, self.role_viewer])
        db.session.flush()

        # Institutions
        self.inst_a = Institution(name_uk='Заповідник А', name_en='Reserve A', code='res_a')
        self.inst_b = Institution(name_uk='Заповідник Б', name_en='Reserve B', code='res_b')
        db.session.add_all([self.inst_a, self.inst_b])
        db.session.flush()

        pw = bcrypt.generate_password_hash('testpass').decode('utf-8')

        # Admin
        self.admin = User(username='admin_user', password_hash=pw)
        self.admin.roles.append(self.role_admin)
        db.session.add(self.admin)

        # Manager - bound to inst_a
        self.manager = User(username='manager_user', password_hash=pw)
        self.manager.roles.append(self.role_manager)
        self.manager.institution_links.append(
            UserInstitution(institution_id=self.inst_a.id, can_export=False)
        )
        db.session.add(self.manager)

        # Regular user created by the manager
        self.regular = User(username='regular_user', password_hash=pw)
        self.regular.roles.append(self.role_viewer)
        self.regular.institution_links.append(
            UserInstitution(institution_id=self.inst_a.id, can_export=False)
        )
        db.session.add(self.regular)
        db.session.flush()

        # Set created_by_id AFTER flush (so we have the ID)
        self.regular.created_by_id = self.manager.id

        db.session.commit()

    def _post(self, url, data, user_id):
        """Login + POST to url."""
        _login(self.client, user_id)
        return self.client.post(url, data=data, follow_redirects=True)

    def _get(self, url, user_id):
        _login(self.client, user_id)
        return self.client.get(url, follow_redirects=True)


# ════════════════════════════════════════════════════════════════════════════
# ACCESS TESTS
# ════════════════════════════════════════════════════════════════════════════

class TestAdminAccess(AdminTestBase):
    """Who is allowed to reach the admin routes."""

    def test_anonymous_redirected_from_user_list(self):
        resp = self.client.get('/uk/admin/users', follow_redirects=True)
        self.assertIn(b'login', resp.data.lower())

    def test_viewer_gets_403_on_user_list(self):
        _login(self.client, self.regular.id)
        resp = self.client.get('/uk/admin/users')
        self.assertEqual(resp.status_code, 403)

    def test_manager_can_access_user_list(self):
        resp = self._get('/uk/admin/users', self.manager.id)
        self.assertEqual(resp.status_code, 200)

    def test_admin_can_access_user_list(self):
        resp = self._get('/uk/admin/users', self.admin.id)
        self.assertEqual(resp.status_code, 200)

    def test_manager_cannot_access_institution_list(self):
        _login(self.client, self.manager.id)
        resp = self.client.get('/uk/admin/institutions')
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_access_institution_list(self):
        resp = self._get('/uk/admin/institutions', self.admin.id)
        self.assertEqual(resp.status_code, 200)

    def test_manager_cannot_access_role_list(self):
        _login(self.client, self.manager.id)
        resp = self.client.get('/uk/admin/roles')
        self.assertEqual(resp.status_code, 403)

    def test_admin_can_access_role_list(self):
        resp = self._get('/uk/admin/roles', self.admin.id)
        self.assertEqual(resp.status_code, 200)


# ════════════════════════════════════════════════════════════════════════════
# USER LIST TESTS
# ════════════════════════════════════════════════════════════════════════════

class TestUserList(AdminTestBase):
    """Filtering in the user list."""

    def test_admin_sees_all_users(self):
        resp = self._get('/uk/admin/users', self.admin.id)
        self.assertIn(b'admin_user', resp.data)
        self.assertIn(b'manager_user', resp.data)
        self.assertIn(b'regular_user', resp.data)

    def test_manager_sees_only_own_users(self):
        resp = self._get('/uk/admin/users', self.manager.id)
        self.assertIn(b'regular_user', resp.data)
        self.assertNotIn(b'admin_user', resp.data)


# ════════════════════════════════════════════════════════════════════════════
# USER CREATION TESTS
# ════════════════════════════════════════════════════════════════════════════

class TestAddUser(AdminTestBase):
    """add_user: success, duplicate username, validation."""

    def _add_url(self):
        return '/uk/admin/users/add'

    def _valid_data(self, username='newuser'):
        return {
            'username': username,
            'password': 'securepass123',
            'email': 'new@example.com',
            'phone': '',
            'first_name': 'Іван',
            'last_name': 'Тест',
        }

    def test_admin_creates_user_successfully(self):
        from app.models import User
        resp = self._post(self._add_url(), self._valid_data('brand_new'), self.admin.id)
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(User.query.filter_by(username='brand_new').first())

    def test_manager_creates_user_successfully(self):
        from app.models import User
        resp = self._post(self._add_url(), self._valid_data('mgr_created'), self.manager.id)
        self.assertEqual(resp.status_code, 200)
        created = User.query.filter_by(username='mgr_created').first()
        self.assertIsNotNone(created)
        self.assertEqual(created.created_by_id, self.manager.id)

    def test_duplicate_username_rejected(self):
        from app.models import User
        resp = self._post(self._add_url(), self._valid_data('regular_user'), self.admin.id)
        # Exactly one regular_user should remain (no new one created)
        count = User.query.filter_by(username='regular_user').count()
        self.assertEqual(count, 1)
        # Response should contain a flash error or the form (not a 302 redirect)
        self.assertEqual(resp.status_code, 200)

    def test_short_username_rejected(self):
        from app.models import User
        resp = self._post(self._add_url(), {
            'username': 'ab',  # < 3 chars
            'password': 'securepass123',
        }, self.admin.id)
        self.assertIsNone(User.query.filter_by(username='ab').first())

    def test_short_password_rejected(self):
        from app.models import User
        resp = self._post(self._add_url(), {
            'username': 'validname',
            'password': '123',  # rejected: < 8 chars and no letter
        }, self.admin.id)
        self.assertIsNone(User.query.filter_by(username='validname').first())

    def test_missing_username_rejected(self):
        from app.models import User
        before = User.query.count()
        self._post(self._add_url(), {'password': 'securepass123'}, self.admin.id)
        self.assertEqual(User.query.count(), before)

    def test_missing_password_rejected(self):
        from app.models import User
        before = User.query.count()
        self._post(self._add_url(), {'username': 'someuser'}, self.admin.id)
        self.assertEqual(User.query.count(), before)

    def test_new_user_has_correct_created_by(self):
        from app.models import User
        self._post(self._add_url(), self._valid_data('tracked_user'), self.manager.id)
        u = User.query.filter_by(username='tracked_user').first()
        self.assertIsNotNone(u)
        self.assertEqual(u.created_by_id, self.manager.id)

    def test_new_user_gets_assigned_role(self):
        from app.models import User
        data = self._valid_data('with_role')
        data['roles'] = [str(self.role_viewer.id)]
        self._post(self._add_url(), data, self.admin.id)
        u = User.query.filter_by(username='with_role').first()
        self.assertIsNotNone(u)
        self.assertIn(self.role_viewer, u.roles)

    def test_new_user_gets_assigned_institution(self):
        from app.models import User
        data = self._valid_data('with_inst')
        data['institutions'] = [str(self.inst_a.id)]
        self._post(self._add_url(), data, self.admin.id)
        u = User.query.filter_by(username='with_inst').first()
        self.assertIsNotNone(u)
        self.assertIn(self.inst_a, u.institutions)


# ════════════════════════════════════════════════════════════════════════════
# USER EDIT TESTS
# ════════════════════════════════════════════════════════════════════════════

class TestEditUser(AdminTestBase):
    """edit_user: success, permission checks."""

    def _edit_url(self, user_id):
        return f'/uk/admin/users/edit/{user_id}'

    def test_admin_can_edit_any_user(self):
        from app.models import User
        resp = self._post(self._edit_url(self.regular.id), {
            'username': 'regular_user',
            'email': 'updated@example.com',
        }, self.admin.id)
        self.assertEqual(resp.status_code, 200)
        u = User.query.get(self.regular.id)
        self.assertEqual(u.email, 'updated@example.com')

    def test_manager_can_edit_user_in_own_institution(self):
        from app.models import User
        resp = self._post(self._edit_url(self.regular.id), {
            'username': 'regular_user',
            'first_name': 'Оновлено',
        }, self.manager.id)
        self.assertEqual(resp.status_code, 200)
        u = User.query.get(self.regular.id)
        self.assertEqual(u.first_name, 'Оновлено')

    def test_manager_cannot_edit_admin(self):
        from app.models import User
        original_email = self.admin.email
        self._post(self._edit_url(self.admin.id), {
            'username': 'admin_user',
            'email': 'hacked@example.com',
        }, self.manager.id)
        # Make sure the admin's email did not change
        u = User.query.get(self.admin.id)
        self.assertNotEqual(u.email, 'hacked@example.com')

    def test_manager_cannot_edit_user_from_other_institution(self):
        """A manager of inst_a cannot edit a user that's only in inst_b."""
        from app.extensions import db
        from app.models import User, UserInstitution

        # Move regular_user to inst_b (remove inst_a)
        self.regular.institution_links = [
            UserInstitution(institution_id=self.inst_b.id, can_export=False)
        ]
        db.session.commit()

        resp = self._post(self._edit_url(self.regular.id), {
            'username': 'regular_user',
            'email': 'forbidden@example.com',
        }, self.manager.id)
        u = User.query.get(self.regular.id)
        self.assertNotEqual(u.email, 'forbidden@example.com')

    def test_password_updated_when_provided(self):
        from app.extensions import bcrypt
        from app.models import User
        self._post(self._edit_url(self.regular.id), {
            'username': 'regular_user',
            'password': 'newpassword123',
        }, self.admin.id)
        u = User.query.get(self.regular.id)
        self.assertTrue(bcrypt.check_password_hash(u.password_hash, 'newpassword123'))

    def test_password_not_changed_when_empty(self):
        from app.extensions import bcrypt
        from app.models import User
        old_hash = self.regular.password_hash
        self._post(self._edit_url(self.regular.id), {
            'username': 'regular_user',
            'password': '',
        }, self.admin.id)
        u = User.query.get(self.regular.id)
        self.assertEqual(u.password_hash, old_hash)

    def test_duplicate_username_on_edit_rejected(self):
        from app.models import User
        self._post(self._edit_url(self.regular.id), {
            'username': 'admin_user',  # already taken
        }, self.admin.id)
        u = User.query.get(self.regular.id)
        # username did not change
        self.assertEqual(u.username, 'regular_user')

    def test_edit_preserves_hidden_roles(self):
        """
        A manager can't see the 'admin' role, so editing
        should not drop that role from another user.
        """
        from app.extensions import db
        from app.models import User, UserInstitution

        # Make 'regular_user' an admin and move them to inst_a
        self.regular.roles.append(self.role_admin)
        db.session.commit()

        # Manager edits - sends an empty role list (doesn't see admin)
        self._post(self._edit_url(self.regular.id), {
            'username': 'regular_user',
            'roles': [],
        }, self.manager.id)

        u = User.query.get(self.regular.id)
        role_names = {r.name for r in u.roles}
        # The admin role should remain (manager didn't see it and couldn't remove it)
        self.assertIn('admin', role_names)


# ════════════════════════════════════════════════════════════════════════════
# USER DELETION TESTS
# ════════════════════════════════════════════════════════════════════════════

class TestDeleteUser(AdminTestBase):
    """delete_user: success, permission checks."""

    def _delete_url(self, user_id):
        return f'/uk/admin/users/delete/{user_id}'

    def test_admin_can_delete_user(self):
        from app.models import User
        self._post(self._delete_url(self.regular.id), {}, self.admin.id)
        self.assertIsNone(User.query.get(self.regular.id))

    def test_manager_can_delete_own_user(self):
        from app.models import User
        self._post(self._delete_url(self.regular.id), {}, self.manager.id)
        self.assertIsNone(User.query.get(self.regular.id))

    def test_cannot_delete_self(self):
        from app.models import User
        self._post(self._delete_url(self.admin.id), {}, self.admin.id)
        self.assertIsNotNone(User.query.get(self.admin.id))

    def test_manager_cannot_delete_user_not_created_by_him(self):
        from app.extensions import db
        from app.models import User
        # Detach created_by_id from the manager
        self.regular.created_by_id = self.admin.id
        db.session.commit()

        self._post(self._delete_url(self.regular.id), {}, self.manager.id)
        self.assertIsNotNone(User.query.get(self.regular.id))

    def test_manager_cannot_delete_user_from_other_institution(self):
        from app.extensions import db
        from app.models import User, UserInstitution
        self.regular.institution_links = [
            UserInstitution(institution_id=self.inst_b.id, can_export=False)
        ]
        db.session.commit()

        self._post(self._delete_url(self.regular.id), {}, self.manager.id)
        self.assertIsNotNone(User.query.get(self.regular.id))


# ════════════════════════════════════════════════════════════════════════════
# INSTITUTION CRUD TESTS
# ════════════════════════════════════════════════════════════════════════════

class TestInstitutionCRUD(AdminTestBase):
    """Institutions: admin only."""

    def test_admin_can_create_institution(self):
        from app.models import Institution
        self._post('/uk/admin/institutions/add', {
            'name_uk': 'Нова установа',
            'name_en': 'New institution',
            'code': 'new_inst',
        }, self.admin.id)
        self.assertIsNotNone(Institution.query.filter_by(code='new_inst').first())

    def test_duplicate_code_rejected(self):
        from app.models import Institution
        self._post('/uk/admin/institutions/add', {
            'name_uk': 'Дублікат',
            'name_en': '',
            'code': 'res_a',  # already exists
        }, self.admin.id)
        count = Institution.query.filter_by(code='res_a').count()
        self.assertEqual(count, 1)

    def test_empty_name_rejected(self):
        from app.models import Institution
        before = Institution.query.count()
        self._post('/uk/admin/institutions/add', {
            'name_uk': '',
            'code': 'valid_code',
        }, self.admin.id)
        self.assertEqual(Institution.query.count(), before)

    def test_admin_can_edit_institution(self):
        from app.models import Institution
        self._post(f'/uk/admin/institutions/edit/{self.inst_a.id}', {
            'name_uk': 'Оновлена назва',
            'name_en': '',
            'code': 'res_a',
        }, self.admin.id)
        inst = Institution.query.get(self.inst_a.id)
        self.assertEqual(inst.name_uk, 'Оновлена назва')

    def test_admin_can_delete_institution(self):
        from app.models import Institution
        self._post(f'/uk/admin/institutions/delete/{self.inst_b.id}', {}, self.admin.id)
        self.assertIsNone(Institution.query.get(self.inst_b.id))

    def test_edit_own_code_not_duplicate(self):
        """An institution can be saved with its own code (not treated as a duplicate)."""
        from app.models import Institution
        self._post(f'/uk/admin/institutions/edit/{self.inst_a.id}', {
            'name_uk': 'Інша назва',
            'name_en': '',
            'code': 'res_a',  # same code - OK
        }, self.admin.id)
        inst = Institution.query.get(self.inst_a.id)
        self.assertEqual(inst.name_uk, 'Інша назва')
        self.assertEqual(inst.code, 'res_a')


# ════════════════════════════════════════════════════════════════════════════
# ROLE CRUD TESTS
# ════════════════════════════════════════════════════════════════════════════

class TestRoleCRUD(AdminTestBase):
    """Roles: admin only."""

    def test_admin_can_create_role(self):
        from app.models import Role
        self._post('/uk/admin/roles/add', {
            'name': 'volunteer',
            'assignable_by': '',
        }, self.admin.id)
        self.assertIsNotNone(Role.query.filter_by(name='volunteer').first())

    def test_duplicate_role_name_rejected(self):
        from app.models import Role
        self._post('/uk/admin/roles/add', {
            'name': 'viewer',  # already exists
            'assignable_by': '',
        }, self.admin.id)
        count = Role.query.filter_by(name='viewer').count()
        self.assertEqual(count, 1)

    def test_empty_role_name_rejected(self):
        from app.models import Role
        before = Role.query.count()
        self._post('/uk/admin/roles/add', {
            'name': '',
            'assignable_by': '',
        }, self.admin.id)
        self.assertEqual(Role.query.count(), before)

    def test_admin_can_edit_role(self):
        from app.models import Role
        self._post(f'/uk/admin/roles/edit/{self.role_viewer.id}', {
            'name': 'viewer',
            'assignable_by': 'admin',
        }, self.admin.id)
        role = Role.query.get(self.role_viewer.id)
        self.assertEqual(role.assignable_by, 'admin')

    def test_cannot_rename_system_role_admin(self):
        from app.models import Role
        self._post(f'/uk/admin/roles/edit/{self.role_admin.id}', {
            'name': 'superadmin',
            'assignable_by': '',
        }, self.admin.id)
        role = Role.query.get(self.role_admin.id)
        self.assertEqual(role.name, 'admin')

    def test_cannot_rename_system_role_manager(self):
        from app.models import Role
        self._post(f'/uk/admin/roles/edit/{self.role_manager.id}', {
            'name': 'local_admin',
            'assignable_by': '',
        }, self.admin.id)
        role = Role.query.get(self.role_manager.id)
        self.assertEqual(role.name, 'manager')

    def test_admin_can_delete_non_system_role(self):
        from app.models import Role
        self._post(f'/uk/admin/roles/delete/{self.role_viewer.id}', {}, self.admin.id)
        self.assertIsNone(Role.query.get(self.role_viewer.id))

    def test_cannot_delete_system_role_admin(self):
        from app.models import Role
        self._post(f'/uk/admin/roles/delete/{self.role_admin.id}', {}, self.admin.id)
        self.assertIsNotNone(Role.query.get(self.role_admin.id))

    def test_cannot_delete_system_role_manager(self):
        from app.models import Role
        self._post(f'/uk/admin/roles/delete/{self.role_manager.id}', {}, self.admin.id)
        self.assertIsNotNone(Role.query.get(self.role_manager.id))


# ════════════════════════════════════════════════════════════════════════════
# SERVICE LAYER TESTS (unit, no HTTP)
# ════════════════════════════════════════════════════════════════════════════

class TestUserService(AdminTestBase):
    """Unit tests for UserService (no HTTP)."""

    def _make_user(self, role_names):
        from unittest.mock import MagicMock
        from app.models import User
        user = MagicMock()
        user.roles = [MagicMock(name=n) for n in role_names]
        for role, name in zip(user.roles, role_names):
            role.name = name
        user.has_role.side_effect = lambda *args: User.has_role(user, *args)
        user.institutions = []
        return user

    def test_admin_can_edit_any_user(self):
        from app.admin.services import UserService
        allowed, _ = UserService.can_edit(self.admin, self.regular)
        self.assertTrue(allowed)

    def test_manager_cannot_edit_admin(self):
        from app.admin.services import UserService
        allowed, msg = UserService.can_edit(self.manager, self.admin)
        self.assertFalse(allowed)
        self.assertIn('адміністратора', msg)

    def test_manager_cannot_edit_user_from_other_institution(self):
        from app.extensions import db
        from app.admin.services import UserService
        from app.models import UserInstitution

        self.regular.institution_links = [
            UserInstitution(institution_id=self.inst_b.id, can_export=False)
        ]
        db.session.commit()

        allowed, msg = UserService.can_edit(self.manager, self.regular)
        self.assertFalse(allowed)

    def test_cannot_delete_self(self):
        from app.admin.services import UserService
        allowed, msg = UserService.can_delete(self.admin, self.admin)
        self.assertFalse(allowed)

    def test_manager_cannot_delete_user_not_created_by_him(self):
        from app.extensions import db
        from app.admin.services import UserService
        self.regular.created_by_id = self.admin.id
        db.session.commit()
        allowed, _ = UserService.can_delete(self.manager, self.regular)
        self.assertFalse(allowed)

    def test_get_available_roles_for_admin(self):
        from app.admin.services import UserService
        roles = UserService.get_available_roles(self.admin)
        names = {r.name for r in roles}
        self.assertIn('admin', names)
        self.assertIn('manager', names)

    def test_get_available_roles_for_manager_excludes_admin_role(self):
        from app.admin.services import UserService
        # The admin role has assignable_by == None in our schema; a manager sees
        # only roles with assignable_by IS NULL or 'manager'.
        # In our seed admin/manager have no restriction — check the general logic:
        # a manager must not get roles with assignable_by='admin'
        from app.extensions import db
        from app.models import Role
        restricted = Role(name='restricted_role', assignable_by='admin')
        db.session.add(restricted)
        db.session.commit()

        roles = UserService.get_available_roles(self.manager)
        names = {r.name for r in roles}
        self.assertNotIn('restricted_role', names)


class TestInstitutionService(AdminTestBase):
    """Unit tests for InstitutionService."""

    def test_is_code_unique_for_new_code(self):
        from app.admin.services import InstitutionService
        self.assertTrue(InstitutionService.is_code_unique('brand_new'))

    def test_is_code_unique_detects_duplicate(self):
        from app.admin.services import InstitutionService
        self.assertFalse(InstitutionService.is_code_unique('res_a'))

    def test_is_code_unique_allows_same_code_for_own_record(self):
        from app.admin.services import InstitutionService
        self.assertTrue(InstitutionService.is_code_unique('res_a', exclude_id=self.inst_a.id))


class TestRoleService(AdminTestBase):
    """Unit tests for RoleService."""

    def test_is_name_unique_for_new_name(self):
        from app.admin.services import RoleService
        self.assertTrue(RoleService.is_name_unique('brand_new_role'))

    def test_is_name_unique_detects_duplicate(self):
        from app.admin.services import RoleService
        self.assertFalse(RoleService.is_name_unique('admin'))

    def test_is_system_role_admin(self):
        from app.admin.services import RoleService
        self.assertTrue(RoleService.is_system_role(self.role_admin))

    def test_is_system_role_manager(self):
        from app.admin.services import RoleService
        self.assertTrue(RoleService.is_system_role(self.role_manager))

    def test_is_not_system_role_viewer(self):
        from app.admin.services import RoleService
        self.assertFalse(RoleService.is_system_role(self.role_viewer))


# ════════════════════════════════════════════════════════════════════════════
# FORM PRESERVATION ON VALIDATION ERROR TESTS (item 1)
# ════════════════════════════════════════════════════════════════════════════

class TestAddUserFormRerender(AdminTestBase):
    """Re-rendering /add preserves institutions and roles on a password error.

    The password is intentionally invalid (digits only, no letters) — the form
    must come back with the same username, checked institutions and roles,
    but with an empty password field.
    """

    def _add_url(self):
        return '/uk/admin/users/add'

    def _post_bad_password(self, extra=None):
        data = {
            'username': 'rerender_user',
            'password': '12345678',  # rejected: no letters
        }
        if extra:
            data.update(extra)
        _login(self.client, self.admin.id)
        return self.client.post(self._add_url(), data=data, follow_redirects=False)

    def test_rerenders_on_bad_password(self):
        resp = self._post_bad_password()
        # Stays on the same page (200, not a redirect)
        self.assertEqual(resp.status_code, 200)

    def test_username_preserved_on_bad_password(self):
        resp = self._post_bad_password()
        self.assertIn(b'rerender_user', resp.data)

    def test_institution_checkbox_preserved_on_bad_password(self):
        resp = self._post_bad_password({'institutions': [str(self.inst_a.id)]})
        # The inst_a access checkbox must be checked
        expected = f'name="institutions" value="{self.inst_a.id}"'.encode()
        self.assertIn(expected, resp.data)
        # Verify the checked attribute is present on the matching checkbox line
        import re
        pattern = (
            rb'<input type="checkbox" name="institutions" value="' +
            str(self.inst_a.id).encode() +
            rb'"[^>]*checked'
        )
        self.assertRegex(resp.data, pattern)

    def test_role_checkbox_preserved_on_bad_password(self):
        resp = self._post_bad_password({'roles': [str(self.role_viewer.id)]})
        import re
        pattern = (
            rb'<input type="checkbox" name="roles" value="' +
            str(self.role_viewer.id).encode() +
            rb'"[^>]*checked'
        )
        self.assertRegex(resp.data, pattern)

    def test_password_field_empty_on_rerender(self):
        resp = self._post_bad_password()
        # PasswordField does not render value= in HTML — the field is always empty
        self.assertNotIn(b'value="12345678"', resp.data)


# ════════════════════════════════════════════════════════════════════════════
# ECOREGION GROUPING TESTS (item 4)
# ════════════════════════════════════════════════════════════════════════════

class TestAddUserEcoregionGrouping(AdminTestBase):
    """The /add form displays institutions grouped by ecoregion."""

    def setUp(self):
        super().setUp()
        from app.extensions import db
        from app.models import Institution
        # Add institutions with ecoregions
        self.eco1_inst1 = Institution(
            name_uk='Установа 1 (Карпати)',
            name_en='Institute 1 (Carpathians)',
            code='eco1_i1',
            ecoregion_uk='Карпатський',
            ecoregion_en='Carpathian',
        )
        self.eco1_inst2 = Institution(
            name_uk='Установа 2 (Карпати)',
            name_en='Institute 2 (Carpathians)',
            code='eco1_i2',
            ecoregion_uk='Карпатський',
            ecoregion_en='Carpathian',
        )
        self.eco2_inst = Institution(
            name_uk='Установа Поліська',
            name_en='Polissia Institute',
            code='eco2_i1',
            ecoregion_uk='Поліський',
            ecoregion_en='Polissia',
        )
        db.session.add_all([self.eco1_inst1, self.eco1_inst2, self.eco2_inst])
        db.session.commit()

    def test_ecoregion_header_present_in_html(self):
        _login(self.client, self.admin.id)
        resp = self.client.get('/uk/admin/users/add')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('Карпатський'.encode('utf-8'), resp.data)
        self.assertIn('Поліський'.encode('utf-8'), resp.data)

    def test_eco_select_all_checkbox_present(self):
        _login(self.client, self.admin.id)
        resp = self.client.get('/uk/admin/users/add')
        self.assertIn(b'eco-select-all', resp.data)

    def test_data_eco_attribute_on_inst_rows(self):
        _login(self.client, self.admin.id)
        resp = self.client.get('/uk/admin/users/add')
        self.assertIn(b'data-eco="\xd0\x9a\xd0\xb0\xd1\x80\xd0\xbf\xd0\xb0\xd1\x82\xd1\x81\xd1\x8c\xd0\xba\xd0\xb8\xd0\xb9"', resp.data)

    def test_build_inst_groups_helper(self):
        """_build_inst_groups grouping logic — unit-level."""
        from app.admin.routes import _build_inst_groups
        from app.models import Institution
        insts = [self.eco1_inst1, self.eco1_inst2, self.eco2_inst]
        groups = _build_inst_groups(insts, 'uk')
        self.assertEqual(len(groups), 2)
        group_keys = [g['eco_key'] for g in groups]
        self.assertIn('Карпатський', group_keys)
        self.assertIn('Поліський', group_keys)
        # The Carpathian group has two institutions
        carpathian = next(g for g in groups if g['eco_key'] == 'Карпатський')
        self.assertEqual(len(carpathian['institutions']), 2)

    def test_build_inst_groups_en_localization(self):
        from app.admin.routes import _build_inst_groups
        insts = [self.eco1_inst1, self.eco2_inst]
        groups = _build_inst_groups(insts, 'en')
        names = {g['eco_name'] for g in groups}
        self.assertIn('Carpathian', names)
        self.assertIn('Polissia', names)

    def test_build_inst_groups_ungrouped_fallback(self):
        from app.admin.routes import _build_inst_groups
        from app.models import Institution
        no_eco = Institution(name_uk='Тест', code='no_eco_inst')
        groups = _build_inst_groups([no_eco], 'uk')
        self.assertEqual(len(groups), 1)
        self.assertIsNone(groups[0]['eco_key'])
        self.assertEqual(groups[0]['eco_name'], 'Без екорегіону')

    def test_build_inst_groups_ungrouped_fallback_en(self):
        from app.admin.routes import _build_inst_groups
        from app.models import Institution
        no_eco = Institution(name_uk='Тест', code='no_eco_inst_en')
        groups = _build_inst_groups([no_eco], 'en')
        self.assertEqual(groups[0]['eco_name'], 'No ecoregion')


if __name__ == '__main__':
    unittest.main(verbosity=2)
