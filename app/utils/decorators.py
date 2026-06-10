from functools import wraps
from flask import abort
from flask_login import current_user
from app.extensions import login_manager 

def role_required(*role_names):
    """Decorator that grants access only if the user holds at least one of the given roles.

    Admin users always pass. Unauthenticated requests are redirected to the login page.
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated:
                return login_manager.unauthorized()

            if current_user.has_role('admin'):
                return f(*args, **kwargs)

            user_has_required_role = any(current_user.has_role(role) for role in role_names)

            if not user_has_required_role:
                abort(403)

            return f(*args, **kwargs)
        return decorated_function
    return decorator

