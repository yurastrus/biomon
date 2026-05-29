"""Робота з базою ct_db. Worker не використовує Flask app context — підключається
напряму через SQLAlchemy і параметри з env vars.

Тут є дублювання моделей з app/camera_traps/models.py — це навмисно:
worker живе в окремому venv, в якому Flask не встановлений. Зміни в нашій
ai_* схемі треба синхронізувати в обох місцях; зате worker і Flask можна
розгортати незалежно.

Для photos/observations використовуємо raw SQL (їх схема стабільна,
ORM нам не потрібен — лише читаємо id+filename).
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
# ORM-моделі для ai_* таблиць (копія з app/camera_traps/models.py).
# При змінах схеми тримати синхронно з основним проектом.
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

    # Worker НЕ керує таблицями photos/observations/species/ai_models
    # своїм SQLAlchemy Base — він тільки INSERT-ить значення. FK-constraint
    # на рівні БД створюється через Flask schema; тут декларуємо лише FK
    # на ai_models (своя таблиця worker'а), а решту як просто Integer,
    # щоб SQLAlchemy ORM не намагалась резолвити referenced tables.
    id                    = Column(Integer, primary_key=True)
    photo_id              = Column(Integer, nullable=False)        # FK у БД: photos.id
    observation_id        = Column(Integer, nullable=False)        # FK у БД: observations.id
    model_id              = Column(Integer, ForeignKey('ai_models.id'), nullable=False)

    prediction_label      = Column(String(64), nullable=True)
    prediction_species_id = Column(Integer, nullable=True)         # FK у БД: species.id
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
    """Створює engine. database_url можна передати явно (для тестів) або
    залишити None — тоді береться з env CT_DATABASE_URL."""
    url = database_url or os.environ.get('CT_DATABASE_URL')
    if not url:
        raise RuntimeError(
            "CT_DATABASE_URL не визначено. Передайте database_url або "
            "виставте env-змінну."
        )
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=300,
        # Кожен запуск worker'а — короткоживучий процес, тримаємо мінімальний пул
        pool_size=2,
        max_overflow=2,
    )


def make_session(engine):
    """Створює одну session. Worker використовує одну сесію на весь прогін."""
    return sessionmaker(bind=engine, expire_on_commit=False)()


# ─────────────────────────────────────────────────────────────────────
# Helpers: реєстрація моделі
# ─────────────────────────────────────────────────────────────────────

def get_or_create_model(
    session,
    name: str,
    version: str,
    config: Optional[dict] = None,
) -> int:
    """Знаходить або створює запис у `ai_models`. Повертає model_id.

    Якщо створюється нова модель — вона стає `is_active=True`, попередні
    деактивуються (умова: тільки одна активна модель з тим же `name`).
    """
    row = session.query(AIModel).filter_by(name=name, version=version).one_or_none()
    if row is not None:
        return row.id

    # Нова модель — деактивуємо попередні з тим же name
    session.query(AIModel).filter(AIModel.name == name, AIModel.is_active.is_(True)) \
        .update({'is_active': False})

    new_model = AIModel(
        name=name,
        version=version,
        config_json=config or {},
        is_active=True,
    )
    session.add(new_model)
    session.flush()  # отримуємо id без commit
    logger.info(f"Registered new AI model: {name} {version} (id={new_model.id})")
    return new_model.id


# ─────────────────────────────────────────────────────────────────────
# Helpers: вибірка observation для обробки
# ─────────────────────────────────────────────────────────────────────

class PendingObservation(NamedTuple):
    observation_id: int
    photos: list[tuple[int, str]]  # [(photo_id, system_filename), ...] у хронологічному порядку


def pick_pending_observations(
    session,
    model_id: int,
    limit: int,
) -> list[PendingObservation]:
    """Повертає `limit` найстаріших pending observation, які ще не оброблені
    активною моделлю (model_id).

    Critically:
      - status='pending' (тільки невизначені людьми)
      - NOT EXISTS у ai_predictions для (observation, model) — тобто або
        ще не оброблялись, або оброблялись іншою моделлю.
      - Фото — у хронологічному порядку, що важливо для sequence-aware
        прогнозу DeepFaune.
    """
    if limit <= 0:
        return []

    # SQL CTE: спершу беремо N найстаріших observation, потім підтягуємо їх фото
    #
    # Важливо: фільтр `EXISTS (... photos.status IN ...)` у CTE — щоб пропустити
    # observations, у яких ВСІ фото архівовані (cleanup-таск видалив файли).
    # Без цього worker бере observation, а потім JOIN не повертає жодної photo —
    # worker тратить SQL-запит марно і не просувається далі.
    sql = text("""
        WITH pending_obs AS (
            SELECT o.id, o.series_start_time
            FROM observations o
            WHERE o.status = 'pending'
              AND NOT EXISTS (
                  SELECT 1 FROM ai_predictions ap
                  WHERE ap.observation_id = o.id AND ap.model_id = :model_id
              )
              -- Вибірковість: пропускаємо серії, для яких уже є КРАЩА/РІВНА
              -- класифікація (модель з accuracy_rank >= активної, напр.
              -- імпортований локальний MDR). Немає сенсу марнувати сервер.
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

    # Групуємо по observation_id, зберігаючи порядок фото
    grouped: dict[int, list[tuple[int, str]]] = {}
    order: list[int] = []  # порядок виходу observation
    for r in rows:
        obs_id = r.observation_id
        if obs_id not in grouped:
            grouped[obs_id] = []
            order.append(obs_id)
        grouped[obs_id].append((r.photo_id, r.system_filename))

    return [PendingObservation(obs_id, grouped[obs_id]) for obs_id in order]


# ─────────────────────────────────────────────────────────────────────
# Helpers: запис прогнозів
# ─────────────────────────────────────────────────────────────────────

def save_observation_predictions(
    session,
    observation_id: int,
    model_id: int,
    photo_id_by_path: dict[str, int],
    predictions: List[PhotoPrediction],
    label_to_species_id,
) -> int:
    """Записує прогнози для однієї observation. Повертає кількість збережених.

    Args:
        photo_id_by_path: словник {абсолютний_шлях: photo_id} — щоб знайти
                          photo_id для кожного PhotoPrediction за його .photo_path.
        label_to_species_id: callable label -> Optional[int]
                             (передаємо `species_map.map_deepfaune_label`).
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
# Helpers: ai_run_queue (для адмін-кнопки)
# ─────────────────────────────────────────────────────────────────────

def pick_queue_request(session, stale_running_minutes: int = 30) -> Optional[AIRunQueue]:
    """Атомарно бере один pending запит з ai_run_queue. Маркує його 'running'.

    Перед тим — стирає 'running' записи, що зависли довше ніж
    `stale_running_minutes` (default 30): автомат помічає як 'failed'.
    Це захищає від випадків коли python-процес був killed (OOM) і
    не встиг оновити статус.

    Використовуємо `FOR UPDATE SKIP LOCKED` щоб два паралельні worker'и
    (якщо колись таке буде) не взяли той самий запит.
    """
    # Auto-fail stale running requests (process was killed, didn't finalize).
    # COMMIT відразу — щоб ці зміни були видимі навіть якщо pending черга
    # потім порожня і викликач закриє сесію без commit'у.
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
    """Маркує запит з черги як завершений (done або failed)."""
    request.status = 'failed' if error else 'done'
    request.finished_at = func.now()
    request.processed_count = processed_count
    if error:
        request.error_msg = error


# ─────────────────────────────────────────────────────────────────────
# Швидкі lookup-функції для early-exit у CLI (без завантаження моделі)
# ─────────────────────────────────────────────────────────────────────

def count_pending_observations(database_url: Optional[str] = None) -> int:
    """Лічильник observations що потребують класифікації від активної моделі.
    Легкий: 1-2 SQL запити, без завантаження AI-адаптера. Використовується
    у cli.py як early-exit щоб не вантажити модель якщо нема роботи.

    Активна модель ідентифікується через env AI_RUNNER_MODEL_NAME/VERSION.
    """
    engine = make_engine(database_url)
    session = make_session(engine)
    try:
        name    = os.environ.get('AI_RUNNER_MODEL_NAME', 'DeepFaune')
        version = os.environ.get('AI_RUNNER_MODEL_VERSION', '1.4.1')
        active  = session.query(AIModel).filter_by(name=name, version=version).first()

        if active is None:
            # Моделі ще нема в БД — рахуємо ВСІ pending observation
            # (бо ще не може бути жодного прогнозу)
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
    """True якщо в ai_run_queue є хоча б один 'pending' запит.

    Робить заодно stale-cleanup (running > N хв → failed) щоб zombie-записи
    не блокували нові тіки і таблиця в адмінці залишалась актуальною.
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

        # 2. Чи є pending
        result = session.execute(text(
            "SELECT EXISTS (SELECT 1 FROM ai_run_queue WHERE status='pending')"
        )).scalar()
        return bool(result)
    finally:
        session.close()


def drain_one_empty_queue_request(database_url: Optional[str] = None) -> int:
    """Маркує найстарший 'pending' запит як 'done' з processed_count=0.

    Викликається коли queue має запит, але нема pending observations для
    класифікації. Без цього наступний cron-тік знову знайшов би той самий
    pending запит у черзі і знову вантажив би модель марно.

    Returns:
        1 якщо успішно drained, 0 якщо нема pending entry.
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
