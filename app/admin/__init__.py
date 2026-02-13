from flask import Blueprint, g

# Створюємо Blueprint з назвою 'admin' та префіксом URL '/admin'
admin_bp = Blueprint(
    'admin', 
    __name__, 
    template_folder='../templates/admin', # Вказуємо, де шукати шаблони
    url_prefix='/<lang_code>/admin' # Додаємо префікс для всіх маршрутів
)

# Імпортуємо маршрути в кінці, щоб уникнути циклічних імпортів
from . import routes

# Додаємо обробник, щоб lang_code був доступний у всіх маршрутах цього blueprint
@admin_bp.url_value_preprocessor
def pull_lang_code(endpoint, values):
    g.lang_code = values.pop('lang_code', 'uk')