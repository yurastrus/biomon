#!/bin/bash
# SPDX-License-Identifier: AGPL-3.0-only
#
# Verify last night's backup artefacts and report to Telegram + Discord.
#
# Two modes:
#   (no args)     verify now, send ✅ summary or 🔴 failure. Called at the end of
#                 /usr/local/bin/full_backup.sh, after the rclone sync.
#   --watchdog    send 🔴 if no successful verification happened today. Runs from
#                 its own cron entry a couple of hours after the backup window,
#                 so a backup that never started is still reported. Silent when
#                 a fresh success stamp exists.
#   --dry-run     print the report instead of sending it (any mode).
#
# What "verified" means here. The checks are deliberately cheap — no dump is
# unpacked, nothing is restored:
#   * every non-template database has a dump from today, plus globals_roles;
#   * every archive starts with the gzip magic 1f 8b, so it is a gzip at all;
#   * archives up to 50 MiB get a real `gzip -t` (CRC32 over the whole stream);
#   * larger ones are checked structurally instead — gzip magic plus a non-zero
#     trailer, which is only written when the stream is closed properly — so the
#     job never spends minutes unpacking the 900 MB geodata dump. ISIZE is stored
#     modulo 2^32, so it is never compared against the file size;
#   * nothing is 0 bytes and nothing is still being written (mtime > 60 s ago);
#   * every camera-trap CSV matches the sha256 in its own manifest.json — those
#     total ~8 MB, so hashing them outright is both cheap and conclusive.
# A dump that is intact but semantically wrong is out of scope: catching that
# needs a restore, which is a different job on a different schedule.
#
# Credentials are never stored here — this file lives in a public repository.
# They are read from, in order of preference:
#   /etc/alert-webhook.conf   (root-only)  DISCORD_WEBHOOK_SERVER, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
#   /var/www/biomon/.env      (fallback)   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
# A missing channel is skipped silently; a report with no channel at all is
# printed to stdout so it still lands in the backup log.
#
# Install:
#   sudo install -m 755 /var/www/biomon/deploy/backup_report.sh /usr/local/bin/
#   sudo crontab -u yura -l | ...  (see the watchdog line at the bottom)

set -uo pipefail

BACKUP_ROOT="${BACKUP_ROOT:-/home/yura/backups}"
POSTGRES_DIR="$BACKUP_ROOT/postgres"
GEOSERVER_DIR="$BACKUP_ROOT/geoserver"
CSV_DIR="$BACKUP_ROOT/phototraps_data"
STAMP_FILE="${BACKUP_STAMP_FILE:-$BACKUP_ROOT/.last_verified_backup}"
BIOMON_ENV="${BIOMON_ENV:-/var/www/biomon/.env}"
WEBHOOK_CONF="${WEBHOOK_CONF:-/etc/alert-webhook.conf}"

# A file younger than this is assumed to be mid-write, not finished.
MIN_AGE_SECONDS="${MIN_AGE_SECONDS:-60}"
# Anything smaller than this is treated as empty rather than "small".
MIN_ARCHIVE_BYTES="${MIN_ARCHIVE_BYTES:-1024}"
# Archives up to this size get a real gzip -t; bigger ones only a structural
# check, so the nightly job never spends minutes decompressing geodata.
GZIP_TEST_MAX_BYTES="${GZIP_TEST_MAX_BYTES:-52428800}"   # 50 MiB

MODE="report"
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --watchdog) MODE="watchdog" ;;
        --dry-run)  DRY_RUN=1 ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done

TODAY=$(date +%Y%m%d)
HOST=$(hostname)
PROBLEMS=()
LINES=()
LARGE_UNVERIFIED=()

human() { numfmt --to=iec --suffix=B "${1:-0}" 2>/dev/null || echo "${1:-0}B"; }

# ── credentials ──────────────────────────────────────────────────────────────
[ -r "$WEBHOOK_CONF" ] && . "$WEBHOOK_CONF"
if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] && [ -r "$BIOMON_ENV" ]; then
    TELEGRAM_BOT_TOKEN=$(grep -m1 '^TELEGRAM_BOT_TOKEN=' "$BIOMON_ENV" | cut -d= -f2- | tr -d '"'"'"' \r')
    TELEGRAM_CHAT_ID=$(grep -m1 '^TELEGRAM_CHAT_ID=' "$BIOMON_ENV" | cut -d= -f2- | tr -d '"'"'"' \r')
fi

