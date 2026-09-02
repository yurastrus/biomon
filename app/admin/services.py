# SPDX-License-Identifier: AGPL-3.0-only
"""Business logic for the admin module, separated from the HTTP layer.

Each service contains pure functions/methods with no knowledge of request/response.
All methods return data or raise exceptions; flush/commit is performed in routes.py.
"""

from datetime import datetime

from sqlalchemy import or_
from app.extensions import db, bcrypt
from app.models import (User, Role, Institution, UserInstitution,
                        VerificationRequest, VerificationRequestInstitution)

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

    #: The four per-module grants, in the order the form shows them.
    ACCESS_FIELDS = ('view_ct', 'export_ct', 'view_pam', 'export_pam')

    @staticmethod
    def module_access_from_legacy(selected_inst_ids, can_export_ids):
        """Translate the old two-column form input into the four-flag shape.

        Kept so callers that still speak "institutions + can_export" (scripts,
        older tests) go through exactly the same writer. Camera traps mirrors what
        the row used to mean; PAM is left off, because the old input cannot say
        anything about it — a PAM grant has to be ticked deliberately.
        """
        inst_ids = {str(i) for i in selected_inst_ids}
        export_ids = {str(i) for i in can_export_ids} & inst_ids
        return {'view_ct': inst_ids, 'export_ct': export_ids,
                'view_pam': set(), 'export_pam': set()}

    @staticmethod
    def _write_institution_links(user, module_access, will_have_export):
        """Replace ``user.institution_links`` from a four-flag selection.

        Rules that live here rather than in the template, because a crafted POST
        must not be able to bypass them:

        * export implies access in the same module — an export tick without the
          matching access tick is dropped;
        * a row is written only when it grants something. A park with all four
          boxes clear leaves no row at all, which is the "no access" state the
          model already used (see WORKLOG 2026-09-02 on the sparse table);
        * ``can_export`` (legacy, read by the live export paths) is kept in sync
          as "may export in either module";
        * without an export-capable role the export flags are not taken from the
          form (its boxes are disabled) — the stored ones are preserved, so a
          temporary role change does not silently wipe them.
        """
        picked = {name: {int(i) for i in module_access.get(name, ()) if str(i).isdigit()}
                  for name in UserService.ACCESS_FIELDS}
        previous = {link.institution_id: link for link in user.institution_links}

        wanted_ids = sorted(set().union(*picked.values())) if picked else []
        links = []
        for inst_id in wanted_ids:
            inst = Institution.query.get(inst_id)
            if inst is None:
                continue

            view_ct = inst_id in picked['view_ct']
            view_pam = inst_id in picked['view_pam']
            export_ct = view_ct and inst_id in picked['export_ct']
            export_pam = view_pam and inst_id in picked['export_pam']

            if not will_have_export:
                old = previous.get(inst_id)
                stored = old.module_flags(user) if old is not None else None
                export_ct = bool(stored and stored['export_ct'] and view_ct)
                export_pam = bool(stored and stored['export_pam'] and view_pam)

            if not (view_ct or view_pam):
                continue

            links.append(UserInstitution(
                institution_id=inst_id,
                can_export=bool(export_ct or export_pam),
                can_view_ct=view_ct,
                can_export_ct=export_ct,
                can_view_pam=view_pam,
                can_export_pam=export_pam,
            ))

        user.institution_links = links
        return links

    @staticmethod
    def create_user(creator, username, password,
                    email, phone, first_name, last_name,
                    selected_inst_ids=(), can_export_ids=(), selected_role_ids=(),
                    module_access=None):
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

        if module_access is None:
            module_access = UserService.module_access_from_legacy(
                selected_inst_ids, can_export_ids)
        UserService._write_institution_links(new_user, module_access, will_have_export)

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
                    selected_inst_ids=(), can_export_ids=(), selected_role_ids=(),
                    module_access=None):
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

        if module_access is None:
            module_access = UserService.module_access_from_legacy(
                selected_inst_ids, can_export_ids)
        # Writes the four per-module flags and keeps the legacy can_export column
        # in sync; preserves stored export flags when the role cannot export.
        UserService._write_institution_links(user, module_access, will_have_export)

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

    Two things can be granted from a request:

    * the **module role** (``ct_verifier`` / ``pam_verifier``) — the right to
      verify at all, which on its own means public locations only;
    * an **institution** the applicant named at registration — access to that
      institution's data, granted one institution at a time.

    Who decides what: an admin decides everything; a manager decides only the
    institutions they themselves have access to, and sees only requests naming
    at least one of them. A request keeps hanging in the queue while any named
    institution is still undecided, which is what lets two parks approve the
    same applicant independently.
    """

    @staticmethod
    def can_decide(requester):
        """Return True if ``requester`` may approve/reject requests at all.

        Managers qualify, but :meth:`scope_rows` narrows what they may touch to
        their own institutions.
        """
        return requester.has_role('admin') or requester.has_role('manager')

    @staticmethod
    def _decider_institution_ids(decider):
        """Institution ids a non-admin decider owns. ``None`` means "all"."""
        if decider.has_role('admin'):
            return None
        return {inst.id for inst in decider.institutions}

    @staticmethod
    def scope_rows(request_obj, decider, status=None):
        """Institution rows of ``request_obj`` that ``decider`` may act on.

        Admins get every row; a manager gets the rows for their institutions.
        """
        allowed = VerificationRequestService._decider_institution_ids(decider)
        rows = request_obj.institution_rows(status=status)
        if allowed is None:
            return rows
        return [r for r in rows if r.institution_id in allowed]

    @staticmethod
    def can_decide_request(request_obj, decider):
        """Return True if ``decider`` may act on this particular request.

        An admin always may. A manager may only when the request still has an
        undecided institution of theirs — a request naming no institution (or
        none of theirs) is not theirs to judge, since approving it would grant a
        site-wide verifier role they do not own.
        """
        if decider.has_role('admin'):
            return True
        if not decider.has_role('manager'):
            return False
        return bool(VerificationRequestService.scope_rows(
            request_obj, decider, status=VerificationRequest.STATUS_PENDING))

    @staticmethod
    def _visible_query(status=None, only_confirmed=True, viewer=None):
        query = VerificationRequest.query.join(
            User, VerificationRequest.user_id == User.id)
        if only_confirmed:
            query = query.filter(User.email_confirmed_at.isnot(None))

        allowed = (VerificationRequestService._decider_institution_ids(viewer)
                   if viewer is not None else None)
        if allowed is None:
            if status in VerificationRequest.STATUSES:
                query = query.filter(VerificationRequest.status == status)
        else:
            # A manager's queue is limited to requests naming an institution of
            # theirs. Empty set => nothing (a manager without institutions has
            # no applicants to judge).
            rows = db.session.query(VerificationRequestInstitution.request_id).filter(
                VerificationRequestInstitution.institution_id.in_(allowed or [-1]))
            if status in VerificationRequest.STATUSES:
                # The status filter applies to *their* row, not to the request:
                # once this manager has answered, the applicant is off their
                # "pending" list even though the request still hangs for the
                # other institutions.
                rows = rows.filter(VerificationRequestInstitution.status == status)
            query = query.filter(VerificationRequest.id.in_(rows))
        return query

    @staticmethod
    def list_requests(status=None, only_confirmed=True, viewer=None):
        """Requests newest-first, optionally filtered by status and by viewer.

        ``only_confirmed`` hides applicants who have not clicked the email link
        yet: their request is not actionable and would otherwise let anyone fill
        the admin's queue with unverified addresses.

        ``viewer`` restricts the list the way :meth:`scope_rows` restricts the
        decision — pass the current user; ``None`` means no restriction.
        """
        return (VerificationRequestService
                ._visible_query(status=status, only_confirmed=only_confirmed,
                                viewer=viewer)
                .order_by(VerificationRequest.requested_at.desc()).all())

    @staticmethod
    def pending_count(viewer=None):
        return VerificationRequestService._visible_query(
            status=VerificationRequest.STATUS_PENDING, viewer=viewer).count()

    @staticmethod
    def resolve_pending_for_roles(user, decider, role_names):
        """Close pending requests that a manual role grant has just satisfied.

        When an admin ticks ``ct_verifier`` in the user form for someone who had
        applied through the queue, the request row stayed ``pending`` forever:
        the queue kept showing work that was already done, and approving it there
        later would send the applicant a second letter. Mark those rows approved
        against the same decider instead (no commit).

        Returns:
            list[str]: modules whose pending request was closed.
        """
        modules = [m for m, role in VerificationRequest.ROLE_BY_MODULE.items()
                   if role in role_names]
        if not modules:
            return []

        now = datetime.utcnow()
        user_inst_ids = {inst.id for inst in user.institutions}
        closed = []
        for req in VerificationRequest.query.filter(
                VerificationRequest.user_id == user.id,
                VerificationRequest.module.in_(modules),
                VerificationRequest.status == VerificationRequest.STATUS_PENDING).all():
            # Institutions the same save has just granted are answered too —
            # otherwise the queue would keep asking for access the person has.
            for row in req.institution_rows(VerificationRequest.STATUS_PENDING):
                if row.institution_id in user_inst_ids:
                    row.status = VerificationRequest.STATUS_APPROVED
                    row.decided_at = now
                    row.decided_by_id = decider.id
            req.status = VerificationRequest.STATUS_APPROVED
            req.decided_at = now
            req.decided_by_id = decider.id
            closed.append(req.module)
        return closed

    @staticmethod
    def _grant_institution(user, institution_id):
        """Attach an institution to the user unless it is already attached."""
        if any(link.institution_id == institution_id
               for link in user.institution_links):
            return False
        user.institution_links.append(
            UserInstitution(institution_id=institution_id, can_export=False))
        return True

    @staticmethod
    def decide(request_obj, decider, approve, institution_ids=None, note=None):
        """Approve or reject one request, institution by institution (no commit).

        ``institution_ids`` are the institutions to grant — the decider's own
        selection, so unticking one in the form *removes* it from the request
        (recorded as ``rejected``). ``None`` means "everything in my scope".
        Rows outside the decider's scope are never touched, which is what keeps
        the request alive for the other institutions' managers.

        A request that names no institution at all behaves as it always did: one
        yes/no granting the module role and no territory.

        Returns:
            Tuple of ``(ok, error_message, outcome)``. ``outcome`` is a dict with
            ``granted`` / ``removed`` (Institution lists), ``closed`` (bool: no
            institution is left undecided) and ``role_granted`` (bool).
        """
        if request_obj.status != VerificationRequest.STATUS_PENDING:
            return False, 'Цей запит уже опрацьовано.', None
        if not request_obj.user.is_email_confirmed:
            return False, 'Користувач ще не підтвердив email-адресу.', None
        if not VerificationRequestService.can_decide_request(request_obj, decider):
            return False, 'Ви не можете вирішувати цей запит.', None

        now = datetime.utcnow()
        user = request_obj.user
        all_rows = request_obj.institution_rows()
        scoped = VerificationRequestService.scope_rows(
            request_obj, decider, status=VerificationRequest.STATUS_PENDING)

        selected = None if institution_ids is None else {int(i) for i in institution_ids}
        granted, removed = [], []
        for row in scoped:
            keep = approve and (selected is None or row.institution_id in selected)
            row.status = (VerificationRequest.STATUS_APPROVED if keep
                          else VerificationRequest.STATUS_REJECTED)
            row.decided_at = now
            row.decided_by_id = decider.id
            (granted if keep else removed).append(row.institution)

        for inst in granted:
            VerificationRequestService._grant_institution(user, inst.id)

        # The role is what makes verification possible at all, so it is granted
        # as soon as anything is approved — a person cleared for one park must
        # not wait for the others to answer.
        role_granted = False
        if approve and (granted or not all_rows):
            from app.utils.registration import get_or_create_role
            role = get_or_create_role(request_obj.role_name)
            if role not in user.roles:
                user.roles.append(role)
                role_granted = True

        if note:
            request_obj.note = note.strip() or None

        still_pending = request_obj.institution_rows(VerificationRequest.STATUS_PENDING)
        closed = not still_pending
        if closed:
            any_approved = bool(request_obj.institution_rows(
                VerificationRequest.STATUS_APPROVED))
            if all_rows:
                request_obj.status = (VerificationRequest.STATUS_APPROVED if any_approved
                                      else VerificationRequest.STATUS_REJECTED)
            else:
                request_obj.status = (VerificationRequest.STATUS_APPROVED if approve
                                      else VerificationRequest.STATUS_REJECTED)
            request_obj.decided_at = now
            request_obj.decided_by_id = decider.id

        return True, None, {'granted': granted, 'removed': removed,
                            'closed': closed, 'role_granted': role_granted}
