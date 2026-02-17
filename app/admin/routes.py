from flask import render_template, redirect, url_for, flash, request, g
from sqlalchemy import func
from . import admin_bp
from app.extensions import db
from flask_babel import gettext as _

@admin_bp.route('/')
def home():
    """Головна сторінка адмін-панелі зі статистикою."""
    return render_template('admin_home.html')
