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
