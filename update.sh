#!/bin/bash
# update.sh — update biomon on the server.
# Run from the deployment directory (e.g. /var/www/biomon):
#   ./update.sh

set -e  # stop on any error

echo "--- Starting biomon update ---"

# 1. Update the main project
git fetch origin
git reset --hard origin/master

# 2. Update all submodules (PAM, camera traps, SDM)
git submodule update --init --recursive --force

# 3. Install/update Python dependencies (if changed)
venv/bin/pip install -q -r requirements.txt

# 4. Apply main-database migrations (Alembic; idempotent at head)
# Deliberately NOT tolerant of failure: `set -e` aborts before the restart, so a
# schema that does not match the deployed code never reaches gunicorn. Skipping
# this step is what took the site down on 2026-08-19 (see WORKLOG).
venv/bin/flask db upgrade

# 5. Apply SDM migrations (idempotent — does nothing if already at head)
venv/bin/flask sdm migrate --apply || echo "[!] SDM migrate returned an error — check manually"

# 6. Clear the Python cache
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# 7. Restart gunicorn (biomon)
sudo systemctl restart biomon

# 8. Restart the SDM worker (if installed)
if systemctl list-unit-files sdm-worker.service &>/dev/null; then
    sudo systemctl restart sdm-worker
    echo "[v] sdm-worker restarted"
else
    echo "[i] sdm-worker not installed yet — skipping"
fi

echo "--- Update complete ---"
echo ""
sudo systemctl status biomon --no-pager -l
echo ""
sudo systemctl status sdm-worker --no-pager -l
