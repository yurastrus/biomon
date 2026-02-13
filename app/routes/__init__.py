# ==============================================
# app/routes/__init__.py
# ==============================================
from flask import Blueprint

# Створюємо основний blueprint
bp = Blueprint('main', __name__)

# Імпортуємо маршрути
from app.routes import main