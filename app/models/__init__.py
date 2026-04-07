from flask_login import UserMixin
from datetime import datetime
from app.extensions import db
from sqlalchemy.dialects.postgresql import ARRAY, TEXT
from sqlalchemy import CheckConstraint

# Таблиця зв'язку Користувач-Роль
user_roles = db.Table('user_roles',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('role_id', db.Integer, db.ForeignKey('role.id'), primary_key=True)
)

# Таблиця зв'язку Користувач-Установа
user_institutions = db.Table('user_institutions',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('institution_id', db.Integer, db.ForeignKey('institutions.id'), primary_key=True)
)

# Визначаємо моделі
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

    institutions = db.relationship('Institution', secondary=user_institutions, backref=db.backref('users', lazy='dynamic'))

    @property
    def full_name(self):
        """
        Повертає повне ім'я користувача, якщо воно вказане.
        В іншому випадку повертає логін як запасний варіант.
        """
        if self.first_name and self.last_name:
            return f"{self.first_name} {self.last_name}"
        # Якщо є тільки одне з полів, повернемо його
        elif self.first_name:
            return self.first_name
        elif self.last_name:
            return self.last_name
        # Якщо жодного поля немає, повертаємо логін
        else:
            return self.username
    
    def has_role(self, *required_roles):
        """
        Перевіряє, чи має користувач хоча б ОДНУ з перерахованих ролей,
        враховуючи ієрархію (вищі ролі автоматично включають нижчі).
        """
        # 1. Супер-адмін завжди має доступ до всього (швидка перевірка)
        if any(role.name == 'admin' for role in self.roles):
            return True

        # 2. СЛОВНИК ІЄРАРХІЇ: Яка роль які права в себе включає
        # (Налаштуй під свої потреби)
        ROLE_HIERARCHY = {
            'manager':['pam_verifier', 'ct_verifier', 'analyst', 'viewer'],
            'pam_verifier':  ['viewer'],
            'ct_verifier': ['viewer'],
            'analyst': ['ct_verifier', 'viewer'],
        }

        # 3. Отримуємо базові ролі користувача з БД
        user_base_roles = {role.name for role in self.roles}
        
        # 4. "Розгортаємо" ролі користувача на основі ієрархії
        expanded_user_roles = set(user_base_roles)
        for role in user_base_roles:
            if role in ROLE_HIERARCHY:
                expanded_user_roles.update(ROLE_HIERARCHY[role])

        # 5. Перевіряємо, чи є збіг між необхідними ролями та розширеними правами
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
        """
        Знаходить або створює профіль користувача для модуля фотопасток.
        Цей метод є "мостом" між двома базами даних.
        """
        # Локальні імпорти, щоб уникнути циклічних залежностей
        from app.camera_traps.database import get_ct_session, close_ct_session
        from app.camera_traps.models import UserProfile  # Виправлено назву!
        
        ct_session = get_ct_session()
        try:
            # Запитуємо профіль з бази даних камера-трапів
            profile = ct_session.query(UserProfile).filter_by(user_id=self.id).first()
            
            if not profile:
                # Якщо профіль ще не існує, створюємо його
                profile = UserProfile(
                    user_id=self.id,
                    camera_trap_role='viewer',  # Роль за замовчуванням
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
