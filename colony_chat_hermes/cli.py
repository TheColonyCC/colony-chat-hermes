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

Day 4 will add ``feed`` (read inbound) and ``send`` (one-shot CLI
send for diagnostics). The v0.1 surface is deliberately register /
status / logout.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from pathlib import Path

from colony_chat_hermes import soul_anchor, wizard
from colony_chat_hermes._version import __version__
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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
