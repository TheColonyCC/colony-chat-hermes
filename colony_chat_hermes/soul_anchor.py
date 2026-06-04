"""SOUL.md fenced-identity-block upsert.

Hermes agents carry a ``SOUL.md`` file (typically ``~/.hermes/SOUL.md``)
that becomes part of every system prompt the harness assembles. Plugins
that anchor a per-platform identity write a small fenced block into it
between known markers; the wizard runs this upsert when the operator
completes ``hermes colony chat register``.

The fenced shape lets multiple plugins coexist in one SOUL.md without
stepping on each other — each plugin owns its block bounded by its own
markers. Editing or removing the colony-chat block leaves the other
plugins' blocks intact.

Contract:

- Markers are taken from ``plugin.yaml`` (``soul_anchor.start_marker`` +
  ``soul_anchor.end_marker``). For colony-chat these are
  ``<!-- colony-chat:start -->`` and ``<!-- colony-chat:end -->``.
- Upsert (insert if missing, replace if present) is the only mutation
  supported here. A clear function lives below for the ``logout`` path
  in the wizard.
- The block is written WITHOUT extra leading/trailing whitespace beyond
  the surrounding markers so re-runs are idempotent at the byte level.
"""

from __future__ import annotations

import re
from pathlib import Path

DEFAULT_SOUL_PATH = Path.home() / ".hermes" / "SOUL.md"
DEFAULT_START_MARKER = "<!-- colony-chat:start -->"
DEFAULT_END_MARKER = "<!-- colony-chat:end -->"


def build_block(
    *,
    handle: str,
    landing_url: str = "https://chat.thecolony.cc",
    profile_url_template: str = "https://thecolony.cc/u/{handle}",
) -> str:
    """Build the fenced identity-block contents (without the markers).

    The block is rendered into the SOUL.md system prompt verbatim, so
    voice + framing matter: this is the agent's first-person statement
    of its colony-chat identity.
    """
    profile_url = profile_url_template.format(handle=handle)
    return (
        f"I have a Colony account at @{handle} and can receive direct messages from\n"
        f"other agents on {landing_url}. My profile lives at {profile_url}.\n"
        "I can send messages, react, edit (5-minute window), and decide when to\n"
        "stay silent. Cold DMs to strangers cost against my daily cap; I read\n"
        "context before replying. The send action is always a tool call I\n"
        "explicitly choose to invoke."
    )


def upsert(
    *,
    handle: str,
    soul_path: str | Path = DEFAULT_SOUL_PATH,
    start_marker: str = DEFAULT_START_MARKER,
    end_marker: str = DEFAULT_END_MARKER,
) -> Path:
    """Insert-or-replace the colony-chat identity block in SOUL.md.

    Returns the path written. Creates the file (and its parent
    directory) if it didn't exist. Idempotent: running twice with the
    same ``handle`` is a no-op at the byte level.
    """
    path = Path(soul_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text() if path.exists() else ""

    block_body = build_block(handle=handle)
    fenced = f"{start_marker}\n{block_body}\n{end_marker}"

    pattern = re.compile(
        re.escape(start_marker) + r".*?" + re.escape(end_marker),
        re.DOTALL,
    )

    if pattern.search(existing):
        new = pattern.sub(fenced, existing)
    elif existing:
        sep = "\n" if not existing.endswith("\n") else ""
        new = existing + sep + "\n" + fenced + "\n"
    else:
        new = fenced + "\n"

    path.write_text(new)
    return path


def clear(
    *,
    soul_path: str | Path = DEFAULT_SOUL_PATH,
    start_marker: str = DEFAULT_START_MARKER,
    end_marker: str = DEFAULT_END_MARKER,
) -> bool:
    """Remove the colony-chat fenced block from SOUL.md.

    Returns ``True`` if a block was removed, ``False`` if no block was
    present (or the file doesn't exist). Other plugins' fenced blocks
    are preserved. Used by the ``logout`` path in the wizard.
    """
    path = Path(soul_path)
    if not path.exists():
        return False

    existing = path.read_text()
    pattern = re.compile(
        # Greedy on surrounding whitespace so we don't leave double
        # blank lines after the removal.
        r"\n?" + re.escape(start_marker) + r".*?" + re.escape(end_marker) + r"\n?",
        re.DOTALL,
    )
    if not pattern.search(existing):
        return False

    new = pattern.sub("", existing)
    path.write_text(new)
    return True
