# SPDX-License-Identifier: AGPL-3.0-only
"""Outbound notifications (Telegram + Discord).

Every notification goes to BOTH messengers, so neither is a single point of
failure. Telegram uses the bot the server already uses for state alerts;
Discord uses per-channel incoming webhooks (no bot token, no gateway).

Routing (see CH_* below): technical/infrastructure events go to the #server
channel, messages written by humans on biomon.app go to #biomon. Telegram has
no such split — it is one chat — so the text carries its own heading.

All credentials come from config, so no secret lives in the source tree:
  TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
  DISCORD_WEBHOOK_SERVER / DISCORD_WEBHOOK_BIOMON
Any target that is not configured is skipped with a log line, so dev/test
environments keep working with no credentials at all.
"""
import re
from html import unescape
from threading import Thread

import requests
from flask import current_app

# Notification channels (Discord). Telegram ignores this — it has one chat.
CH_SERVER = "server"   # інфраструктура, стан сервісів, технічні події
CH_BIOMON = "biomon"   # написане людьми на biomon.app (форма зв'язку, запити доступу)

# channel -> config key holding the Discord webhook URL
_WEBHOOK_KEYS = {
    CH_SERVER: "DISCORD_WEBHOOK_SERVER",
    CH_BIOMON: "DISCORD_WEBHOOK_BIOMON",
}

TELEGRAM_LIMIT = 4096
DISCORD_LIMIT = 2000


def _to_discord(text):
    """Telegram-flavoured HTML -> Discord markdown.

    Call sites compose one string with <b>/<i>/<code> tags and HTML-escaped
    values (Telegram's parse_mode=HTML). Discord renders markdown instead, so
    the tags are translated and the entities unescaped — otherwise the user
    would literally read "<b>Email:</b>" and "&#39;".
    """
    out = re.sub(r"</?b>", "**", text)
    out = re.sub(r"</?i>", "*", out)
    out = re.sub(r"</?code>", "`", out)
    out = re.sub(r"<[^>]+>", "", out)  # any other tag: drop, never show raw
    return unescape(out)


def _truncate(text, limit):
    """Both APIs reject over-long bodies; a trimmed alert beats a dropped one."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _send_telegram(token, chat_id, text):
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": _truncate(text, TELEGRAM_LIMIT),
              "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=10,
    )
    if resp.status_code != 200:
        current_app.logger.warning(
            "Telegram notify failed: HTTP %s %s", resp.status_code, resp.text[:300]
        )


def _send_discord(webhook_url, text):
    resp = requests.post(
        webhook_url,
        json={"content": _truncate(_to_discord(text), DISCORD_LIMIT)},
        timeout=10,
    )
    # Webhooks answer 204 No Content on success.
    if resp.status_code not in (200, 204):
        current_app.logger.warning(
            "Discord notify failed: HTTP %s %s", resp.status_code, resp.text[:300]
        )


def _deliver(app, text, telegram, webhook_url):
    """Run both deliveries in one background thread; never raise into the request.

    Each target is guarded separately so a failing Telegram does not cancel the
    Discord copy (that redundancy is the whole point of sending to both).
    """
    with app.app_context():
        if telegram:
            try:
                _send_telegram(telegram[0], telegram[1], text)
            except Exception as e:  # network/DNS/timeout
                current_app.logger.warning("Telegram notify error: %s", e)
        if webhook_url:
            try:
                _send_discord(webhook_url, text)
            except Exception as e:
                current_app.logger.warning("Discord notify error: %s", e)


def send_notification(text, channel=CH_SERVER):
    """Fire-and-forget one notification to Telegram and to a Discord channel.

    `channel` selects the Discord target (CH_SERVER / CH_BIOMON). Unconfigured
    targets are skipped, so a missing webhook never breaks the request path.
    """
    app = current_app._get_current_object()

    token = app.config.get("TELEGRAM_BOT_TOKEN")
    chat_id = app.config.get("TELEGRAM_CHAT_ID")
    telegram = (token, chat_id) if token and chat_id else None

    key = _WEBHOOK_KEYS.get(channel)
    if key is None:
        app.logger.warning("Unknown notification channel %r — falling back to %s",
                           channel, CH_SERVER)
        key = _WEBHOOK_KEYS[CH_SERVER]
    webhook_url = app.config.get(key)

    if not telegram and not webhook_url:
        app.logger.info("No notification target configured — skipping")
        return
    if not telegram:
        app.logger.info("Telegram not configured — sending to Discord only")
    if not webhook_url:
        app.logger.info("Discord webhook %s not configured — sending to Telegram only", key)

    Thread(target=_deliver, args=(app, text, telegram, webhook_url), daemon=True).start()
