# SPDX-License-Identifier: AGPL-3.0-only
"""Working with the ct_db database. The worker does not use a Flask app context —
it connects directly via SQLAlchemy with parameters from env vars.

There is model duplication with app/camera_traps/models.py — this is intentional:
the worker lives in a separate venv where Flask is not installed. Changes to our
ai_* schema must be kept in sync in both places; in exchange the worker and Flask
can be deployed independently.

For photos/observations we use raw SQL (their schema is stable, we do not need
the ORM — we only read id+filename).
"""

from __future__ import annotations

import json
import logging
import os
from typing import List, NamedTuple, Optional

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, String,
    Text, UniqueConstraint, create_engine, func, text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import declarative_base, sessionmaker

from .adapter import PhotoPrediction


logger = logging.getLogger(__name__)

Base = declarative_base()


# ─────────────────────────────────────────────────────────────────────
# ORM models for the ai_* tables (a copy from app/camera_traps/models.py).
# On schema changes keep in sync with the main project.
# ─────────────────────────────────────────────────────────────────────

class AIModel(Base):
    __tablename__ = 'ai_models'

    id          = Column(Integer, primary_key=True)
    name        = Column(String(64), nullable=False)
    version     = Column(String(32), nullable=False)
    config_json = Column(JSONB, nullable=True)
    is_active   = Column(Boolean, default=True, nullable=False)
    created_at  = Column(DateTime, default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint('name', 'version', name='uq_ai_models_name_version'),
    )


class AIPrediction(Base):
    __tablename__ = 'ai_predictions'

    # The worker does NOT manage the photos/observations/species/ai_models tables
    # with its own SQLAlchemy Base — it only INSERTs values. The FK constraint
    # at the DB level is created via the Flask schema; here we declare only the FK
    # to ai_models (the worker's own table), and the rest as plain Integer,
    # so the SQLAlchemy ORM does not try to resolve the referenced tables.
    id                    = Column(Integer, primary_key=True)
    photo_id              = Column(Integer, nullable=False)        # FK in DB: photos.id
    observation_id        = Column(Integer, nullable=False)        # FK in DB: observations.id
    model_id              = Column(Integer, ForeignKey('ai_models.id'), nullable=False)

    prediction_label      = Column(String(64), nullable=True)
    prediction_species_id = Column(Integer, nullable=True)         # FK in DB: species.id
    prediction_score      = Column(Float, nullable=True)

    base_label            = Column(String(64), nullable=True)
    base_score            = Column(Float, nullable=True)

    top1_label            = Column(String(64), nullable=True)
    top1_score            = Column(Float, nullable=True)

    animal_count          = Column(Integer, nullable=True)
    human_count           = Column(Integer, nullable=True)
    bbox_json             = Column(JSONB, nullable=True)

    processed_at          = Column(DateTime, default=func.now(), nullable=False)
    error_msg             = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint('photo_id', 'model_id', name='uq_ai_predictions_photo_model'),
        Index('idx_ai_pred_filter', 'model_id', 'prediction_species_id', 'observation_id'),
        Index('idx_ai_pred_observation', 'observation_id'),
    )


class AIRunQueue(Base):
    __tablename__ = 'ai_run_queue'

    id              = Column(Integer, primary_key=True)
    requested_by    = Column(Integer, nullable=False)
    requested_at    = Column(DateTime, default=func.now(), nullable=False)
    n_observations  = Column(Integer, nullable=False)
    status          = Column(String(16), default='pending', nullable=False)
    started_at      = Column(DateTime, nullable=True)
    finished_at     = Column(DateTime, nullable=True)
    processed_count = Column(Integer, nullable=True)
    error_msg       = Column(Text, nullable=True)


# ─────────────────────────────────────────────────────────────────────
# Connection / session factory
# ─────────────────────────────────────────────────────────────────────

