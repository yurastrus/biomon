"""Read-only check of the main DB before/after the docs migration (d0c5a1b2c3e4).

Prints: alembic version, whether any doc_* table exists, table count and row
counts of key tables. Opens the connection read-only; changes nothing.

    venv/Scripts/python -I scripts/docs_migration_precheck.py
"""
import os
import sys

from dotenv import dotenv_values
import psycopg2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
url = dotenv_values(os.path.join(ROOT, '.env')).get('DATABASE_URL')
if not url or not url.startswith('postgresql'):
    sys.exit('DATABASE_URL is not a PostgreSQL URL')

conn = psycopg2.connect(url)
conn.set_session(readonly=True, autocommit=True)
cur = conn.cursor()
cur.execute('select current_database(), version_num from alembic_version')
print('db / alembic:', cur.fetchall())
cur.execute("select count(*) from information_schema.tables where table_schema='public'")
print('public tables:', cur.fetchone()[0])
cur.execute("select table_name from information_schema.tables "
            "where table_schema='public' and table_name like 'doc\\_%' order by 1")
print('doc_* tables:', [r[0] for r in cur.fetchall()])
for t in ('user', 'role', 'institutions', 'user_institutions'):
    cur.execute(f'select count(*) from "{t}"')
    print(f'rows {t}:', cur.fetchone()[0])
cur.execute("select to_regclass('public.doc_topic')")
if cur.fetchone()[0]:
    cur.execute('select code, name_uk from doc_topic order by position')
    print('doc_topic:', cur.fetchall())
conn.close()
