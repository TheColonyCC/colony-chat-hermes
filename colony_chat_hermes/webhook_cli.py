"""``colony-chat-hermes webhook setup / list / delete``.

Wraps the multi-step Mode A onboarding into one command:

1. Generate a fresh HMAC secret with ``secrets.token_urlsafe(32)``.
2. ``POST`` the webhook subscription to Colony.
3. Persist both ``COLONY_CHAT_WEBHOOK_SECRET`` and
   ``COLONY_CHAT_WEBHOOK_ID`` into the operator's ``.env`` (mode 0600).
4. Print a systemd unit hint so the operator can wire production.

The secret is generated CLIENT-SIDE so the operator never has to
trust an HTTP transport for the secret material — the only place the
secret lives in plaintext is the local .env and the platform's
encrypted store.

``webhook list`` and ``webhook delete <id>`` are bundled here for
day-2 maintenance.
"""

from __future__ import annotations

import contextlib
import os
import secrets
from pathlib import Path
from typing import Any

from colony_chat_hermes.tools._common import API_KEY_ENV_VAR

WEBHOOK_SECRET_ENV = "COLONY_CHAT_WEBHOOK_SECRET"
WEBHOOK_ID_ENV = "COLONY_CHAT_WEBHOOK_ID"


def _read_env_var(env_path: Path, key: str) -> str | None:
    if not env_path.exists():
        return None
    prefix = f"{key}="
    for raw in env_path.read_text().splitlines():
        if raw.startswith(prefix):
            return raw[len(prefix) :]
    return None


def _set_env_vars(env_path: Path, values: dict[str, str]) -> None:
    """Insert-or-replace ``key=value`` lines in the .env, preserving others."""
    env_path.parent.mkdir(parents=True, exist_ok=True)
    existing: list[str] = []
    if env_path.exists():
        existing = env_path.read_text().splitlines()

    by_key: dict[str, int] = {}
    for i, line in enumerate(existing):
        if "=" in line:
            k = line.split("=", 1)[0]
            by_key[k] = i

    for k, v in values.items():
        line = f"{k}={v}"
        if k in by_key:
            existing[by_key[k]] = line
        else:
            existing.append(line)
            by_key[k] = len(existing) - 1

    body = "\n".join(existing)
    if body and not body.endswith("\n"):
        body += "\n"
    env_path.write_text(body)
    # On filesystems that don't support chmod (network mounts, WSL with
    # weird mounts), we still wrote the file — swallow the error.
    with contextlib.suppress(OSError):
        env_path.chmod(0o600)


def _build_client(env_path: Path) -> Any:
    api_key = os.environ.get(API_KEY_ENV_VAR) or _read_env_var(env_path, API_KEY_ENV_VAR)
    if not api_key:
        return None
    from colony_chat import ColonyChat

    kwargs: dict[str, Any] = {"api_key": api_key}
    base = os.environ.get("COLONY_CHAT_API_BASE")
    if base:
        kwargs["base_url"] = base
    return ColonyChat(**kwargs)


def cmd_setup(
    *,
    url: str,
    env_path: Path,
    events: list[str] | None = None,
    secret_override: str | None = None,
    client_factory: Any = _build_client,
) -> tuple[int, str]:
    """Subscribe a webhook + persist creds to .env.

    Returns (exit_code, message). The ``client_factory`` and
    ``secret_override`` parameters make this testable without
    monkeypatching globals.
    """
    if not url.startswith("https://"):
        return 2, f"--url must be https:// (got {url!r})"

    client = client_factory(env_path)
    if client is None:
        return 1, (
            f"no api_key configured (set {API_KEY_ENV_VAR} or run `colony-chat-hermes register`)"
        )

    secret = secret_override or secrets.token_urlsafe(32)
    try:
        result = client.subscribe_webhook(
            url=url,
            secret=secret,
            events=events or ["direct_message"],
        )
    except Exception as e:
        return 1, f"subscribe_webhook failed: {type(e).__name__}: {e}"

    webhook_id: str | None = None
    if isinstance(result, dict):
        webhook_id = result.get("id") or result.get("webhook_id")
    if not webhook_id:
        return 1, (
            f"subscribe_webhook returned no id (got {result!r}) — "
            "webhook may have been created but cannot persist id"
        )

    _set_env_vars(
        env_path,
        {WEBHOOK_SECRET_ENV: secret, WEBHOOK_ID_ENV: str(webhook_id)},
    )

    lines = [
        f"✓ Webhook subscribed: id={webhook_id}",
        f"  url:    {url}",
        f"  events: {','.join(events or ['direct_message'])}",
        f"  secret: {secret[:8]}…   (persisted to {env_path})",
        "",
        "Now start the daemon:",
        "  colony-chat-hermes daemon --mode both",
        "",
        "Systemd unit hint:",
        "  [Service]",
        f"  EnvironmentFile={env_path}",
        "  ExecStart=%h/.local/bin/colony-chat-hermes daemon --mode both",
        "  Restart=on-failure",
    ]
    return 0, "\n".join(lines)


def cmd_list(*, env_path: Path, client_factory: Any = _build_client) -> tuple[int, str]:
    """List the caller's registered webhooks."""
    client = client_factory(env_path)
    if client is None:
        return 1, "no api_key configured"
    try:
        hooks = client.list_webhooks()
    except Exception as e:
        return 1, f"list_webhooks failed: {type(e).__name__}: {e}"
    if not hooks:
        return 0, "(no webhooks registered)"
    lines = []
    for h in hooks:
        if not isinstance(h, dict):
            continue
        wid = h.get("id", "?")
        url = h.get("url", "?")
        active = h.get("is_active", True)
        evs = h.get("events") or []
        evs_str = ",".join(evs) if isinstance(evs, list) else str(evs)
        flag = "active" if active else "DISABLED"
        lines.append(f"  {wid}  [{flag}]  {url}  events=[{evs_str}]")
    return 0, "\n".join(lines)


def cmd_delete(
    *,
    webhook_id: str,
    env_path: Path,
    client_factory: Any = _build_client,
) -> tuple[int, str]:
    """Delete a webhook subscription."""
    client = client_factory(env_path)
    if client is None:
        return 1, "no api_key configured"
    try:
        client.unsubscribe_webhook(webhook_id)
    except Exception as e:
        return 1, f"unsubscribe_webhook failed: {type(e).__name__}: {e}"
    # If this is the webhook the operator's daemon is using, clear the
    # env vars too — silently leaving stale creds in .env causes the
    # next daemon start to log spurious "webhook missing" errors.
    persisted_id = _read_env_var(env_path, WEBHOOK_ID_ENV)
    if persisted_id == webhook_id:
        existing = env_path.read_text().splitlines() if env_path.exists() else []
        keep = [
            line
            for line in existing
            if not (
                line.startswith(f"{WEBHOOK_SECRET_ENV}=") or line.startswith(f"{WEBHOOK_ID_ENV}=")
            )
        ]
        body = "\n".join(keep)
        if body and not body.endswith("\n"):
            body += "\n"
        env_path.write_text(body)
        return 0, f"✓ Deleted webhook {webhook_id} (cleared persisted env vars too)"
    return 0, f"✓ Deleted webhook {webhook_id}"
