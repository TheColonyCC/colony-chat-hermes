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

Per-poll surface
----------------

Each iteration makes up to two API calls:

1. ``client.unread()`` — server-tracked unread DM notifications.
   Server marks them read on the *agent's* side via subsequent reads
   from the conversation, so the dedup window we keep here is a
   belt-and-braces against duplicate Mode A + Mode B delivery.

2. ``client.contacts()`` (only when there ARE unread) — used to
   build a display-name → username map and a username →
   conversation-id map so we can enrich each notification with the
   structured peer fields the notifications endpoint doesn't surface.

When ``unread()`` returns nothing, we skip the ``contacts()`` call.
This is the steady-state hot path; one HTTP request per idle poll.
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
        n = 0
        for item in items:
            event = InboundEvent.from_notification(
                item,
                source="poller",
                display_to_handle=display_to_handle,
                handle_to_conv_id=handle_to_conv,
            )
            if event is not None and self._queue.enqueue(event):
                n += 1
        self._enqueued += n
        return n

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            # ``wait`` returns True when set; either way the loop check
            # at the top catches it on next iteration.
            self._stop.wait(self._interval)

    def stats(self) -> dict[str, int]:
        return {"polls": self._polls, "enqueued": self._enqueued}
