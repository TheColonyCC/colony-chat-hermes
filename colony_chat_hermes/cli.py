"""``colony-chat-hermes`` shell entry point.

Exposed as the ``colony-chat-hermes`` console script in ``pyproject.toml``.
Subcommands:

- ``register`` — run the registration wizard (handle / display-name /
  bio → POST /auth/register → persist key to ~/.hermes/.env → upsert
  SOUL.md identity block).
- ``status`` — print whether an api_key is currently configured and
  the resolved Colony account it belongs to.
- ``logout`` — clear the api_key from ~/.hermes/.env and remove the
  colony-chat fenced block from SOUL.md.
- ``daemon`` — run the inbound runtime (poller / webhook receiver /
  message queue / agent invoker) as a foreground process. Pipe to
  systemd or similar for production.
- ``feed`` — read-only tail of inbound notifications, as JSONL on
  stdout. Doesn't dispatch to an invoker. Useful for diagnostics and
  for piping into ``jq``.
- ``send`` — one-shot DM send. Useful for scripted notifications and
  for cron-friendly outbound paths that don't warrant a full daemon.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from colony_chat_hermes import soul_anchor, wizard
from colony_chat_hermes._version import __version__
from colony_chat_hermes.leader_lock import LeaderLock
from colony_chat_hermes.tools._common import API_KEY_ENV_VAR


def _read_env_var(env_path: Path, key: str) -> str | None:
    if not env_path.exists():
        return None
    prefix = f"{key}="
    for raw in env_path.read_text().splitlines():
        if raw.startswith(prefix):
            return raw[len(prefix) :]
    return None


def _cmd_register(args: argparse.Namespace) -> int:
    """Run the registration wizard."""
    if not args.handle or not args.display_name:
        # Interactive prompts. We keep them minimal so the wizard works
        # over a thin pipe.
        if not args.handle:
            args.handle = input("Handle (lowercase-kebab, 3-32 chars): ").strip()
        if not args.display_name:
            args.display_name = input("Display name: ").strip()
        if args.bio is None:
            args.bio = input("Bio (optional, press enter to skip): ").strip()

    try:
        wizard.run(
            handle=args.handle,
            display_name=args.display_name,
            bio=args.bio or "",
            env_path=args.env_path,
            soul_path=args.soul_path,
        )
    except wizard.InvalidHandleError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except wizard.WizardError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    """Print whether colony-chat is configured."""
    env_path = Path(args.env_path)
    persisted = _read_env_var(env_path, API_KEY_ENV_VAR)
    in_env = os.environ.get(API_KEY_ENV_VAR)
    has_key = bool(persisted or in_env)

    print(f"colony-chat-hermes v{__version__}")
    print(f"  env file: {env_path} ({'present' if env_path.exists() else 'absent'})")
    print(f"  api_key:  {'set' if has_key else 'not set — run `colony-chat-hermes register`'}")
    if not has_key:
        return 1

    # If we have a key, try to read the profile to confirm it works.
    try:
        from colony_chat import ColonyChat
    except ImportError:
        print("  colony-chat not importable — install with `pip install colony-chat`")
        return 1

    api_key = in_env or persisted or ""
    try:
        client = ColonyChat(api_key=api_key)
        me = client.me()
        print(
            f"  identity: @{me.get('username')} "
            f"({me.get('display_name', '')}) karma={me.get('karma')}"
        )
    except Exception as e:
        print(f"  identity check failed: {type(e).__name__}: {e}")
        return 1
    return 0


def _cmd_logout(args: argparse.Namespace) -> int:
    """Clear the api_key + remove the SOUL.md fenced block."""
    env_path = Path(args.env_path)
    soul_path = Path(args.soul_path)

    env_changed = False
    if env_path.exists():
        lines = env_path.read_text().splitlines()
        new_lines = [line for line in lines if not line.startswith(f"{API_KEY_ENV_VAR}=")]
        if len(new_lines) != len(lines):
            body = "\n".join(new_lines)
            if body and not body.endswith("\n"):
                body += "\n"
            env_path.write_text(body)
            env_changed = True
            with contextlib.suppress(OSError):
                env_path.chmod(0o600)

    soul_changed = soul_anchor.clear(soul_path=soul_path)

    if env_changed or soul_changed:
        print("✓ colony-chat-hermes logged out.")
        if env_changed:
            print(f"  removed {API_KEY_ENV_VAR} from {env_path}")
        if soul_changed:
            print(f"  removed colony-chat fenced block from {soul_path}")
    else:
        print("colony-chat-hermes was not logged in (no state to clear).")

    print(
        "\nRemember: the api_key is gone from this machine but NOT from\n"
        "Colony's servers. Your account is still alive. Use your external\n"
        "backup to re-import the same identity, or `register` to create a\n"
        "fresh agent."
    )
    return 0


def _resolve_api_key(env_path: Path) -> str | None:
    """Look up the api_key in env first, then the persisted .env file."""
    return os.environ.get(API_KEY_ENV_VAR) or _read_env_var(env_path, API_KEY_ENV_VAR)


def _build_client(env_path: Path) -> Any:
    """Construct a ``colony_chat.ColonyChat`` from env + persisted key.

    Returns ``None`` if no api_key is available; the caller surfaces a
    helpful error instead of letting an exception escape from deep
    inside the SDK.
    """
    api_key = _resolve_api_key(env_path)
    if not api_key:
        return None
    from colony_chat import ColonyChat

    kwargs: dict[str, Any] = {"api_key": api_key}
    base = os.environ.get("COLONY_CHAT_API_BASE")
    if base:
        kwargs["base_url"] = base
    return ColonyChat(**kwargs)


def _cmd_daemon(args: argparse.Namespace) -> int:
    """Run the inbound runtime as a foreground process."""
    from colony_chat_hermes.daemon import Orchestrator, load_invoker

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    client = _build_client(Path(args.env_path))
    if client is None:
        print(
            f"error: no api_key configured (set {API_KEY_ENV_VAR} or run "
            "`colony-chat-hermes register`)",
            file=sys.stderr,
        )
        return 1

    try:
        invoker = load_invoker(args.invoker)
    except (ValueError, ImportError, AttributeError) as e:
        print(f"error: invoker spec {args.invoker!r}: {e}", file=sys.stderr)
        return 2

    lock = LeaderLock(lock_path=Path(args.lock_path)) if args.lock_path else None
    try:
        orch = Orchestrator(
            client=client,
            invoker=invoker,
            mode=args.mode,
            poll_interval=args.poll_interval,
            poll_limit=args.poll_limit,
            webhook_host=args.webhook_host,
            webhook_port=args.webhook_port,
            webhook_path=args.webhook_path,
            webhook_secret=args.webhook_secret or os.environ.get("COLONY_CHAT_WEBHOOK_SECRET"),
            webhook_id=args.webhook_id or os.environ.get("COLONY_CHAT_WEBHOOK_ID"),
            webhook_recovery_interval=args.recovery_interval,
            queue_maxsize=args.queue_maxsize,
            leader_lock=lock,
        )
    except (ValueError, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    try:
        orch.run_until_signal()
    except RuntimeError as e:
        # leader-lock contention raised by orchestrator.start()
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


def _cmd_feed(args: argparse.Namespace) -> int:
    """Read-only tail of inbound notifications, as JSONL on stdout.

    Doesn't dispatch to an invoker. The poller fires its normal
    interval; the consumer here is just ``print(event.to_json())``.
    Use ``--once`` for a one-shot read of currently-unread.
    """
    from colony_chat_hermes.daemon import MessageQueue, NotificationPoller

    client = _build_client(Path(args.env_path))
    if client is None:
        print(
            f"error: no api_key configured (set {API_KEY_ENV_VAR} or run "
            "`colony-chat-hermes register`)",
            file=sys.stderr,
        )
        return 1

    queue = MessageQueue(maxsize=args.queue_maxsize)
    poller = NotificationPoller(
        client=client,
        message_queue=queue,
        interval=args.interval,
        limit=args.limit,
    )

    def _drain() -> None:
        while True:
            event = queue.dequeue(timeout=0.1)
            if event is None:
                return
            sys.stdout.write(event.to_json() + "\n")
            sys.stdout.flush()

    if args.once:
        poller.poll_once()
        _drain()
        return 0

    import signal as _signal
    import threading

    stop = threading.Event()

    def _handle(*_a: Any) -> None:
        stop.set()

    for sig in (_signal.SIGINT, _signal.SIGTERM):
        _signal.signal(sig, _handle)

    poller.start()
    try:
        while not stop.is_set():
            event = queue.dequeue(timeout=1.0)
            if event is None:
                continue
            sys.stdout.write(event.to_json() + "\n")
            sys.stdout.flush()
    finally:
        poller.stop()
    return 0


def _cmd_send(args: argparse.Namespace) -> int:
    """One-shot DM send."""
    client = _build_client(Path(args.env_path))
    if client is None:
        print(
            f"error: no api_key configured (set {API_KEY_ENV_VAR} or run "
            "`colony-chat-hermes register`)",
            file=sys.stderr,
        )
        return 1

    body = args.body
    if body == "-":
        body = sys.stdin.read().rstrip("\n")
    if not body:
        print("error: body is empty", file=sys.stderr)
        return 2

    try:
        result = client.send(
            to=args.handle,
            text=body,
            idempotency_key=args.idempotency_key,
        )
    except Exception as e:
        print(f"error: send failed: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    # Pull the message_id out of either flat or nested shape.
    mid: str | None = None
    if isinstance(result, dict):
        mid = result.get("message_id") or result.get("id")
        if not mid and isinstance(result.get("message"), dict):
            mid = result["message"].get("id")
    if mid:
        print(mid)
    else:
        json.dump(result, sys.stdout)
        sys.stdout.write("\n")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="colony-chat-hermes",
        description=(
            "Hermes Agent plugin for colony-chat — focused DM surface "
            "on The Colony (chat.thecolony.cc)."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    default_env = str(Path.home() / ".hermes" / ".env")
    default_soul = str(Path.home() / ".hermes" / "SOUL.md")

    sub = parser.add_subparsers(dest="command", required=True)

    p_reg = sub.add_parser("register", help="Register a Colony agent and persist the key.")
    p_reg.add_argument("--handle", help="Lowercase-kebab handle (3-32 chars).")
    p_reg.add_argument("--display-name", dest="display_name", help="What humans see.")
    p_reg.add_argument("--bio", default=None, help="Optional one-line bio.")
    p_reg.add_argument(
        "--env-path", default=default_env, help=f"Where to write the key (default: {default_env})."
    )
    p_reg.add_argument(
        "--soul-path",
        default=default_soul,
        help=f"SOUL.md path (default: {default_soul}).",
    )
    p_reg.set_defaults(func=_cmd_register)

    p_status = sub.add_parser("status", help="Show current plugin state.")
    p_status.add_argument("--env-path", default=default_env)
    p_status.set_defaults(func=_cmd_status)

    p_logout = sub.add_parser("logout", help="Clear the api_key + remove the SOUL.md fenced block.")
    p_logout.add_argument("--env-path", default=default_env)
    p_logout.add_argument("--soul-path", default=default_soul)
    p_logout.set_defaults(func=_cmd_logout)

    default_lock = str(Path.home() / ".hermes" / "locks" / "colony-chat-hermes.lock")

    p_daemon = sub.add_parser(
        "daemon",
        help="Run the inbound runtime (poller / webhook receiver / invoker).",
    )
    p_daemon.add_argument("--env-path", default=default_env)
    p_daemon.add_argument(
        "--mode",
        choices=["poll", "webhook", "both"],
        default=os.environ.get("COLONY_CHAT_DAEMON_MODE", "poll"),
        help="Inbound mode. Default 'poll' (Mode B). 'webhook' = Mode A. 'both' = both.",
    )
    p_daemon.add_argument(
        "--poll-interval",
        type=float,
        default=float(os.environ.get("COLONY_CHAT_POLL_INTERVAL_SEC", "15")),
        help="Seconds between /notifications polls (default: 15).",
    )
    p_daemon.add_argument(
        "--poll-limit",
        type=int,
        default=int(os.environ.get("COLONY_CHAT_POLL_LIMIT", "50")),
        help="Notifications to fetch per poll (default: 50).",
    )
    p_daemon.add_argument(
        "--webhook-host",
        default=os.environ.get("COLONY_CHAT_WEBHOOK_HOST", "127.0.0.1"),
        help="Bind interface for the webhook receiver (default: 127.0.0.1).",
    )
    p_daemon.add_argument(
        "--webhook-port",
        type=int,
        default=int(os.environ.get("COLONY_CHAT_WEBHOOK_PORT", "8765")),
        help="Bind port for the webhook receiver (default: 8765).",
    )
    p_daemon.add_argument(
        "--webhook-path",
        default=os.environ.get("COLONY_CHAT_WEBHOOK_PATH", "/webhook"),
        help="URL path the webhook receiver listens on (default: /webhook).",
    )
    p_daemon.add_argument(
        "--webhook-secret",
        default=None,
        help="HMAC secret. If unset, falls back to COLONY_CHAT_WEBHOOK_SECRET env.",
    )
    p_daemon.add_argument(
        "--webhook-id",
        default=None,
        help=(
            "Colony webhook ID to monitor for platform-side auto-disable "
            "(triggers re-enable). Falls back to COLONY_CHAT_WEBHOOK_ID env."
        ),
    )
    p_daemon.add_argument(
        "--recovery-interval",
        type=float,
        default=float(os.environ.get("COLONY_CHAT_RECOVERY_INTERVAL_SEC", "300")),
        help="Seconds between auto-recovery checks (default: 300).",
    )
    p_daemon.add_argument(
        "--invoker",
        default=os.environ.get("COLONY_CHAT_INVOKER", "log_only"),
        help=(
            "Invoker spec: 'log_only', 'log_only:<path>', "
            "'subprocess:<cmd>', or '<module>:<callable>' (default: log_only)."
        ),
    )
    p_daemon.add_argument(
        "--queue-maxsize",
        type=int,
        default=int(os.environ.get("COLONY_CHAT_QUEUE_MAXSIZE", "100")),
        help="Max queued events before backpressure drops new ones (default: 100).",
    )
    p_daemon.add_argument(
        "--lock-path",
        default=default_lock,
        help=(
            "Path to the leader-lock file. Set to empty string to skip "
            "leader-lock acquisition entirely (default: ~/.hermes/locks/colony-chat-hermes.lock)."
        ),
    )
    p_daemon.add_argument(
        "--log-level",
        default=os.environ.get("COLONY_CHAT_LOG_LEVEL", "INFO"),
        help="Python logging level (default: INFO).",
    )
    p_daemon.set_defaults(func=_cmd_daemon)

    p_feed = sub.add_parser(
        "feed",
        help="Tail inbound notifications to stdout as JSONL (read-only).",
    )
    p_feed.add_argument("--env-path", default=default_env)
    p_feed.add_argument("--once", action="store_true", help="Print one batch and exit.")
    p_feed.add_argument(
        "--interval",
        type=float,
        default=float(os.environ.get("COLONY_CHAT_POLL_INTERVAL_SEC", "15")),
    )
    p_feed.add_argument("--limit", type=int, default=50)
    p_feed.add_argument("--queue-maxsize", type=int, default=100)
    p_feed.set_defaults(func=_cmd_feed)

    p_send = sub.add_parser("send", help="One-shot DM send.")
    p_send.add_argument("--env-path", default=default_env)
    p_send.add_argument("handle", help="Recipient handle.")
    p_send.add_argument(
        "body",
        help="Message body, or '-' to read from stdin.",
    )
    p_send.add_argument(
        "--idempotency-key",
        dest="idempotency_key",
        default=None,
        help="Idempotency key (server-side dedup on retry).",
    )
    p_send.set_defaults(func=_cmd_send)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
