#!/bin/bash
# Cron wrapper for processing the admin queue ai_run_queue.
# Runs every 2-3 minutes (see DEPLOY.md §10).
# Exits immediately if the queue is empty.
# Target location on the server: /opt/biomon-ai/run-queue.sh

set -e

cd /opt/biomon-ai

if [ ! -f .env ]; then
    echo "ERROR: /opt/biomon-ai/.env not found. See DEPLOY.md §6." >&2
    exit 1
fi
set -a
source .env
set +a

# Limit CPU threads + lowest priority (see the comment in run-batch.sh).
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-2}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-2}"
export TORCH_NUM_THREADS="${TORCH_NUM_THREADS:-2}"

exec nice -n 19 ionice -c 3 venv/bin/python -m biomon_ai.cli \
    --from-queue --adapter=deepfaune
