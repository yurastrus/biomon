# SPDX-License-Identifier: AGPL-3.0-only
"""
Read-only audit: does the "My statistics" block of the PAM verification page
(/api/verification/stats) match a direct count for each filter combination?

Run from the project root (prints, writes nothing):
    venv/bin/python -m scripts.audit_pam_verification_stats [username]

For the user's most-verified institution, species and class it compares the
API numbers with independent SQL over segment_verifications:
total / positive / negative (and shows unknown / skipped votes, which the
"Total" card includes) and the remaining count with the queue rule
(pending, not yet answered by the user, user's access).
"""
import sys

from sqlalchemy import text

from app import create_app
from app.models import User
from app.pam.utils import get_pam_db_connection


def main(username='yurastrus'):
    app = create_app()
    with app.app_context():
        user = User.query.filter_by(username=username).first()
        client = app.test_client()
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user.id)
            sess['_fresh'] = True
        conn = get_pam_db_connection()
        try:
            inst = conn.execute(text("""
                SELECT li.institution_id FROM segment_verifications sv
                JOIN segments seg ON seg.id = sv.segment_id
                JOIN recordings r ON r.recording_id = seg.recording_id
                JOIN location_institutions li ON li.location_id = r.location_id
                WHERE sv.user_id = :u GROUP BY 1 ORDER BY COUNT(*) DESC LIMIT 1
            """), {'u': user.id}).scalar()
            sp, cls = conn.execute(text("""
                SELECT seg.species_id, s.class FROM segment_verifications sv
                JOIN segments seg ON seg.id = sv.segment_id
                JOIN species s ON s.species_id = seg.species_id
                WHERE sv.user_id = :u GROUP BY 1, 2 ORDER BY COUNT(*) DESC LIMIT 1
            """), {'u': user.id}).fetchone()

            def truth(inst_id=None, species_id=None, class_name=None):
                cond, params = [], {'u': user.id}
                if inst_id:
                    cond.append("EXISTS (SELECT 1 FROM recordings r JOIN location_institutions li "
                                "ON li.location_id = r.location_id WHERE r.recording_id = seg.recording_id "
                                "AND li.institution_id = :i)")
                    params['i'] = inst_id
                if species_id:
                    cond.append('seg.species_id = :sp')
                    params['sp'] = species_id
                if class_name:
                    cond.append('s.class = :c')
                    params['c'] = class_name
                where = ' AND '.join(cond) or 'TRUE'
                votes = conn.execute(text(f"""
                    SELECT COUNT(*) AS total,
                           COUNT(*) FILTER (WHERE sv.verification_result = 1) AS pos,
                           COUNT(*) FILTER (WHERE sv.verification_result = 0) AS neg,
                           COUNT(*) FILTER (WHERE sv.verification_result = 2) AS unknown,
                           COUNT(*) FILTER (WHERE sv.verification_result IS NULL) AS skipped
                    FROM segment_verifications sv
                    JOIN segments seg ON seg.id = sv.segment_id
                    JOIN species s ON s.species_id = seg.species_id
                    WHERE sv.user_id = :u AND {where}
                """), params).fetchone()
                remaining = conn.execute(text(f"""
                    SELECT COUNT(*) FROM segments seg JOIN species s ON s.species_id = seg.species_id
                    WHERE seg.status = 'pending' AND {where}
                      AND NOT EXISTS (SELECT 1 FROM segment_verifications sv
                                      WHERE sv.segment_id = seg.id AND sv.user_id = :u)
                """), params).scalar()
                return votes, remaining

            cases = [
                ('no filter', '', {}),
                ('institution', f'institution_ids={inst}', {'inst_id': inst}),
                ('class', f'class_name={cls}', {'class_name': cls}),
                ('species', f'species_id={sp}', {'species_id': sp}),
                ('class+inst', f'class_name={cls}&institution_ids={inst}',
                 {'class_name': cls, 'inst_id': inst}),
                ('species+inst', f'species_id={sp}&institution_ids={inst}',
                 {'species_id': sp, 'inst_id': inst}),
            ]
            print(f'user={username} institution={inst} species={sp} class={cls}')
            for label, query, kw in cases:
                api = client.get('/uk/api/verification/stats?' + query).get_json()['user_stats']
                votes, remaining = truth(**kw)
                got = (api['total_verifications'], api['positive_verifications'],
                       api['negative_verifications'], api['remaining_verifications'])
                want = (votes.total, votes.pos, votes.neg, remaining)
                flag = 'OK ' if got == want else 'BAD'
                print(f'{flag} {label:13} api total/pos/neg/remaining={got} '
                      f'direct={want} (unknown={votes.unknown}, skipped={votes.skipped})')
        finally:
            conn.close()


if __name__ == '__main__':
    main(*sys.argv[1:])
