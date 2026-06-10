"""Business logic for the admin module, separated from the HTTP layer.

Each service contains pure functions/methods with no knowledge of request/response.
All methods return data or raise exceptions; flush/commit is performed in routes.py.
"""

from sqlalchemy import or_
from app.extensions import db, bcrypt
from app.models import User, Role, Institution, UserInstitution

# Roles that grant export rights
EXPORT_ROLES = frozenset({'analyst', 'manager', 'admin'})
# System roles that cannot be renamed or deleted
SYSTEM_ROLES = frozenset({'admin', 'manager'})


# ===========================================================================
# User service
# ===========================================================================

class UserService:

    @staticmethod
    def get_available_institutions(requester):
        """Return institutions the requester is allowed to assign."""
        if requester.has_role('admin'):
            return Institution.query.all()
        return list(requester.institutions)

    @staticmethod
    def get_available_roles(requester):
        """Return roles the requester is allowed to assign."""
        if requester.has_role('admin'):
            return Role.query.all()
        return Role.query.filter(
            or_(Role.assignable_by == None, Role.assignable_by == 'manager')
        ).all()

    @staticmethod
    def can_edit(requester, target):
        """Check whether requester may edit target.

        Returns:
            Tuple of (True, None) on success or (False, error_message) on denial.
        """
        if requester.has_role('admin'):
            return True, None

        if target.has_role('admin'):
            return False, 'Доступ заборонено: Ви не можете редагувати адміністратора сайту.'

        my_ids = {i.id for i in requester.institutions}
        target_ids = {i.id for i in target.institutions}
        if not my_ids & target_ids:
            return False, 'Доступ заборонено: Цей користувач не належить до вашої установи.'

        return True, None

    @staticmethod
    def can_delete(requester, target):
        """Check whether requester may delete target.

        Returns:
            Tuple of (True, None) on success or (False, error_message) on denial.
        """
        if requester.id == target.id:
            return False, 'Помилка: Ви не можете видалити власного користувача!'

        if requester.has_role('admin'):
            return True, None

        if target.created_by_id != requester.id:
            return False, 'Доступ заборонено: Ви можете видаляти лише створених вами користувачів.'

        my_ids = {i.id for i in requester.institutions}
        target_ids = {i.id for i in target.institutions}
        if not my_ids & target_ids:
            return False, 'Доступ заборонено: Цей користувач не належить до вашої установи.'

        return True, None

    @staticmethod
    def _role_names_for_ids(role_ids):
        """Return a set of role names for the given list of role IDs."""
        if not role_ids:
            return set()
        int_ids = [int(x) for x in role_ids]
        return {r.name for r in Role.query.filter(Role.id.in_(int_ids)).all()}

    @staticmethod
    def create_user(creator, username, password,
                    email, phone, first_name, last_name,
                    selected_inst_ids, can_export_ids, selected_role_ids):
        """Create a new user and add it to the session (no commit).

        Returns:
            The newly created User instance.
        """
        hashed_pw = bcrypt.generate_password_hash(password).decode('utf-8')
        new_user = User(
            username=username,
            password_hash=hashed_pw,
            email=email or None,
            phone=phone or None,
            first_name=first_name or None,
            last_name=last_name or None,
            created_by_id=creator.id,
        )

        role_names = UserService._role_names_for_ids(selected_role_ids)
        will_have_export = bool(role_names & EXPORT_ROLES)

        for i_id in selected_inst_ids:
            inst = Institution.query.get(int(i_id))
            if inst:
                new_user.institution_links.append(
                    UserInstitution(
                        institution_id=inst.id,
                        can_export=will_have_export and (str(i_id) in can_export_ids),
                    )
                )

        for r_id in selected_role_ids:
            role = Role.query.get(int(r_id))
            if role:
                new_user.roles.append(role)

        db.session.add(new_user)
        return new_user

    @staticmethod
    def update_user(user, available_roles,
                    username, email, phone, first_name, last_name,
                    new_password,
                    selected_inst_ids, can_export_ids, selected_role_ids):
        """Update an existing user in place (no commit).

        Args:
            available_roles: roles visible to the requester in the form — needed
                to avoid accidentally removing hidden roles (e.g. admin).
        """
        user.username = username
        user.email = email or None
        user.phone = phone or None
        user.first_name = first_name or None
        user.last_name = last_name or None

        if new_password:
            user.password_hash = bcrypt.generate_password_hash(new_password).decode('utf-8')

        # Determine whether the user will have export rights after the update
        selected_role_names = UserService._role_names_for_ids(selected_role_ids)
        # Compare by ID, not object identity — safe across sessions
        available_role_ids = {r.id for r in available_roles}
        hidden_role_names = {r.name for r in user.roles if r.id not in available_role_ids}
        will_have_export = bool((selected_role_names | hidden_role_names) & EXPORT_ROLES)

        # Preserve existing can_export flags so a role change does not silently reset them
        existing_export_map = {link.institution_id: link.can_export for link in user.institution_links}

        user.institution_links = []
        for i_id in selected_inst_ids:
            inst = Institution.query.get(int(i_id))
            if inst:
                if will_have_export:
                    new_can_export = (str(i_id) in can_export_ids)
                else:
                    new_can_export = existing_export_map.get(inst.id, False)
                user.institution_links.append(
                    UserInstitution(institution_id=inst.id, can_export=new_can_export)
                )

        # Retain roles not visible in the form so managers can't accidentally remove hidden roles.
        # Compare by ID — correct regardless of SQLAlchemy's identity map.
        roles_to_keep = [r for r in user.roles if r.id not in available_role_ids]
        user.roles = roles_to_keep
        for r_id in selected_role_ids:
            role = Role.query.get(int(r_id))
            if role:
                user.roles.append(role)

    @staticmethod
    def delete_user(user):
        """Delete the user from the session (no commit)."""
        db.session.delete(user)


