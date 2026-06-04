"""Tests for the colony-chat-hermes CLI."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from colony_chat_hermes import cli, soul_anchor, wizard


class TestParserShape:
    def test_register_subcommand_accepts_flags(self) -> None:
        parser = cli._build_parser()
        args = parser.parse_args(["register", "--handle", "x", "--display-name", "y", "--bio", "z"])
        assert args.command == "register"
        assert args.handle == "x"
        assert args.display_name == "y"
        assert args.bio == "z"

    def test_status_subcommand(self) -> None:
        parser = cli._build_parser()
        args = parser.parse_args(["status"])
        assert args.command == "status"

    def test_logout_subcommand(self) -> None:
        parser = cli._build_parser()
        args = parser.parse_args(["logout"])
        assert args.command == "logout"


class TestRegisterCommand:
    def test_invokes_wizard_with_args(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}

        def fake_run(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                handle="x",
                api_key="col_x",
                user_id="u",
                env_path=Path(kwargs["env_path"]),
                soul_path=Path(kwargs["soul_path"]),
            )

        monkeypatch.setattr(wizard, "run", fake_run)
        rc = cli.main(
            [
                "register",
                "--handle",
                "my-agent",
                "--display-name",
                "My Agent",
                "--bio",
                "hi",
                "--env-path",
                str(tmp_path / ".env"),
                "--soul-path",
                str(tmp_path / "SOUL.md"),
            ]
        )
        assert rc == 0
        assert captured["handle"] == "my-agent"
        assert captured["display_name"] == "My Agent"
        assert captured["bio"] == "hi"

    def test_handles_invalid_handle_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def raises_invalid(**_kwargs):
            raise wizard.InvalidHandleError("nope")

        monkeypatch.setattr(wizard, "run", raises_invalid)
        rc = cli.main(
            [
                "register",
                "--handle",
                "ab",
                "--display-name",
                "X",
                "--bio",
                "",
                "--env-path",
                str(tmp_path / ".env"),
                "--soul-path",
                str(tmp_path / "SOUL.md"),
            ]
        )
        assert rc == 2

    def test_handles_generic_wizard_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def raises_wizard(**_kwargs):
            raise wizard.WizardError("network blip")

        monkeypatch.setattr(wizard, "run", raises_wizard)
        rc = cli.main(
            [
                "register",
                "--handle",
                "my-agent",
                "--display-name",
                "X",
                "--bio",
                "",
                "--env-path",
                str(tmp_path / ".env"),
                "--soul-path",
                str(tmp_path / "SOUL.md"),
            ]
        )
        assert rc == 1


class TestStatusCommand:
    def test_reports_unset_when_no_key(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.delenv("COLONY_CHAT_API_KEY", raising=False)
        env_path = tmp_path / ".env"
        rc = cli.main(["status", "--env-path", str(env_path)])
        assert rc == 1
        captured = capsys.readouterr().out
        assert "not set" in captured

    def test_reports_set_and_resolves_identity(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        env_path = tmp_path / ".env"
        env_path.write_text(f"{cli.API_KEY_ENV_VAR}=col_status_test\n")
        monkeypatch.delenv("COLONY_CHAT_API_KEY", raising=False)

        # Inject a fake colony_chat module.
        class FakeColonyChat:
            def __init__(self, api_key: str) -> None:
                self.api_key = api_key

            def me(self) -> dict:
                return {
                    "id": "u-x",
                    "username": "tester",
                    "display_name": "Tester",
                    "karma": 42,
                }

        import sys
        import types

        fake = types.ModuleType("colony_chat")
        fake.ColonyChat = FakeColonyChat  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "colony_chat", fake)

        rc = cli.main(["status", "--env-path", str(env_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "@tester" in out
        assert "karma=42" in out


class TestLogoutCommand:
    def test_removes_key_from_env_and_clears_soul(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        env_path = tmp_path / ".env"
        soul_path = tmp_path / "SOUL.md"
        env_path.write_text(f"{cli.API_KEY_ENV_VAR}=col_to_clear\nOTHER=keep\n")
        soul_anchor.upsert(handle="x", soul_path=soul_path)

        rc = cli.main(["logout", "--env-path", str(env_path), "--soul-path", str(soul_path)])
        assert rc == 0
        env_body = env_path.read_text()
        assert "col_to_clear" not in env_body
        assert "OTHER=keep" in env_body
        soul_body = soul_path.read_text()
        assert soul_anchor.DEFAULT_START_MARKER not in soul_body

        out = capsys.readouterr().out
        assert "logged out" in out

    def test_noop_when_no_state(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        rc = cli.main(
            [
                "logout",
                "--env-path",
                str(tmp_path / ".env"),
                "--soul-path",
                str(tmp_path / "SOUL.md"),
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "was not logged in" in out


# ── v0.2.0: daemon / feed / send ────────────────────────────────────


class TestParserDaemonShape:
    def test_daemon_subcommand_defaults(self) -> None:
        parser = cli._build_parser()
        args = parser.parse_args(["daemon"])
        assert args.command == "daemon"
        assert args.mode == "poll"
        assert args.invoker == "log_only"
        assert args.queue_maxsize == 100

    def test_daemon_overrides(self) -> None:
        parser = cli._build_parser()
        args = parser.parse_args(
            [
                "daemon",
                "--mode",
                "both",
                "--webhook-secret",
                "s3cret",
                "--webhook-id",
                "wh-1",
                "--poll-interval",
                "5",
                "--invoker",
                "log_only:/tmp/x.jsonl",
            ]
        )
        assert args.mode == "both"
        assert args.webhook_secret == "s3cret"
        assert args.poll_interval == 5.0
        assert args.invoker == "log_only:/tmp/x.jsonl"

    def test_feed_subcommand(self) -> None:
        parser = cli._build_parser()
        args = parser.parse_args(["feed", "--once"])
        assert args.command == "feed"
        assert args.once is True

    def test_send_subcommand_requires_handle_and_body(self) -> None:
        parser = cli._build_parser()
        args = parser.parse_args(["send", "alice", "hi"])
        assert args.command == "send"
        assert args.handle == "alice"
        assert args.body == "hi"


class TestDaemonCommand:
    def test_returns_1_without_api_key(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.delenv(cli.API_KEY_ENV_VAR, raising=False)
        rc = cli.main(["daemon", "--env-path", str(tmp_path / ".env"), "--lock-path", ""])
        assert rc == 1
        assert "no api_key" in capsys.readouterr().err

    def test_returns_2_on_bad_invoker_spec(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_test")
        # Stub colony_chat.ColonyChat so _build_client doesn't fail
        import sys
        import types

        class _Fake:
            def __init__(self, **kwargs):
                pass

        fake = types.ModuleType("colony_chat")
        fake.ColonyChat = _Fake  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "colony_chat", fake)

        rc = cli.main(
            [
                "daemon",
                "--env-path",
                str(tmp_path / ".env"),
                "--lock-path",
                "",
                "--invoker",
                "nonsense",
            ]
        )
        assert rc == 2
        assert "nonsense" in capsys.readouterr().err

    def test_returns_2_on_invalid_mode(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # webhook mode without --webhook-secret env or flag → Orchestrator raises.
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_test")
        monkeypatch.delenv("COLONY_CHAT_WEBHOOK_SECRET", raising=False)
        import sys
        import types

        class _Fake:
            def __init__(self, **kwargs):
                pass

        fake = types.ModuleType("colony_chat")
        fake.ColonyChat = _Fake  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "colony_chat", fake)

        rc = cli.main(
            [
                "daemon",
                "--env-path",
                str(tmp_path / ".env"),
                "--lock-path",
                "",
                "--mode",
                "webhook",
            ]
        )
        assert rc == 2
        assert "webhook_secret" in capsys.readouterr().err


class TestFeedCommand:
    def test_returns_1_without_api_key(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.delenv(cli.API_KEY_ENV_VAR, raising=False)
        rc = cli.main(["feed", "--env-path", str(tmp_path / ".env"), "--once"])
        assert rc == 1
        assert "no api_key" in capsys.readouterr().err

    def test_once_prints_jsonl(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_test")
        import sys
        import types

        class _Fake:
            def __init__(self, **kwargs):
                pass

            def unread(self, limit: int = 50):
                return [
                    {
                        "id": "m1",
                        "notification_type": "direct_message",
                        "conversation_id": "c1",
                        "from_username": "alice",
                        "body": "hi",
                        "created_at": "2026-06-04T12:00:00Z",
                    }
                ]

        fake = types.ModuleType("colony_chat")
        fake.ColonyChat = _Fake  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "colony_chat", fake)

        rc = cli.main(["feed", "--env-path", str(tmp_path / ".env"), "--once"])
        assert rc == 0
        out = capsys.readouterr().out
        import json as _json

        line = out.strip()
        parsed = _json.loads(line)
        assert parsed["message_id"] == "m1"
        assert parsed["from_handle"] == "alice"


class TestSendCommand:
    def test_returns_1_without_api_key(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.delenv(cli.API_KEY_ENV_VAR, raising=False)
        rc = cli.main(["send", "--env-path", str(tmp_path / ".env"), "alice", "hi"])
        assert rc == 1
        assert "no api_key" in capsys.readouterr().err

    def test_rejects_empty_body(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_test")
        import sys
        import types

        class _Fake:
            def __init__(self, **kwargs):
                pass

        fake = types.ModuleType("colony_chat")
        fake.ColonyChat = _Fake  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "colony_chat", fake)

        rc = cli.main(["send", "--env-path", str(tmp_path / ".env"), "alice", ""])
        assert rc == 2
        assert "empty" in capsys.readouterr().err

    def test_prints_message_id_on_success(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_test")
        import sys
        import types

        sent: dict = {}

        class _Fake:
            def __init__(self, **kwargs):
                pass

            def send(self, *, to, text, idempotency_key=None):
                sent["to"] = to
                sent["text"] = text
                sent["idempotency_key"] = idempotency_key
                return {"message_id": "m99", "delivered": True}

        fake = types.ModuleType("colony_chat")
        fake.ColonyChat = _Fake  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "colony_chat", fake)

        rc = cli.main(
            [
                "send",
                "--env-path",
                str(tmp_path / ".env"),
                "alice",
                "hi",
                "--idempotency-key",
                "k1",
            ]
        )
        assert rc == 0
        assert sent == {"to": "alice", "text": "hi", "idempotency_key": "k1"}
        assert capsys.readouterr().out.strip() == "m99"

    def test_handles_send_exception(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_test")
        import sys
        import types

        class _Fake:
            def __init__(self, **kwargs):
                pass

            def send(self, **kwargs):
                raise RuntimeError("server down")

        fake = types.ModuleType("colony_chat")
        fake.ColonyChat = _Fake  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "colony_chat", fake)

        rc = cli.main(["send", "--env-path", str(tmp_path / ".env"), "alice", "hi"])
        assert rc == 1
        assert "server down" in capsys.readouterr().err

    def test_reads_body_from_stdin(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_test")
        import io
        import sys
        import types

        class _Fake:
            def __init__(self, **kwargs):
                pass

            def send(self, *, to, text, idempotency_key=None):
                return {"id": "m42"}

        fake = types.ModuleType("colony_chat")
        fake.ColonyChat = _Fake  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "colony_chat", fake)
        monkeypatch.setattr(sys, "stdin", io.StringIO("piped body\n"))

        rc = cli.main(["send", "--env-path", str(tmp_path / ".env"), "alice", "-"])
        assert rc == 0
        assert capsys.readouterr().out.strip() == "m42"

    def test_prints_raw_result_when_id_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_test")
        import sys
        import types

        class _Fake:
            def __init__(self, **kwargs):
                pass

            def send(self, **kwargs):
                return {"queued": True}

        fake = types.ModuleType("colony_chat")
        fake.ColonyChat = _Fake  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "colony_chat", fake)

        rc = cli.main(["send", "--env-path", str(tmp_path / ".env"), "alice", "hi"])
        assert rc == 0
        import json as _json

        assert _json.loads(capsys.readouterr().out.strip()) == {"queued": True}


class TestBuildClient:
    def test_resolves_from_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_envkey")
        import sys
        import types

        captured: dict = {}

        class _Fake:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        fake = types.ModuleType("colony_chat")
        fake.ColonyChat = _Fake  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "colony_chat", fake)

        client = cli._build_client(tmp_path / ".env")
        assert client is not None
        assert captured["api_key"] == "col_envkey"

    def test_returns_none_without_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(cli.API_KEY_ENV_VAR, raising=False)
        client = cli._build_client(tmp_path / ".env")
        assert client is None

    def test_threads_base_url_override(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(cli.API_KEY_ENV_VAR, "col_test")
        monkeypatch.setenv("COLONY_CHAT_API_BASE", "https://staging.example/api/v1")
        import sys
        import types

        captured: dict = {}

        class _Fake:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        fake = types.ModuleType("colony_chat")
        fake.ColonyChat = _Fake  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "colony_chat", fake)

        cli._build_client(tmp_path / ".env")
        assert captured["base_url"] == "https://staging.example/api/v1"
