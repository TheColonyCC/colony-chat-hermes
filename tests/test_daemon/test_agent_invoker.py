"""Tests for the invoker registry + the consumer thread."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import types
from pathlib import Path
from typing import Any

import pytest

from colony_chat_hermes.daemon.agent_invoker import (
    AgentInvoker,
    load_invoker,
    log_only_invoker,
    subprocess_invoker,
)
from colony_chat_hermes.daemon.events import InboundEvent
from colony_chat_hermes.daemon.message_queue import MessageQueue


def _evt(mid: str = "m1") -> InboundEvent:
    return InboundEvent(
        message_id=mid,
        conversation_id="c1",
        from_handle="alice",
        body="hi",
        ts="2026-06-04T12:00:00Z",
        source="poller",
    )


# ── log_only ──────────────────────────────────────────────────────


class TestLogOnly:
    def test_writes_jsonl_to_path(self, tmp_path: Path) -> None:
        target = tmp_path / "inbound.jsonl"
        invoker = log_only_invoker(path=target)
        invoker(_evt("m1"))
        invoker(_evt("m2"))
        lines = target.read_text().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["message_id"] == "m1"
        assert json.loads(lines[1])["message_id"] == "m2"

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "tree" / "inbound.jsonl"
        invoker = log_only_invoker(path=target)
        invoker(_evt())
        assert target.exists()


# ── subprocess ────────────────────────────────────────────────────


class TestSubprocess:
    def test_passes_event_json_on_stdin(self, tmp_path: Path) -> None:
        captured = tmp_path / "captured.txt"
        # ``cat`` echoes stdin; pipe via shell to write to a file.
        invoker = subprocess_invoker(
            command=[
                sys.executable,
                "-c",
                (
                    "import sys, pathlib; "
                    f"pathlib.Path({str(captured)!r}).write_text(sys.stdin.read())"
                ),
            ]
        )
        invoker(_evt("m1"))
        recorded = json.loads(captured.read_text())
        assert recorded["message_id"] == "m1"

    def test_logs_on_nonzero_exit(self, caplog: pytest.LogCaptureFixture) -> None:
        invoker = subprocess_invoker(command=[sys.executable, "-c", "import sys; sys.exit(2)"])
        with caplog.at_level("INFO"):
            invoker(_evt())
        assert any("subprocess exited 2" in r.message for r in caplog.records)

    def test_logs_on_timeout(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def _raise(*args: Any, **kwargs: Any) -> Any:
            raise subprocess.TimeoutExpired(cmd=kwargs.get("args") or args[0], timeout=0.1)

        monkeypatch.setattr(subprocess, "run", _raise)
        invoker = subprocess_invoker(command=["sleep", "60"], timeout=0.1)
        with caplog.at_level("WARNING"):
            invoker(_evt())
        assert any("timed out" in r.message for r in caplog.records)

    def test_logs_on_oserror(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def _raise(*args: Any, **kwargs: Any) -> Any:
            raise OSError("exec failed")

        monkeypatch.setattr(subprocess, "run", _raise)
        invoker = subprocess_invoker(command=["nonexistent-cmd"])
        with caplog.at_level("WARNING"):
            invoker(_evt())
        assert any("OSError" in r.message for r in caplog.records)

    def test_rejects_empty_command(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            subprocess_invoker(command=[])


# ── load_invoker ──────────────────────────────────────────────────


class TestLoadInvoker:
    def test_log_only_default_path(self, tmp_path: Path) -> None:
        invoker = load_invoker("log_only", log_path=tmp_path / "x.jsonl")
        invoker(_evt())
        assert (tmp_path / "x.jsonl").exists()

    def test_log_only_explicit_path(self, tmp_path: Path) -> None:
        target = tmp_path / "explicit.jsonl"
        invoker = load_invoker(f"log_only:{target}")
        invoker(_evt())
        assert target.exists()

    def test_subprocess(self, tmp_path: Path) -> None:
        target = tmp_path / "out.txt"
        spec = (
            f"subprocess:{sys.executable} -c "
            f"'import sys, pathlib; pathlib.Path(\"{target}\").write_text(sys.stdin.read())'"
        )
        invoker = load_invoker(spec)
        invoker(_evt("m1"))
        assert json.loads(target.read_text())["message_id"] == "m1"

    def test_dotted_callable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called: list[InboundEvent] = []

        def _factory() -> Any:
            def _inner(event: InboundEvent) -> None:
                called.append(event)

            return _inner

        fake = types.ModuleType("test_dotted_module")
        fake.factory = _factory  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "test_dotted_module", fake)

        invoker = load_invoker("test_dotted_module:factory")
        invoker(_evt("m1"))
        assert len(called) == 1
        assert called[0].message_id == "m1"

    def test_dotted_callable_rejects_non_callable_attr(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = types.ModuleType("test_bad_attr")
        fake.not_callable = "string"  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "test_bad_attr", fake)
        with pytest.raises(ValueError, match="not callable"):
            load_invoker("test_bad_attr:not_callable")

    def test_dotted_callable_rejects_non_callable_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake = types.ModuleType("test_bad_result")
        fake.factory = lambda: "string"  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "test_bad_result", fake)
        with pytest.raises(ValueError, match="not return a callable"):
            load_invoker("test_bad_result:factory")

    def test_unknown_spec(self) -> None:
        with pytest.raises(ValueError, match="unknown invoker spec"):
            load_invoker("nonsense")


# ── AgentInvoker consumer thread ──────────────────────────────────


class TestAgentInvoker:
    def test_dispatches_from_queue(self) -> None:
        q = MessageQueue(maxsize=10)
        captured: list[InboundEvent] = []

        def _invoker(e: InboundEvent) -> None:
            captured.append(e)

        consumer = AgentInvoker(message_queue=q, invoker=_invoker, dequeue_timeout=0.05)
        consumer.start()
        try:
            q.enqueue(_evt("m1"))
            q.enqueue(_evt("m2"))
            deadline = time.time() + 2.0
            while len(captured) < 2 and time.time() < deadline:
                time.sleep(0.02)
            assert [e.message_id for e in captured] == ["m1", "m2"]
        finally:
            consumer.stop(timeout=2.0)
        assert consumer.stats()["dispatched"] == 2

    def test_logs_and_continues_on_invoker_exception(self) -> None:
        q = MessageQueue(maxsize=10)
        attempts: list[InboundEvent] = []

        def _invoker(e: InboundEvent) -> None:
            attempts.append(e)
            if e.message_id == "m1":
                raise RuntimeError("boom")

        consumer = AgentInvoker(message_queue=q, invoker=_invoker, dequeue_timeout=0.05)
        consumer.start()
        try:
            q.enqueue(_evt("m1"))
            q.enqueue(_evt("m2"))
            deadline = time.time() + 2.0
            while len(attempts) < 2 and time.time() < deadline:
                time.sleep(0.02)
            assert [e.message_id for e in attempts] == ["m1", "m2"]
        finally:
            consumer.stop(timeout=2.0)
        assert consumer.stats()["dispatched"] == 1
        assert consumer.stats()["errors"] == 1

    def test_start_is_idempotent(self) -> None:
        q = MessageQueue(maxsize=10)
        consumer = AgentInvoker(message_queue=q, invoker=lambda e: None, dequeue_timeout=0.05)
        consumer.start()
        consumer.start()
        consumer.stop(timeout=2.0)

    def test_dispatch_one_returns_false_on_invoker_error(self) -> None:
        q = MessageQueue(maxsize=10)

        def _invoker(e: InboundEvent) -> None:
            raise RuntimeError("boom")

        consumer = AgentInvoker(message_queue=q, invoker=_invoker)
        assert consumer.dispatch_one(_evt()) is False
