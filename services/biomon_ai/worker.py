# SPDX-License-Identifier: AGPL-3.0-only
"""Core worker logic: takes a pending observation from ct_db, runs it through
the adapter, saves the predictions. Does not depend on Flask.

Two modes of operation:
    run_batch()      — takes the N oldest pending observations and processes
                       them. Used by the nightly cron.
    run_from_queue() — takes one request from `ai_run_queue` (admin button)
                       and processes the count specified in it.

Called from the CLI (`cli.py`).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from sqlalchemy import func, text

from .adapter import IClassifier
from .db import (
    AIRunQueue,
    finish_queue_request,
    get_or_create_model,
    make_engine,
    make_session,
    pick_pending_observations,
    pick_queue_request,
    save_observation_predictions,
)
from .species_map import map_deepfaune_label, refresh_label_map

logger = logging.getLogger(__name__)


def _resolve_photo_paths(
    upload_path: str,
    photos: list[tuple[int, str]],
) -> tuple[list[str], dict[str, int]]:
    """Build absolute paths for an observation's photo list.

    Files are looked up in this order:
      1. pending_photos/raw/<filename>         — original (if uploaded)
      2. pending_photos/thumbnails/<filename>  — thumbnail (~800x800px)

    The same fallback pattern as in `routes.serve_raw_photo` — in biomon
    some series are uploaded WITHOUT raw files, only thumbnails. That is
    enough for AI too: DeepFaune yolov8s works at 960px, the classifier
    crops a 182px region — on an 800px thumbnail the result is only slightly
    worse due to the upscale to 960px.

    Returns:
        (paths_in_order, path_to_photo_id):
          paths_in_order — chronologically ordered paths for the adapter
          path_to_photo_id — dict for reverse mapping when saving
    """
    raw_dir   = os.path.join(upload_path, 'pending_photos', 'raw')
    thumb_dir = os.path.join(upload_path, 'pending_photos', 'thumbnails')
    paths = []
    path_to_id = {}
    for photo_id, system_filename in photos:
        raw_path   = os.path.join(raw_dir, system_filename)
        thumb_path = os.path.join(thumb_dir, system_filename)
        if os.path.exists(raw_path):
            chosen = raw_path
        elif os.path.exists(thumb_path):
            chosen = thumb_path
            logger.debug(
                f"Photo {photo_id}: using thumbnail (no raw on disk)"
            )
        else:
            logger.warning(
                f"Photo not on disk (neither raw nor thumbnail), skipping: "
                f"id={photo_id}, filename={system_filename}"
            )
            continue
        paths.append(chosen)
        path_to_id[chosen] = photo_id
    return paths, path_to_id


def process_batch(
    adapter: IClassifier,
    upload_path: str,
    max_observations: int,
    database_url: Optional[str] = None,
    on_progress=None,
) -> int:
    """Process up to `max_observations` pending observations. Returns the
    number actually processed (those for which something was written to
    ai_predictions).

    Per-observation errors do not abort the whole run: if one fails we skip
    it and move to the next, but an exception is NOT re-raised upward (for
    cron compatibility). Only if something crashes at the DB-session level
    do we re-raise.

    Args:
        on_progress: optional callback `f(processed_count)` called AFTER EACH
            successfully saved observation. Purpose — to update
            `ai_run_queue.processed_count` during the run so the UI sees
            progress (and if the process dies, the actual result is not lost).
    """
    engine = make_engine(database_url)
    session = make_session(engine)

    try:
        # 1. Register/find the active model
        model_id = get_or_create_model(
            session,
            name=adapter.name,
            version=adapter.version,
            config=adapter.config,
        )
        session.commit()  # the model must be in the DB before prediction records

        # 1b. Load the label map from the DB (ai_label_map — the single source
        #     of truth). If the table is missing/empty — the built-in fallback
        #     remains.
        n_map = refresh_label_map(session)
        if n_map:
            logger.info(f"Label map: loaded {n_map} rows from ai_label_map")

        # 2. Take pending observations. The SQL is capped at 10× max_observations
        # because in prod there are many "orphan" observations (status='pending',
        # photos.status='pending', but no file — cleanup left a record without a file).
        # In Python we iterate until max_observations are SUCCESSFULLY processed.
        sql_limit = max(max_observations * 10, 50)
        observations = pick_pending_observations(
            session,
            model_id=model_id,
            limit=sql_limit,
        )
        logger.info(
            f"Model {adapter.name} {adapter.version} (id={model_id}): "
            f"SQL returned {len(observations)} candidate observation(s); "
            f"will process up to {max_observations} successful"
        )

        if not observations:
            return 0

        processed = 0
        for obs in observations:
            if processed >= max_observations:
                logger.info(
                    f"Reached target ({max_observations} successful). Stopping."
                )
                break
            try:
                paths, path_to_id = _resolve_photo_paths(upload_path, obs.photos)
                if not paths:
                    logger.warning(
                        f"Observation {obs.observation_id}: no photos on disk, skipping"
                    )
                    continue

                logger.info(
                    f"Observation {obs.observation_id}: classifying {len(paths)} photo(s)"
                )

                predictions = adapter.predict_observation(paths)
                saved = save_observation_predictions(
                    session=session,
                    observation_id=obs.observation_id,
                    model_id=model_id,
                    photo_id_by_path=path_to_id,
                    predictions=predictions,
                    label_to_species_id=map_deepfaune_label,
                )
                session.commit()
                processed += 1
                logger.info(
                    f"Observation {obs.observation_id}: saved {saved} prediction(s)"
                )

                # Callback with the actual count — to update progress in the UI
                if on_progress is not None:
                    try:
                        on_progress(processed)
                    except Exception as e:
                        # Don't abort the batch over a progress-reporting error
                        logger.warning(f"on_progress callback failed: {e}")

            except Exception as e:
                session.rollback()
                logger.exception(
                    f"Observation {obs.observation_id} failed: {e}"
                )
                # Move on — other observations should not be affected

        return processed

    finally:
        session.close()


def process_batch_tracked(
    adapter: IClassifier,
    upload_path: str,
    max_observations: int,
    requested_by: int = 0,
    database_url: Optional[str] = None,
) -> int:
    """Like process_batch, but automatically creates an ai_run_queue record
    to track progress. Used for cron runs (`cli --batch=N`) so that all runs
    — both manual via the admin button and the nightly automatic ones — are
    visible in one table.

    Args:
        requested_by: id of the initiating user. Defaults to 0 — the
                      "system/cron" marker (there is no user with id=0 in the DB).
    """
    engine = make_engine(database_url)

    # 1. Create a queue record with status 'running'
    session = make_session(engine)
    try:
        row = AIRunQueue(
            requested_by=requested_by,
            n_observations=max_observations,
            status='running',
            started_at=func.now(),
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        queue_id = row.id
    finally:
        session.close()

    logger.info(
        f"Tracking cron batch in ai_run_queue: id={queue_id}, "
        f"n={max_observations}, requested_by={requested_by}"
    )

    # 2. Callback to update processed_count in real time
    def _update_progress(count: int):
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE ai_run_queue SET processed_count = :c WHERE id = :i"),
                {'c': count, 'i': queue_id},
            )
            conn.commit()

    # 3. Run the batch and finalize the record
    try:
        processed = process_batch(
            adapter=adapter,
            upload_path=upload_path,
            max_observations=max_observations,
            database_url=database_url,
            on_progress=_update_progress,
        )
        with engine.connect() as conn:
            if processed == 0:
                # Nothing processed — delete the empty cron record from the DB
                conn.execute(
                    text("DELETE FROM ai_run_queue WHERE id=:i"),
                    {'i': queue_id},
                )
                logger.info(f"Queue entry {queue_id} deleted (idle cron, nothing to process)")
            else:
                conn.execute(
                    text(
                        "UPDATE ai_run_queue "
                        "SET status='done', finished_at=NOW(), processed_count=:c "
                        "WHERE id=:i"
                    ),
                    {'c': processed, 'i': queue_id},
                )
                logger.info(f"Queue entry {queue_id} marked 'done'. Processed: {processed}")
            conn.commit()
        return processed
    except Exception as e:
        with engine.connect() as conn:
            conn.execute(
                text(
                    "UPDATE ai_run_queue "
                    "SET status='failed', finished_at=NOW(), error_msg=:msg "
                    "WHERE id=:i"
                ),
                {'msg': f'Cron batch crashed: {e}'[:500], 'i': queue_id},
            )
            conn.commit()
        logger.exception(f"Queue entry {queue_id} marked 'failed'")
        raise


def run_from_queue(
    adapter: IClassifier,
    upload_path: str,
    database_url: Optional[str] = None,
) -> Optional[int]:
    """Process one request from `ai_run_queue` (from the admin button).

    Returns:
        The number of observations processed, or None if the queue is empty.
    """
    engine = make_engine(database_url)
    session = make_session(engine)

    # 1. Take one pending request (FOR UPDATE SKIP LOCKED)
    request = pick_queue_request(session)
    if request is None:
        session.close()
        logger.info("Queue is empty, nothing to do")
        return None

    queue_id = request.id
    n_obs = request.n_observations
    requested_by = request.requested_by

    # Commit status='running' immediately so another worker won't grab it
    session.commit()
    logger.info(
        f"Picked queue request id={queue_id} (n={n_obs}, requested_by={requested_by})"
    )

    # Callback that updates processed_count in ai_run_queue via a separate
    # short connection (does not conflict with the batch's main session).
    # Runs after each observation — the UI sees progress in real time and
    # even if the process dies, the DB keeps the up-to-date counter.
    def _report_progress(count: int):
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE ai_run_queue SET processed_count = :c WHERE id = :i"),
                {'c': count, 'i': queue_id},
            )
            conn.commit()

    # 2. Process. Errors inside process_batch are caught per-observation
    try:
        processed = process_batch(
            adapter=adapter,
            upload_path=upload_path,
            max_observations=n_obs,
            database_url=database_url,
            on_progress=_report_progress,
        )
        # Reload the request after process_batch (everything committed in another session)
        request = session.get(type(request), queue_id)
        finish_queue_request(session, request, processed_count=processed)
        session.commit()
        logger.info(f"Queue request id={queue_id} done, processed={processed}")
        return processed
    except Exception as e:
        session.rollback()
        # Reload and mark as failed
        request = session.get(type(request), queue_id)
        finish_queue_request(session, request, processed_count=0, error=str(e))
        session.commit()
        logger.exception(f"Queue request id={queue_id} FAILED: {e}")
        raise
    finally:
        session.close()
