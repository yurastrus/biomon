"""Read-only probe: find PAM segments of Bubo bubo / Aegolius funereus that fall
inside the full 5-min recordings Tetiana Kornienko identified offline
(file list from a screenshot, 2026-11-26). Writes nothing.

    venv/Scripts/python -m scripts.probe_tetiana_owl_segments
"""
from sqlalchemy import text
from app import create_app

# (recording stem, species she reported in the file name)
FILES = [
    ("VERBUKY_20260419_202002", "B bubo"),
    ("VERBUKY_20260422_041900", "B bubo"),
    ("VERBUKY_20260428_193902", "B bubo"),
    ("VERPRALIS2_20260307_031500", "S aluco B bubo"),
    ("VERPRALIS2_20260312_061102", "S aluco B bubo"),
    ("VERPRALIS2_20260323_183802", "B bubo"),
    ("VERPRALIS2_20260325_054302", "S aluco B bubo"),
    ("VERPRALIS2_20260421_222800", "B bubo"),
    ("VERPRALIS2_20260428_013700", "B bubo"),
    ("VERBUKY_20260406_230400", "A funereus"),
    ("VERCENTER_20260418_232300", "A funereus"),
    ("VERBUKY_20260413_044202", "S aluco B bubo"),
    ("VERBUKY_20260310_042000", "B bubo"),
    ("VERBUKY_20260316_192102", "B bubo M+F"),
]
TARGET = ('Bubo bubo', 'Aegolius funereus')


def main():
    app = create_app()
    with app.app_context():
        from app.pam.utils import get_pam_db_connection
        from app.models import User
        for u in User.query.filter(User.last_name.ilike('%Корнієнко%') | User.first_name.ilike('%Тетян%')).all():
            print('USER', u.id, u.username, u.first_name, u.last_name, [r.name for r in u.roles])
        conn = get_pam_db_connection()
        try:
            for stem, said in FILES:
                recs = conn.execute(text("""
                    SELECT r.recording_id, r.filename, r.location_id, l.location_name, r.datetime_start
                    FROM recordings r LEFT JOIN locations l USING (location_id)
                    WHERE r.filename ILIKE :p"""), {'p': f'%{stem}%'}).fetchall()
                segs = conn.execute(text("""
                    SELECT s.id, sp.scientific_name, s.status, s.verification_count,
                           s.positive_verifications, s.start_s, s.confidence_level, s.filename,
                           (SELECT string_agg(sv.user_id || ':' || coalesce(sv.verification_result::text,'null'), ',')
                              FROM segment_verifications sv WHERE sv.segment_id = s.id) AS votes
                    FROM segments s JOIN species sp ON sp.species_id = s.species_id
                    LEFT JOIN recordings r ON r.recording_id = s.recording_id
                    WHERE (r.filename ILIKE :p OR s.filename ILIKE :p)
                    ORDER BY sp.scientific_name, s.start_s"""), {'p': f'%{stem}%'}).fetchall()
                print(f"\n== {stem}  [{said}]  recordings={len(recs)}")
                for r in recs:
                    print('   rec', r.recording_id, r.location_name, r.datetime_start, r.filename[-60:])
                for s in segs:
                    mark = '**' if s.scientific_name in TARGET else '  '
                    print(f"  {mark} seg {s.id} {s.scientific_name} {s.status} vc={s.verification_count} pos={s.positive_verifications} start={s.start_s} conf={s.confidence_level} votes={s.votes} file={s.filename}")
        finally:
            conn.close()

if __name__ == '__main__':
    main()
