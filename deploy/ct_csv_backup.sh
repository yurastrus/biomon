#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only
#
# Second-tier backup: per-institution CSV export of camera-trap data.
#
# Writes <BACKUP_ROOT>/phototraps_data/<Park_Name>/<CODE>_ct_occurrence_<date>.csv.
# That path sits inside the directory full_backup.sh already mirrors to Google
# Drive, so the CSVs reach Drive on the existing rclone sync — this script does
# not upload anything itself.
#
# Install (once, as root):
#     install -m 755 /var/www/biomon/deploy/ct_csv_backup.sh /usr/local/bin/
#
# Wire into the nightly pipeline by adding this block to
# /usr/local/bin/full_backup.sh, AFTER the GeoServer section and BEFORE
# section 5 (the rclone sync) — the CSVs must exist before the mirror runs:
#
#     # ==========================================
#     # 4b. CSV-БЕКАП ДАНИХ ФОТОПАСТОК
#     # ==========================================
#     log "4b. Експортую CSV даних фотопасток по установах..."
#     if /usr/local/bin/ct_csv_backup.sh >> "$LOG_FILE" 2>&1; then
#         log "✓ CSV-експорт фотопасток завершено"
#     else
#         log "⚠ УВАГА: CSV-експорт фотопасток завершився з помилкою"
#         # Навмисно НЕ чіпаємо BACKUP_SUCCESS: дампи БД — головний рівень,
#         # і збій вторинного CSV не має скасовувати синхронізацію з Drive.
#     fi
#
# Standalone cron alternative (if you would rather keep full_backup.sh untouched,
# 02:25 UTC — five minutes before the dump job):
#     25 2 * * * /usr/local/bin/ct_csv_backup.sh >> /home/yura/backups/ct_csv_backup.log 2>&1

set -uo pipefail

APP_DIR="${BIOMON_DIR:-/var/www/biomon}"
VENV_FLASK="$APP_DIR/venv/bin/flask"
LOG_PREFIX="[ct-csv-backup]"

log() { echo "$LOG_PREFIX [$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

if [ ! -x "$VENV_FLASK" ]; then
    log "ПОМИЛКА: не знайдено $VENV_FLASK"
    exit 1
fi

cd "$APP_DIR" || { log "ПОМИЛКА: немає доступу до $APP_DIR"; exit 1; }

log "старт"
# `flask ct-csv-backup` exits non-zero when at least one institution failed;
# institutions that succeeded are already written, so the exit code is a signal,
# not a rollback.
# FLASK_APP comes from $APP_DIR/.env, which the flask CLI loads itself.
"$VENV_FLASK" ct-csv-backup "$@"
STATUS=$?

if [ $STATUS -eq 0 ]; then
    log "готово"
else
    log "завершено з помилками (код $STATUS)"
fi
exit $STATUS
