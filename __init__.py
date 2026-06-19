"""Hermes directory-plugin entry shim.

``hermes plugins install <owner/repo>`` git-clones this repo into
``~/.hermes/plugins/<name>/`` and imports **this** file as the plugin module,
then calls ``register(ctx)``. The real implementation lives in the
``colony_chat_hermes`` package next to this file, so we put this directory on
``sys.path`` (a git clone isn't pip-installed, so its absolute imports wouldn't
resolve otherwise) and re-export ``register``.

The pip / entry-point install path (``pip install colony-chat-hermes``) imports
``colony_chat_hermes`` directly via the ``hermes_agent.plugins`` entry point and
never touches this shim — it is excluded from the wheel.
"""

import os as _os
import sys as _sys

_here = _os.path.dirname(_os.path.abspath(__file__))
if _here not in _sys.path:
    _sys.path.insert(0, _here)

from colony_chat_hermes import register  # noqa: E402

__all__ = ["register"]
