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
    __tablename__ = 'user_institutions'
    user_id        = db.Column(db.Integer, db.ForeignKey('user.id'), primary_key=True)
    institution_id = db.Column(db.Integer, db.ForeignKey('institutions.id'), primary_key=True)
    can_export     = db.Column(db.Boolean, default=False, nullable=False)

    institution = db.relationship('Institution')

class Institution(db.Model):
    __tablename__ = 'institutions'
    id = db.Column(db.Integer, primary_key=True)
    name_uk = db.Column(db.String(255), nullable=False)
    name_en = db.Column(db.String(255))
    code = db.Column(db.String(50), unique=True)
    ecoregion_uk = db.Column(db.String(100))
    ecoregion_en = db.Column(db.String(100))

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

    # viewonly=True: read-only join; mutations go through institution_links
    institutions = db.relationship('Institution', secondary=lambda: UserInstitution.__table__, viewonly=True)
    institution_links = db.relationship('UserInstitution', cascade='all, delete-orphan')

    @property
    def export_institutions(self):
        """Institutions the user is allowed to export data from."""
        return [link.institution for link in self.institution_links if link.can_export]

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
    note = db.Column(db.Text, nullable=True)

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

    def __repr__(self):
        return f'<VerificationRequest user={self.user_id} {self.module} [{self.status}]>'


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
