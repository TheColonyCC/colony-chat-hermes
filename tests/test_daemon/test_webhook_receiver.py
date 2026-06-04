"""Tests for the Mode A webhook receiver + auto-recovery worker."""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
import sys
import threading
import time
import types
import urllib.error
import urllib.request
from typing import Any
from unittest.mock import MagicMock

import pytest

from colony_chat_hermes.daemon.message_queue import MessageQueue
from colony_chat_hermes.daemon.webhook_receiver import (
    SIGNATURE_HEADER,
    WebhookAutoRecovery,
    WebhookReceiver,
)

# ── Helpers ────────────────────────────────────────────────────────


def _free_port() -> int:
    """Pick an ephemeral port that's currently unbound."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def fake_colony_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a fake ``colony_chat`` module with a working ``verify_signature``."""

    class FakeColonyChat:
        @staticmethod
        def verify_signature(body: bytes, signature_header: str, secret: str) -> bool:
            expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, signature_header or "")

    fake_module = types.ModuleType("colony_chat")
    fake_module.ColonyChat = FakeColonyChat  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "colony_chat", fake_module)


# ── Receiver init / validation ────────────────────────────────────


class TestReceiverInit:
    def test_rejects_empty_secret(self) -> None:
        with pytest.raises(ValueError, match="secret is required"):
            WebhookReceiver(message_queue=MessageQueue(maxsize=10), secret="")


# ── End-to-end POST handling ──────────────────────────────────────


class TestPost:
    def _post(
        self,
        url: str,
        body: bytes,
        signature: str,
        timeout: float = 2.0,
    ) -> tuple[int, bytes]:
        req = urllib.request.Request(
            url, data=body, headers={SIGNATURE_HEADER: signature}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read() or b""

    @pytest.fixture
    def receiver(self, fake_colony_chat: None) -> Any:
        q = MessageQueue(maxsize=10)
        port = _free_port()
        r = WebhookReceiver(message_queue=q, secret="s3cret", port=port)
        r.start()
        try:
            time.sleep(0.05)  # let server bind
            yield r, q, port
        finally:
            r.stop(timeout=2.0)

    def test_signed_post_is_accepted(
        self, receiver: tuple[WebhookReceiver, MessageQueue, int]
    ) -> None:
        r, q, port = receiver
        body = json.dumps(
            {
                "id": "m1",
                "notification_type": "direct_message",
                "conversation_id": "c1",
                "from_username": "alice",
                "body": "hi",
                "created_at": "2026-06-04T12:00:00Z",
            }
        ).encode()
        sig = _sign(body, "s3cret")
        status, resp = self._post(f"http://127.0.0.1:{port}/webhook", body, sig)
        assert status == 200
        assert json.loads(resp) == {"ok": True}
        time.sleep(0.05)
        assert q.qsize() == 1
        assert r.stats()["received"] == 1

    def test_bad_signature_returns_401(
        self, receiver: tuple[WebhookReceiver, MessageQueue, int]
    ) -> None:
        r, q, port = receiver
        body = b'{"id":"m2","notification_type":"direct_message"}'
        status, _ = self._post(f"http://127.0.0.1:{port}/webhook", body, "wrong-sig")
        assert status == 401
        assert q.qsize() == 0
        assert r.stats()["rejected_bad_sig"] == 1

    def test_malformed_json_returns_400(
        self, receiver: tuple[WebhookReceiver, MessageQueue, int]
    ) -> None:
        r, q, port = receiver
        body = b"not-json"
        sig = _sign(body, "s3cret")
        status, _ = self._post(f"http://127.0.0.1:{port}/webhook", body, sig)
        assert status == 400
        assert q.qsize() == 0
        assert r.stats()["rejected_bad_body"] == 1

    def test_wrong_path_returns_404(
        self, receiver: tuple[WebhookReceiver, MessageQueue, int]
    ) -> None:
        _r, _q, port = receiver
        body = b"{}"
        sig = _sign(body, "s3cret")
        status, _ = self._post(f"http://127.0.0.1:{port}/wrong", body, sig)
        assert status == 404

    def test_non_dm_payload_returns_200_but_does_not_enqueue(
        self, receiver: tuple[WebhookReceiver, MessageQueue, int]
    ) -> None:
        # Server still returns 200 so Colony's retry logic doesn't
        # mark the delivery failed — but nothing reaches the queue.
        r, q, port = receiver
        body = json.dumps({"id": "n1", "notification_type": "comment_reply"}).encode()
        sig = _sign(body, "s3cret")
        status, _ = self._post(f"http://127.0.0.1:{port}/webhook", body, sig)
        assert status == 200
        assert q.qsize() == 0
        assert r.stats()["received"] == 0


# ── Auto-recovery ─────────────────────────────────────────────────


class TestAutoRecovery:
    def test_re_enables_disabled_webhook(self) -> None:
        client = MagicMock()
        client.list_webhooks.return_value = [
            {"id": "wh-1", "is_active": False},
            {"id": "wh-2", "is_active": True},
        ]
        recovery = WebhookAutoRecovery(client=client, webhook_id="wh-1", interval=60)
        assert recovery.check_once() is True
        client.update_webhook.assert_called_once_with("wh-1", is_active=True)
        assert recovery.stats()["recoveries"] == 1

    def test_noop_when_active(self) -> None:
        client = MagicMock()
        client.list_webhooks.return_value = [{"id": "wh-1", "is_active": True}]
        recovery = WebhookAutoRecovery(client=client, webhook_id="wh-1", interval=60)
        assert recovery.check_once() is False
        client.update_webhook.assert_not_called()

    def test_noop_when_webhook_missing(self) -> None:
        client = MagicMock()
        client.list_webhooks.return_value = [{"id": "other"}]
        recovery = WebhookAutoRecovery(client=client, webhook_id="wh-1", interval=60)
        assert recovery.check_once() is False

    def test_swallows_list_exception(self) -> None:
        client = MagicMock()
        client.list_webhooks.side_effect = RuntimeError("net down")
        recovery = WebhookAutoRecovery(client=client, webhook_id="wh-1", interval=60)
        assert recovery.check_once() is False

    def test_swallows_update_exception(self) -> None:
        client = MagicMock()
        client.list_webhooks.return_value = [{"id": "wh-1", "is_active": False}]
        client.update_webhook.side_effect = RuntimeError("update failed")
        recovery = WebhookAutoRecovery(client=client, webhook_id="wh-1", interval=60)
        assert recovery.check_once() is False

    def test_ignores_malformed_entries(self) -> None:
        client = MagicMock()
        client.list_webhooks.return_value = [
            "string-not-dict",
            None,
            {"id": "wh-1", "is_active": True},
        ]
        recovery = WebhookAutoRecovery(client=client, webhook_id="wh-1", interval=60)
        assert recovery.check_once() is False

    def test_lifecycle(self) -> None:
        client = MagicMock()
        client.list_webhooks.return_value = []
        stop = threading.Event()
        recovery = WebhookAutoRecovery(
            client=client, webhook_id="wh-1", interval=0.05, stop_event=stop
        )
        recovery.start()
        recovery.start()  # idempotent
        time.sleep(0.15)
        recovery.stop(timeout=2.0)

    def test_rejects_invalid_interval(self) -> None:
        with pytest.raises(ValueError, match="interval"):
            WebhookAutoRecovery(client=MagicMock(), webhook_id="wh-1", interval=0)
