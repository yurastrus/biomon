# myproject/app/utils/decorators.py

from functools import wraps
from flask import abort
from flask_login import current_user
from app.extensions import login_manager 

def role_required(*role_names):
    """
    Декоратор, який перевіряє, чи має поточний користувач хоча б ОДНУ
    з перерахованих ролей.
    Також завжди надає доступ користувачам з роллю 'admin'.
    Якщо користувач не автентифікований, перенаправляє на сторінку входу.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # 1. Якщо користувач не автентифікований, використовуємо вашу стандартну логіку
            if not current_user.is_authenticated:
                return login_manager.unauthorized()

            # 2. Якщо користувач - адмін, він має доступ до всього
            # (Метод has_role вже включає цю перевірку, тому цей блок можна спростити)
            if current_user.has_role('admin'):
                return f(*args, **kwargs)
            
            # 3. Перевіряємо, чи має користувач хоча б одну з необхідних ролей
            # Метод has_role вже перевіряє наявність ролі у користувача
            user_has_required_role = any(current_user.has_role(role) for role in role_names)
            
            if not user_has_required_role:
                abort(403) # Якщо жодна з ролей не підійшла, забороняємо доступ
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator