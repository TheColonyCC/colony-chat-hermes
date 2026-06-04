"""Bounded FIFO of inbound events with dedup by ``message_id``.

Both the poller and the webhook receiver can produce the same event
(Colony delivers the notification AND fires the webhook for a
``direct_message`` when both are configured). Dedup is the
*consumer's* seatbelt — the producers have no view of what arrived
from the other channel.

The dedup window is a fixed-size LRU of recently-seen message IDs.
1024 is well above the realistic burst rate; the window survives
process restart only when the operator pairs the daemon with a
persistent invoker (the default log_only invoker already writes a
durable JSONL of every accepted event, which is the audit trail an
operator would use to reconcile on cold start).
"""

from __future__ import annotations

import contextlib
import queue
import threading
from collections import deque

from colony_chat_hermes.daemon.events import InboundEvent


class MessageQueue:
    """Thread-safe bounded queue with message-id dedup."""

    def __init__(self, *, maxsize: int = 100, dedup_window: int = 1024) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        if dedup_window < 1:
            raise ValueError("dedup_window must be >= 1")
        self._q: queue.Queue[InboundEvent] = queue.Queue(maxsize=maxsize)
        self._seen_order: deque[str] = deque(maxlen=dedup_window)
        self._seen_set: set[str] = set()
        self._lock = threading.Lock()
        self._dropped_full = 0
        self._dropped_dup = 0

    def enqueue(self, event: InboundEvent) -> bool:
        """Accept the event unless it's a duplicate or the queue is full.

        Returns ``True`` if accepted; ``False`` if dropped. Dropped
        events bump one of the ``stats()`` counters so the operator can
        tell apart "we lost data" from "we deduped a known event".
        """
        with self._lock:
            if event.message_id in self._seen_set:
                self._dropped_dup += 1
                return False
            # The deque drops the oldest entry on append-past-maxlen.
            # Keep the set in sync by removing whatever fell off.
            if (
                self._seen_order.maxlen is not None
                and len(self._seen_order) == self._seen_order.maxlen
            ):
                old = self._seen_order.popleft()
                self._seen_set.discard(old)
            self._seen_order.append(event.message_id)
            self._seen_set.add(event.message_id)
        try:
            self._q.put_nowait(event)
            return True
        except queue.Full:
            # Roll back the dedup admission so a future re-attempt at
            # the same message_id can succeed. Otherwise a transient
            # backpressure spike would mark the event as "seen" and
            # silently drop subsequent legitimate retries.
            with self._lock:
                self._seen_set.discard(event.message_id)
                with contextlib.suppress(ValueError):
                    self._seen_order.remove(event.message_id)
                self._dropped_full += 1
            return False

    def dequeue(self, *, timeout: float = 1.0) -> InboundEvent | None:
        """Block up to ``timeout`` seconds for an event."""
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def qsize(self) -> int:
        return self._q.qsize()

    def stats(self) -> dict[str, int]:
        """Counters since process start."""
        return {
            "qsize": self._q.qsize(),
            "seen": len(self._seen_set),
            "dropped_dup": self._dropped_dup,
            "dropped_full": self._dropped_full,
        }
