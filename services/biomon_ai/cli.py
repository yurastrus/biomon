# SPDX-License-Identifier: AGPL-3.0-only
"""Entry point for the AI worker.

USAGE EXAMPLES
    # Tests on a dev machine (.env next to it):
    venv/Scripts/python -m services.biomon_ai.cli --help
    venv/Scripts/python -m services.biomon_ai.cli --batch=5 --adapter=stub -v

    # Production server with a separate venv:
    /opt/biomon-ai/venv/bin/python -m biomon_ai.cli --batch=100

    # Nightly cron:
    0 2 * * * /opt/biomon-ai/run-batch.sh

    # Reacting to the admin button (one request from ai_run_queue):
    /opt/biomon-ai/venv/bin/python -m biomon_ai.cli --from-queue

CONFIGURATION (from env or .env next to cli.py):
    CT_DATABASE_URL              — required, connection to ct_db
    CAMERA_TRAP_UPLOAD_PATH      — required, where the photos are stored
    AI_RUNNER_THRESHOLD          — confidence threshold (default 0.8)
    AI_RUNNER_MAX_PER_RUN        — fallback for --batch if not specified
    AI_RUNNER_MODEL_NAME         — model name (default 'DeepFaune')
    AI_RUNNER_MODEL_VERSION      — model version (default '1.4.1')

EXIT CODES:
    0  — success (including "no work")
    1  — invalid invocation / missing configuration
    2  — critical error during processing (logs above)
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

# Load .env optionally: on the server config comes from a systemd EnvironmentFile
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed — this is fine in production with systemd

from .adapter import IClassifier, StubAdapter
from .worker import process_batch_tracked, run_from_queue


logger = logging.getLogger('biomon_ai')


def build_adapter(name: str, threshold: float) -> IClassifier:
    """Create an adapter instance by name."""
    if name == 'stub':
        return StubAdapter()
    elif name == 'deepfaune':
        # Import inside — so stub mode works on a dev machine without torch
        try:
            from .deepfaune_adapter import DeepFauneAdapter  # noqa: implemented in Step 8
        except ImportError as e:
            raise RuntimeError(
                f"DeepFauneAdapter unavailable: {e}. "
                "Check that torch+ultralytics are installed and DeepFaune is cloned "
                "(see services/biomon_ai/DEPLOY.md)."
            ) from e
        return DeepFauneAdapter(threshold=threshold)
    else:
        raise ValueError(f"Unknown adapter: {name}")


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog='biomon_ai',
        description='AI classifier for camera-trap photos',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        '--batch',
        type=int,
        metavar='N',
        help='Process the N oldest pending observations (nightly cron)',
    )
    mode.add_argument(
        '--from-queue',
        action='store_true',
        help='Take one pending request from ai_run_queue (admin button)',
    )

    p.add_argument(
        '--adapter',
        choices=['stub', 'deepfaune'],
        default='deepfaune',
        help="Which classifier to use. 'stub' — for tests without a model. "
             "Default: deepfaune.",
    )
    p.add_argument(
        '--upload-path',
        help='Override CAMERA_TRAP_UPLOAD_PATH (where the photos are stored)',
    )
    p.add_argument(
        '--threshold',
        type=float,
        help='Override AI_RUNNER_THRESHOLD (confidence threshold)',
    )
    p.add_argument(
        '-v', '--verbose',
        action='store_true',
        help='DEBUG logs',
    )

    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%H:%M:%S',
    )

    # ── Configuration ─────────────────────────────────────────────────
    upload_path = args.upload_path or os.environ.get('CAMERA_TRAP_UPLOAD_PATH')
    if not upload_path:
        print(
            'ERROR: --upload-path or env CAMERA_TRAP_UPLOAD_PATH is required',
            file=sys.stderr,
        )
        return 1

    if not os.environ.get('CT_DATABASE_URL'):
        print('ERROR: env CT_DATABASE_URL is not defined', file=sys.stderr)
        return 1

    # ── Pause gate: skip the whole run if a camera-trap upload is in progress ─
    # An upload sets a short pause lease (ai_control.pause_until). While it is
    # active we load nothing and touch no queue, so heavy classification does not
    # compete with the upload for DB/CPU/RAM. For --from-queue we return WITHOUT
    # draining the pending request, so it is picked up on a later tick once the
    # pause lifts. The lease expires on its own if the uploader dies, so this can
    # never wedge the worker permanently. See app/camera_traps/ai_runner.py.
    from .db import is_ai_paused
    if is_ai_paused():
        logger.info('AI is paused (camera-trap upload in progress). Skipping run.')
        return 0

    threshold = (
        args.threshold
        if args.threshold is not None
        else float(os.environ.get('AI_RUNNER_THRESHOLD', '0.8'))
    )

    # ── Early-exit: do NOT load the model if there is no work ─────────
    # DeepFaune ViT takes ~30 s + 2 GB RAM to load. Check BEFORE
    # build_adapter() whether there is anything to process at all. Saves CPU/RAM
    # on frequent cron ticks with an empty queue (especially */3 min for the queue).
    from .db import (
        count_pending_observations,
        has_pending_queue_request,
        drain_one_empty_queue_request,
    )

    if args.from_queue:
        if not has_pending_queue_request():
            logger.info('Queue is empty — nothing to do, skipping model load.')
            return 0
        pending = count_pending_observations()
        if pending == 0:
            drained = drain_one_empty_queue_request()
            logger.info(
                f'Queue has entry but no pending observations. '
                f'Drained {drained} queue request(s) as done(0). Model not loaded.'
            )
            return 0
        logger.info(f'Queue has entry, {pending} pending observations. Loading model...')
    else:
        # --batch=N mode
        pending = count_pending_observations()
        if pending == 0:
            logger.info('No pending observations to classify. Skipping model load.')
            return 0
        logger.info(f'{pending} pending observations. Loading model for batch={args.batch}...')

    # ── Adapter (heavy operation — only when there is definitely work) ─
    try:
        adapter = build_adapter(args.adapter, threshold)
    except RuntimeError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        return 1

    logger.info(
        f"Adapter: {adapter.name} {adapter.version} | "
        f"upload_path={upload_path} | threshold={threshold}"
    )

    # ── Execution ──────────────────────────────────────────────────────
    try:
        if args.from_queue:
            result = run_from_queue(adapter, upload_path)
            if result is None:
                logger.info('Queue is empty')
            else:
                logger.info(f'Queue request done. Processed: {result}')
        else:
            # --batch=N: write to ai_run_queue with requested_by=0 (cron/system),
            # so it is visible on the admin page alongside manual requests
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
