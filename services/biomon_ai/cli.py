"""Точка входу для AI-worker'а.

ПРИКЛАДИ ЗАПУСКУ
    # Тести на dev-машині (.env поряд):
    venv/Scripts/python -m services.biomon_ai.cli --help
    venv/Scripts/python -m services.biomon_ai.cli --batch=5 --adapter=stub -v

    # Прод-сервер з окремим venv:
    /opt/biomon-ai/venv/bin/python -m biomon_ai.cli --batch=100

    # Нічний cron:
    0 2 * * * /opt/biomon-ai/run-batch.sh

    # Реакція на адмін-кнопку (один запит з ai_run_queue):
    /opt/biomon-ai/venv/bin/python -m biomon_ai.cli --from-queue

КОНФІГУРАЦІЯ (з env або .env поряд з cli.py):
    CT_DATABASE_URL              — обов'язково, підключення до ct_db
    CAMERA_TRAP_UPLOAD_PATH      — обов'язково, де лежать фото
    AI_RUNNER_THRESHOLD          — поріг впевненості (default 0.8)
    AI_RUNNER_MAX_PER_RUN        — fallback для --batch якщо не вказано
    AI_RUNNER_MODEL_NAME         — назва моделі (default 'DeepFaune')
    AI_RUNNER_MODEL_VERSION      — версія моделі (default '1.4.1')

EXIT CODES:
    0  — успіх (включно з "немає роботи")
    1  — некоректний виклик / відсутня конфігурація
    2  — критична помилка під час обробки (логи зверху)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

# .env завантажуємо опційно: на сервері конфіг приходить з systemd EnvironmentFile
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv не встановлено — це нормально на проді з systemd

from .adapter import IClassifier, StubAdapter
from .worker import process_batch_tracked, run_from_queue


logger = logging.getLogger('biomon_ai')


def build_adapter(name: str, threshold: float) -> IClassifier:
    """Створює інстанс адаптера за іменем."""
    if name == 'stub':
        return StubAdapter()
    elif name == 'deepfaune':
        # Імпорт всередині — щоб stub-режим працював на dev-машині без torch
        try:
            from .deepfaune_adapter import DeepFauneAdapter  # noqa: реалізується у Кроці 8
        except ImportError as e:
            raise RuntimeError(
                f"DeepFauneAdapter недоступний: {e}. "
                "Перевірте що torch+ultralytics встановлені та DeepFaune склонована "
                "(див. services/biomon_ai/DEPLOY.md)."
            ) from e
        return DeepFauneAdapter(threshold=threshold)
    else:
        raise ValueError(f"Unknown adapter: {name}")


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog='biomon_ai',
        description='AI-класифікатор фотографій з фотопасток',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        '--batch',
        type=int,
        metavar='N',
        help='Обробити N найстаріших pending observation (нічний cron)',
    )
    mode.add_argument(
        '--from-queue',
        action='store_true',
        help='Взяти один pending запит з ai_run_queue (адмін-кнопка)',
    )

    p.add_argument(
        '--adapter',
        choices=['stub', 'deepfaune'],
        default='deepfaune',
        help="Який класифікатор використати. 'stub' — для тестів без моделі. "
             "Default: deepfaune.",
    )
    p.add_argument(
        '--upload-path',
        help='Override CAMERA_TRAP_UPLOAD_PATH (де лежать фото)',
    )
    p.add_argument(
        '--threshold',
        type=float,
        help='Override AI_RUNNER_THRESHOLD (поріг впевненості)',
    )
    p.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='DEBUG логи',
    )

    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    )

    # ── Конфігурація ──────────────────────────────────────────────────
    upload_path = args.upload_path or os.environ.get('CAMERA_TRAP_UPLOAD_PATH')
    if not upload_path:
        print(
            'ERROR: потрібен --upload-path або env CAMERA_TRAP_UPLOAD_PATH',
            file=sys.stderr,
        )
        return 1

    if not os.environ.get('CT_DATABASE_URL'):
        print('ERROR: env CT_DATABASE_URL не визначено', file=sys.stderr)
        return 1

    threshold = (
        args.threshold
        if args.threshold is not None
        else float(os.environ.get('AI_RUNNER_THRESHOLD', '0.8'))
    )

    # ── Адаптер ────────────────────────────────────────────────────────
    try:
        adapter = build_adapter(args.adapter, threshold)
    except RuntimeError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        return 1

    logger.info(
        f"Adapter: {adapter.name} {adapter.version} | "
        f"upload_path={upload_path} | threshold={threshold}"
    )

    # ── Виконання ──────────────────────────────────────────────────────
    try:
        if args.from_queue:
            result = run_from_queue(adapter, upload_path)
            if result is None:
                logger.info('Queue is empty')
            else:
                logger.info(f'Queue request done. Processed: {result}')
        else:
            # --batch=N: записуємо в ai_run_queue з requested_by=0 (cron/system),
            # щоб видно було в адмін-сторінці поряд з ручними запитами
            processed = process_batch_tracked(
                adapter=adapter,
                upload_path=upload_path,
                max_observations=args.batch,
                requested_by=0,
            )
            logger.info(f'Batch done. Processed: {processed}/{args.batch}')

        return 0

    except KeyboardInterrupt:
        logger.warning('Interrupted by user')
        return 130
    except Exception as e:
        logger.exception(f'Critical error: {e}')
        return 2


if __name__ == '__main__':
    sys.exit(main())
