from .journal_models import Tag, TagTranslation, Issue, JournalArticle, JournalArticleTranslation

from flask_login import UserMixin
from datetime import datetime
from app.extensions import db
from sqlalchemy.dialects.postgresql import ARRAY, TEXT
from sqlalchemy import CheckConstraint

user_roles = db.Table('user_roles',
    db.Column('user_id', db.Integer, db.ForeignKey('user.id'), primary_key=True),
    db.Column('role_id', db.Integer, db.ForeignKey('role.id'), primary_key=True)
)

# === КРОК 2: Визначаємо ВСІ моделі в одному місці ===

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(20), unique=True, nullable=False)
    password_hash = db.Column(db.String(60), nullable=False)
    roles = db.relationship('Role', secondary=user_roles, backref=db.backref('users', lazy='dynamic'))

    first_name = db.Column(db.String(50), nullable=True)
    last_name = db.Column(db.String(50), nullable=True)

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
    
    def has_role(self, *role_names):
        """
        Перевіряє, чи має користувач хоча б ОДНУ з перерахованих ролей.
        Завжди повертає True, якщо користувач має роль 'admin'.
        """
        # 1. Якщо користувач - адмін, він має доступ до всього.
        if any(role.name == 'admin' for role in self.roles):
            return True

        # 2. Створюємо множину (set) ролей, які є у користувача, для швидкого пошуку.
        user_roles = {role.name for role in self.roles}

        # 3. Перевіряємо, чи є хоча б один збіг між ролями користувача
        #    і ролями, які ми шукаємо.
        for role_name in role_names:
            if role_name in user_roles:
                return True # Знайшли збіг, одразу повертаємо True

        # 4. Якщо після перевірки всіх ролей збігів не знайдено.
        return False

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
    #articles = db.relationship('Article', secondary=article_roles, back_populates='required_roles')
    #webmaps = db.relationship('WebMap', secondary=webmap_roles, back_populates='required_roles')
    #rs_tools = db.relationship('RSTool', secondary=rs_tool_roles, back_populates='required_roles')

    def __repr__(self):
        return f"Role('{self.name}')"
    
from app.extensions import login_manager

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
