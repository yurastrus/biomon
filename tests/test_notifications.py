# SPDX-License-Identifier: AGPL-3.0-only
"""Outbound notifications: dual delivery (Telegram + Discord) and routing.

The sender runs in a daemon thread, so every test joins the threads it spawned
before asserting — otherwise the assertions race the delivery.
"""
import threading
from unittest.mock import patch

import pytest

from app.utils.notifications import (CH_BIOMON, CH_SERVER, _to_discord,
                                     send_notification)

TG_URL = "https://api.telegram.org/bot"
HOOK_SERVER = "https://discord.com/api/webhooks/1/server"
HOOK_BIOMON = "https://discord.com/api/webhooks/2/biomon"


@pytest.fixture
def configured(app):
    app.config.update(
        TELEGRAM_BOT_TOKEN="tok",
        TELEGRAM_CHAT_ID="123",
        DISCORD_WEBHOOK_SERVER=HOOK_SERVER,
        DISCORD_WEBHOOK_BIOMON=HOOK_BIOMON,
    )
    return app


class FakeResp:
    def __init__(self, status_code=204, text=""):
        self.status_code = status_code
        self.text = text


def _capture():
    """Patch requests.post and return the list it records calls into."""
    calls = []

    def _post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return FakeResp(200 if url.startswith(TG_URL) else 204)

    return calls, patch("app.utils.notifications.requests.post", side_effect=_post)


def _send_and_join(app, text, **kwargs):
    """Send, then wait for the delivery thread so assertions are not racy."""
    before = set(threading.enumerate())
    with app.test_request_context():
        send_notification(text, **kwargs)
    for t in set(threading.enumerate()) - before:
        t.join(timeout=5)


def test_sends_to_both_messengers(configured):
    calls, patcher = _capture()
    with patcher:
        _send_and_join(configured, "плаїн текст")
    urls = [c["url"] for c in calls]
    assert any(u.startswith(TG_URL) for u in urls), "Telegram не отримав повідомлення"
    assert HOOK_SERVER in urls, "Discord не отримав повідомлення"


def test_channel_routing(configured):
    calls, patcher = _capture()
    with patcher:
        _send_and_join(configured, "звернення", channel=CH_BIOMON)
    urls = [c["url"] for c in calls]
    assert HOOK_BIOMON in urls
    assert HOOK_SERVER not in urls


def test_default_channel_is_server(configured):
    calls, patcher = _capture()
    with patcher:
        _send_and_join(configured, "технічне")
    assert HOOK_SERVER in [c["url"] for c in calls]


def test_html_becomes_markdown_for_discord(configured):
    calls, patcher = _capture()
    with patcher:
        _send_and_join(configured, "<b>Імʼя:</b> Ivan &amp; Co &#39;test&#39;")
    tg = next(c for c in calls if c["url"].startswith(TG_URL))
    dc = next(c for c in calls if c["url"] == HOOK_SERVER)
    # Telegram keeps the HTML it parses itself...
    assert "<b>" in tg["data"]["text"]
    # ...Discord gets markdown with entities unescaped and no raw tags.
    content = dc["json"]["content"]
    assert "**Імʼя:**" in content
    assert "<" not in content and "&amp;" not in content and "&#39;" not in content
    assert "Ivan & Co 'test'" in content


def test_discord_payload_is_truncated(configured):
    calls, patcher = _capture()
    with patcher:
        _send_and_join(configured, "я" * 5000)
    dc = next(c for c in calls if c["url"] == HOOK_SERVER)
    tg = next(c for c in calls if c["url"].startswith(TG_URL))
    assert len(dc["json"]["content"]) == 2000
    assert len(tg["data"]["text"]) == 4096


def test_telegram_failure_does_not_block_discord(configured):
    """The redundancy is the point: one dead target must not eat the other."""
    calls = []

    def _post(url, **kwargs):
        calls.append(url)
        if url.startswith(TG_URL):
            raise RuntimeError("network down")
        return FakeResp(204)

    with patch("app.utils.notifications.requests.post", side_effect=_post):
        _send_and_join(configured, "текст")
    assert HOOK_SERVER in calls


def test_no_targets_configured_is_a_noop(app):
    app.config.update(TELEGRAM_BOT_TOKEN=None, TELEGRAM_CHAT_ID=None,
                      DISCORD_WEBHOOK_SERVER=None, DISCORD_WEBHOOK_BIOMON=None)
    calls, patcher = _capture()
    with patcher:
        _send_and_join(app, "нікуди")
    assert calls == []


def test_only_discord_configured(app):
    app.config.update(TELEGRAM_BOT_TOKEN=None, TELEGRAM_CHAT_ID=None,
                      DISCORD_WEBHOOK_SERVER=HOOK_SERVER)
    calls, patcher = _capture()
    with patcher:
        _send_and_join(app, "лише discord")
    assert [c["url"] for c in calls] == [HOOK_SERVER]


def test_unknown_channel_falls_back_to_server(configured):
    calls, patcher = _capture()
    with patcher:
        _send_and_join(configured, "хтозна", channel="nonexistent")
    assert HOOK_SERVER in [c["url"] for c in calls]


def test_to_discord_drops_unknown_tags():
    assert _to_discord("<u>x</u> <i>y</i> <code>z</code>") == "x *y* `z`"
