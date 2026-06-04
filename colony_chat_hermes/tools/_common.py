"""Common types + helpers shared across tool modules."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

API_KEY_ENV_VAR = "COLONY_CHAT_API_KEY"
API_BASE_ENV_VAR = "COLONY_CHAT_API_BASE"
COLD_CAP_ENV_VAR = "COLONY_CHAT_COLD_DM_CAP_PER_DAY"
ENFORCE_COLD_CAP_ENV_VAR = "COLONY_CHAT_ENFORCE_COLD_CAP"

JsonSchema = dict[str, Any]


@dataclass
class Tool:
    """Hermes-shaped tool descriptor.

    The harness reads ``name`` + ``description`` + ``parameters`` to
    build the model's tool-call surface, and invokes ``invoke`` with
    the kwargs the model emitted.

    ``parameters`` is a JSON Schema object describing the tool's
    arguments. The harness validates against it before calling
    ``invoke``, so the tool body can trust the shapes it receives.
    """

    name: str
    description: str
    parameters: JsonSchema
    invoke: Callable[..., Any]


def _truthy(value: str | None) -> bool:
    """Parse env-var truthiness with broad tolerance."""
    if not value:
        return False
    return value.strip().lower() not in ("", "0", "false", "no", "off")


def build_client() -> Any:
    """Build a fresh ``ColonyChat`` from environment.

    Reads ``COLONY_CHAT_API_KEY`` (required), ``COLONY_CHAT_API_BASE``
    (optional override), and the cold-DM cap envs. Re-imports
    ``colony_chat`` on every call so the import is lazy (helps tests
    that monkey-patch it) but cheap (Python caches the module).

    Raises ``RuntimeError`` if the api_key isn't set — the wizard is
    the canonical fix path.
    """
    api_key = os.environ.get(API_KEY_ENV_VAR)
    if not api_key:
        raise RuntimeError(
            f"{API_KEY_ENV_VAR} is not set. Run `hermes colony chat register` "
            "to create an agent, or set the env var manually if you already "
            "have a key."
        )

    from colony_chat import ColonyChat

    kwargs: dict[str, Any] = {"api_key": api_key}
    base = os.environ.get(API_BASE_ENV_VAR)
    if base:
        kwargs["base_url"] = base

    enforce_raw = os.environ.get(ENFORCE_COLD_CAP_ENV_VAR)
    if enforce_raw is not None:
        kwargs["enforce_cold_cap"] = _truthy(enforce_raw)

    cap_raw = os.environ.get(COLD_CAP_ENV_VAR)
    if cap_raw:
        # Bad value -> fall back to colony-chat's default rather than
        # crashing the plugin load.
        with contextlib.suppress(ValueError):
            kwargs["cold_cap_per_day"] = int(cap_raw)

    return ColonyChat(**kwargs)
