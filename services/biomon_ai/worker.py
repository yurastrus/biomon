"""Основна логіка worker'а: бере pending observation з ct_db, прогоняє через
adapter, зберігає прогнози. Не залежить від Flask.

Два режими роботи:
    run_batch()      — бере N найстаріших pending observation і обробляє.
                       Використовується нічним cron'ом.
    run_from_queue() — бере один запит з `ai_run_queue` (адмін-кнопка)
                       і обробляє вказану в ньому кількість.

Викликається з CLI (`cli.py`).
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
from .species_map import map_deepfaune_label

logger = logging.getLogger(__name__)


def _resolve_photo_paths(
    upload_path: str,
    photos: list[tuple[int, str]],
) -> tuple[list[str], dict[str, int]]:
    """Будує абсолютні шляхи для списку фото observation'у.

    Шукаємо файли в такому порядку:
      1. pending_photos/raw/<filename>         — оригінал (якщо завантажений)
      2. pending_photos/thumbnails/<filename>  — мініатюра (~800x800px)

    Той самий fallback-патерн що в `routes.serve_raw_photo` — у біомоні
    частина серій завантажуються БЕЗ raw-файлів, тільки мініатюри.
    Для AI це теж достатньо: DeepFaune yolov8s працює з 960px, класифікатор
    нарізає 182px crop — на 800px мініатюрі результат лише трохи гірший
    через дотрімування resize до 960px.

    Returns:
        (paths_in_order, path_to_photo_id):
          paths_in_order — хронологічно впорядковані шляхи для adapter'а
          path_to_photo_id — словник для зворотного мапінгу при збереженні
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
    """Обробляє до `max_observations` pending observation. Повертає кількість
    фактично оброблених (тих, для яких щось записано в ai_predictions).

    Errors per-observation не зривають весь прогін: впала одна — пропускаємо
    і йдемо до наступної, але exception вище НЕ підіймаємо (для cron-сумісності).
    Тільки якщо щось крашить рівень БД-сесії — підіймаємо.

    Args:
        on_progress: опційний callback `f(processed_count)` що викликається
            ПІСЛЯ КОЖНОЇ успішно збереженої observation. Призначення —
            оновлювати `ai_run_queue.processed_count` під час прогону,
            щоб UI бачив прогрес (і якщо процес помре, не втратили
            фактичний результат).
    """
    engine = make_engine(database_url)
    session = make_session(engine)

    try:
        # 1. Реєструємо/знаходимо активну модель
        model_id = get_or_create_model(
            session,
            name=adapter.name,
            version=adapter.version,
            config=adapter.config,
        )
        session.commit()  # модель має бути в БД до записів прогнозів

        # 2. Беремо pending observations. SQL обмежений 10× max_observations,
        # бо у проді багато "сирітських" observations (status='pending',
        # photos.status='pending', але файлу нема — cleanup залишив запис без файлу).
        # У Python ітеруємо до max_observations УСПІШНО оброблених.
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

                # Callback з фактичним count — для оновлення progress у UI
                if on_progress is not None:
                    try:
                        on_progress(processed)
                    except Exception as e:
                        # Не зривати batch через помилку звітування про прогрес
                        logger.warning(f"on_progress callback failed: {e}")

            except Exception as e:
                session.rollback()
                logger.exception(
                    f"Observation {obs.observation_id} failed: {e}"
                )
                # Йдемо далі — інші observation не повинні постраждати

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
    """Як process_batch, але автоматично створює запис у ai_run_queue для
    відстеження прогресу. Використовується для cron-запусків (`cli --batch=N`),
    щоб усі прогони — і ручні через адмін-кнопку, і нічні автоматичні —
    були видні в одній таблиці.

    Args:
        requested_by: id користувача-ініціатора. За замовчуванням 0 — маркер
                      "system/cron" (у БД немає юзера з id=0).
    """
    engine = make_engine(database_url)

    # 1. Створюємо queue-запис із статусом 'running'
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

    # 2. Callback для оновлення processed_count у real-time
    def _update_progress(count: int):
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE ai_run_queue SET processed_count = :c WHERE id = :i"),
                {'c': count, 'i': queue_id},
            )
            conn.commit()

    # 3. Виконуємо batch і фіналізуємо запис
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
                # Нічого не оброблено — порожній крон-запис видаляємо з БД
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
    """Обробляє один запит з `ai_run_queue` (від адмін-кнопки).

    Returns:
        Кількість оброблених observation, або None якщо черга порожня.
    """
    engine = make_engine(database_url)
    session = make_session(engine)

    # 1. Беремо один pending запит (FOR UPDATE SKIP LOCKED)
    request = pick_queue_request(session)
    if request is None:
        session.close()
        logger.info("Queue is empty, nothing to do")
        return None

    queue_id = request.id
    n_obs = request.n_observations
    requested_by = request.requested_by

    # Фіксуємо status='running' відразу, щоб інший worker не взяв
    session.commit()
    logger.info(
        f"Picked queue request id={queue_id} (n={n_obs}, requested_by={requested_by})"
    )

    # Callback що оновлює processed_count у ai_run_queue через окреме
    # коротке з'єднання (не конфліктує з основною сесією batch'у).
    # Виконується після кожної observation — UI бачить прогрес у real-time
    # і навіть якщо процес помре, в БД лишиться актуальний counter.
    def _report_progress(count: int):
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE ai_run_queue SET processed_count = :c WHERE id = :i"),
                {'c': count, 'i': queue_id},
            )
            conn.commit()

    # 2. Обробляємо. Errors всередині process_batch ловляться per-observation
    try:
        processed = process_batch(
            adapter=adapter,
            upload_path=upload_path,
            max_observations=n_obs,
            database_url=database_url,
            on_progress=_report_progress,
        )
        # Перезагружаємо request після process_batch (у іншій сесії все commit'нулось)
        request = session.get(type(request), queue_id)
        finish_queue_request(session, request, processed_count=processed)
        session.commit()
        logger.info(f"Queue request id={queue_id} done, processed={processed}")
        return processed
    except Exception as e:
        session.rollback()
        # Перезавантажуємо і маркуємо як failed
        request = session.get(type(request), queue_id)
        finish_queue_request(session, request, processed_count=0, error=str(e))
        session.commit()
        logger.exception(f"Queue request id={queue_id} FAILED: {e}")
        raise
    finally:
        session.close()
