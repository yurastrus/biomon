#!/bin/bash
# Cron wrapper for the nightly batch run of AI classification.
# Invoked by cron — do NOT run by hand from the biomon repo!
# Target location on the server: /opt/biomon-ai/run-batch.sh
# (as a symlink or copy of this file — see DEPLOY.md §1, §9).
#
# Runs at the lowest CPU/IO priority with a limited number of CPU threads
# so it does not overload the server. If someone visits the site during a
# run, gunicorn keeps the AI work in the background and the web stays
# responsive. Throughput is roughly ~10 sec/series.

set -e

cd /opt/biomon-ai

if [ ! -f .env ]; then
    echo "ERROR: /opt/biomon-ai/.env not found. See DEPLOY.md §6." >&2
    exit 1
fi
set -a
source .env
set +a

# Limit PyTorch / BLAS / OpenMP CPU threads.
# On a 4-core server we set 2 — half the CPU goes to the worker,
# half stays for the web app + GeoServer + PostgreSQL.
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-2}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-2}"
export TORCH_NUM_THREADS="${TORCH_NUM_THREADS:-2}"

# flock -n        — non-blocking lock. If the previous batch is still running,
#                   a new invocation exits immediately with exit 1 (no error).
#                   This lets the batch run frequently (e.g. every 30 min at
#                   night) without risking parallel runs and OOM.
# nice -n 19      — lowest CPU priority; anything else preempts it
# ionice -c 3     — idle IO class; does not block disk I/O
exec flock -n /tmp/biomon-ai-batch.lock \
    nice -n 19 ionice -c 3 venv/bin/python -m biomon_ai.cli \
    --batch="${AI_RUNNER_MAX_PER_RUN:-100}" \
    --adapter=deepfaune
