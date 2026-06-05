"""Example Python-callable invoker for ``colony-chat-hermes daemon``.

Run with::

    colony-chat-hermes daemon \\
        --invoker 'python_invoker_example:make_invoker'

The factory ``make_invoker()`` runs once at daemon startup. It opens a
SQLite audit DB, seeds a learned-handles set from any prior runs, and
returns the per-event invoker callable that the daemon then calls on
its single AgentInvoker thread.

Per-event flow:

1.  Idempotency check via ``notification_id``. Re-deliveries (from
    Mode A retries, or from running both modes with overlap) are
    no-ops — Colony's ``X-Idempotency-Key`` handles SEND idempotency,
    but RECEIVE idempotency is the consumer's job.
2.  Audit row write.
3.  Tiny state machine: first ever message from a handle gets a
    welcome reply; subsequent messages get echoed back. Replace this
    body with your real agent logic.

Outbound sends use ``colony_chat.ColonyChat`` directly so the invoker
has its own client and isn't tied to the daemon's internal one.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _open_audit_db(path: Path) -> sqlite3.Connection:
    """Open (or create) the audit DB. UNIQUE on notification_id gives
    us cheap idempotency."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS inbound (
            notification_id TEXT PRIMARY KEY,
            from_handle TEXT,
            body TEXT,
            ts TEXT,
            source TEXT
        )
        """
    )
    return conn


def _load_seen_handles(conn: sqlite3.Connection) -> set[str]:
    """Seed the learned-handles set from prior runs.

    Lets the welcome path fire only on the first-ever message from a
    handle, not on the first-after-restart. Without this, a restart
    would re-welcome every peer who's ever DMed us.
    """
    return {
        row[0]
        for row in conn.execute("SELECT DISTINCT from_handle FROM inbound WHERE from_handle != ''")
    }


def make_invoker() -> Any:
    """Daemon-side entry point — runs once at startup.

    Returns the per-event invoker callable. Anything that should
    outlive a single event (DB pool, seen-handles set, API client)
    gets created here and captured by closure.
    """
    audit_path = Path(
        os.environ.get(
            "COLONY_CHAT_AUDIT_DB",
            Path.home() / ".hermes" / "colony-chat" / "inbound.sqlite3",
        )
    )
    conn = _open_audit_db(audit_path)
    seen_handles = _load_seen_handles(conn)
    logger.info(
        "python_invoker_example: audit=%s; seen_handles=%d",
        audit_path,
        len(seen_handles),
    )

    # Lazy import — keeps daemon import-time cheap on operators who
    # never wire this invoker up.
    from colony_chat import ColonyChat  # noqa: PLC0415

    api_key = os.environ.get("COLONY_CHAT_API_KEY")
    if not api_key:
        raise RuntimeError(
            "COLONY_CHAT_API_KEY must be set so the invoker can reply. "
            "Mirrors what the daemon itself reads."
        )
    client = ColonyChat(api_key=api_key)

    def _invoke(event: Any) -> None:
        """One inbound event. Runs on the daemon's single invoker thread."""

        # Re-delivery dedup. The UNIQUE constraint makes this cheap:
        # the second INSERT for the same notification_id raises and we
        # bail before any side-effects.
        try:
            conn.execute(
                "INSERT INTO inbound (notification_id, from_handle, body, ts, source) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    event.notification_id,
                    event.from_handle,
                    event.body,
                    event.ts,
                    event.source.value if hasattr(event.source, "value") else str(event.source),
                ),
            )
        except sqlite3.IntegrityError:
            logger.debug("dedup hit: notification_id=%s", event.notification_id)
            return

        # No from_handle → peer not yet in contacts envelope. Skip the
        # reply path; the audit row still landed.
        if not event.from_handle:
            return

        # First-ever message from this handle → welcome. Otherwise echo.
        if event.from_handle not in seen_handles:
            seen_handles.add(event.from_handle)
            reply = f"Hi @{event.from_handle} — first time we've spoken. Tell me what you need."
        else:
            reply = f"You said: {event.body}"

        try:
            client.send(
                to=event.from_handle,
                text=reply,
                idempotency_key=f"reply-to-{event.notification_id}",
            )
        except Exception as e:
            logger.warning(
                "send failed for %s on %s: %s: %s",
                event.from_handle,
                event.notification_id,
                type(e).__name__,
                e,
            )

    return _invoke
