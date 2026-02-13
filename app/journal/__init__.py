from flask import Blueprint, g

journal_bp = Blueprint(
    'journal',
    __name__,
    url_prefix='/<lang_code>/journal'
)

@journal_bp.url_defaults
def add_language_code(endpoint, values):
    if 'lang_code' not in values:
        values['lang_code'] = g.lang_code

from . import routes