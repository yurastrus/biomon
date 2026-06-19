# SPDX-License-Identifier: AGPL-3.0-only
from flask import Blueprint

bp = Blueprint('main', __name__)

# Import routes at module end to avoid circular imports
from app.routes import main