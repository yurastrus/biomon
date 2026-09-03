# SPDX-License-Identifier: AGPL-3.0-only
from flask_login import UserMixin
from datetime import datetime
from app.extensions import db
from sqlalchemy.dialects.postgresql import ARRAY, TEXT
from sqlalchemy import CheckConstraint

user_roles = db.Table('user_roles',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('role_id', db.Integer, db.ForeignKey('role.id'), primary_key=True)
)

class UserInstitution(db.Model):
    """One person's access to one institution's data, per module.

    Four independent grants: see and export camera-trap data, see and export PAM
    data. A row exists only while it grants something; no row at all means no
    access beyond public locations (the table is deliberately sparse).

    Until 2026-09-03 a single ``can_export`` column carried "may download" for
    both modules and the row's mere existence carried "may see" for both. That
    column is gone; the four flags below are the only truth. See WORKLOG
    2026-09-02 (phases 1-3) and 2026-09-03 (phase 4) for the migration path.
    """
    __tablename__ = 'user_institutions'
    user_id        = db.Column(db.Integer, db.ForeignKey('user.id'), primary_key=True)
    institution_id = db.Column(db.Integer, db.ForeignKey('institutions.id'), primary_key=True)

    can_view_ct    = db.Column(db.Boolean, nullable=False, default=False,
                               server_default=db.false())
    can_export_ct  = db.Column(db.Boolean, nullable=False, default=False,
                               server_default=db.false())
    can_view_pam   = db.Column(db.Boolean, nullable=False, default=False,
                               server_default=db.false())
    can_export_pam = db.Column(db.Boolean, nullable=False, default=False,
                               server_default=db.false())

    institution = db.relationship('Institution')

    def module_flags(self, user=None):
        """Return ``{'view_ct', 'export_ct', 'view_pam', 'export_pam'}`` as bools.

        The columns answer directly now. ``user`` is accepted and ignored: it
        used to resolve rows written before the split, and keeping the parameter
        means the callers in shared-ct / shared-pam need no coordinated change.

        Returns:
            dict[str, bool]
        """
        return {
            'view_ct': bool(self.can_view_ct),
            'export_ct': bool(self.can_export_ct),
            'view_pam': bool(self.can_view_pam),
            'export_pam': bool(self.can_export_pam),
        }

