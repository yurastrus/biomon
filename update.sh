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

# 4. Apply SDM migrations (idempotent — does nothing if already at head)
venv/bin/flask sdm migrate --apply || echo "[!] SDM migrate returned an error — check manually"

# 5. Clear the Python cache
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# 6. Restart gunicorn (biomon)
sudo systemctl restart biomon

# 7. Restart the SDM worker (if installed)
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