send() {
    local text="$1" sent=0
    if [ "$DRY_RUN" = "1" ]; then
        printf '%s\n' "$text"
        return 0
    fi
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        curl -s -m 20 -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="${TELEGRAM_CHAT_ID}" --data-urlencode "text=${text}" >/dev/null && sent=1
    fi
    if [ -n "${DISCORD_WEBHOOK_SERVER:-}" ]; then
        printf '%s' "$text" \
            | python3 -c 'import json,sys; print(json.dumps({"content": sys.stdin.read()[:1900]}))' \
            | curl -s -m 20 -X POST -H "Content-Type: application/json" -d @- \
              "${DISCORD_WEBHOOK_SERVER}" >/dev/null && sent=1
    fi
    # No channel configured is itself worth seeing, and the caller redirects
    # stdout into the backup log.
    [ "$sent" = "1" ] || printf 'no alert channel configured; report follows\n%s\n' "$text"
}

# ── watchdog ─────────────────────────────────────────────────────────────────
# Runs long after the backup window. Its whole job is the case the reporting
# path cannot cover: full_backup.sh never started, died before reaching the
# report, or the machine was off.
if [ "$MODE" = "watchdog" ]; then
    if [ -r "$STAMP_FILE" ] && [ "$(cat "$STAMP_FILE" 2>/dev/null)" = "$TODAY" ]; then
        exit 0
    fi
    last=$(cat "$STAMP_FILE" 2>/dev/null || echo "ніколи")
    send "🔴 БЕКАП НЕ ПІДТВЕРДЖЕНО на ${HOST}
Сьогодні (${TODAY}) не було успішної перевірки бекапу.
Остання успішна: ${last}
Схоже, full_backup.sh не запустився або впав до етапу перевірки.
Дивись /home/yura/backups/full_backup.log"
    exit 1
fi

# ── gzip sanity, without decompressing ───────────────────────────────────────
check_archive() {
    local path="$1" label="$2"
    local size age isize

    size=$(stat -c%s "$path" 2>/dev/null || echo 0)
    if [ "$size" -lt "$MIN_ARCHIVE_BYTES" ]; then
        PROBLEMS+=("$label: розмір ${size} B — порожній або обрізаний")
        return 1
    fi

    age=$(( $(date +%s) - $(stat -c%Y "$path") ))
    if [ "$age" -lt "$MIN_AGE_SECONDS" ]; then
        PROBLEMS+=("$label: змінений ${age} с тому — можливо, ще пишеться")
        return 1
    fi

    # gzip magic
    if [ "$(head -c2 "$path" | od -An -tx1 | tr -d ' \n')" != "1f8b" ]; then
        PROBLEMS+=("$label: не gzip (немає сигнатури 1f8b)")
        return 1
    fi

    if [ "$size" -le "$GZIP_TEST_MAX_BYTES" ]; then
        # Small enough to verify properly: gzip -t walks the whole stream and
        # checks the CRC32. That is the only way to be certain.
        if ! gzip -t "$path" 2>/dev/null; then
            PROBLEMS+=("$label: gzip -t не пройшов — архів пошкоджено")
            return 1
        fi
    else
        # Too big to decompress inside a nightly job. Fall back to the trailer:
        # the last 4 bytes hold the uncompressed size and are written only when
        # the stream is closed, so a zero there means the writer never finished.
        #
        # Deliberately NOT compared against the compressed size. ISIZE is stored
        # modulo 2^32 and geodata unpacks to more than 4 GiB, so the counter
        # wraps — that comparison flagged a perfectly good 906 MB archive.
        isize=$(tail -c4 "$path" | od -An -tu4 | tr -d ' \n')
        if [ -z "$isize" ] || [ "$isize" -le 0 ]; then
            PROBLEMS+=("$label: порожній gzip-трейлер — архів обірвано")
            return 1
        fi
        LARGE_UNVERIFIED+=("$label")
    fi

    LINES+=("  ✓ $label — $(human "$size")")
    TOTAL_BYTES=$(( TOTAL_BYTES + size ))
    return 0
}

TOTAL_BYTES=0

# ── 1. PostgreSQL dumps ──────────────────────────────────────────────────────
LINES+=("📦 Дампи БД:")
DB_NAMES=$(sudo -n -u postgres psql -t -A -c \
    "SELECT datname FROM pg_database WHERE NOT datistemplate AND datname <> 'postgres';" 2>/dev/null)
if [ -z "$DB_NAMES" ]; then
    PROBLEMS+=("не вдалося отримати список баз у postgres")
