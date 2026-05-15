#!/bin/bash
# Cron-обгортка для нічного batch-прогону AI-класифікації.
# Викликається cron'ом — НЕ запускати руками з vbiomon-репо!
# Цільове розташування на сервері: /opt/biomon-ai/run-batch.sh
# (як symlink або копія цього файла — див. DEPLOY.md §1, §9).
#
# Експортує env-змінні з /opt/biomon-ai/.env і запускає worker.
# Exit code worker'а пропагується назовні (0 — OK, 2 — критична помилка).

set -e

cd /opt/biomon-ai

# Завантажуємо змінні з .env (CT_DATABASE_URL, CAMERA_TRAP_UPLOAD_PATH тощо)
if [ ! -f .env ]; then
    echo "ERROR: /opt/biomon-ai/.env not found. See DEPLOY.md §6." >&2
    exit 1
fi
set -a
source .env
set +a

# AI_RUNNER_MAX_PER_RUN — ліміт серій за один прогін (default 100, з .env)
exec venv/bin/python -m biomon_ai.cli \
    --batch="${AI_RUNNER_MAX_PER_RUN:-100}" \
    --adapter=deepfaune
