"""Tests for the Hermes plugin registration hook."""

from __future__ import annotations

import json

import colony_chat_hermes
from colony_chat_hermes._register import PLUGIN_NAME, TOOL_PREFIX, register_plugin
from colony_chat_hermes._version import __version__


class _FakeCtx:
    """Stand-in for Hermes' PluginContext.register_tool contract."""

    def __init__(self) -> None:
        self.registered: list[dict] = []

    def register_tool(self, **kwargs) -> None:
        self.registered.append(kwargs)


class TestRegisterPlugin:
    def test_returns_registration_record(self) -> None:
        result = register_plugin(object())
        assert result.name == PLUGIN_NAME
        assert result.version == __version__
        assert result.tool_prefix == TOOL_PREFIX

    def test_includes_eleven_v01_tools(self) -> None:
        result = register_plugin(object())
        assert len(result.tools) == 11
        assert all(t.name.startswith("colony_chat_") for t in result.tools)

    def test_plugin_name_matches_manifest(self) -> None:
        # The plugin name in _register.py must match plugin.yaml — the
        # harness uses this string to scope tool calls + logs.
        assert PLUGIN_NAME == "colony_chat"

    def test_registers_tools_via_ctx(self) -> None:
        # Given a real PluginContext (has register_tool), every tool is
        # registered with the Hermes contract: name, toolset, full function
        # schema (args under "parameters"), and a callable handler.
        ctx = _FakeCtx()
        record = register_plugin(ctx)
        assert [r["name"] for r in ctx.registered] == [t.name for t in record.tools]
        for r in ctx.registered:
            assert r["toolset"] == "colony_chat"
            assert r["schema"]["parameters"]["type"] == "object"
            assert r["schema"]["name"] == r["name"]
            assert r["schema"]["description"]
            assert callable(r["handler"])
            assert r["is_async"] is False

    def test_ctx_handler_unpacks_args_and_returns_json(self) -> None:
        # The handler Hermes invokes is handler(args: dict, **kwargs) -> str:
        # it unpacks the model's args into the tool's invoke, JSON-encodes the
        # result, and ignores framework kwargs.
        from colony_chat_hermes._register import _make_handler

        captured = {}

        def _invoke(*, to, text):
            captured["to"], captured["text"] = to, text
            return {"message_id": "m-1", "to": to}

        tool = type("T", (), {"name": "colony_chat_send_dm", "invoke": staticmethod(_invoke)})()
        handler = _make_handler(tool)
        out = handler({"to": "alice", "text": "hi"}, parent_agent=object(), session_id="x")
        assert isinstance(out, str)
        assert json.loads(out)["message_id"] == "m-1"
        assert captured == {"to": "alice", "text": "hi"}


class TestPublicSurface:
    def test_module_exports_register_and_version(self) -> None:
        assert hasattr(colony_chat_hermes, "register")
        assert callable(colony_chat_hermes.register)
        assert colony_chat_hermes.__version__ == __version__

    def test_register_returns_record_via_public_callable(self) -> None:
        # The public ``register`` is what the harness calls. Smoke-test
        # the end-to-end path by skipping the git-clone shim (we're
        # running under pytest where colony-chat is already installed).
        record = colony_chat_hermes.register(object())
        assert record.name == "colony_chat"
        assert len(record.tools) == 11
