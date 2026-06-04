"""Mode A inbound: HTTP receiver for Colony webhook deliveries.

Verifies HMAC-SHA256 on the raw body via
``colony_chat.ColonyChat.verify_signature`` before enqueuing — a
malformed or unsigned delivery returns ``401`` and never reaches the
queue.

Stdlib ``http.server`` is intentional: a webhook target is a
low-frequency surface (one HTTP exchange per inbound DM, often
seconds apart). ThreadingHTTPServer's one-thread-per-request behavior
is adequate; adding aiohttp / FastAPI for a single endpoint is dep
bloat.

The receiver does NOT terminate TLS — the operator fronts it with a
reverse proxy (nginx, caddy, traefik, tailscale funnel) that handles
the public HTTPS URL. The receiver binds to 127.0.0.1:8765 by default
on the assumption a same-host proxy proxies into it.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from colony_chat_hermes.daemon.events import InboundEvent
from colony_chat_hermes.daemon.message_queue import MessageQueue

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Colony-Signature"


class WebhookReceiver:
    """Threaded HTTP server that enqueues verified webhook deliveries."""

    def __init__(
        self,
        *,
        message_queue: MessageQueue,
        secret: str,
        host: str = "127.0.0.1",
        port: int = 8765,
        path: str = "/webhook",
    ) -> None:
        if not secret:
            raise ValueError("secret is required — unsigned webhooks are not accepted")
        self._queue = message_queue
        self._secret = secret
        self._host = host
        self._port = port
        self._path = path
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._received = 0
        self._rejected_bad_sig = 0
        self._rejected_bad_body = 0

    def start(self) -> None:
        """Bind the port and start serving in a worker thread."""
        handler_cls = self._build_handler()
        self._server = ThreadingHTTPServer((self._host, self._port), handler_cls)
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="colony-chat-webhook", daemon=True
        )
        self._thread.start()
        logger.info(
            "colony-chat-webhook: listening on http://%s:%d%s",
            self._host,
            self._port,
            self._path,
        )

    def stop(self, timeout: float = 5.0) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def stats(self) -> dict[str, int]:
        return {
            "received": self._received,
            "rejected_bad_sig": self._rejected_bad_sig,
            "rejected_bad_body": self._rejected_bad_body,
        }

    def _build_handler(self) -> type[BaseHTTPRequestHandler]:
        # Capture references on the outer class. http.server instantiates
        # the handler per request, so we route writes back to the
        # WebhookReceiver via this closure.
        receiver = self

        class _Handler(BaseHTTPRequestHandler):
            # Quiet the default per-request stderr line — route to our
            # logger at DEBUG instead.
            server_version = "colony-chat-hermes/webhook"

            def log_message(self, format: str, *args: Any) -> None:
                logger.debug("webhook: " + format, *args)

            def do_POST(self) -> None:
                if self.path != receiver._path:
                    self.send_response(404)
                    self.end_headers()
                    return

                try:
                    length = int(self.headers.get("Content-Length") or "0")
                except ValueError:
                    self.send_response(411)
                    self.end_headers()
                    return

                body = self.rfile.read(length) if length > 0 else b""
                sig = self.headers.get(SIGNATURE_HEADER, "")

                if not _verify(body, sig, receiver._secret):
                    receiver._rejected_bad_sig += 1
                    self.send_response(401)
                    self.end_headers()
                    return

                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    receiver._rejected_bad_body += 1
                    self.send_response(400)
                    self.end_headers()
                    return

                event = InboundEvent.from_notification(payload, source="webhook")
                if event is not None:
                    receiver._queue.enqueue(event)
                    receiver._received += 1

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

        return _Handler


def _verify(body: bytes, signature_header: str, secret: str) -> bool:
    """Indirection so tests can patch the verify call without importing colony_chat.

    Lazy import so the daemon module is loadable in environments where
    ``colony_chat`` is installed at runtime via the git-clone shim.
    """
    from colony_chat import ColonyChat  # noqa: PLC0415

    return ColonyChat.verify_signature(body, signature_header, secret)


class WebhookAutoRecovery:
    """Periodically re-enables platform-disabled webhooks.

    Colony auto-disables a webhook after a run of consecutive delivery
    failures (10 at time of writing). When the receiver comes back up
    after an outage, the platform-side ``is_active`` is still False —
    until the operator manually re-enables, no events flow. This
    worker polls the webhook's state at a slow cadence (5 min by
    default) and POSTs ``update_webhook(is_active=True)`` if needed.
    """

    def __init__(
        self,
        *,
        client: Any,
        webhook_id: str,
        interval: float = 300.0,
        stop_event: threading.Event | None = None,
    ) -> None:
        if interval <= 0:
            raise ValueError("interval must be > 0")
        self._client = client
        self._webhook_id = webhook_id
        self._interval = interval
        self._stop = stop_event or threading.Event()
        self._thread: threading.Thread | None = None
        self._recoveries = 0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop, name="colony-chat-webhook-recovery", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def check_once(self) -> bool:
        """Return True if a re-enable was attempted (used by tests)."""
        try:
            webhooks = self._client.list_webhooks()
        except Exception as e:
            logger.warning(
                "colony-chat-recovery: list_webhooks() failed: %s: %s",
                type(e).__name__,
                e,
            )
            return False
        for wh in webhooks or []:
            if not isinstance(wh, dict):
                continue
            if wh.get("id") != self._webhook_id:
                continue
            if wh.get("is_active", True) is False:
                try:
                    self._client.update_webhook(self._webhook_id, is_active=True)
                    self._recoveries += 1
                    logger.info(
                        "colony-chat-recovery: re-enabled webhook %s",
                        self._webhook_id,
                    )
                    return True
                except Exception as e:
                    logger.warning(
                        "colony-chat-recovery: update_webhook() failed: %s: %s",
                        type(e).__name__,
                        e,
                    )
                    return False
        return False

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.check_once()
            self._stop.wait(self._interval)

    def stats(self) -> dict[str, int]:
        return {"recoveries": self._recoveries}
