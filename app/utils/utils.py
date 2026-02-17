# /app/utils/utils.py

from flask import request
#from flask_mail import Message
from urllib.parse import urlparse
#from threading import Thread
#from app.extensions import mail

def is_safe_url(target):
    """Перевіряє чи безпечний URL для редиректу"""
    if not target: 
        return False
    test_url = urlparse(target)
    return test_url.scheme in ('', 'http', 'https') and \
           (not test_url.netloc or test_url.netloc == request.host)
