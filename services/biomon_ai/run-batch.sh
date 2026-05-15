#!/bin/bash
# Cron-обгортка для нічного batch-прогону AI-класифікації.
# Викликається cron'ом — НЕ запускати руками з biomon-репо!
# Цільове розташування на сервері: /opt/biomon-ai/run-batch.sh
# (як symlink або копія цього файла — див. DEPLOY.md §1, §9).
#
# Запускається з найнижчим CPU/IO-пріоритетом, обмежені CPU-потоки —
# щоб не валити сервер. Зайде хто на сайт під час прогону — gunicorn
# відкине AI на бек, веб залишиться чуйним. Швидкість — ~10 сек/серія.

set -e

cd /opt/biomon-ai

if [ ! -f .env ]; then
    echo "ERROR: /opt/biomon-ai/.env not found. See DEPLOY.md §6." >&2
    exit 1
fi
set -a
source .env
set +a

# Обмеження PyTorch / BLAS / OpenMP CPU-потоків.
# На 4-ядерному сервері виставляємо 2 — половина CPU йде воркеру,
# половина залишається веб-додатку + GeoServer + PostgreSQL.
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-2}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-2}"
export TORCH_NUM_THREADS="${TORCH_NUM_THREADS:-2}"

# flock -n        — non-blocking lock. Якщо попередній batch ще біжить,
#                   новий запуск ВІДРАЗУ вийде з exit 1 без помилки.
#                   Це дозволяє запускати batch часто (напр. кожні 30хв
#                   уночі) без ризику паралельних прогонів і OOM.
# nice -n 19      — найнижчий CPU-пріоритет, будь-що випередить
# ionice -c 3     — idle класс IO, не блокує дисковий I/O
exec flock -n /tmp/biomon-ai-batch.lock \
    nice -n 19 ionice -c 3 venv/bin/python -m biomon_ai.cli \
    --batch="${AI_RUNNER_MAX_PER_RUN:-100}" \
    --adapter=deepfaune
