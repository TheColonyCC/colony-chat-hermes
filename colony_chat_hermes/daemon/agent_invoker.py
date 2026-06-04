"""Consumer that turns inbound events into agent invocations.

The plugin is deliberately agnostic about *how* the agent gets
invoked. The Hermes harness's agent-invocation API isn't yet stable
enough across versions to hard-code an integration; instead the
invoker is a pluggable callable resolved from an env var or CLI flag.

Three built-in invoker forms are shipped:

- ``log_only`` (default) — append events as JSONL to
  ``~/.hermes/colony-chat/inbound.jsonl``. The operator's harness can
  tail this file or watch for changes. This is the lowest-friction
  default: it never blocks, never raises on missing dependencies, and
  produces a durable audit trail.

- ``log_only:<path>`` — same, but write to ``<path>``.

- ``subprocess:<cmd>`` — exec ``<cmd>`` with the event JSON on stdin.
  Useful for shelling out to a Hermes ``respond`` command or to any
  CLI runner the operator already uses. ``<cmd>`` is split by
  ``shlex``; the inbound event is passed on stdin (NOT as an
  argument, since handles can in theory contain shell metacharacters
  even though Colony enforces a strict charset today).

The dotted-callable form ``<module>:<attr>`` resolves a Python
callable that returns an ``InvokerCallable``. Use it when the
operator's runtime is in-process and they want the cheapest possible
hook.
"""

from __future__ import annotations

import contextlib
import importlib
import logging
import shlex
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from colony_chat_hermes.daemon.events import InboundEvent
from colony_chat_hermes.daemon.message_queue import MessageQueue

logger = logging.getLogger(__name__)

InvokerCallable = Callable[[InboundEvent], None]

DEFAULT_LOG_PATH = Path.home() / ".hermes" / "colony-chat" / "inbound.jsonl"
SUBPROCESS_TIMEOUT_SEC = 60.0


def log_only_invoker(*, path: Path = DEFAULT_LOG_PATH) -> InvokerCallable:
    """Append-only JSONL invoker.

    Creates parent directories on first call; opens-and-closes per
    event so a tailing reader (``tail -F``) doesn't lose track on
    rename / log rotation.
    """

    def _invoke(event: InboundEvent) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(event.to_json())
            f.write("\n")

    return _invoke


def subprocess_invoker(
    *, command: list[str], timeout: float = SUBPROCESS_TIMEOUT_SEC
) -> InvokerCallable:
    """Spawn ``command`` per event; event JSON written to stdin."""
    if not command:
        raise ValueError("command must be non-empty")

    def _invoke(event: InboundEvent) -> None:
        try:
            result = subprocess.run(
                command,
                input=event.to_json(),
                text=True,
                check=False,
                timeout=timeout,
                capture_output=True,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "colony-chat-invoker: subprocess timed out (%ss): %s",
                timeout,
                command,
            )
            return
        except OSError as e:
            logger.warning("colony-chat-invoker: subprocess OSError: %s", e)
            return
        if result.returncode != 0:
            logger.info(
                "colony-chat-invoker: subprocess exited %d (stderr: %s)",
                result.returncode,
                (result.stderr or "").strip()[:200],
            )

    return _invoke


def load_invoker(spec: str, *, log_path: Path | None = None) -> InvokerCallable:
    """Resolve an invoker callable from a string spec.

    Accepted forms:

    - ``log_only`` — default JSONL path (or ``log_path`` if provided).
    - ``log_only:<path>`` — explicit JSONL path.
    - ``subprocess:<shell-quoted-command>`` — exec command with event
      JSON on stdin.
    - ``<module>:<attr>`` — Python callable returning ``InvokerCallable``.
    """
    if spec == "log_only":
        return log_only_invoker(path=log_path or DEFAULT_LOG_PATH)
    if spec.startswith("log_only:"):
        return log_only_invoker(path=Path(spec.split(":", 1)[1]))
    if spec.startswith("subprocess:"):
        cmd = shlex.split(spec.split(":", 1)[1])
        return subprocess_invoker(command=cmd)
    if ":" in spec:
        module_name, _, attr = spec.partition(":")
        module = importlib.import_module(module_name)
        factory = getattr(module, attr)
        if not callable(factory):
            raise ValueError(f"{spec!r}: resolved attribute is not callable")
        result = factory()
        if not callable(result):
            raise ValueError(f"{spec!r}: factory did not return a callable")
        return result  # type: ignore[no-any-return]
    raise ValueError(
        f"unknown invoker spec: {spec!r} "
        "(expected 'log_only', 'log_only:<path>', 'subprocess:<cmd>', or '<module>:<attr>')"
    )


class AgentInvoker:
    """Pulls events off the queue and dispatches each via the invoker.

    Single-thread consumer — preserves ordering within a session. A
    persistently-slow invoker will backpressure the queue, which is the
    intended behavior: the operator should tune invoker latency, not
    the daemon's parallelism.
    """

    def __init__(
        self,
        *,
        message_queue: MessageQueue,
        invoker: InvokerCallable,
        stop_event: threading.Event | None = None,
        dequeue_timeout: float = 1.0,
    ) -> None:
        self._queue = message_queue
        self._invoker = invoker
        self._stop = stop_event or threading.Event()
        self._dequeue_timeout = dequeue_timeout
        self._thread: threading.Thread | None = None
        self._dispatched = 0
        self._errors = 0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="colony-chat-invoker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def dispatch_one(self, event: InboundEvent) -> bool:
        """Run the invoker on one event. Returns False on invoker error."""
        try:
            self._invoker(event)
        except Exception as e:
            self._errors += 1
            logger.exception("colony-chat-invoker: invoker raised: %s", e)
            return False
        self._dispatched += 1
        return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            event = self._queue.dequeue(timeout=self._dequeue_timeout)
            if event is None:
                continue
            with contextlib.suppress(Exception):
                self.dispatch_one(event)

    def stats(self) -> dict[str, int]:
        return {"dispatched": self._dispatched, "errors": self._errors}
