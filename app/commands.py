# app/commands.py
"""
Flask CLI-команди для адміністративних задач.

Реєстрація: викликається з app/__init__.py через register_commands(app).
"""
import os
import sys
from pathlib import Path

import click


def register_commands(app):

    @app.cli.command('send-id-reminders')
    def send_id_reminders():
        """Надсилає тижневі email-нагадування про непроідентифіковані серії.

        Використання:
            flask send-id-reminders

        Налаштування cron (щопонеділка о 9:00):
            0 9 * * 1 cd /var/www/biomon && venv/bin/flask send-id-reminders >> /var/log/biomon_reminders.log 2>&1
        """
        from app.camera_traps.notifications import send_identification_reminders
        click.echo("Перевіряю непроідентифіковані серії та надсилаю нагадування...")
        sent, skipped = send_identification_reminders()
        click.echo(f"Готово: надіслано {sent} листів, {skipped} користувачів пропущено (немає серій).")

    # ──────────────────────────────────────────────────────────────
    # SDM CLI-команди (flask sdm check / build-grid / ...)
    # ──────────────────────────────────────────────────────────────
    #
    # Підтримуємо два варіанти розміщення shared-sdm:
    #   1. Git submodule: app/sdm/          ← рекомендовано
    #   2. Сусідній репозиторій: ../shared-sdm  ← тимчасово
    #
    # Перший знайдений шлях додається у sys.path.
    _sdm_candidates = [
        Path(__file__).resolve().parent / "sdm",          # submodule: app/sdm/
        Path(__file__).resolve().parents[2] / "shared-sdm",  # sibling repo
    ]
    _shared_sdm_root = next((p for p in _sdm_candidates if p.is_dir()), None)
    if _shared_sdm_root and str(_shared_sdm_root) not in sys.path:
        sys.path.insert(0, str(_shared_sdm_root))

    try:
        from adapters.biomon_cli import register as register_sdm_cli
        register_sdm_cli(app)
    except ImportError as e:
        # Не валимо весь biomon, якщо shared-sdm ще не готовий — просто
        # попереджаємо у консоль при старті.
        app.logger.warning(
            "SDM CLI не зареєстровано (%s). "
            "Перевірте, що shared-sdm знаходиться поруч з biomon.",
            e,
        )