else
    while read -r db; do
        [ -n "$db" ] || continue
        newest=$(ls -t "$POSTGRES_DIR/${db}_backup_${TODAY}"*.sql.gz 2>/dev/null | head -1)
        if [ -z "$newest" ]; then
            PROBLEMS+=("нема сьогоднішнього дампу бази $db")
            continue
        fi
        check_archive "$newest" "$db"
    done <<< "$DB_NAMES"
fi

globals=$(ls -t "$POSTGRES_DIR/globals_roles_${TODAY}"*.sql.gz 2>/dev/null | head -1)
if [ -z "$globals" ]; then
    PROBLEMS+=("нема сьогоднішнього дампу глобальних ролей")
else
    check_archive "$globals" "globals_roles"
fi

# ── 2. GeoServer ─────────────────────────────────────────────────────────────
geo=$(ls -t "$GEOSERVER_DIR/"*"${TODAY}"*.tar.gz 2>/dev/null | head -1)
LINES+=("🗺 GeoServer:")
if [ -z "$geo" ]; then
    PROBLEMS+=("нема сьогоднішнього архіву GeoServer")
else
    check_archive "$geo" "$(basename "$geo")"
fi

# ── 3. Camera-trap CSVs ──────────────────────────────────────────────────────
# Each institution folder carries a manifest with the sha256 of its current CSV.
# Re-hashing ~8 MB is fast and proves the file on disk is the file that was
# written, not a truncated copy.
LINES+=("🦌 CSV фотопасток:")
csv_ok=0; csv_bytes=0
if [ ! -d "$CSV_DIR" ]; then
    PROBLEMS+=("нема каталогу $CSV_DIR")
else
    for manifest in "$CSV_DIR"/*/manifest.json; do
        [ -e "$manifest" ] || continue
        folder=$(dirname "$manifest")
        park=$(basename "$folder")
        want_file=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["file"])' "$manifest" 2>/dev/null)
        want_hash=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha256"])' "$manifest" 2>/dev/null)
        if [ -z "$want_file" ] || [ -z "$want_hash" ]; then
            PROBLEMS+=("$park: нечитабельний manifest.json")
            continue
        fi
        target="$folder/$want_file"
        if [ ! -f "$target" ]; then
            PROBLEMS+=("$park: маніфест указує на $want_file, якого немає")
            continue
        fi
        got_hash=$(sha256sum "$target" | cut -d' ' -f1)
        if [ "$got_hash" != "$want_hash" ]; then
            PROBLEMS+=("$park: sha256 не збігається з маніфестом — файл пошкоджено")
            continue
        fi
        csv_ok=$(( csv_ok + 1 ))
        csv_bytes=$(( csv_bytes + $(stat -c%s "$target") ))
    done
    if [ "$csv_ok" -eq 0 ]; then
        PROBLEMS+=("жодного перевіреного CSV фотопасток")
    else
        LINES+=("  ✓ ${csv_ok} установ, $(human "$csv_bytes"), sha256 збігається")
        TOTAL_BYTES=$(( TOTAL_BYTES + csv_bytes ))
    fi
fi

if [ "${#LARGE_UNVERIFIED[@]}" -gt 0 ]; then
    LINES+=("  ℹ великі архіви перевірено структурно, без розпакування: $(IFS=', '; echo "${LARGE_UNVERIFIED[*]}")")
fi

# ── 4. Disk headroom ─────────────────────────────────────────────────────────
VOL=$(df -h --output=target,pcent,avail "$BACKUP_ROOT" 2>/dev/null | tail -1)
LINES+=("💾 Диск: $(echo "$VOL" | awk '{print $2" зайнято, "$3" вільно"}')")

# ── report ───────────────────────────────────────────────────────────────────
if [ "${#PROBLEMS[@]}" -eq 0 ]; then
    echo "$TODAY" > "$STAMP_FILE"
    send "✅ Бекап успішний на ${HOST} ($(date '+%Y-%m-%d %H:%M'))
Разом: $(human "$TOTAL_BYTES")

$(printf '%s\n' "${LINES[@]}")"
    exit 0
else
    send "🔴 ПРОБЛЕМА З БЕКАПОМ на ${HOST} ($(date '+%Y-%m-%d %H:%M'))

Не пройшло перевірку:
$(printf '  ✗ %s\n' "${PROBLEMS[@]}")

Що вдалося:
$(printf '%s\n' "${LINES[@]}")

Лог: /home/yura/backups/full_backup.log"
    exit 1
fi

# Cron for the watchdog (yura), 05:00 UTC — 2.5 h after the backup window:
#   0 5 * * * /usr/local/bin/backup_report.sh --watchdog >> /home/yura/backups/cron_backup.log 2>&1
