"""Hermes plugin registration hook.

Hermes loads a directory plugin by importing its ``__init__.py`` and calling
``register(ctx)`` with a ``PluginContext``. Tools are added to the global tool
registry by calling ``ctx.register_tool(name, toolset, schema, handler, ...)``
for each one — the harness does **not** read a return value. We also return a
``PluginRegistration`` record for introspection/tests (Hermes ignores it).

The daemon-side runtime (notification poller, webhook receiver, message queue,
agent invoker) is a separate process started via ``colony-chat-hermes daemon``
and wires in through a pluggable invoker callable (see ``--invoker``), so this
entry point only needs to register tools.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from colony_chat_hermes import tools
from colony_chat_hermes._version import __version__

PLUGIN_NAME = "colony_chat"
TOOL_PREFIX = "colony_chat_"
#: Hermes toolset key the tools are grouped under (toggled via `hermes tools`).
TOOLSET = "colony_chat"


@dataclass
class PluginRegistration:
    """Introspection record returned from :func:`register_plugin`.

    Hermes ignores this — tools are registered via ``ctx.register_tool`` — but
    tests and other callers read it.
    """

    name: str = PLUGIN_NAME
    version: str = __version__
    tool_prefix: str = TOOL_PREFIX
    tools: list[tools.Tool] = field(default_factory=list)


def _make_handler(tool: tools.Tool):
    """Adapt a colony_chat ``Tool`` to the Hermes registry handler contract.

    Hermes dispatches tools as ``handler(args: dict, **kwargs) -> str`` (see
    ``tools.registry.dispatch``). We unpack the model's args into the tool's
    keyword-only ``invoke`` and JSON-encode the result so the model receives a
    string, ignoring any framework kwargs (parent_agent, session_id, …).
    """

    def handler(args: dict | None = None, **_kwargs) -> str:
        result = tool.invoke(**(args or {}))
        return json.dumps(result, default=str)

    handler.__name__ = tool.name
    return handler


def register_plugin(ctx: object) -> PluginRegistration:
    """Register the plugin's tools with the Hermes harness.

    Hermes calls ``register(ctx)`` with a ``PluginContext`` exposing
    ``register_tool(...)``; we call it once per tool so they appear alongside
    the built-in tools. ``ctx`` may be a non-Hermes object in tests (no
    ``register_tool``) — then we just return the record.
    """
    reg = PluginRegistration(tools=tools.build_all())
    register_tool = getattr(ctx, "register_tool", None)
    if callable(register_tool):
        for t in reg.tools:
            # Hermes' registry expects ``schema`` to be the full OpenAI function
            # object — ``get_definitions`` emits ``{"type":"function","function":
            # {**schema, "name": ...}}``. The args JSON-schema must live under a
            # ``parameters`` key; a bare parameters object => zero-arg tool.
            register_tool(
                name=t.name,
                toolset=TOOLSET,
                schema={
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
                handler=_make_handler(t),
                description=t.description,
                is_async=False,
                emoji="💬",
            )
    return reg
