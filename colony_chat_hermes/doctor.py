"""``colony-chat-hermes doctor`` — diagnostic for first-run setup.

Walks a checklist of "everything an operator might have wrong on day
1" and reports each item with a ``✓`` / ``✗`` / ``⚠`` marker plus a
human-readable note. Read-only — never mutates state. Safe to run at
any time.

Checks
------

1.  ``COLONY_CHAT_API_KEY`` is set (env or persisted .env).
2.  ``colony_chat`` package is importable.
3.  ``client.me()`` succeeds — confirms the api_key is valid and
    points at a real account; prints the resolved handle.
4.  ``client.contacts()`` succeeds — confirms basic API reachability
    on a different endpoint.
5.  Leader-lock state — file exists / writable / not currently held
    by another process.
6.  SOUL.md fenced identity block presence — confirms the wizard's
    persistence path ran successfully.
7.  Invoker spec is resolvable — ``load_invoker(spec)`` doesn't
    raise. Catches typos in the env var / CLI flag.
8.  Mode A webhook config consistency (only when
    ``COLONY_CHAT_WEBHOOK_SECRET`` or ``COLONY_CHAT_WEBHOOK_ID`` is
    set) — both env vars present, webhook_id exists in the
    account's registered webhooks, and ``is_active`` is True.

Each check returns a tuple of (status, label, detail). Status is one
of ``"ok"``, ``"warn"``, ``"fail"``. Exit code is 0 if everything is
``ok`` or ``warn``; 1 if anything is ``fail``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from colony_chat_hermes.leader_lock import _DEFAULT_LOCK_DIR, _DEFAULT_LOCK_NAME
from colony_chat_hermes.soul_anchor import DEFAULT_END_MARKER, DEFAULT_START_MARKER
from colony_chat_hermes.tools._common import API_KEY_ENV_VAR

logger = logging.getLogger(__name__)

# Status constants — kept as strings rather than an enum to keep
# the rendering layer trivial (just dispatch on a string).
STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"

Status = str
CheckResult = tuple[Status, str, str]


def _read_env_var(env_path: Path, key: str) -> str | None:
    if not env_path.exists():
        return None
    prefix = f"{key}="
    for raw in env_path.read_text().splitlines():
        if raw.startswith(prefix):
            return raw[len(prefix) :]
    return None


def _resolve_api_key(env_path: Path) -> str | None:
    return os.environ.get(API_KEY_ENV_VAR) or _read_env_var(env_path, API_KEY_ENV_VAR)


def _check_api_key(env_path: Path) -> CheckResult:
    if _resolve_api_key(env_path) is None:
        return (
            STATUS_FAIL,
            "api_key configured",
            f"set {API_KEY_ENV_VAR} or run `colony-chat-hermes register`",
        )
    where = "env var" if os.environ.get(API_KEY_ENV_VAR) else f".env ({env_path})"
    return (STATUS_OK, "api_key configured", f"resolved from {where}")


def _check_sdk_importable() -> CheckResult:
    try:
        import colony_chat  # noqa: F401
    except ImportError as e:
        return (
            STATUS_FAIL,
            "colony_chat importable",
            f"ImportError: {e} — `pip install colony-chat`",
        )
    return (STATUS_OK, "colony_chat importable", "")


def _build_client(env_path: Path) -> Any:
    api_key = _resolve_api_key(env_path)
    if not api_key:
        return None
    from colony_chat import ColonyChat

    kwargs: dict[str, Any] = {"api_key": api_key}
    base = os.environ.get("COLONY_CHAT_API_BASE")
    if base:
        kwargs["base_url"] = base
    return ColonyChat(**kwargs)


def _check_me(client: Any) -> CheckResult:
    try:
        me = client.me()
    except Exception as e:
        return (
            STATUS_FAIL,
            "identity resolves",
            f"{type(e).__name__}: {e} — api_key may be revoked",
        )
    handle = me.get("username") if isinstance(me, dict) else None
    if not handle:
        return (
            STATUS_FAIL,
            "identity resolves",
            "me() returned a shape without ``username``",
        )
    karma = me.get("karma") if isinstance(me, dict) else None
    karma_note = ""
    if karma is not None and karma < 5:
        karma_note = (
            f" — karma={karma}; <5 means Colony blocks outbound DMs (receive works, send needs ≥5)"
        )
    return (
        STATUS_OK if not karma_note else STATUS_WARN,
        "identity resolves",
        f"@{handle}{karma_note}",
    )


def _check_contacts(client: Any) -> CheckResult:
    try:
        convs = client.contacts()
    except Exception as e:
        return (
            STATUS_FAIL,
            "api reachable",
            f"contacts() failed: {type(e).__name__}: {e}",
        )
    return (
        STATUS_OK,
        "api reachable",
        f"contacts() returned {len(convs)} conversation(s)",
    )


def _check_leader_lock(lock_path: Path) -> CheckResult:
    if not lock_path.parent.exists():
        return (
            STATUS_WARN,
            "leader-lock path writable",
            f"parent dir does not exist (will be created on daemon start): {lock_path.parent}",
        )
    if not os.access(lock_path.parent, os.W_OK):
        return (
            STATUS_FAIL,
            "leader-lock path writable",
            f"no write access to {lock_path.parent}",
        )
    # If the lock file already exists, check whether something currently holds it.
    if lock_path.exists():
        # Try to acquire briefly; if it blocks, somebody has it.
        from colony_chat_hermes.leader_lock import LeaderLock, LeaderLockBusy

        lock = LeaderLock(lock_path=lock_path)
        try:
            lock.acquire()
        except LeaderLockBusy:
            try:
                pid_text = lock_path.read_text().strip().splitlines()[0]
            except (OSError, IndexError):
                pid_text = "?"
            return (
                STATUS_WARN,
                "leader-lock state",
                f"another process holds {lock_path} (pid {pid_text}) — "
                "a daemon is already running on this host",
            )
        else:
            lock.release()
    return (STATUS_OK, "leader-lock state", f"available at {lock_path}")


def _check_soul_anchor(soul_path: Path) -> CheckResult:
    if not soul_path.exists():
        return (
            STATUS_WARN,
            "SOUL.md identity block",
            f"file does not exist: {soul_path} (run `register` to create)",
        )
    text = soul_path.read_text()
    if DEFAULT_START_MARKER not in text or DEFAULT_END_MARKER not in text:
        return (
            STATUS_WARN,
            "SOUL.md identity block",
            f"no colony-chat fenced block in {soul_path}",
        )
    return (STATUS_OK, "SOUL.md identity block", f"present at {soul_path}")


def _check_invoker_spec(spec: str) -> CheckResult:
    from colony_chat_hermes.daemon.agent_invoker import load_invoker

    try:
        load_invoker(spec)
    except (ValueError, ImportError, AttributeError) as e:
        return (
            STATUS_FAIL,
            "invoker spec resolvable",
            f"{type(e).__name__}: {e} (spec={spec!r})",
        )
    return (STATUS_OK, "invoker spec resolvable", spec)


def _check_webhook_config(client: Any) -> CheckResult | None:
    """Only run when the operator has *some* Mode A config set.

    If neither env var is present we return ``None`` and the caller
    omits the check entirely — most operators run Mode B only.
    """
    secret = os.environ.get("COLONY_CHAT_WEBHOOK_SECRET")
    webhook_id = os.environ.get("COLONY_CHAT_WEBHOOK_ID")
    if not secret and not webhook_id:
        return None

    if not secret:
        return (
            STATUS_FAIL,
            "webhook config",
            "COLONY_CHAT_WEBHOOK_ID is set but COLONY_CHAT_WEBHOOK_SECRET is not — "
            "Mode A receiver will reject all deliveries",
        )
    if not webhook_id:
        return (
            STATUS_WARN,
            "webhook config",
            "COLONY_CHAT_WEBHOOK_SECRET is set but COLONY_CHAT_WEBHOOK_ID is not — "
            "auto-recovery on platform-side auto-disable is disabled",
        )

    try:
        hooks = client.list_webhooks()
    except Exception as e:
        return (
            STATUS_FAIL,
            "webhook config",
            f"list_webhooks() failed: {type(e).__name__}: {e}",
        )
    for h in hooks or []:
        if isinstance(h, dict) and h.get("id") == webhook_id:
            active = h.get("is_active", True)
            return (
                STATUS_OK if active else STATUS_WARN,
                "webhook config",
                f"id={webhook_id} "
                + ("active" if active else "DISABLED — auto-recovery re-enables next check"),
            )
    return (
        STATUS_FAIL,
        "webhook config",
        f"id={webhook_id} not in your registered webhooks "
        "(was it deleted? re-run `colony-chat-hermes webhook setup`)",
    )


def run_doctor(
    *,
    env_path: Path,
    soul_path: Path,
    lock_path: Path,
    invoker_spec: str,
    emit: Callable[[str], None],
) -> int:
    """Execute the checklist; return 0 on all-ok/warn, 1 on any fail."""
    results: list[CheckResult] = []

    # Independent checks first
    results.append(_check_api_key(env_path))
    results.append(_check_sdk_importable())

    client = None
    if results[0][0] == STATUS_OK and results[1][0] == STATUS_OK:
        try:
            client = _build_client(env_path)
        except Exception as e:
            results.append((STATUS_FAIL, "client construction", f"{type(e).__name__}: {e}"))
    if client is not None:
        results.append(_check_me(client))
        results.append(_check_contacts(client))
        wh = _check_webhook_config(client)
        if wh is not None:
            results.append(wh)
    else:
        results.append(
            (
                STATUS_WARN,
                "identity resolves",
                "skipped (api_key or colony_chat unavailable)",
            )
        )

    results.append(_check_leader_lock(lock_path))
    results.append(_check_soul_anchor(soul_path))
    results.append(_check_invoker_spec(invoker_spec))

    failed = 0
    for status, label, detail in results:
        if status == STATUS_OK:
            marker = "✓"
        elif status == STATUS_WARN:
            marker = "⚠"
        else:
            marker = "✗"
            failed += 1
        line = f"  {marker} {label}"
        if detail:
            line += f" — {detail}"
        emit(line)

    if failed:
        emit("")
        emit(f"{failed} check(s) failed. See above for what to fix.")
        return 1
    emit("")
    emit("All checks passed.")
    return 0


def default_lock_path() -> Path:
    return _DEFAULT_LOCK_DIR / _DEFAULT_LOCK_NAME
