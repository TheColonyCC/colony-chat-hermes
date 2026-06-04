"""Tests for the typed tool surface.

Verifies delegation correctness (every tool routes its inputs into the
matching `colony_chat` method) and schema soundness (the JSON Schemas
the tools advertise reflect the kwargs `invoke` actually accepts).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from colony_chat_hermes import tools as tools_pkg
from colony_chat_hermes.tools import _common, dms, safety


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Stub `build_client()` everywhere with a MagicMock and return it."""
    mock = MagicMock()
    # Patch the symbol in every tool module that imported it.
    monkeypatch.setattr(_common, "build_client", lambda: mock)
    monkeypatch.setattr(dms, "build_client", lambda: mock)
    monkeypatch.setattr(safety, "build_client", lambda: mock)
    return mock


class TestBuildAll:
    def test_returns_six_tools_in_stable_order(self) -> None:
        all_tools = tools_pkg.build_all()
        assert [t.name for t in all_tools] == [
            "colony_chat_send_dm",
            "colony_chat_get_thread",
            "colony_chat_list_conversations",
            "colony_chat_react",
            "colony_chat_block",
            "colony_chat_mark_spam",
        ]

    def test_every_tool_has_required_fields(self) -> None:
        for t in tools_pkg.build_all():
            assert t.name and t.name.startswith("colony_chat_")
            assert t.description
            assert isinstance(t.parameters, dict)
            assert t.parameters.get("type") == "object"
            assert "properties" in t.parameters
            assert callable(t.invoke)

    def test_every_tool_disallows_additional_properties(self) -> None:
        # additionalProperties: false on every tool — closes the door on
        # silent acceptance of malformed model-emitted kwargs.
        for t in tools_pkg.build_all():
            assert t.parameters.get("additionalProperties") is False


class TestSendDm:
    def test_delegates_username_and_body(self, fake_client: MagicMock) -> None:
        tool = dms.build_send_dm()
        tool.invoke(username="alice", body="hi")
        fake_client.send.assert_called_once_with(to="alice", text="hi", idempotency_key=None)

    def test_forwards_idempotency_key(self, fake_client: MagicMock) -> None:
        tool = dms.build_send_dm()
        tool.invoke(username="alice", body="hi", idempotency_key="k1")
        fake_client.send.assert_called_once_with(to="alice", text="hi", idempotency_key="k1")

    def test_schema_marks_username_and_body_required(self) -> None:
        tool = dms.build_send_dm()
        assert tool.parameters["required"] == ["username", "body"]
        assert "idempotency_key" not in tool.parameters["required"]


class TestGetThread:
    def test_delegates_username_to_thread(self, fake_client: MagicMock) -> None:
        tool = dms.build_get_thread()
        tool.invoke(username="alice")
        fake_client.thread.assert_called_once_with(with_="alice")


class TestListConversations:
    def test_delegates_to_contacts(self, fake_client: MagicMock) -> None:
        tool = dms.build_list_conversations()
        tool.invoke()
        fake_client.contacts.assert_called_once_with()

    def test_schema_has_no_required_params(self) -> None:
        tool = dms.build_list_conversations()
        assert tool.parameters["properties"] == {}


class TestReact:
    def test_delegates_message_id_and_emoji(self, fake_client: MagicMock) -> None:
        tool = dms.build_react()
        tool.invoke(message_id="m1", emoji="🔥")
        fake_client.react.assert_called_once_with(message_id="m1", emoji="🔥")


class TestBlock:
    def test_delegates_handle(self, fake_client: MagicMock) -> None:
        tool = safety.build_block()
        tool.invoke(username="alice")
        fake_client.block.assert_called_once_with(handle="alice")


class TestMarkSpam:
    def test_defaults_reason_code_to_spam(self, fake_client: MagicMock) -> None:
        tool = safety.build_mark_spam()
        tool.invoke(username="alice")
        fake_client.mark_spam.assert_called_once_with(
            handle="alice", reason_code="spam", description=None
        )

    def test_forwards_reason_code_and_description(self, fake_client: MagicMock) -> None:
        tool = safety.build_mark_spam()
        tool.invoke(
            username="alice",
            reason_code="harassment",
            description="repeat slurs",
        )
        fake_client.mark_spam.assert_called_once_with(
            handle="alice", reason_code="harassment", description="repeat slurs"
        )

    def test_schema_lists_reason_code_enum(self) -> None:
        tool = safety.build_mark_spam()
        enum = tool.parameters["properties"]["reason_code"]["enum"]
        assert set(enum) == {
            "spam",
            "harassment",
            "misinformation",
            "off_topic",
            "prompt_injection",
            "other",
        }


class TestBuildClient:
    def test_raises_when_api_key_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("COLONY_CHAT_API_KEY", raising=False)
        with pytest.raises(RuntimeError) as ei:
            _common.build_client()
        assert "COLONY_CHAT_API_KEY" in str(ei.value)
        assert "register" in str(ei.value)

    def test_constructs_with_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COLONY_CHAT_API_KEY", "col_test")
        captured: dict[str, Any] = {}

        class FakeColonyChat:
            def __init__(self, **kwargs: Any) -> None:
                captured.update(kwargs)

        import sys
        import types

        fake_module = types.ModuleType("colony_chat")
        fake_module.ColonyChat = FakeColonyChat  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "colony_chat", fake_module)

        _common.build_client()
        assert captured["api_key"] == "col_test"

    def test_threads_base_url_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COLONY_CHAT_API_KEY", "col_test")
        monkeypatch.setenv("COLONY_CHAT_API_BASE", "https://staging.example/api/v1")
        captured: dict[str, Any] = {}

        class FakeColonyChat:
            def __init__(self, **kwargs: Any) -> None:
                captured.update(kwargs)

        import sys
        import types

        fake = types.ModuleType("colony_chat")
        fake.ColonyChat = FakeColonyChat  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "colony_chat", fake)

        _common.build_client()
        assert captured["base_url"] == "https://staging.example/api/v1"

    def test_threads_cold_cap_envs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COLONY_CHAT_API_KEY", "col_test")
        monkeypatch.setenv("COLONY_CHAT_COLD_DM_CAP_PER_DAY", "50")
        monkeypatch.setenv("COLONY_CHAT_ENFORCE_COLD_CAP", "false")
        captured: dict[str, Any] = {}

        class FakeColonyChat:
            def __init__(self, **kwargs: Any) -> None:
                captured.update(kwargs)

        import sys
        import types

        fake = types.ModuleType("colony_chat")
        fake.ColonyChat = FakeColonyChat  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "colony_chat", fake)

        _common.build_client()
        assert captured["enforce_cold_cap"] is False
        assert captured["cold_cap_per_day"] == 50

    def test_invalid_cap_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("COLONY_CHAT_API_KEY", "col_test")
        monkeypatch.setenv("COLONY_CHAT_COLD_DM_CAP_PER_DAY", "not-a-number")
        captured: dict[str, Any] = {}

        class FakeColonyChat:
            def __init__(self, **kwargs: Any) -> None:
                captured.update(kwargs)

        import sys
        import types

        fake = types.ModuleType("colony_chat")
        fake.ColonyChat = FakeColonyChat  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "colony_chat", fake)

        _common.build_client()
        # Cap not threaded — falls back to colony-chat's default.
        assert "cold_cap_per_day" not in captured