def make_engine(database_url: Optional[str] = None):
    """Create an engine. database_url can be passed explicitly (for tests) or
    left None — then it is taken from env CT_DATABASE_URL."""
    url = database_url or os.environ.get('CT_DATABASE_URL')
    if not url:
        raise RuntimeError(
            "CT_DATABASE_URL is not defined. Pass database_url or "
            "set the env variable."
        )
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=300,
        # Each worker run is a short-lived process, keep a minimal pool
        pool_size=2,
        max_overflow=2,
    )


def make_session(engine):
    """Create a single session. The worker uses one session for the whole run."""
    return sessionmaker(bind=engine, expire_on_commit=False)()


# ─────────────────────────────────────────────────────────────────────
# Helpers: model registration
# ─────────────────────────────────────────────────────────────────────

def get_or_create_model(
    session,
    name: str,
    version: str,
    config: Optional[dict] = None,
) -> int:
    """Find or create a row in `ai_models`. Returns model_id.

    When a new model is created — it becomes `is_active=True`, previous ones
    are deactivated (invariant: only one active model with the same `name`).
    """
    # IMPORTANT: filter by is_active=True. After imported models appear
    # (same name+version, but a different level_id, is_active=False), a plain
    # filter_by(name, version) returns several rows → MultipleResultsFound.
    # The worker always works with ITS OWN active server-side model.
    row = (session.query(AIModel)
           .filter_by(name=name, version=version, is_active=True)
           .one_or_none())
    if row is not None:
        return row.id

    # New model — deactivate previous ones with the same name
    session.query(AIModel).filter(AIModel.name == name, AIModel.is_active.is_(True)) \
        .update({'is_active': False})

    new_model = AIModel(
        name=name,
        version=version,
        config_json=config or {},
        is_active=True,
    )
    session.add(new_model)
    session.flush()  # get the id without committing
    logger.info(f"Registered new AI model: {name} {version} (id={new_model.id})")
    return new_model.id


# ─────────────────────────────────────────────────────────────────────
# Helpers: selecting observations for processing
# ─────────────────────────────────────────────────────────────────────

class PendingObservation(NamedTuple):
    observation_id: int
    photos: list[tuple[int, str]]  # [(photo_id, system_filename), ...] in chronological order


def pick_pending_observations(
    session,
    model_id: int,
    limit: int,
) -> list[PendingObservation]:
    """Return the `limit` oldest pending observations not yet processed
    by the active model (model_id).

    Critically:
      - status='pending' (only the ones not yet decided by humans)
      - NOT EXISTS in ai_predictions for (observation, model) — i.e. either
        not yet processed, or processed by another model.
      - Photos are in chronological order, which matters for DeepFaune's
        sequence-aware prediction.
    """
    if limit <= 0:
        return []

    # SQL CTE: first take the N oldest observations, then pull in their photos
    #
    # Important: the `EXISTS (... photos.status IN ...)` filter in the CTE — to skip
    # observations where ALL photos are archived (the cleanup task removed the files).
    # Without it the worker picks an observation, then the JOIN returns no photos —
    # the worker wastes an SQL query and makes no progress.
    sql = text("""
        WITH pending_obs AS (
            SELECT o.id, o.series_start_time
            FROM observations o
            WHERE o.status = 'pending'
              AND NOT EXISTS (
                  SELECT 1 FROM ai_predictions ap
                  WHERE ap.observation_id = o.id AND ap.model_id = :model_id
              )
              -- Selectivity: skip series that already have a BETTER/EQUAL
              -- classification (a model with accuracy_rank >= the active one, e.g.
              -- an imported local MDR). No point wasting the server.
              AND NOT EXISTS (
                  SELECT 1 FROM ai_predictions ap2
                  JOIN ai_models m2 ON m2.id = ap2.model_id
                  LEFT JOIN ai_model_levels l2 ON l2.id = m2.level_id
                  WHERE ap2.observation_id = o.id
                    AND COALESCE(l2.accuracy_rank, 0) >= (
                        SELECT COALESCE(l.accuracy_rank, 0)
                        FROM ai_models m
                        LEFT JOIN ai_model_levels l ON l.id = m.level_id
                        WHERE m.id = :model_id
                    )
              )
              AND EXISTS (
                  SELECT 1 FROM photos p
                  WHERE p.observation_id = o.id
                    AND p.status IN ('grouped', 'pending', 'completed')
              )
            ORDER BY o.series_start_time ASC
            LIMIT :limit_obs
        )
        SELECT po.id AS observation_id, p.id AS photo_id, p.system_filename
        FROM pending_obs po
        JOIN photos p ON p.observation_id = po.id
        WHERE p.status IN ('grouped', 'pending', 'completed')
        ORDER BY po.series_start_time ASC, p.captured_at ASC, p.id ASC
    """)

    rows = session.execute(sql, {'model_id': model_id, 'limit_obs': limit}).fetchall()

    # Group by observation_id, preserving photo order
    grouped: dict[int, list[tuple[int, str]]] = {}
    order: list[int] = []  # output order of observations
    for r in rows:
        obs_id = r.observation_id
        if obs_id not in grouped:
            grouped[obs_id] = []
            order.append(obs_id)
        grouped[obs_id].append((r.photo_id, r.system_filename))

    return [PendingObservation(obs_id, grouped[obs_id]) for obs_id in order]


