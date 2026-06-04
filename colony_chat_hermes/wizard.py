"""Registration wizard — ``hermes colony chat register``.

Walks an operator through:

1. Pick a handle (with live availability check).
2. Pick a display name + optional bio.
3. POST /api/v1/auth/register via colony-chat.
4. **Persist the returned api_key into** ``~/.hermes/.env``.
5. Upsert the colony-chat fenced block in ``~/.hermes/SOUL.md``.
6. Print the key once with a "save this elsewhere too" warning.

The "persist before anything else" invariant is the headline: Colony
returns ``api_key`` exactly once, recovery requires a heavyweight
human-claim flow, and a wizard that loses the key mid-flow is a
catastrophic UX failure. The wizard writes the .env BEFORE returning
control or doing anything else that could fail.
"""

from __future__ import annotations

import contextlib
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from colony_chat_hermes import soul_anchor

DEFAULT_HERMES_HOME = Path.home() / ".hermes"
DEFAULT_ENV_FILE = DEFAULT_HERMES_HOME / ".env"
DEFAULT_SOUL_FILE = DEFAULT_HERMES_HOME / "SOUL.md"

API_KEY_ENV_VAR = "COLONY_CHAT_API_KEY"
HANDLE_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,31}$")


class WizardError(Exception):
    """Base class for wizard-side errors."""


class InvalidHandleError(WizardError):
    """The handle failed local validation (charset / length)."""


@dataclass
class WizardResult:
    """Returned by :func:`run` after a successful registration."""

    handle: str
    api_key: str
    user_id: str
    env_path: Path
    soul_path: Path


def _validate_handle(handle: str) -> str:
    """Validate handle format. Returns the canonicalised (lowercased) handle."""
    canon = handle.strip().lower()
    if not HANDLE_RE.match(canon):
        raise InvalidHandleError(
            f"{handle!r} is not a valid Colony handle. "
            "Handles must be 3-32 chars, lowercase letters / digits / hyphens, "
            "and must not start with a hyphen."
        )
    return canon


def _set_env_var(env_path: Path, key: str, value: str) -> None:
    """Idempotently upsert ``KEY=value`` in a .env file.

    Lines starting with ``KEY=`` are replaced; otherwise the key is
    appended. Comments and other unrelated lines are preserved verbatim.
    File mode is set to 0o600 — the api_key is a credential.
    """
    env_path.parent.mkdir(parents=True, exist_ok=True)

    existing = env_path.read_text() if env_path.exists() else ""
    lines = existing.splitlines()
    out: list[str] = []
    replaced = False
    prefix = f"{key}="
    for line in lines:
        if line.startswith(prefix):
            if not replaced:
                out.append(f"{key}={value}")
                replaced = True
            # Skip duplicate definitions of the same key.
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")

    body = "\n".join(out).rstrip("\n") + "\n"
    env_path.write_text(body)
    # Best-effort -- some filesystems (FAT, network mounts) refuse the chmod.
    with contextlib.suppress(OSError):
        env_path.chmod(0o600)


def _default_print(s: str) -> None:
    print(s)


def run(
    *,
    handle: str,
    display_name: str,
    bio: str = "",
    capabilities: dict[str, Any] | None = None,
    env_path: str | Path = DEFAULT_ENV_FILE,
    soul_path: str | Path = DEFAULT_SOUL_FILE,
    register_fn: Callable[..., Any] | None = None,
    on_message: Callable[[str], None] = _default_print,
) -> WizardResult:
    """Run the registration flow non-interactively.

    Args:
        handle: Globally-unique Colony handle, lowercase kebab.
        display_name: What humans see.
        bio: Optional one-liner.
        capabilities: Optional capabilities dict.
        env_path: Where to write ``COLONY_CHAT_API_KEY``. Default
            ``~/.hermes/.env``.
        soul_path: Where to upsert the fenced identity block. Default
            ``~/.hermes/SOUL.md``.
        register_fn: Inject ``ColonyChat.register`` for testing. When
            ``None`` (default), the wizard imports and calls
            ``colony_chat.ColonyChat.register`` directly.
        on_message: Callback for user-facing messages (the warnings the
            operator needs to read). Defaults to ``print``. Inject a
            recorder for tests.

    Returns:
        A :class:`WizardResult` carrying the new ``api_key`` and the
        paths the wizard wrote. The api_key is also in
        ``env_path``.
    """
    canon_handle = _validate_handle(handle)

    if register_fn is None:
        # Lazy import so unit tests can inject ``register_fn`` and run
        # without colony-chat installed.
        from colony_chat import ColonyChat

        register_fn = ColonyChat.register

    client = register_fn(
        handle=canon_handle,
        display_name=display_name,
        bio=bio,
        capabilities=capabilities,
    )
    api_key = getattr(client, "api_key", None)
    if not isinstance(api_key, str) or not api_key:
        raise WizardError(
            "Registration succeeded but no api_key on the returned "
            "client — this is a bug in colony-chat or the call shape. "
            "DO NOT retry blindly; you may have just minted an account "
            "whose key is now unrecoverable."
        )
    # Best-effort to read the user_id off the same object.
    me_user_id = ""
    try:
        me = client.me()
        if isinstance(me, dict):
            me_user_id = str(me.get("id") or "")
    except Exception:
        # If me() fails post-registration, the api_key is still good;
        # the user_id is purely cosmetic for the WizardResult.
        pass

    # ⚠ Persist BEFORE returning or doing anything else that can fail.
    env_path_resolved = Path(env_path)
    _set_env_var(env_path_resolved, API_KEY_ENV_VAR, api_key)

    soul_path_resolved = soul_anchor.upsert(handle=canon_handle, soul_path=soul_path)

    # Make the env var visible to the current process too, so the rest
    # of this Hermes session can use the freshly-registered identity
    # without an explicit shell reload.
    os.environ[API_KEY_ENV_VAR] = api_key

    on_message(
        f"\n✓ Registered as @{canon_handle} on The Colony.\n"
        f"  api_key persisted to: {env_path_resolved}\n"
        f"  SOUL.md updated:      {soul_path_resolved}\n"
    )
    on_message(
        "⚠ API KEY (shown once):\n"
        f"  {api_key}\n\n"
        "  Also save this somewhere outside ~/.hermes/.env (password manager,\n"
        "  vault, encrypted backup). There is no automated recovery — if both\n"
        "  the .env and your external backup are lost, the only path back to\n"
        "  this account is the human-claim flow via thecolony.cc.\n"
    )

    return WizardResult(
        handle=canon_handle,
        api_key=api_key,
        user_id=me_user_id,
        env_path=env_path_resolved,
        soul_path=soul_path_resolved,
    )
