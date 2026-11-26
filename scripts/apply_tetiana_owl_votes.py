"""Add Tetiana Kornienko's (user 14) offline verdicts as segment votes.

She identified full 5-min recordings offline (file names ending in the species
found, screenshot 2026-11-26). Segments of Bubo bubo / Aegolius funereus inside
those recordings were matched by scripts.probe_tetiana_owl_segments; she had
already voted in the app on all but these four Bubo bubo segments, each in a
recording she marked "B bubo". Dry run by default; --apply writes.

    venv/Scripts/python -m scripts.apply_tetiana_owl_votes [--apply]
"""
import sys
from sqlalchemy import text
from app import create_app

USER_ID = 14
SEGMENTS = [31916, 31908, 31902, 31933]  # all Bubo bubo, verdict positive


def main():
    apply = '--apply' in sys.argv
    app = create_app()
    with app.app_context():
        from app.pam.utils import get_pam_db_connection
        conn = get_pam_db_connection()
        trans = conn.begin() if not conn.in_transaction() else None
        try:
            for sid in SEGMENTS:
                row = conn.execute(text("""SELECT s.id, sp.scientific_name, s.status, s.verification_count,
                       EXISTS(SELECT 1 FROM segment_verifications v WHERE v.segment_id=s.id AND v.user_id=:u) AS has_vote
                       FROM segments s JOIN species sp USING (species_id) WHERE s.id=:s FOR UPDATE OF s"""),
                                   {'s': sid, 'u': USER_ID}).fetchone()
                assert row and row.scientific_name == 'Bubo bubo', row
                if row.has_vote:
                    print('skip (already voted)', row); continue
                print('insert', row)
                conn.execute(text("""INSERT INTO segment_verifications (segment_id, user_id, verification_result, verified_at)
                                     VALUES (:s, :u, 1, CURRENT_TIMESTAMP)"""), {'s': sid, 'u': USER_ID})
            for r in conn.execute(text("SELECT id,status,verification_count,positive_verifications FROM segments WHERE id = ANY(:i)"), {'i': SEGMENTS}):
                print('after', tuple(r))
            if apply:
                (trans or conn).commit(); print('COMMITTED')
            else:
                (trans or conn).rollback(); print('DRY RUN, rolled back')
        finally:
            conn.close()

if __name__ == '__main__':
    main()
