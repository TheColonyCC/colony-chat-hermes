"""Tests for the daemon orchestrator (wiring + lifecycle)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from colony_chat_hermes.daemon.events import InboundEvent
from colony_chat_hermes.daemon.orchestrator import Orchestrator
from colony_chat_hermes.leader_lock import LeaderLock


def _payload(mid: str) -> dict[str, object]:
    return {
        "id": mid,
        "notification_type": "direct_message",
        "conversation_id": "c1",
        "from_username": "alice",
        "body": "hi",
        "created_at": "2026-06-04T12:00:00Z",
    }


@pytest.fixture
def client() -> MagicMock:
    c = MagicMock()
    c.unread.return_value = []
    c.list_webhooks.return_value = []
    return c


class TestInit:
    def test_rejects_invalid_mode(self, client: MagicMock) -> None:
        with pytest.raises(ValueError, match="invalid mode"):
            Orchestrator(client=client, invoker=lambda e: None, mode="garbage")  # type: ignore[arg-type]

    def test_webhook_mode_requires_secret(self, client: MagicMock) -> None:
        with pytest.raises(ValueError, match="webhook_secret"):
            Orchestrator(client=client, invoker=lambda e: None, mode="webhook")

    def test_both_mode_requires_secret(self, client: MagicMock) -> None:
        with pytest.raises(ValueError, match="webhook_secret"):
            Orchestrator(client=client, invoker=lambda e: None, mode="both")

    def test_poll_mode_no_receiver(self, client: MagicMock) -> None:
        orch = Orchestrator(client=client, invoker=lambda e: None, mode="poll")
        # Receiver should be absent in poll-only mode.
        assert orch.stats()["mode"] == "poll"
        assert "receiver" not in orch.stats()

    def test_both_mode_creates_recovery_only_with_id(self, client: MagicMock) -> None:
        # No webhook_id → no recovery worker.
        orch = Orchestrator(
            client=client,
            invoker=lambda e: None,
            mode="both",
            webhook_secret="s",
        )
        assert "recovery" not in orch.stats()
        # With webhook_id → recovery worker present.
        orch2 = Orchestrator(
            client=client,
            invoker=lambda e: None,
            mode="both",
            webhook_secret="s",
            webhook_id="wh-1",
        )
        assert "recovery" in orch2.stats()


class TestLifecycle:
    def test_start_stop(self, client: MagicMock) -> None:
        dispatched: list[InboundEvent] = []
        client.unread.return_value = [_payload("m1")]

        orch = Orchestrator(
            client=client,
            invoker=dispatched.append,
            mode="poll",
            poll_interval=0.05,
        )
        orch.start()
        try:
            deadline = time.time() + 2.0
            while not dispatched and time.time() < deadline:
                time.sleep(0.02)
            assert len(dispatched) == 1
            assert dispatched[0].message_id == "m1"
        finally:
            orch.stop()

    def test_leader_lock_acquisition(self, client: MagicMock, tmp_path: Path) -> None:
        lock_path = tmp_path / "x.lock"
        lock1 = LeaderLock(lock_path=lock_path)
        lock2 = LeaderLock(lock_path=lock_path)

        o1 = Orchestrator(client=client, invoker=lambda e: None, mode="poll", leader_lock=lock1)
        o2 = Orchestrator(client=client, invoker=lambda e: None, mode="poll", leader_lock=lock2)

        o1.start()
        try:
            with pytest.raises(RuntimeError, match="already holds"):
                o2.start()
        finally:
            o1.stop()

    def test_stop_after_start_without_lock(self, client: MagicMock) -> None:
        # Make sure stop is safe even when the orchestrator was not
        # started with a lock.
        orch = Orchestrator(client=client, invoker=lambda e: None, mode="poll")
        orch.start()
        orch.stop()


class TestStats:
    def test_includes_components(self, client: MagicMock) -> None:
        orch = Orchestrator(
            client=client,
            invoker=lambda e: None,
            mode="both",
            webhook_secret="s",
            webhook_id="wh-1",
        )
        stats = orch.stats()
        assert stats["mode"] == "both"
        assert "queue" in stats
        assert "invoker" in stats
        assert "poller" in stats
        assert "receiver" in stats
        assert "recovery" in stats


class TestRunDaemon:
    def test_run_daemon_smoke(self, client: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        # Patch run_until_signal so we don't actually block on a signal.
        from colony_chat_hermes.daemon import orchestrator as orch_mod

        called: list[Any] = []

        def _fake_run_until_signal(self: Any) -> None:
            called.append(self)

        monkeypatch.setattr(orch_mod.Orchestrator, "run_until_signal", _fake_run_until_signal)
        orch_mod.run_daemon(client=client, invoker=lambda e: None, mode="poll")
        assert len(called) == 1