# ─────────────────────────────────────────────────────────────────────
# Helpers: saving predictions
# ─────────────────────────────────────────────────────────────────────

def save_observation_predictions(
    session,
    observation_id: int,
    model_id: int,
    photo_id_by_path: dict[str, int],
    predictions: List[PhotoPrediction],
    label_to_species_id,
) -> int:
    """Save predictions for a single observation. Returns the number saved.

    Args:
        photo_id_by_path: a dict {absolute_path: photo_id} — to find the
                          photo_id for each PhotoPrediction by its .photo_path.
        label_to_species_id: callable label -> Optional[int]
                             (we pass `species_map.map_deepfaune_label`).
    """
    rows = []
    for p in predictions:
        photo_id = photo_id_by_path.get(p.photo_path)
        if photo_id is None:
            logger.warning(
                f"PhotoPrediction with unknown path: {p.photo_path}. Skipped."
            )
            continue
        rows.append(AIPrediction(
            photo_id=photo_id,
            observation_id=observation_id,
            model_id=model_id,
            prediction_label=p.prediction_label,
            prediction_species_id=label_to_species_id(p.prediction_label),
            prediction_score=p.prediction_score,
            base_label=p.base_label,
            base_score=p.base_score,
            top1_label=p.top1_label,
            top1_score=p.top1_score,
            animal_count=p.animal_count,
            human_count=p.human_count,
            bbox_json=p.bbox,
            error_msg=p.error,
        ))

    session.bulk_save_objects(rows)
    return len(rows)


# ─────────────────────────────────────────────────────────────────────
# Helpers: ai_run_queue (for the admin button)
# ─────────────────────────────────────────────────────────────────────

def pick_queue_request(session, stale_running_minutes: int = 30) -> Optional[AIRunQueue]:
    """Atomically take one pending request from ai_run_queue. Marks it 'running'.

    Beforehand — clears 'running' records that have been stuck longer than
    `stale_running_minutes` (default 30): marks them as 'failed' automatically.
    This guards against cases where the python process was killed (OOM) and
    did not manage to update the status.

    We use `FOR UPDATE SKIP LOCKED` so that two parallel workers
    (if there ever are any) do not take the same request.
    """
    # Auto-fail stale running requests (process was killed, didn't finalize).
    # COMMIT immediately — so these changes are visible even if the pending queue
    # is then empty and the caller closes the session without committing.
    session.execute(text("""
        UPDATE ai_run_queue
        SET status = 'failed',
            finished_at = NOW(),
            error_msg = COALESCE(error_msg, '') || :msg
        WHERE status = 'running'
          AND started_at < NOW() - (INTERVAL '1 minute' * :n)
    """), {
        'n': stale_running_minutes,
        'msg': f' [auto] Stale running > {stale_running_minutes}min, likely OOM-killed.',
    })
    session.commit()

    row = session.execute(text("""
        SELECT id FROM ai_run_queue
        WHERE status = 'pending'
        ORDER BY requested_at ASC
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    """)).fetchone()

    if row is None:
        return None

    request = session.get(AIRunQueue, row.id)
    request.status = 'running'
    request.started_at = func.now()
    return request


