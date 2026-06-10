from flask import Blueprint, g

admin_bp = Blueprint(
    'admin',
    __name__,
    template_folder='../templates/admin',
    url_prefix='/<lang_code>/admin'
)

# Import routes at module end to avoid circular imports
from . import routes


@admin_bp.url_value_preprocessor
def pull_lang_code(endpoint, values):
    g.lang_code = values.pop('lang_code', 'uk')