"""Tests for the registration wizard."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from colony_chat_hermes import wizard
from colony_chat_hermes.wizard import (
    API_KEY_ENV_VAR,
    InvalidHandleError,
    WizardError,
)


def _fake_register_factory(*, api_key: str = "col_fresh_test_key", user_id: str = "u-test"):
    """Return a register-shaped callable plus a tracker dict."""
    calls: list[dict] = []

    def fake(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            api_key=api_key,
            me=lambda: {
                "id": user_id,
                "username": kwargs["handle"],
                "display_name": kwargs["display_name"],
            },
        )

    return fake, calls


class TestValidateHandle:
    @pytest.mark.parametrize("handle", ["my-agent", "agent42", "a-b-c", "abc"])
    def test_valid_handles_accepted(self, handle: str) -> None:
        assert wizard._validate_handle(handle) == handle

    def test_lowercases_input(self) -> None:
        assert wizard._validate_handle("My-Agent") == "my-agent"

    def test_strips_whitespace(self) -> None:
        assert wizard._validate_handle("  my-agent  ") == "my-agent"

    @pytest.mark.parametrize(
        "handle",
        [
            "ab",  # too short
            "a" * 33,  # too long
            "-leading",  # leading hyphen
            "with space",  # space
            "with.dot",  # dot
            "with_underscore",  # underscore
            "with!bang",
        ],
    )
    def test_invalid_handles_raise(self, handle: str) -> None:
        with pytest.raises(InvalidHandleError):
            wizard._validate_handle(handle)


class TestSetEnvVar:
    def test_appends_to_empty_file(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        wizard._set_env_var(env, "X", "y")
        assert env.read_text() == "X=y\n"

    def test_replaces_existing_key(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("OTHER=keep\nX=old\nALSO=keep\n")
        wizard._set_env_var(env, "X", "new")
        body = env.read_text()
        assert "X=new" in body
        assert "X=old" not in body
        assert "OTHER=keep" in body
        assert "ALSO=keep" in body

    def test_collapses_duplicate_key_definitions(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("X=one\nX=two\nX=three\n")
        wizard._set_env_var(env, "X", "final")
        lines = [line for line in env.read_text().splitlines() if line.startswith("X=")]
        assert lines == ["X=final"]

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        env = tmp_path / "nested" / "deep" / ".env"
        wizard._set_env_var(env, "X", "y")
        assert env.exists()

    def test_sets_mode_0600(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        wizard._set_env_var(env, "X", "y")
        mode = env.stat().st_mode & 0o777
        assert mode == 0o600


class TestRun:
    def test_happy_path_returns_result_and_persists(self, tmp_path: Path) -> None:
        register, calls = _fake_register_factory()
        env_path = tmp_path / ".env"
        soul_path = tmp_path / "SOUL.md"
        messages: list[str] = []

        result = wizard.run(
            handle="my-agent",
            display_name="My Agent",
            bio="hi",
            env_path=env_path,
            soul_path=soul_path,
            register_fn=register,
            on_message=messages.append,
        )

        assert result.handle == "my-agent"
        assert result.api_key == "col_fresh_test_key"
        assert result.user_id == "u-test"
        assert result.env_path == env_path
        assert result.soul_path == soul_path

        # Underlying register call shape
        assert len(calls) == 1
        assert calls[0]["handle"] == "my-agent"
        assert calls[0]["display_name"] == "My Agent"
        assert calls[0]["bio"] == "hi"

        # Persisted artifacts
        env_body = env_path.read_text()
        assert f"{API_KEY_ENV_VAR}=col_fresh_test_key" in env_body
        soul_body = soul_path.read_text()
        assert "@my-agent" in soul_body
        # Messages mentioned both the success line and the warning.
        joined = "\n".join(messages)
        assert "Registered as @my-agent" in joined
        assert "API KEY" in joined
        assert "col_fresh_test_key" in joined

    def test_canonicalises_uppercase_handle(self, tmp_path: Path) -> None:
        register, calls = _fake_register_factory()
        wizard.run(
            handle="My-Agent",
            display_name="X",
            env_path=tmp_path / ".env",
            soul_path=tmp_path / "SOUL.md",
            register_fn=register,
            on_message=lambda _: None,
        )
        assert calls[0]["handle"] == "my-agent"

    def test_invalid_handle_short_circuits_before_register(self, tmp_path: Path) -> None:
        register, calls = _fake_register_factory()
        with pytest.raises(InvalidHandleError):
            wizard.run(
                handle="ab",
                display_name="X",
                env_path=tmp_path / ".env",
                soul_path=tmp_path / "SOUL.md",
                register_fn=register,
                on_message=lambda _: None,
            )
        assert calls == []  # never called

    def test_register_returning_no_api_key_raises(self, tmp_path: Path) -> None:
        def bad_register(**kwargs):
            return SimpleNamespace(api_key=None, me=lambda: {})

        with pytest.raises(WizardError) as ei:
            wizard.run(
                handle="my-agent",
                display_name="X",
                env_path=tmp_path / ".env",
                soul_path=tmp_path / "SOUL.md",
                register_fn=bad_register,
                on_message=lambda _: None,
            )
        assert "api_key" in str(ei.value)

    def test_me_failure_does_not_break_registration(self, tmp_path: Path) -> None:
        def register(**kwargs):
            def me():
                raise RuntimeError("network blip")

            return SimpleNamespace(api_key="col_x", me=me)

        result = wizard.run(
            handle="my-agent",
            display_name="X",
            env_path=tmp_path / ".env",
            soul_path=tmp_path / "SOUL.md",
            register_fn=register,
            on_message=lambda _: None,
        )
        # api_key still persisted; user_id is empty (cosmetic).
        assert result.api_key == "col_x"
        assert result.user_id == ""

    def test_sets_env_var_in_current_process(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
        register, _ = _fake_register_factory(api_key="col_inproc")
        wizard.run(
            handle="my-agent",
            display_name="X",
            env_path=tmp_path / ".env",
            soul_path=tmp_path / "SOUL.md",
            register_fn=register,
            on_message=lambda _: None,
        )
        import os

        assert os.environ.get(API_KEY_ENV_VAR) == "col_inproc"
