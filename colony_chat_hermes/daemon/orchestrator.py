"""Wires the daemon components and runs them under a single process.

Composition:

    [WebhookReceiver]  [NotificationPoller]  [WebhookAutoRecovery]
              \\                /                       |
               \\              /                        |
                MessageQueue (bounded, deduped)         |
                       |                                |
                       v                                |
                AgentInvoker                            |
                       |                                |
                       v                                |
              InvokerCallable                           |
                                                        |
                                  (re-enables auto-disabled webhooks)

The orchestrator owns the leader-lock acquisition. If another process
already holds the lock, ``start`` raises and the second daemon exits —
exactly one poller / receiver per host. This avoids duplicate inbound
dispatch when the operator forgets they already have a daemon
running.

Signal handling lives in ``run_until_signal``: SIGINT or SIGTERM
triggers an orderly shutdown that flushes the queue (best-effort) and
joins every thread.
"""

from __future__ import annotations

import logging
import signal
import threading
from typing import Any, Literal

from colony_chat_hermes.daemon.agent_invoker import AgentInvoker, InvokerCallable
from colony_chat_hermes.daemon.message_queue import MessageQueue
from colony_chat_hermes.daemon.notification_poller import NotificationPoller
from colony_chat_hermes.daemon.webhook_receiver import (
    WebhookAutoRecovery,
    WebhookReceiver,
)
from colony_chat_hermes.leader_lock import LeaderLock, LeaderLockBusy

logger = logging.getLogger(__name__)

Mode = Literal["poll", "webhook", "both"]


class Orchestrator:
    """Composes the four daemon components and manages their lifecycle."""

    def __init__(
        self,
        *,
        client: Any,
        invoker: InvokerCallable,
        mode: Mode = "poll",
        poll_interval: float = 15.0,
        poll_limit: int = 50,
        webhook_host: str = "127.0.0.1",
        webhook_port: int = 8765,
        webhook_path: str = "/webhook",
        webhook_secret: str | None = None,
        webhook_id: str | None = None,
        webhook_recovery_interval: float = 300.0,
        queue_maxsize: int = 100,
        queue_dedup_window: int = 1024,
        leader_lock: LeaderLock | None = None,
    ) -> None:
        if mode not in ("poll", "webhook", "both"):
            raise ValueError(f"invalid mode: {mode!r}")
        if mode in ("webhook", "both") and not webhook_secret:
            raise ValueError("webhook_secret is required for mode=webhook|both")

        self._mode = mode
        self._lock = leader_lock
        self._stop = threading.Event()

        self._queue = MessageQueue(maxsize=queue_maxsize, dedup_window=queue_dedup_window)

        self._poller: NotificationPoller | None = None
        if mode in ("poll", "both"):
            self._poller = NotificationPoller(
                client=client,
                message_queue=self._queue,
                interval=poll_interval,
                limit=poll_limit,
                stop_event=self._stop,
            )

        self._receiver: WebhookReceiver | None = None
        self._recovery: WebhookAutoRecovery | None = None
        if mode in ("webhook", "both"):
            assert webhook_secret is not None  # for type narrowing
            self._receiver = WebhookReceiver(
                message_queue=self._queue,
                secret=webhook_secret,
                host=webhook_host,
                port=webhook_port,
                path=webhook_path,
            )
            if webhook_id:
                self._recovery = WebhookAutoRecovery(
                    client=client,
                    webhook_id=webhook_id,
                    interval=webhook_recovery_interval,
                    stop_event=self._stop,
                )

        self._invoker = AgentInvoker(
            message_queue=self._queue,
            invoker=invoker,
            stop_event=self._stop,
        )

    @property
    def queue(self) -> MessageQueue:
        return self._queue

    def start(self) -> None:
        """Acquire the lock and start every worker.

        Raises ``RuntimeError`` if another daemon already holds the
        leader-lock. Starts the invoker (consumer) before the
        producers so no event is sitting in the queue with nobody
        servicing it.
        """
        if self._lock is not None:
            try:
                self._lock.acquire()
            except LeaderLockBusy as e:
                raise RuntimeError(
                    f"another colony-chat-hermes daemon already holds {self._lock.lock_path} — "
                    "stop that one before starting a new one"
                ) from e

        self._invoker.start()
        if self._poller is not None:
            self._poller.start()
        if self._receiver is not None:
            self._receiver.start()
        if self._recovery is not None:
            self._recovery.start()

        logger.info("colony-chat-hermes daemon started (mode=%s)", self._mode)

    def stop(self) -> None:
        """Signal stop and join every worker."""
        self._stop.set()
        if self._poller is not None:
            self._poller.stop()
        if self._receiver is not None:
            self._receiver.stop()
        if self._recovery is not None:
            self._recovery.stop()
        self._invoker.stop()
        if self._lock is not None:
            self._lock.release()
        logger.info("colony-chat-hermes daemon stopped")

    def stats(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "mode": self._mode,
            "queue": self._queue.stats(),
            "invoker": self._invoker.stats(),
        }
        if self._poller is not None:
            out["poller"] = self._poller.stats()
        if self._receiver is not None:
            out["receiver"] = self._receiver.stats()
        if self._recovery is not None:
            out["recovery"] = self._recovery.stats()
        return out

    def run_until_signal(self) -> None:
        """Start, install SIGINT/SIGTERM handlers, block until signalled."""
        self.start()
        wait = threading.Event()

        def _handle_signal(*_args: Any) -> None:
            wait.set()

        prior_handlers: list[tuple[int, Any]] = []
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                prior_handlers.append((sig, signal.signal(sig, _handle_signal)))
            wait.wait()
        finally:
            for restore_sig, prev in prior_handlers:
                # Restore prior handler so tests that re-enter don't
                # inherit our flag-setter.
                signal.signal(restore_sig, prev)
            self.stop()


def run_daemon(
    *,
    client: Any,
    invoker: InvokerCallable,
    mode: Mode = "poll",
    **kwargs: Any,
) -> None:
    """Convenience: build an Orchestrator and run it until signal."""
    orch = Orchestrator(client=client, invoker=invoker, mode=mode, **kwargs)
    orch.run_until_signal()
