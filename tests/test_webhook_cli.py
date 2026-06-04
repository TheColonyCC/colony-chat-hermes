"""Tests for the ``webhook setup / list / delete`` subcommands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from colony_chat_hermes import webhook_cli


def _client_factory(client: MagicMock) -> object:
    """Return a callable matching the (env_path) -> client signature."""

    def _f(_env_path: Path) -> MagicMock:
        return client

    return _f


class TestSetup:
    def test_requires_https_url(self, tmp_path: Path) -> None:
        rc, msg = webhook_cli.cmd_setup(
            url="http://insecure.example",
            env_path=tmp_path / ".env",
            client_factory=lambda _e: MagicMock(),
        )
        assert rc == 2
        assert "https://" in msg

    def test_returns_1_without_api_key(self, tmp_path: Path) -> None:
        rc, msg = webhook_cli.cmd_setup(
            url="https://x.example/webhook",
            env_path=tmp_path / ".env",
            client_factory=lambda _e: None,
        )
        assert rc == 1
        assert "no api_key" in msg

    def test_happy_path_persists_secret_and_id(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        client = MagicMock()
        client.subscribe_webhook.return_value = {"id": "wh-123"}
        rc, msg = webhook_cli.cmd_setup(
            url="https://x.example/webhook",
            env_path=env,
            secret_override="s3cret-xxxxx",
            client_factory=_client_factory(client),
        )
        assert rc == 0
        assert "wh-123" in msg
        body = env.read_text()
        assert "COLONY_CHAT_WEBHOOK_SECRET=s3cret-xxxxx" in body
        assert "COLONY_CHAT_WEBHOOK_ID=wh-123" in body
        # subscribe_webhook called with expected kwargs
        client.subscribe_webhook.assert_called_once()
        kwargs = client.subscribe_webhook.call_args.kwargs
        assert kwargs["url"] == "https://x.example/webhook"
        assert kwargs["secret"] == "s3cret-xxxxx"
        assert kwargs["events"] == ["direct_message"]

    def test_custom_events(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.subscribe_webhook.return_value = {"id": "wh-1"}
        webhook_cli.cmd_setup(
            url="https://x.example/webhook",
            env_path=tmp_path / ".env",
            events=["direct_message", "message_reaction"],
            secret_override="s",
            client_factory=_client_factory(client),
        )
        assert client.subscribe_webhook.call_args.kwargs["events"] == [
            "direct_message",
            "message_reaction",
        ]

    def test_handles_subscribe_failure(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.subscribe_webhook.side_effect = RuntimeError("rate limited")
        rc, msg = webhook_cli.cmd_setup(
            url="https://x.example/webhook",
            env_path=tmp_path / ".env",
            secret_override="s",
            client_factory=_client_factory(client),
        )
        assert rc == 1
        assert "rate limited" in msg

    def test_handles_missing_id_in_response(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.subscribe_webhook.return_value = {"queued": True}
        rc, msg = webhook_cli.cmd_setup(
            url="https://x.example/webhook",
            env_path=tmp_path / ".env",
            secret_override="s",
            client_factory=_client_factory(client),
        )
        assert rc == 1
        assert "no id" in msg

    def test_preserves_other_env_lines(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("COLONY_CHAT_API_KEY=col_existing\nOTHER=value\n")
        client = MagicMock()
        client.subscribe_webhook.return_value = {"id": "wh-1"}
        webhook_cli.cmd_setup(
            url="https://x.example/webhook",
            env_path=env,
            secret_override="s",
            client_factory=_client_factory(client),
        )
        body = env.read_text()
        assert "COLONY_CHAT_API_KEY=col_existing" in body
        assert "OTHER=value" in body
        assert "COLONY_CHAT_WEBHOOK_SECRET=s" in body

    def test_replaces_existing_webhook_env_vars(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("COLONY_CHAT_WEBHOOK_SECRET=old_secret\nCOLONY_CHAT_WEBHOOK_ID=wh-old\n")
        client = MagicMock()
        client.subscribe_webhook.return_value = {"id": "wh-new"}
        webhook_cli.cmd_setup(
            url="https://x.example/webhook",
            env_path=env,
            secret_override="new_secret",
            client_factory=_client_factory(client),
        )
        body = env.read_text()
        assert "COLONY_CHAT_WEBHOOK_SECRET=new_secret" in body
        assert "COLONY_CHAT_WEBHOOK_ID=wh-new" in body
        assert "old_secret" not in body
        assert "wh-old" not in body


class TestList:
    def test_empty(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.list_webhooks.return_value = []
        rc, msg = webhook_cli.cmd_list(
            env_path=tmp_path / ".env", client_factory=_client_factory(client)
        )
        assert rc == 0
        assert "no webhooks" in msg

    def test_renders_each_webhook(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.list_webhooks.return_value = [
            {
                "id": "wh-1",
                "url": "https://x.example/a",
                "is_active": True,
                "events": ["direct_message"],
            },
            {
                "id": "wh-2",
                "url": "https://x.example/b",
                "is_active": False,
                "events": ["direct_message", "message_reaction"],
            },
        ]
        rc, msg = webhook_cli.cmd_list(
            env_path=tmp_path / ".env", client_factory=_client_factory(client)
        )
        assert rc == 0
        assert "wh-1" in msg and "wh-2" in msg
        assert "active" in msg
        assert "DISABLED" in msg
        assert "direct_message,message_reaction" in msg

    def test_returns_1_without_api_key(self, tmp_path: Path) -> None:
        rc, msg = webhook_cli.cmd_list(env_path=tmp_path / ".env", client_factory=lambda _e: None)
        assert rc == 1
        assert "api_key" in msg

    def test_handles_list_failure(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.list_webhooks.side_effect = RuntimeError("server bonk")
        rc, msg = webhook_cli.cmd_list(
            env_path=tmp_path / ".env", client_factory=_client_factory(client)
        )
        assert rc == 1
        assert "server bonk" in msg


class TestDelete:
    def test_deletes(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("OTHER=keep\n")
        client = MagicMock()
        rc, _ = webhook_cli.cmd_delete(
            webhook_id="wh-1",
            env_path=env,
            client_factory=_client_factory(client),
        )
        assert rc == 0
        client.unsubscribe_webhook.assert_called_once_with("wh-1")
        # Persisted .env should remain unchanged because this wasn't the
        # currently-bound webhook.
        assert env.read_text() == "OTHER=keep\n"

    def test_clears_env_when_deleting_currently_bound(self, tmp_path: Path) -> None:
        env = tmp_path / ".env"
        env.write_text("COLONY_CHAT_WEBHOOK_SECRET=s\nCOLONY_CHAT_WEBHOOK_ID=wh-1\nOTHER=keep\n")
        client = MagicMock()
        rc, msg = webhook_cli.cmd_delete(
            webhook_id="wh-1",
            env_path=env,
            client_factory=_client_factory(client),
        )
        assert rc == 0
        assert "cleared persisted env vars" in msg
        body = env.read_text()
        assert "WEBHOOK_SECRET" not in body
        assert "WEBHOOK_ID" not in body
        assert "OTHER=keep" in body

    def test_handles_unsubscribe_failure(self, tmp_path: Path) -> None:
        client = MagicMock()
        client.unsubscribe_webhook.side_effect = RuntimeError("not found")
        rc, msg = webhook_cli.cmd_delete(
            webhook_id="wh-1",
            env_path=tmp_path / ".env",
            client_factory=_client_factory(client),
        )
        assert rc == 1
        assert "not found" in msg

    def test_returns_1_without_api_key(self, tmp_path: Path) -> None:
        rc, msg = webhook_cli.cmd_delete(
            webhook_id="wh-1",
            env_path=tmp_path / ".env",
            client_factory=lambda _e: None,
        )
        assert rc == 1
        assert "api_key" in msg


class TestCli:
    def test_doctor_via_cli(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from colony_chat_hermes import cli

        monkeypatch.delenv(cli.API_KEY_ENV_VAR, raising=False)
        rc = cli.main(
            [
                "doctor",
                "--env-path",
                str(tmp_path / ".env"),
                "--soul-path",
                str(tmp_path / "SOUL.md"),
                "--lock-path",
                str(tmp_path / ".lock"),
                "--invoker",
                "log_only",
            ]
        )
        assert rc == 1  # no api_key

    def test_webhook_list_via_cli(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from colony_chat_hermes import cli

        monkeypatch.delenv(cli.API_KEY_ENV_VAR, raising=False)
        rc = cli.main(["webhook", "list", "--env-path", str(tmp_path / ".env")])
        assert rc == 1  # no api_key
