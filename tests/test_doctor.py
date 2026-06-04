"""Tests for the ``doctor`` diagnostic subcommand."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from colony_chat_hermes import cli, doctor, soul_anchor


def _install_fake_colony_chat(monkeypatch: pytest.MonkeyPatch, **methods: object) -> MagicMock:
    """Install a ``colony_chat`` module whose ``ColonyChat()`` returns a MagicMock.

    The returned mock can be customized via ``methods`` kwargs (e.g.
    ``me=...`` to set the return value of ``client.me()``).
    """
    fake_client = MagicMock()
    for k, v in methods.items():
        if isinstance(v, Exception):
            getattr(fake_client, k).side_effect = v
        else:
            getattr(fake_client, k).return_value = v

    class FakeColonyChat:
        def __init__(self, **kwargs: object) -> None:
            self._kwargs = kwargs

        def __new__(cls, **kwargs: object) -> FakeColonyChat:  # type: ignore[misc]
            # Return the MagicMock so attribute lookups in the doctor
            # hit our preconfigured methods.
            return fake_client  # type: ignore[return-value]

    fake_module = types.ModuleType("colony_chat")
    fake_module.ColonyChat = FakeColonyChat  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "colony_chat", fake_module)
    return fake_client


def _all_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    return (
        tmp_path / ".env",
        tmp_path / "SOUL.md",
        tmp_path / "colony-chat-hermes.lock",
    )


class TestApiKeyCheck:
    def test_missing_key_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env, soul, lock = _all_paths(tmp_path)
        monkeypatch.delenv(cli.API_KEY_ENV_VAR, raising=False)
        captured: list[str] = []
        rc = doctor.run_doctor(
            env_path=env,
            soul_path=soul,
            lock_path=lock,
            invoker_spec="log_only",
            emit=captured.append,
        )
        assert rc == 1
        joined = "\n".join(captured)
        assert "api_key configured" in joined
        assert "✗" in joined

    def test_env_var_satisfies(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env, soul, lock = _all_paths(tmp_path)
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_test")
        _install_fake_colony_chat(
            monkeypatch,
            me={"username": "alice", "karma": 10},
            contacts=[],
        )
        captured: list[str] = []
        rc = doctor.run_doctor(
            env_path=env,
            soul_path=soul,
            lock_path=lock,
            invoker_spec="log_only",
            emit=captured.append,
        )
        joined = "\n".join(captured)
        assert "api_key configured" in joined
        assert rc == 0  # SOUL.md missing is warn, not fail


class TestIdentityCheck:
    def test_warns_on_low_karma(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env, soul, lock = _all_paths(tmp_path)
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_test")
        _install_fake_colony_chat(
            monkeypatch,
            me={"username": "alice", "karma": 0},
            contacts=[],
        )
        captured: list[str] = []
        rc = doctor.run_doctor(
            env_path=env,
            soul_path=soul,
            lock_path=lock,
            invoker_spec="log_only",
            emit=captured.append,
        )
        joined = "\n".join(captured)
        assert "karma=0" in joined
        assert "≥5" in joined
        assert "⚠" in joined
        assert rc == 0  # warn doesn't fail

    def test_fails_when_me_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env, soul, lock = _all_paths(tmp_path)
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_test")
        _install_fake_colony_chat(monkeypatch, me=RuntimeError("api_key revoked"))
        captured: list[str] = []
        rc = doctor.run_doctor(
            env_path=env,
            soul_path=soul,
            lock_path=lock,
            invoker_spec="log_only",
            emit=captured.append,
        )
        assert rc == 1
        assert "api_key revoked" in "\n".join(captured)

    def test_fails_when_me_returns_no_username(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env, soul, lock = _all_paths(tmp_path)
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_test")
        _install_fake_colony_chat(monkeypatch, me={}, contacts=[])
        captured: list[str] = []
        rc = doctor.run_doctor(
            env_path=env,
            soul_path=soul,
            lock_path=lock,
            invoker_spec="log_only",
            emit=captured.append,
        )
        assert rc == 1
        assert "username" in "\n".join(captured)


class TestContactsCheck:
    def test_fails_when_contacts_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env, soul, lock = _all_paths(tmp_path)
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_test")
        _install_fake_colony_chat(
            monkeypatch,
            me={"username": "alice", "karma": 10},
            contacts=RuntimeError("server down"),
        )
        captured: list[str] = []
        rc = doctor.run_doctor(
            env_path=env,
            soul_path=soul,
            lock_path=lock,
            invoker_spec="log_only",
            emit=captured.append,
        )
        assert rc == 1
        assert "server down" in "\n".join(captured)


class TestSoulAnchorCheck:
    def test_warns_when_missing(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env, soul, lock = _all_paths(tmp_path)
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_test")
        _install_fake_colony_chat(monkeypatch, me={"username": "a", "karma": 10}, contacts=[])
        captured: list[str] = []
        doctor.run_doctor(
            env_path=env,
            soul_path=soul,
            lock_path=lock,
            invoker_spec="log_only",
            emit=captured.append,
        )
        joined = "\n".join(captured)
        assert "SOUL.md" in joined and "⚠" in joined

    def test_ok_when_present(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env, soul, lock = _all_paths(tmp_path)
        soul_anchor.upsert(handle="alice", soul_path=soul)
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_test")
        _install_fake_colony_chat(monkeypatch, me={"username": "alice", "karma": 10}, contacts=[])
        captured: list[str] = []
        rc = doctor.run_doctor(
            env_path=env,
            soul_path=soul,
            lock_path=lock,
            invoker_spec="log_only",
            emit=captured.append,
        )
        joined = "\n".join(captured)
        assert "SOUL.md identity block — present" in joined.replace("✓ ", "")
        assert rc == 0


class TestInvokerSpecCheck:
    def test_fails_on_bad_spec(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env, soul, lock = _all_paths(tmp_path)
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_test")
        _install_fake_colony_chat(monkeypatch, me={"username": "a", "karma": 10}, contacts=[])
        captured: list[str] = []
        rc = doctor.run_doctor(
            env_path=env,
            soul_path=soul,
            lock_path=lock,
            invoker_spec="garbage",
            emit=captured.append,
        )
        assert rc == 1
        joined = "\n".join(captured)
        assert "invoker spec" in joined and "garbage" in joined


class TestLeaderLockCheck:
    def test_detects_currently_held_lock(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from colony_chat_hermes.leader_lock import LeaderLock

        env, soul, lock_path = _all_paths(tmp_path)
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_test")
        _install_fake_colony_chat(monkeypatch, me={"username": "a", "karma": 10}, contacts=[])
        # Hold the lock from another LeaderLock instance.
        held = LeaderLock(lock_path=lock_path)
        held.acquire()
        try:
            captured: list[str] = []
            doctor.run_doctor(
                env_path=env,
                soul_path=soul,
                lock_path=lock_path,
                invoker_spec="log_only",
                emit=captured.append,
            )
        finally:
            held.release()
        joined = "\n".join(captured)
        assert "another process holds" in joined
        assert "⚠" in joined


class TestWebhookConfigCheck:
    def test_skipped_when_no_webhook_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env, soul, lock = _all_paths(tmp_path)
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_test")
        monkeypatch.delenv("COLONY_CHAT_WEBHOOK_SECRET", raising=False)
        monkeypatch.delenv("COLONY_CHAT_WEBHOOK_ID", raising=False)
        _install_fake_colony_chat(monkeypatch, me={"username": "a", "karma": 10}, contacts=[])
        captured: list[str] = []
        doctor.run_doctor(
            env_path=env,
            soul_path=soul,
            lock_path=lock,
            invoker_spec="log_only",
            emit=captured.append,
        )
        joined = "\n".join(captured)
        assert "webhook config" not in joined

    def test_fails_when_secret_missing_but_id_set(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env, soul, lock = _all_paths(tmp_path)
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_test")
        monkeypatch.delenv("COLONY_CHAT_WEBHOOK_SECRET", raising=False)
        monkeypatch.setenv("COLONY_CHAT_WEBHOOK_ID", "wh-1")
        _install_fake_colony_chat(monkeypatch, me={"username": "a", "karma": 10}, contacts=[])
        captured: list[str] = []
        rc = doctor.run_doctor(
            env_path=env,
            soul_path=soul,
            lock_path=lock,
            invoker_spec="log_only",
            emit=captured.append,
        )
        assert rc == 1
        assert "WEBHOOK_SECRET" in "\n".join(captured)

    def test_ok_when_id_active(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env, soul, lock = _all_paths(tmp_path)
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_test")
        monkeypatch.setenv("COLONY_CHAT_WEBHOOK_SECRET", "s")
        monkeypatch.setenv("COLONY_CHAT_WEBHOOK_ID", "wh-1")
        _install_fake_colony_chat(
            monkeypatch,
            me={"username": "a", "karma": 10},
            contacts=[],
            list_webhooks=[
                {"id": "wh-1", "is_active": True, "url": "https://x", "events": ["direct_message"]}
            ],
        )
        captured: list[str] = []
        rc = doctor.run_doctor(
            env_path=env,
            soul_path=soul,
            lock_path=lock,
            invoker_spec="log_only",
            emit=captured.append,
        )
        joined = "\n".join(captured)
        assert "webhook config" in joined and "active" in joined
        assert rc == 0

    def test_warns_when_disabled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env, soul, lock = _all_paths(tmp_path)
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_test")
        monkeypatch.setenv("COLONY_CHAT_WEBHOOK_SECRET", "s")
        monkeypatch.setenv("COLONY_CHAT_WEBHOOK_ID", "wh-1")
        _install_fake_colony_chat(
            monkeypatch,
            me={"username": "a", "karma": 10},
            contacts=[],
            list_webhooks=[{"id": "wh-1", "is_active": False}],
        )
        captured: list[str] = []
        rc = doctor.run_doctor(
            env_path=env,
            soul_path=soul,
            lock_path=lock,
            invoker_spec="log_only",
            emit=captured.append,
        )
        joined = "\n".join(captured)
        assert "DISABLED" in joined and "⚠" in joined
        # Warn doesn't fail
        assert rc == 0

    def test_fails_when_id_not_in_account(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env, soul, lock = _all_paths(tmp_path)
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_test")
        monkeypatch.setenv("COLONY_CHAT_WEBHOOK_SECRET", "s")
        monkeypatch.setenv("COLONY_CHAT_WEBHOOK_ID", "wh-1")
        _install_fake_colony_chat(
            monkeypatch,
            me={"username": "a", "karma": 10},
            contacts=[],
            list_webhooks=[{"id": "wh-other"}],
        )
        captured: list[str] = []
        rc = doctor.run_doctor(
            env_path=env,
            soul_path=soul,
            lock_path=lock,
            invoker_spec="log_only",
            emit=captured.append,
        )
        joined = "\n".join(captured)
        assert "not in your registered webhooks" in joined
        assert rc == 1
