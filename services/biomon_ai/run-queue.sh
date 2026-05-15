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

# Обмеження CPU-потоків + найнижчий пріоритет (див. run-batch.sh §коментар).
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-2}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-2}"
export TORCH_NUM_THREADS="${TORCH_NUM_THREADS:-2}"

exec nice -n 19 ionice -c 3 venv/bin/python -m biomon_ai.cli \
    --from-queue --adapter=deepfaune
