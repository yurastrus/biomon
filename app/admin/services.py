# SPDX-License-Identifier: AGPL-3.0-only
"""Business logic for the admin module, separated from the HTTP layer.

Each service contains pure functions/methods with no knowledge of request/response.
All methods return data or raise exceptions; flush/commit is performed in routes.py.
"""

from datetime import datetime

from sqlalchemy import or_
from app.extensions import db, bcrypt
from app.models import User, Role, Institution, UserInstitution, VerificationRequest

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

        # SEC: never grant a role the creator isn't allowed to assign, even if a
        # crafted request supplies its id directly (the form's allow-list is not a
        # security boundary — the route reads request.form.getlist('roles') raw).
        allowed_role_ids = {str(r.id) for r in UserService.get_available_roles(creator)}
        selected_role_ids = [rid for rid in selected_role_ids if str(rid) in allowed_role_ids]

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
        # SEC: only (re)assign roles the requester is allowed to grant. The
        # roles_to_keep logic below preserves hidden roles the user already has;
        # this filter blocks ADDING a hidden role (e.g. a manager crafting a POST
        # with roles=<admin id>). Non-numeric/garbage ids are dropped too.
        _allowed_role_ids = {r.id for r in available_roles}
        selected_role_ids = [rid for rid in selected_role_ids
                             if str(rid).isdigit() and int(rid) in _allowed_role_ids]

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

    #: value of the ecoregion <select> that means "I will type a new one"
    ECOREGION_NEW = '__new__'

    @staticmethod
    def get_ecoregions():
        """Distinct ecoregions currently in use, as [{'uk': ..., 'en': ...}].

        The list IS the vocabulary: there is no separate ecoregions table, so the
        dropdown offers what other institutions already use and a new value is
        added simply by being typed. Keeping the uk/en pair together is the point
        — picking "Полісся" must also store "Polissia", which two independent free
        text fields would let drift apart.
        """
        rows = (db.session.query(Institution.ecoregion_uk, Institution.ecoregion_en)
                .filter(Institution.ecoregion_uk.isnot(None),
                        Institution.ecoregion_uk != '')
                .distinct().all())
        merged = {}
        for uk, en in rows:
            # if the same uk name appears with and without an en name, keep the en one
            if uk not in merged or (en and not merged[uk]):
                merged[uk] = en
        return [{'uk': uk, 'en': merged[uk]} for uk in sorted(merged)]

    @staticmethod
    def resolve_ecoregion(choice, new_uk, new_en):
        """Turn the form's (choice, new_uk, new_en) into the (uk, en) to store.

        Args:
            choice: selected <option> value — '' (none), an existing uk name, or
                :data:`ECOREGION_NEW`.
            new_uk / new_en: the free-text fields, used only for ECOREGION_NEW.

        Returns:
            tuple[str | None, str | None]

        Raises:
            ValueError: with a user-facing message, when a new region was chosen
                without a Ukrainian name, or the selected value is not a known
                ecoregion (a crafted POST).
        """
        choice = (choice or '').strip()
        if not choice:
            return None, None

        if choice == InstitutionService.ECOREGION_NEW:
            uk = (new_uk or '').strip()
            en = (new_en or '').strip()
            if not uk:
                raise ValueError('Вкажіть назву нового природного регіону українською.')
            return uk, (en or None)

        for eco in InstitutionService.get_ecoregions():
            if eco['uk'] == choice:
                return eco['uk'], eco['en']
        raise ValueError('Невідомий природний регіон.')

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
    def create(name_uk, name_en, code, ecoregion_uk=None, ecoregion_en=None):
        """Create an institution and add it to the session (no commit)."""
        inst = Institution(name_uk=name_uk, name_en=name_en or None, code=code,
                           ecoregion_uk=ecoregion_uk or None,
                           ecoregion_en=ecoregion_en or None)
        db.session.add(inst)
        return inst

    @staticmethod
    def update(inst, name_uk, name_en, code, ecoregion_uk=None, ecoregion_en=None):
        """Update institution fields in place (no commit)."""
        inst.name_uk = name_uk
        inst.name_en = name_en or None
        inst.code = code
        inst.ecoregion_uk = ecoregion_uk or None
        inst.ecoregion_en = ecoregion_en or None

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


# ===========================================================================
# Verification-request service
# ===========================================================================

class VerificationRequestService:
    """Decisions on self-registered users' requests for verification rights.

    Approval grants exactly one role and no institutions, so the new verifier
    sees public locations only. Territory access remains a separate, manual
    grant — see the module docstring of app/utils/registration.py.
    """

    @staticmethod
    def can_decide(requester):
        """Return True if ``requester`` may approve/reject requests.

        Admin-only for now. Managers are the intended next step: they would be
        limited to requests they can see, which requires deciding what "their"
        applicants means (no institution is attached at this point) — so the
        rule stays deliberately narrow instead of guessing.
        """
        return requester.has_role('admin')

    @staticmethod
    def list_requests(status=None, only_confirmed=True):
        """Requests newest-first, optionally filtered by status.

        ``only_confirmed`` hides applicants who have not clicked the email link
        yet: their request is not actionable and would otherwise let anyone fill
        the admin's queue with unverified addresses.
        """
        query = VerificationRequest.query.join(
            User, VerificationRequest.user_id == User.id)
        if status in VerificationRequest.STATUSES:
            query = query.filter(VerificationRequest.status == status)
        if only_confirmed:
            query = query.filter(User.email_confirmed_at.isnot(None))
        return query.order_by(VerificationRequest.requested_at.desc()).all()

    @staticmethod
    def pending_count():
        return VerificationRequest.query.join(
            User, VerificationRequest.user_id == User.id
        ).filter(
            VerificationRequest.status == VerificationRequest.STATUS_PENDING,
            User.email_confirmed_at.isnot(None),
        ).count()

    @staticmethod
    def decide(request_obj, decider, approve, note=None):
        """Approve or reject one request (no commit).

        Returns:
            Tuple of (True, None) on success or (False, error_message).
        """
        if request_obj.status != VerificationRequest.STATUS_PENDING:
            return False, 'Цей запит уже опрацьовано.'
        if not request_obj.user.is_email_confirmed:
            return False, 'Користувач ще не підтвердив email-адресу.'

        if approve:
            from app.utils.registration import get_or_create_role
            role = get_or_create_role(request_obj.role_name)
            if role not in request_obj.user.roles:
                request_obj.user.roles.append(role)
            request_obj.status = VerificationRequest.STATUS_APPROVED
        else:
            request_obj.status = VerificationRequest.STATUS_REJECTED

        request_obj.decided_at = datetime.utcnow()
        request_obj.decided_by_id = decider.id
        request_obj.note = (note or '').strip() or None
        return True, None
