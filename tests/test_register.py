"""Tests for the Hermes plugin registration hook."""

from __future__ import annotations

import colony_chat_hermes
from colony_chat_hermes._register import PLUGIN_NAME, TOOL_PREFIX, register_plugin
from colony_chat_hermes._version import __version__


class TestRegisterPlugin:
    def test_returns_registration_record(self) -> None:
        result = register_plugin(harness=object())
        assert result.name == PLUGIN_NAME
        assert result.version == __version__
        assert result.tool_prefix == TOOL_PREFIX

    def test_includes_eleven_v01_tools(self) -> None:
        result = register_plugin(harness=object())
        assert len(result.tools) == 11
        assert all(t.name.startswith("colony_chat_") for t in result.tools)

    def test_plugin_name_matches_manifest(self) -> None:
        # The plugin name in _register.py must match plugin.yaml — the
        # harness uses this string to scope tool calls + logs.
        assert PLUGIN_NAME == "colony_chat"


class TestPublicSurface:
    def test_module_exports_register_and_version(self) -> None:
        assert hasattr(colony_chat_hermes, "register")
        assert callable(colony_chat_hermes.register)
        assert colony_chat_hermes.__version__ == __version__

    def test_register_returns_record_via_public_callable(self) -> None:
        # The public ``register`` is what the harness calls. Smoke-test
        # the end-to-end path by skipping the git-clone shim (we're
        # running under pytest where colony-chat is already installed).
        record = colony_chat_hermes.register(harness=object())
        assert record.name == "colony_chat"
        assert len(record.tools) == 11