def finish_queue_request(
    session,
    request: AIRunQueue,
    processed_count: int,
    error: Optional[str] = None,
):
    """Mark a queue request as finished (done or failed)."""
    request.status = 'failed' if error else 'done'
    request.finished_at = func.now()
    request.processed_count = processed_count
    if error:
        request.error_msg = error


# ─────────────────────────────────────────────────────────────────────
# Fast lookup functions for early-exit in the CLI (without loading the model)
# ─────────────────────────────────────────────────────────────────────

def count_pending_observations(database_url: Optional[str] = None) -> int:
    """Count observations that need classification by the active model.
    Lightweight: 1-2 SQL queries, without loading the AI adapter. Used
    in cli.py as an early-exit to avoid loading the model when there is no work.

    The active model is identified via env AI_RUNNER_MODEL_NAME/VERSION.
    """
    engine = make_engine(database_url)
    session = make_session(engine)
    try:
        name    = os.environ.get('AI_RUNNER_MODEL_NAME', 'DeepFaune')
        version = os.environ.get('AI_RUNNER_MODEL_VERSION', '1.4.1')
        active  = session.query(AIModel).filter_by(name=name, version=version).first()

        if active is None:
            # The model is not in the DB yet — count ALL pending observations
            # (since there cannot be any prediction yet)
            mid_clause = ""
            params = {}
        else:
            mid_clause = """
                AND NOT EXISTS (
                    SELECT 1 FROM ai_predictions ap
                    WHERE ap.observation_id = o.id AND ap.model_id = :mid
                )
            """
            params = {'mid': active.id}

        sql = text(f"""
            SELECT COUNT(*) FROM observations o
            WHERE o.status = 'pending'
              {mid_clause}
              AND EXISTS (
                  SELECT 1 FROM photos p
                  WHERE p.observation_id = o.id
                    AND p.status IN ('grouped', 'pending', 'completed')
              )
        """)
        return int(session.execute(sql, params).scalar() or 0)
    finally:
        session.close()


def has_pending_queue_request(database_url: Optional[str] = None,
                              stale_minutes: int = 30) -> bool:
    """True if there is at least one 'pending' request in ai_run_queue.

    Also performs a stale-cleanup (running > N min → failed) so that zombie records
    do not block new ticks and the table in the admin panel stays up to date.
    """
    engine = make_engine(database_url)
    session = make_session(engine)
    try:
        # 1. Stale-cleanup
        session.execute(text("""
            UPDATE ai_run_queue
            SET status='failed',
                finished_at=NOW(),
                error_msg=COALESCE(error_msg,'') ||
                          ' [auto] Stale running > ' || :n || 'min (early-exit)'
            WHERE status='running'
              AND started_at < NOW() - (INTERVAL '1 minute' * :n)
        """), {'n': stale_minutes})
        session.commit()

        # 2. Is there anything pending
        result = session.execute(text(
            "SELECT EXISTS (SELECT 1 FROM ai_run_queue WHERE status='pending')"
        )).scalar()
        return bool(result)
    finally:
        session.close()


def drain_one_empty_queue_request(database_url: Optional[str] = None) -> int:
    """Mark the oldest 'pending' request as 'done' with processed_count=0.

    Called when the queue has a request but there are no pending observations
    to classify. Without this, the next cron tick would find the same pending
    request again and load the model for nothing.

    Returns:
        1 if successfully drained, 0 if there is no pending entry.
    """
    engine = make_engine(database_url)
    session = make_session(engine)
    try:
        result = session.execute(text("""
            UPDATE ai_run_queue
            SET status='done',
                finished_at=NOW(),
                processed_count=0,
                error_msg='No pending observations at run time (model not loaded)'
            WHERE id = (
                SELECT id FROM ai_run_queue
                WHERE status='pending'
                ORDER BY requested_at ASC
                LIMIT 1
            )
        """))
        session.commit()
        return int(result.rowcount or 0)
    finally:
        session.close()
