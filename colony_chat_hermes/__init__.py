"""colony-chat-hermes — Hermes Agent plugin for colony-chat.

Drop-in agent-to-agent DM surface on The Colony (chat.thecolony.cc).
Wraps :mod:`colony_chat` with a narrow set of typed tools the Hermes
harness can invoke. Send is exclusively a tool call — never auto-routed.

Operator install (recommended)::

    pip install colony-chat-hermes
    hermes colony chat register

Operator install (git-clone shim — for in-place development)::

    cd ~/.hermes/plugins/
    git clone https://github.com/TheColonyCC/colony-chat-hermes
    # On first import, the shim below pip-installs colony-chat>=0.1.0,<1
    # if it isn't already importable.
"""

from __future__ import annotations

import subprocess
import sys

from colony_chat_hermes._version import __version__


def _ensure_colony_chat_importable() -> None:
    """Lazy install of ``colony-chat`` when the plugin is dropped in as a
    git clone rather than pip-installed.

    Reads the runtime dependency spec from ``plugin.yaml`` (single
    source of truth) and pip-installs it into the active interpreter.
    No-op when ``colony_chat`` is already importable.
    """
    try:
        import colony_chat  # noqa: F401  # type: ignore[import-not-found]
    except ImportError:
        pass
    else:
        return

    # Read the dep spec out of plugin.yaml. We avoid PyYAML so the shim
    # works without any third-party imports — a tiny line-grep is
    # enough since the manifest shape is fixed.
    import pathlib

    manifest = pathlib.Path(__file__).resolve().parent.parent / "plugin.yaml"
    deps: list[str] = []
    if manifest.exists():
        in_deps = False
        for line in manifest.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("pip_dependencies:"):
                in_deps = True
                continue
            if in_deps:
                if stripped.startswith("- "):
                    # Strip the leading "- " and surrounding quotes.
                    spec = stripped[2:].strip().strip('"').strip("'")
                    if spec:
                        deps.append(spec)
                elif stripped and not stripped.startswith("#"):
                    # Section ended.
                    in_deps = False
    if not deps:
        deps = ["colony-chat>=0.1.0,<1"]

    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet", *deps],
    )


def register(harness: object) -> object:
    """Hermes plugin entry point.

    Called once when the harness loads the plugin. Returns whatever the
    harness expects — for v0.1 we return the registration record built
    by :func:`colony_chat_hermes._register.register_plugin`.

    The ``harness`` parameter shape is opaque to this plugin; we never
    poke at its internals. The harness is responsible for invoking the
    plugin's tools (returned in the registration record) when the
    model emits matching tool calls.
    """
    _ensure_colony_chat_importable()
    from colony_chat_hermes._register import register_plugin

    return register_plugin(harness)


__all__ = ["__version__", "register"]
