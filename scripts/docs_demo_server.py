"""Local demo of the docs module on a throw-away SQLite database.

Never touches the real databases: DATABASE_URL is forced to a SQLite file given
on the command line (default: ./instance/docs_demo.sqlite). Creates the tables,
the default topics, an admin ``demo_admin`` and a topic editor ``demo_editor``
(password ``demo`` for both, local only) and one sample page, then serves on
http://127.0.0.1:5055/uk/docs/ .

    venv/Scripts/python scripts/docs_demo_server.py [path/to/demo.sqlite]

Sign in by visiting /uk/docs/_demo_login/<username> (exists only in this script).
"""
import os
import sys

DB_PATH = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else
                          os.path.join('instance', 'docs_demo.sqlite'))
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
os.environ['DATABASE_URL'] = 'sqlite:///' + DB_PATH.replace('\\', '/')
os.environ.setdefault('SECRET_KEY', 'docs-demo-only')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import redirect  # noqa: E402
from flask_login import login_user  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import bcrypt, db  # noqa: E402

app = create_app('testing')
app.config.update(WTF_CSRF_ENABLED=True, TESTING=False,
                  DOCS_UPLOAD_PATH=os.path.join(os.path.dirname(DB_PATH), 'docs_demo_uploads'))


@app.route('/<lang_code>/docs/_demo_login/<username>')
def _demo_login(lang_code, username):
    from app.models import User
    login_user(User.query.filter_by(username=username).first_or_404())
    return redirect(f'/{lang_code}/docs/')


with app.app_context():
    from app.docs.cli import seed_topics
    from app.docs.models import DocEditor, DocPage, DocPageTranslation, DocTopic
    from app.models import Role, User

    db.create_all()
    seed_topics()
    admin_role = Role.query.filter_by(name='admin').first() or Role(name='admin')
    viewer_role = Role.query.filter_by(name='viewer').first() or Role(name='viewer')
    for name, role in (('demo_admin', admin_role), ('demo_editor', viewer_role)):
        if not User.query.filter_by(username=name).first():
            u = User(username=name, password_hash=bcrypt.generate_password_hash('demo').decode(),
                     first_name=name.split('_')[1].capitalize(), last_name='Demo')
            u.roles.append(role)
            db.session.add(u)
    db.session.commit()
    ed = User.query.filter_by(username='demo_editor').one()
    ct = DocTopic.query.filter_by(code='ct').one()
    if not DocEditor.query.filter_by(user_id=ed.id).first():
        db.session.add(DocEditor(user_id=ed.id, topic_id=ct.id))
    if not DocPage.query.first():
        p = DocPage(slug='vstanovlennia-fotopastky', topic_id=ct.id)
        db.session.add(p)
        db.session.flush()
        db.session.add(DocPageTranslation(
            page_id=p.id, lang='uk', title='Встановлення фотопастки',
            body='## Перед виходом у поле\n\n1. Перевірте батареї.\n2. Відформатуйте карту.\n\n'
                 '> Порада: кріпіть камеру на висоті коліна.\n\n'
                 '| Параметр | Значення |\n|---|---|\n| Висота | 40–60 см |\n'))
    db.session.commit()

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5055, debug=False)
