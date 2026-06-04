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
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from colony_chat_hermes.daemon.events import InboundEvent
from colony_chat_hermes.daemon.message_queue import MessageQueue

logger = logging.getLogger(__name__)


class NotificationPoller:
    """Polls ``client.unread()`` and enqueues new direct_message events."""

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
            # on. The next iteration will retry. If the failure is
            # persistent (revoked key, host down) the operator will
            # see it in the logs.
            logger.warning("colony-chat-poller: unread() failed: %s: %s", type(e).__name__, e)
            return 0
        n = 0
        for item in items or []:
            event = InboundEvent.from_notification(item, source="poller")
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
