# SPDX-License-Identifier: AGPL-3.0-only
"""Local dev server whose camera-trap database is the SANDBOX copy, never prod.

The normal dev server reads CT_DATABASE_URL from .env, which tunnels to the
PRODUCTION ct_db. For trying out anything that writes identifications, run
this instead. It points ct_db at the private sandbox cluster on the server
(port 5544, reached through `ssh -N -L 5545:localhost:5544 yurastrus.dev`) and
refuses to start if the connection lands anywhere else. The main database
(users, logins) is still the one from .env; this server never writes to it on
the identify pages.

    venv/Scripts/python scripts/sandbox_server.py      # http://localhost:5001
"""
import os
import sys

os.environ['CT_DATABASE_URL'] = 'postgresql://ct_user@localhost:5545/ct_db'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app import create_app  # noqa: E402
from app.camera_traps.database import get_ct_engine  # noqa: E402

app = create_app()
with app.app_context():
    with get_ct_engine().connect() as conn:
        port = conn.execute(text('SELECT inet_server_port()')).scalar()
if port != 5544:
    sys.exit(f'REFUSING: ct_db is on server port {port}, not the sandbox (5544)')
print('ct_db = SANDBOX (server port 5544)')

if __name__ == '__main__':
    app.run(port=5001, use_reloader=False)