class Institution(db.Model):
    __tablename__ = 'institutions'
    id = db.Column(db.Integer, primary_key=True)
    name_uk = db.Column(db.String(255), nullable=False)
    name_en = db.Column(db.String(255))
    code = db.Column(db.String(50), unique=True)
    ecoregion_uk = db.Column(db.String(100))
    ecoregion_en = db.Column(db.String(100))

    def label(self, lang='uk'):
        """Name in the requested language, falling back to Ukrainian.

        Takes the language as an argument instead of reading ``g.lang_code`` so
        the model stays usable outside a request (emails, CLI, notifications).
        """
        if lang == 'en' and self.name_en:
            return self.name_en
        return self.name_uk

    def __repr__(self):
        return f'<Institution {self.name_uk}>'

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True, nullable=False)
    password_hash = db.Column(db.String(60), nullable=False)
    email = db.Column(db.String(120), index=True)
    phone = db.Column(db.String(20))
    roles = db.relationship('Role', secondary=user_roles, backref=db.backref('users', lazy='dynamic'))

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    creator = db.relationship('User', remote_side=[id], backref='created_users')

    first_name = db.Column(db.String(50), nullable=True)
    last_name = db.Column(db.String(50), nullable=True)

    # ── Self-service registration (#registration) ─────────────────────────────
    # is_active overrides UserMixin.is_active on purpose: Flask-Login refuses to
    # log in a user for which it is False, and load_user() below drops the
    # session of an account deactivated mid-session. A self-registered account
    # starts inactive and is activated by clicking the emailed confirmation link.
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    email_confirmed_at = db.Column(db.DateTime, nullable=True)
    self_registered = db.Column(db.Boolean, default=False, nullable=False)
    # Language the person registered in — emails are sent in it (they are not
    # generated inside a request, so there is no g.lang_code to fall back on).
    locale = db.Column(db.String(5), nullable=True)

    # ── Email notification opt-outs ───────────────────────────────────────────
    # Default True: an account is subscribed until the person says otherwise, so
    # adding a notification type never silently misses existing users. The set of
    # these columns is described by app/utils/notifications.NOTIFICATION_PREFS —
    # add the PAM one there together with its column.
    notify_ct_pending = db.Column(db.Boolean, default=True, nullable=False,
                                  server_default=db.true())

    # viewonly=True: read-only join; mutations go through institution_links
    institutions = db.relationship('Institution', secondary=lambda: UserInstitution.__table__, viewonly=True)
    institution_links = db.relationship('UserInstitution', cascade='all, delete-orphan')

    #: Module codes used by the per-module access helpers below.
    MODULE_CT = 'ct'
    MODULE_PAM = 'pam'

    def module_institutions(self, module):
        """Institutions this person may work with **in one module**.

        The single source of truth for "where may they see data" since access
        was split per module (see WORKLOG 2026-09-02). Reads the four flags on
        each link through :meth:`UserInstitution.module_flags`, so a row written
        before those columns existed still resolves to what it used to mean.

        An admin is unrestricted elsewhere in the code by an explicit
        ``has_role('admin')`` check, so this returns their stored rows as-is and
        does not silently mean "everything".

        Args:
            module: ``'ct'`` or ``'pam'``.
        """
        key = 'view_pam' if module == self.MODULE_PAM else 'view_ct'
        return [link.institution for link in self.institution_links
                if link.institution is not None and link.module_flags(self)[key]]

    def allowed_institution_ids(self, module='ct'):
        """Ids of :meth:`module_institutions` — the shape most filters want."""
        key = 'view_pam' if module == self.MODULE_PAM else 'view_ct'
        return [link.institution_id for link in self.institution_links
                if link.module_flags(self)[key]]

    def export_institutions_for(self, module):
        """Institutions this person may download data from in one module."""
        key = 'export_pam' if module == self.MODULE_PAM else 'export_ct'
        return [link.institution for link in self.institution_links
                if link.institution is not None and link.module_flags(self)[key]]

    def export_institution_ids(self, module='ct'):
        key = 'export_pam' if module == self.MODULE_PAM else 'export_ct'
        return [link.institution_id for link in self.institution_links
                if link.module_flags(self)[key]]

    @property
    def export_institutions(self):
        """Institutions the user may export from in **either** module.

        The module-blind answer, kept because shared-ct / shared-pam ask hosts
        for it when the host has no per-module methods (``/var/www/myproject``).
        Inside biomon prefer :meth:`export_institutions_for`.
        """
        return [link.institution for link in self.institution_links
                if link.can_export_ct or link.can_export_pam]

    @property
    def full_name(self):
        """Return the user's full name, falling back to username if names are not set."""
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        elif self.first_name:
            return self.first_name
        elif self.last_name:
            return self.last_name
        else:
            return self.username
    
    def has_role(self, *required_roles):
        """Return True if the user holds at least one of the given roles.

        Respects the role hierarchy: higher roles implicitly include lower ones.
        Admin always passes regardless of the required roles list.
        """
        if any(role.name == 'admin' for role in self.roles):
            return True

        ROLE_HIERARCHY = {
            'manager':['pam_verifier', 'ct_verifier', 'analyst', 'viewer'],
            'pam_verifier':  ['viewer'],
            'ct_verifier': ['viewer'],
            'analyst': ['ct_verifier', 'viewer'],
        }

        user_base_roles = {role.name for role in self.roles}
        expanded_user_roles = set(user_base_roles)
        for role in user_base_roles:
            if role in ROLE_HIERARCHY:
                expanded_user_roles.update(ROLE_HIERARCHY[role])

        for req_role in required_roles:
            if req_role in expanded_user_roles:
                return True

        return False
    
    @property
    def is_email_confirmed(self):
        return self.email_confirmed_at is not None

    def verification_request(self, module):
        """Return this user's request for the given module, or None."""
        for req in self.verification_requests:
            if req.module == module:
                return req
        return None

    def is_local_admin(self):
        """Return True if the user is an institution admin (manager)."""
        return self.has_role('manager')
    
    def __repr__(self):
        return f"User('{self.username}')"
    
    def get_ct_profile(self):
        """Find or create this user's camera-traps module profile.

        Acts as a bridge between the main database and ct_db.
        """
        from app.camera_traps.database import get_ct_session, close_ct_session
        from app.camera_traps.models import UserProfile

        ct_session = get_ct_session()
        try:
            profile = ct_session.query(UserProfile).filter_by(user_id=self.id).first()

            if not profile:
                profile = UserProfile(
                    user_id=self.id,
                    camera_trap_role='viewer',  # default role
                    identifications_count=0,
                    accuracy_score=0.0
                )
                ct_session.add(profile)
                ct_session.commit()

            return profile
        finally:
            close_ct_session()

