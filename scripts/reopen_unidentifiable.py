# SPDX-License-Identifier: AGPL-3.0-only
"""
Send completed "not identifiable" series with fewer than five verifiers back
to the identification queue.

Run from the project root (DRY RUN by default, nothing is changed):
    venv/Scripts/python -m scripts.reopen_unidentifiable            # Windows
    venv/bin/python -m scripts.reopen_unidentifiable --apply        # Linux

Since 2026-10-30 a series whose consensus is "Неможливо визначити" (-6)
closes only with at least five verifiers (consensus.UNIDENTIFIABLE_MIN_VOTERS):
a sixth person may still recognise the animal. Series closed under the old
rule (two to four verifiers) are reopened here with utils.reopen_observation:
status of the series and its photos back to 'pending', the AI correctness
flag cleared. Votes are kept. Archived series are NOT touched: their photos
are already deleted, nobody could look at them again.

The nightly cleanup already skips these series (background_tasks.
_kept_by_consensus_ids), so there is no hurry between deploy and --apply.
"""
import sys

from sqlalchemy import text

from app import create_app
from app.camera_traps.consensus import (
    UNIDENTIFIABLE_MIN_VOTERS, UNIDENTIFIABLE_SPECIES_ID, consensus_ctes)
from app.camera_traps.database import get_ct_session, close_ct_session
from app.camera_traps.utils import reopen_observation


def main():
    apply = '--apply' in sys.argv
    app = create_app()
    with app.app_context():
        session = get_ct_session()
        try:
            port = session.execute(text('SELECT inet_server_port()')).scalar()
            print(f"ct_db on server port {port} ({'SANDBOX' if port == 5544 else 'PRODUCTION'})")
            rows = session.execute(text(
                "WITH " + consensus_ctes(
                    """FROM identifications i JOIN photos p ON i.photo_id = p.id
                       JOIN observations o ON o.id = p.observation_id
                       WHERE o.status = 'completed'""") + """
                SELECT rc.observation_id, rc.voters, o.photo_count
                FROM RankedConsensus rc JOIN observations o ON o.id = rc.observation_id
                WHERE rc.rn = 1 AND rc.species_id = :sp AND rc.voters < :min
                ORDER BY rc.observation_id"""),
                {'sp': UNIDENTIFIABLE_SPECIES_ID, 'min': UNIDENTIFIABLE_MIN_VOTERS}).fetchall()
            print(f'Completed "not identifiable" series with < {UNIDENTIFIABLE_MIN_VOTERS} '
                  f'verifiers: {len(rows)} ({sum(r[2] or 0 for r in rows)} photos)')
            for obs_id, voters, photos in rows[:20]:
                print(f'   series {obs_id}: {voters} verifiers, {photos} photos')
            if len(rows) > 20:
                print(f'   ... and {len(rows) - 20} more')
            if not apply:
                print('\nDRY RUN: nothing changed. Add --apply to reopen them.')
                session.rollback()
                return
            for obs_id, _voters, _photos in rows:
                reopen_observation(obs_id, db_session=session)
            session.commit()
            print(f'\nReopened {len(rows)} series.')
        finally:
            close_ct_session()


if __name__ == '__main__':
    main()
