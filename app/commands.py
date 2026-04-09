# app/commands.py
"""
Flask CLI-команди для адміністративних задач.

Реєстрація: викликається з app/__init__.py через register_commands(app).
"""
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
