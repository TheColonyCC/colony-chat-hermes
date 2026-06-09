"""Mode B inbound: poll Colony ``/notifications`` and enqueue DMs.

Default cadence is 15s, configurable via ``COLONY_CHAT_POLL_INTERVAL_SEC``
or the ``daemon`` CLI flag. The poller is the always-available channel —
the webhook receiver (Mode A) is preferred when the operator has a
public HTTPS endpoint, but most agents start without one.

The poller is a single daemon thread (in the Python-threading sense)
owned by the orchestrator. It loops until ``stop_event`` is set, sleeps
between polls via ``Event.wait`` so a stop interrupts the sleep
immediately, and silences network errors at the loop level so a
transient outage doesn't kill the daemon.

Per-poll surface (v0.3.0: tail-verified, parse-fallback)
---------------------------------------------------------

Each iteration:

1. ``client.unread()`` — server-tracked unread DM notifications.
   This stays the cheap "anything new?" trigger: when it returns
   nothing, the iteration is over. Steady-state idle cost is one
   HTTP request per poll, exactly as before.

2. ``client.contacts()`` (only when there ARE unread) — builds the
   display-name → username and username → conversation-id maps used
   to resolve which peer each notification refers to.

3. ``client.tail(handle, since_id=<watermark>)`` per peer with new
   notifications — fetches the *authoritative* ``Message`` rows
   (structured sender / body / conversation_id / created_at) instead
   of trusting the notification's pre-formatted ``"Display: body"``
   string. Events built this way carry the real ``message_id``,
   which becomes the queue's dedup key. A per-handle watermark
   (newest message id seen) keeps each tail call incremental.

The parse path from v0.2.1 survives as the **fallback**: when the
peer handle can't be resolved, the client predates ``tail()``, or
the tail call fails, the notification-parsed event is enqueued
exactly as before. Strictly more robust, never less.

The smoke-test history that motivated this: both prior live bugs
(v0.1.2, v0.2.1) came from reconstructing message data out of the
notification envelope. Tail reads eliminate the reconstruction for
the common case. The tail endpoint is a non-destructive read — it
does not mark the conversation read (verified live 2026-06-09).
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from colony_chat_hermes.daemon.events import InboundEvent
from colony_chat_hermes.daemon.message_queue import MessageQueue

logger = logging.getLogger(__name__)


class NotificationPoller:
    """Polls ``client.unread()`` + enriches via ``client.contacts()``."""

    def __init__(
        self,
        *,
        client: Any,
        message_queue: MessageQueue,
        interval: float = 15.0,
        limit: int = 50,
        stop_event: threading.Event | None = None,
    ) -> None:
        if interval <= 0:
            raise ValueError("interval must be > 0")
        if limit < 1:
            raise ValueError("limit must be >= 1")
        self._client = client
        self._queue = message_queue
        self._interval = interval
        self._limit = limit
        self._stop = stop_event or threading.Event()
        self._thread: threading.Thread | None = None
        self._polls = 0
        self._enqueued = 0
        # Per-peer high-water mark: newest message id seen via tail().
        self._watermarks: dict[str, str] = {}
        # Own username, lazily resolved via client.me() on first tail
        # use — needed to drop outbound messages from tail output
        # (Colony's tail rows carry ``from_self: null``; direction
        # comes from comparing sender.username against ourselves).
        self._self_handle: str | None = None

    def start(self) -> None:
        """Spawn the poll thread if not already running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="colony-chat-poller", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal stop and join the thread."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _enrichment_maps(self) -> tuple[dict[str, str], dict[str, str]]:
        """Build display→username + username→conv_id maps from contacts().

        Returns empty maps on failure so the caller can still emit
        events with from_handle / conversation_id unresolved (we'd
        rather deliver an event with empty enrichment fields than
        drop it entirely).
        """
        try:
            convs = self._client.contacts()
        except Exception as e:
            logger.warning(
                "colony-chat-poller: contacts() failed: %s: %s",
                type(e).__name__,
                e,
            )
            return {}, {}
        display_to_handle: dict[str, str] = {}
        handle_to_conv: dict[str, str] = {}
        for cv in convs or []:
            if not isinstance(cv, dict):
                continue
            peer = cv.get("other_user")
            if not isinstance(peer, dict):
                continue
            username = peer.get("username")
            display = peer.get("display_name")
            conv_id = cv.get("id")
            if username and display:
                display_to_handle[str(display)] = str(username)
            if username and conv_id:
                handle_to_conv[str(username)] = str(conv_id)
        return display_to_handle, handle_to_conv

    def poll_once(self) -> int:
        """Run a single poll iteration. Returns count enqueued.

        Used by tests directly and by the ``feed`` CLI subcommand to
        produce one batch of output without standing up the full
        daemon.
        """
        self._polls += 1
        try:
            items = self._client.unread(limit=self._limit)
        except Exception as e:
            # Don't crash the loop on transient failures — log and move
            # on. The next iteration will retry.
            logger.warning(
                "colony-chat-poller: unread() failed: %s: %s",
                type(e).__name__,
                e,
            )
            return 0
        items = items or []
        if not items:
            return 0
        display_to_handle, handle_to_conv = self._enrichment_maps()
        parsed: list[InboundEvent] = []
        for item in items:
            event = InboundEvent.from_notification(
                item,
                source="poller",
                display_to_handle=display_to_handle,
                handle_to_conv_id=handle_to_conv,
            )
            if event is not None:
                parsed.append(event)

        # Group by resolved peer; one tail call per peer regardless of
        # how many notifications they generated this tick.
        by_handle: dict[str, list[InboundEvent]] = {}
        for event in parsed:
            by_handle.setdefault(event.from_handle, []).append(event)

        n = 0
        for handle, fallback_events in by_handle.items():
            events: list[InboundEvent] | None = None
            if handle:
                events = self._tail_events(handle, handle_to_conv.get(handle, ""))
            if events is None:
                # Tail unavailable / failed — v0.2.1 parse path.
                events = fallback_events
            for event in events:
                if self._queue.enqueue(event):
                    n += 1
        self._enqueued += n
        return n

    def _resolve_self_handle(self) -> str:
        """Own username, cached after the first successful lookup."""
        if self._self_handle is None:
            try:
                me = self._client.me()
                self._self_handle = str(me.get("username") or "") if isinstance(me, dict) else ""
            except Exception as e:
                logger.warning(
                    "colony-chat-poller: me() failed: %s: %s",
                    type(e).__name__,
                    e,
                )
                self._self_handle = ""
        return self._self_handle

    def _tail_events(self, handle: str, conv_id: str) -> list[InboundEvent] | None:
        """Authoritative events for ``handle`` via the tail endpoint.

        Returns ``None`` when tail is unavailable (old colony-chat,
        network failure, unexpected shape) so the caller falls back to
        the notification-parsed events. Returns ``[]`` when tail
        succeeded but everything new was outbound or already read —
        in that case the parsed fallback must NOT run, or it would
        re-deliver bodies the tail path already delivered on an
        earlier tick.
        """
        tail = getattr(self._client, "tail", None)
        if tail is None:
            return None
        try:
            messages = tail(handle, since_id=self._watermarks.get(handle))
        except Exception as e:
            logger.warning(
                "colony-chat-poller: tail(%s) failed: %s: %s",
                handle,
                type(e).__name__,
                e,
            )
            return None
        if not isinstance(messages, list):
            return None
        rows = [m for m in messages if isinstance(m, dict)]
        # Advance the watermark over EVERYTHING returned (rows are
        # oldest → newest), including outbound/read rows, so the next
        # tick tails only genuinely-new messages.
        for row in reversed(rows):
            row_id = row.get("id")
            if row_id:
                self._watermarks[handle] = str(row_id)
                break
        self_handle = self._resolve_self_handle()
        out: list[InboundEvent] = []
        for row in rows:
            sender = row.get("sender")
            sender_name = sender.get("username") if isinstance(sender, dict) else None
            if self_handle and sender_name == self_handle:
                continue  # our own outbound
            if row.get("is_read"):
                continue  # delivered on an earlier tick / other channel
            event = InboundEvent.from_message(
                row,
                peer_handle=handle,
                source="poller",
                conversation_id_fallback=conv_id,
            )
            if event is not None:
                out.append(event)
        return out

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            # ``wait`` returns True when set; either way the loop check
            # at the top catches it on next iteration.
            self._stop.wait(self._interval)

    def stats(self) -> dict[str, int]:
        return {"polls": self._polls, "enqueued": self._enqueued}
