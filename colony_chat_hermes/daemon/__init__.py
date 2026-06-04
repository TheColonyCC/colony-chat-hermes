"""Daemon-side runtime for colony-chat-hermes.

Exports the orchestrator + the component classes so operators that
want to embed (rather than run as a subprocess) have a clean
import surface.
"""

from __future__ import annotations

from colony_chat_hermes.daemon.agent_invoker import (
    DEFAULT_LOG_PATH,
    AgentInvoker,
    InvokerCallable,
    load_invoker,
    log_only_invoker,
    subprocess_invoker,
)
from colony_chat_hermes.daemon.events import EventSource, InboundEvent
from colony_chat_hermes.daemon.message_queue import MessageQueue
from colony_chat_hermes.daemon.notification_poller import NotificationPoller
from colony_chat_hermes.daemon.orchestrator import Mode, Orchestrator, run_daemon
from colony_chat_hermes.daemon.webhook_receiver import (
    SIGNATURE_HEADER,
    WebhookAutoRecovery,
    WebhookReceiver,
)

__all__ = [
    "DEFAULT_LOG_PATH",
    "SIGNATURE_HEADER",
    "AgentInvoker",
    "EventSource",
    "InboundEvent",
    "InvokerCallable",
    "MessageQueue",
    "Mode",
    "NotificationPoller",
    "Orchestrator",
    "WebhookAutoRecovery",
    "WebhookReceiver",
    "load_invoker",
    "log_only_invoker",
    "run_daemon",
    "subprocess_invoker",
]
