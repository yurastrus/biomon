# SPDX-License-Identifier: AGPL-3.0-only
"""
Backfill ai_predictions.was_correct for already-completed series (Idea 4).

Run from the project root (AFTER scripts.init_ai_was_correct):
    venv/Scripts/python -m scripts.backfill_was_correct          # Windows
    venv/bin/python -m scripts.backfill_was_correct              # Linux

What it does:
    For each observation with status='completed', it determines the consensus
    species (the same algorithm as check_consensus_for_observation: the maximum
    number of votes among distinct (user_id, species_id)) and sets was_correct
    on all related ai_predictions:
        prediction_species_id == winner  -> True
        prediction_species_id != winner  -> False
        prediction_species_id IS NULL     -> None (AI did not identify a species)

    Idempotent: re-running produces the same result.
    As of 2026-06: ~14 completed series with AI, ~139 predictions.
"""

from sqlalchemy import func

from app import create_app
from app.camera_traps.database import get_ct_session, close_ct_session
from app.camera_traps.models import (
    Observation, Identification, Photo, AIPrediction,
)


def _winner_species_id(sess, obs_id):
    rows = (
        sess.query(Identification.user_id, Identification.species_id)
        .join(Photo, Identification.photo_id == Photo.id)
        .filter(Photo.observation_id == obs_id)
        .distinct()
        .all()
    )
    if not rows:
        return None
    votes = {}
    for _uid, sid in rows:
        votes[sid] = votes.get(sid, 0) + 1
    return max(votes.items(), key=lambda x: x[1])[0]


def main():
    app = create_app()
    with app.app_context():
        sess = get_ct_session()
        try:
            completed = (
                sess.query(Observation.id)
                .filter(Observation.status == 'completed')
                .all()
            )
            updated_preds = 0
            updated_obs = 0
            for (obs_id,) in completed:
                preds = (
                    sess.query(AIPrediction)
                    .filter(AIPrediction.observation_id == obs_id)
                    .all()
                )
                if not preds:
                    continue
                winner = _winner_species_id(sess, obs_id)
                if winner is None:
                    continue
                for p in preds:
                    if p.prediction_species_id is None:
                        p.was_correct = None
                    else:
                        p.was_correct = (p.prediction_species_id == winner)
                    updated_preds += 1
                updated_obs += 1
            sess.commit()
            print(f"Backfilled {updated_preds} predictions "
                  f"across {updated_obs} completed observations.")
        except Exception:
            sess.rollback()
            raise
        finally:
            close_ct_session()


if __name__ == '__main__':
    main()
