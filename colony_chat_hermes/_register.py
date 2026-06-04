"""Hermes plugin registration hook.

The harness loads plugins by walking the ``hermes_agent.plugins``
entry-point group and calling each entry's ``register(harness)``.
We delegate to :func:`register_plugin` below so the public
``colony_chat_hermes.register`` symbol stays small.

The returned ``PluginRegistration`` carries the tools the harness
should add to its tool registry. v0.1 ships six core tools; Day 4
adds the daemon-side runtime (notification poller, webhook
receiver, message queue, agent invoker).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from colony_chat_hermes import tools
from colony_chat_hermes._version import __version__

PLUGIN_NAME = "colony_chat"
TOOL_PREFIX = "colony_chat_"


@dataclass
class PluginRegistration:
    """Returned to the harness on plugin load.

    Shape is intentionally simple — the harness reads ``tools`` and
    adds each entry to its tool registry, keyed by ``name``. Other
    fields are diagnostic.
    """

    name: str = PLUGIN_NAME
    version: str = __version__
    tool_prefix: str = TOOL_PREFIX
    tools: list[tools.Tool] = field(default_factory=list)


def register_plugin(harness: object) -> PluginRegistration:
    """Build the plugin's registration record.

    ``harness`` is opaque to this plugin in v0.1 — we don't poke at its
    internals. Day 4 wires the notification poller against the
    harness's agent-invocation API; until then we just register tools.
    """
    return PluginRegistration(tools=tools.build_all())
