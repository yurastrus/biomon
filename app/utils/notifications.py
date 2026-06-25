# SPDX-License-Identifier: AGPL-3.0-only
"""Outbound notifications (Telegram).

Reuses the bot the server already uses for state alerts; the bot token and
target chat are read from config (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID) so no
secret lives in the source tree.
"""
from threading import Thread

import requests
from flask import current_app


def _post_to_telegram(app, token, chat_id, text):
    """Send one message in a background thread; never raise into the request."""
    with app.app_context():
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
                      "disable_web_page_preview": True},
                timeout=10,
            )
            if resp.status_code != 200:
                current_app.logger.warning(
                    "Telegram notify failed: HTTP %s %s", resp.status_code, resp.text[:300]
                )
        except Exception as e:  # network/DNS/timeout — must not break the form
            current_app.logger.warning("Telegram notify error: %s", e)


def send_telegram_notification(text):
    """Fire-and-forget a Telegram message to the configured chat.

    No-op (with a log line) when the bot is not configured, so dev/test
    environments without a token still work.
    """
    app = current_app._get_current_object()
    token = app.config.get("TELEGRAM_BOT_TOKEN")
    chat_id = app.config.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        app.logger.info("Telegram not configured — skipping notification")
        return
    Thread(target=_post_to_telegram, args=(app, token, chat_id, text), daemon=True).start()