class Role(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(20), unique=True, nullable=False)
    assignable_by = db.Column(db.String(20), nullable=True)
    def __repr__(self):
        return f"Role('{self.name}')"

class VerificationRequest(db.Model):
    """A self-registered user's request for verification rights in one module.

    One row per (user, module) so that "photos + sounds" is two independently
    decidable requests, and so the decision keeps an audit trail (who, when,
    why) instead of a boolean on the user.

    Approving a request grants the matching role and nothing else: no
    institutions are attached, which — by the existing access model in
    camera_traps/pam — means the person sees public locations only
    (``visibility_level == 0``). Wider territory access stays a manual,
    per-institution grant in the admin panel.
    """
    __tablename__ = 'verification_requests'

    MODULE_CT = 'ct'
    MODULE_PAM = 'pam'
    MODULES = (MODULE_CT, MODULE_PAM)
    #: role granted on approval, per module
    ROLE_BY_MODULE = {MODULE_CT: 'ct_verifier', MODULE_PAM: 'pam_verifier'}

    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete='CASCADE'),
                        nullable=False, index=True)
    module = db.Column(db.String(10), nullable=False)
    status = db.Column(db.String(10), nullable=False, default=STATUS_PENDING, index=True)
    requested_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    decided_at = db.Column(db.DateTime, nullable=True)
    decided_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    #: the decider's comment, shown to the applicant in the decision email
    note = db.Column(db.Text, nullable=True)
    #: what the applicant wrote about themselves at registration (motivation and
    #: experience). Free text, the same on every module request of one signup —
    #: it is the person speaking, not a per-module answer.
    applicant_note = db.Column(db.Text, nullable=True)

    user = db.relationship('User', foreign_keys=[user_id],
                           backref=db.backref('verification_requests',
                                              cascade='all, delete-orphan'))
    decided_by = db.relationship('User', foreign_keys=[decided_by_id])

    __table_args__ = (
        db.UniqueConstraint('user_id', 'module', name='uq_verification_request_user_module'),
        CheckConstraint("module IN ('ct', 'pam')", name='ck_verification_request_module'),
        CheckConstraint("status IN ('pending', 'approved', 'rejected')",
                        name='ck_verification_request_status'),
    )

    @property
    def role_name(self):
        return self.ROLE_BY_MODULE[self.module]

    # ── requested institutions ────────────────────────────────────────────
    # A request may name institutions the applicant wants to work with. Each is
    # decided on its own (by that institution's manager, or by an admin), so the
    # request as a whole stays pending while any institution still is — see
    # :class:`VerificationRequestInstitution`.

    def institution_rows(self, status=None):
        rows = list(self.institution_requests)
        if status is not None:
            rows = [r for r in rows if r.status == status]
        return sorted(rows, key=lambda r: (r.institution.name_uk if r.institution else ''))

    @property
    def pending_institution_rows(self):
        return self.institution_rows(self.STATUS_PENDING)

    @property
    def approved_institution_rows(self):
        return self.institution_rows(self.STATUS_APPROVED)

    @property
    def requested_institutions(self):
        return [r.institution for r in self.institution_rows() if r.institution]

    def __repr__(self):
        return f'<VerificationRequest user={self.user_id} {self.module} [{self.status}]>'