# ===========================================================================
# Institution service
# ===========================================================================

class InstitutionService:

    @staticmethod
    def is_code_unique(code, exclude_id=None):
        """Return True if the institution code is unique (exclude_id for edit mode)."""
        existing = Institution.query.filter_by(code=code).first()
        if existing is None:
            return True
        if exclude_id and existing.id == exclude_id:
            return True
        return False

    @staticmethod
    def create(name_uk, name_en, code):
        """Create an institution and add it to the session (no commit)."""
        inst = Institution(name_uk=name_uk, name_en=name_en or None, code=code)
        db.session.add(inst)
        return inst

    @staticmethod
    def update(inst, name_uk, name_en, code):
        """Update institution fields in place (no commit)."""
        inst.name_uk = name_uk
        inst.name_en = name_en or None
        inst.code = code

    @staticmethod
    def delete(inst):
        """Delete the institution from the session (no commit)."""
        db.session.delete(inst)


# ===========================================================================
# Role service
# ===========================================================================

class RoleService:

    @staticmethod
    def is_name_unique(name, exclude_id=None):
        """Return True if the role name is unique."""
        existing = Role.query.filter_by(name=name).first()
        if existing is None:
            return True
        if exclude_id and existing.id == exclude_id:
            return True
        return False

    @staticmethod
    def is_system_role(role):
        """Return True if the role is a system role (admin/manager) that cannot be renamed or deleted."""
        return role.name in SYSTEM_ROLES

    @staticmethod
    def create(name, assignable_by):
        """Create a role and add it to the session (no commit)."""
        role = Role(name=name, assignable_by=assignable_by or None)
        db.session.add(role)
        return role

    @staticmethod
    def update(role, name, assignable_by):
        """Update role fields in place (no commit)."""
        role.name = name
        role.assignable_by = assignable_by or None

    @staticmethod
    def delete(role):
        """Delete the role from the session (no commit)."""
        db.session.delete(role)
