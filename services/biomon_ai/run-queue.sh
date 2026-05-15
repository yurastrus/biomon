#!/bin/bash
# Cron-обгортка для опрацювання адмін-черги ai_run_queue.
# Запускається кожні 2-3 хвилини (див. DEPLOY.md §10).
# Якщо черга порожня — миттєво виходить.
# Цільове розташування на сервері: /opt/biomon-ai/run-queue.sh

set -e

cd /opt/biomon-ai

if [ ! -f .env ]; then
    echo "ERROR: /opt/biomon-ai/.env not found. See DEPLOY.md §6." >&2
    exit 1
fi
set -a
source .env
set +a

exec venv/bin/python -m biomon_ai.cli --from-queue --adapter=deepfaune
