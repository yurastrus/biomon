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

    # viewonly=True: read-only join; mutations go through institution_links
    institutions = db.relationship('Institution', secondary=lambda: UserInstitution.__table__, viewonly=True)
    institution_links = db.relationship('UserInstitution', cascade='all, delete-orphan')

    @property
    def export_institutions(self):
        """Установи, з яких користувач має право експортувати дані."""
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
    
    def is_local_admin(self):
        """Перевіряє, чи є користувач адміном установи (менеджером)."""
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
    return User.query.get(int(user_id))
