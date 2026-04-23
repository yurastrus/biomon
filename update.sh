#!/bin/bash
# update.sh — оновлення biomon на сервері.
# Запускати з /var/www/biomon:
#   ./update.sh

set -e  # зупинитись при будь-якій помилці

echo "--- Починаю оновлення biomon ---"

# 1. Оновлюємо головний проект
git pull origin master

# 2. Оновлюємо всі підмодулі (ПАМ, Фотопастки, SDM)
git submodule update --init --recursive

# 3. Встановлюємо/оновлюємо Python-залежності (якщо змінились)
venv/bin/pip install -q -r requirements.txt

# 4. Застосовуємо SDM-міграції (idempotent — якщо вже на head, нічого не робить)
venv/bin/flask sdm migrate --apply || echo "[!] SDM migrate повернув помилку — перевір вручну"

# 5. Очищуємо кеш Python
find . -name "__pycache__" -type d -exec rm -rf {} + 2>/dev/null || true

# 6. Перезавантажуємо gunicorn (biomon)
sudo systemctl restart biomon

# 7. Перезавантажуємо SDM worker (graceful: SIGTERM → завершує задачу → рестарт)
sudo systemctl restart sdm-worker

echo "--- Оновлення завершено ---"
echo ""
sudo systemctl status biomon --no-pager -l
echo ""
sudo systemctl status sdm-worker --no-pager -l
