# SPDX-License-Identifier: AGPL-3.0-only
"""Read-only audit of ct_db before allowing several species per series.

Answers the questions the multi-species change depends on, against the real
database, inside a READ ONLY transaction (the server refuses any write):

  * does any verifier already have more than one species in one series?
  * does any (photo, user) pair already have more than one row?
  * how many closed series have a winner WITHOUT a strict majority, and how
    many have a tie at the top (where ROW_NUMBER picks arbitrarily)?
  * what privileges does the app role have (can we make a scratch copy)?

Usage:
    venv/Scripts/python -m scripts.audit_multi_species
"""
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

QUERIES = [
    ("identifications rows", "SELECT COUNT(*) FROM identifications"),
    ("observations by status",
     "SELECT status, COUNT(*) FROM observations GROUP BY status ORDER BY 1"),
    ("rows with species_id NULL", "SELECT COUNT(*) FROM identifications WHERE species_id IS NULL"),
    ("(photo,user) pairs with >1 row",
     "SELECT COUNT(*) FROM (SELECT photo_id, user_id FROM identifications "
     "GROUP BY 1,2 HAVING COUNT(*) > 1) t"),
    ("(series,user) pairs with >1 distinct species",
     """SELECT COUNT(*) FROM (
          SELECT p.observation_id, i.user_id FROM identifications i
          JOIN photos p ON p.id = i.photo_id
          GROUP BY 1,2 HAVING COUNT(DISTINCT COALESCE(i.species_id, -999999)) > 1) t"""),
    ("  ...of them in closed series",
     """SELECT COUNT(*) FROM (
          SELECT p.observation_id, i.user_id FROM identifications i
          JOIN photos p ON p.id = i.photo_id
          JOIN observations o ON o.id = p.observation_id
          WHERE o.status IN ('completed','archived')
          GROUP BY 1,2 HAVING COUNT(DISTINCT COALESCE(i.species_id, -999999)) > 1) t"""),
    ("(series,user) with >1 quantity value",
     """SELECT COUNT(*) FROM (
          SELECT p.observation_id, i.user_id FROM identifications i
          JOIN photos p ON p.id = i.photo_id
          GROUP BY 1,2 HAVING COUNT(DISTINCT i.quantity) > 1) t"""),
    ("closed series: winner without strict majority",
     """WITH v AS (
          SELECT p.observation_id, i.species_id, COUNT(DISTINCT i.user_id) vc
          FROM identifications i JOIN photos p ON p.id = i.photo_id
          GROUP BY 1,2),
        t AS (SELECT p.observation_id, COUNT(DISTINCT i.user_id) voters
              FROM identifications i JOIN photos p ON p.id = i.photo_id GROUP BY 1),
        w AS (SELECT observation_id, MAX(vc) top FROM v GROUP BY 1)
        SELECT COUNT(*) FROM w JOIN t USING (observation_id)
        JOIN observations o ON o.id = w.observation_id
        WHERE o.status IN ('completed','archived') AND w.top * 2 <= t.voters"""),
    ("closed series: tie at top on (votes, max qty)",
     """WITH v AS (
          SELECT p.observation_id, i.species_id, COUNT(DISTINCT i.user_id) vc,
                 MAX(i.quantity) mq
          FROM identifications i JOIN photos p ON p.id = i.photo_id
          GROUP BY 1,2),
        r AS (SELECT observation_id, vc, mq,
                     RANK() OVER (PARTITION BY observation_id ORDER BY vc DESC, mq DESC) rk
              FROM v)
        SELECT COUNT(*) FROM (SELECT observation_id FROM r WHERE rk = 1
                              GROUP BY 1 HAVING COUNT(*) > 1) x
        JOIN observations o ON o.id = x.observation_id
        WHERE o.status IN ('completed','archived')"""),
    ("closed series: tie at top on votes only",
     """WITH v AS (
          SELECT p.observation_id, i.species_id, COUNT(DISTINCT i.user_id) vc
          FROM identifications i JOIN photos p ON p.id = i.photo_id
          GROUP BY 1,2),
        r AS (SELECT observation_id, RANK() OVER (PARTITION BY observation_id ORDER BY vc DESC) rk
              FROM v)
        SELECT COUNT(*) FROM (SELECT observation_id FROM r WHERE rk = 1
                              GROUP BY 1 HAVING COUNT(*) > 1) x
        JOIN observations o ON o.id = x.observation_id
        WHERE o.status IN ('completed','archived')"""),
    ("constraints on identifications",
     """SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint
        WHERE conrelid = 'identifications'::regclass ORDER BY 1"""),
    ("indexes on identifications",
     "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'identifications' ORDER BY 1"),
    ("role: createdb / create schema in ct_db",
     """SELECT r.rolcreatedb, has_database_privilege(current_user, current_database(), 'CREATE'),
               (SELECT tableowner FROM pg_tables WHERE tablename = 'identifications') = current_user
        FROM pg_roles r WHERE r.rolname = current_user"""),
    ("ct_db size", "SELECT pg_size_pretty(pg_database_size(current_database()))"),
    ("server version", "SHOW server_version"),
]


def main():
    load_dotenv()
    url = os.environ.get('CT_DATABASE_URL')
    if not url:
        sys.exit('CT_DATABASE_URL is not set')
    engine = create_engine(url)
    with engine.connect() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        for title, sql in QUERIES:
            rows = conn.execute(text(sql)).fetchall()
            if len(rows) == 1 and len(rows[0]) == 1:
                print(f"{title}: {rows[0][0]}")
            else:
                print(f"{title}:")
                for r in rows:
                    print("   ", *r)
        conn.rollback()


if __name__ == '__main__':
    main()