class VerificationRequestInstitution(db.Model):
    """One institution named in a verification request, decided on its own.

    Why a row per institution rather than a list on the request: an applicant who
    asks to work with two national parks needs a decision from each park's
    manager, and a manager may only decide for their own institution. Approving
    one row grants access to that institution and leaves the rest pending, so the
    request keeps hanging in the other managers' queues (and in the admin's) with
    only the undecided institutions shown.

    ``status`` mirrors the parent's vocabulary. ``rejected`` also covers "the
    decider removed this institution from the request" — the two are the same
    outcome for the applicant, and keeping the row preserves the audit trail.
    """
    __tablename__ = 'verification_request_institutions'

    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(db.Integer,
                           db.ForeignKey('verification_requests.id', ondelete='CASCADE'),
                           nullable=False, index=True)
    institution_id = db.Column(db.Integer,
                               db.ForeignKey('institutions.id', ondelete='CASCADE'),
                               nullable=False, index=True)
    status = db.Column(db.String(10), nullable=False,
                       default=VerificationRequest.STATUS_PENDING, index=True)
    decided_at = db.Column(db.DateTime, nullable=True)
    decided_by_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    request = db.relationship(
        'VerificationRequest',
        backref=db.backref('institution_requests', cascade='all, delete-orphan',
                           passive_deletes=True))
    institution = db.relationship('Institution')
    decided_by = db.relationship('User', foreign_keys=[decided_by_id])

    __table_args__ = (
        db.UniqueConstraint('request_id', 'institution_id',
                            name='uq_verification_request_institution'),
        CheckConstraint("status IN ('pending', 'approved', 'rejected')",
                        name='ck_verification_request_institution_status'),
    )

    def __repr__(self):
        return (f'<VerificationRequestInstitution req={self.request_id} '
                f'inst={self.institution_id} [{self.status}]>')


class ContactSubmission(db.Model):
    """A message sent through the public contact form.

    Stored in the main DB and pushed to Telegram on creation; the admin replies
    by email manually (no personal email is embedded in the site). The `status`
    column tracks the handling lifecycle.
    """
    __tablename__ = 'contact_submissions'

    STATUS_NEW = 'new'
    STATUS_READ = 'read'
    STATUS_REPLIED = 'replied'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(120), nullable=False)
    subject = db.Column(db.String(200))
    message = db.Column(db.Text, nullable=False)
    submitted_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    ip_address = db.Column(db.String(45))  # fits IPv4 and IPv6
    status = db.Column(db.String(20), default=STATUS_NEW, nullable=False)

    def __repr__(self):
        return f'<ContactSubmission {self.id} {self.email} [{self.status}]>'


class SiteTextContent(db.Model):
    __tablename__ = 'site_text_content'
    id = db.Column(db.Integer, primary_key=True)
    page_key = db.Column(db.String(50), unique=True, nullable=False)
    title_uk = db.Column(db.Text)
    body_uk = db.Column(db.Text)
    title_en = db.Column(db.Text)
    body_en = db.Column(db.Text)

from app.extensions import login_manager

@login_manager.user_loader
def load_user(user_id):
    """Load the session's user, dropping the session if the account is disabled.

    Flask-Login only consults ``is_active`` when logging in, so without this
    check a user deactivated (or not yet email-confirmed) mid-session would keep
    their access until the cookie expired.
    """
    user = User.query.get(int(user_id))
    if user is None or not user.is_active:
        return None
    return user
