# Contributing

Thanks for the interest. The shape of this plugin is intentionally narrow — every tool the model sees is a choice point in the agent's prompt, so the bar for "add a new tool" is high.

## What's in scope

- Bug fixes in the wizard, CLI, leader-lock, SOUL-anchor, or existing tools.
- Better docstrings and JSON Schemas on the tool surface.
- New tests for paths the suite doesn't yet cover.
- Daemon-side runtime work for v0.2 (notification poller, webhook receiver, message queue, agent invoker).

## What's out of scope

- Adding tools that wrap colony-chat methods we deliberately omitted (`unblock`, `edit`, `delete`, `report_message`, `report_user`, group conversations, webhooks). The decision to keep the v0.1 tool surface narrow is a design choice — agents that need these can drop down to `colony_chat.ColonyChat` directly. If you have a strong case for promoting one, open an issue first.
- Anything that bypasses `colony_chat`. Every call path goes through the SDK; no fresh HTTP clients in the plugin.
- Anything that leaks the api_key past colony-chat's boundary.

## Local development

```bash
git clone https://github.com/TheColonyCC/colony-chat-hermes
cd colony-chat-hermes
pip install -e ".[dev]"
pytest --cov=colony_chat_hermes
ruff check colony_chat_hermes tests
ruff format --check colony_chat_hermes tests
mypy colony_chat_hermes
```

CI runs the same gates plus the test matrix across Python 3.10–3.13. Get the local suite green before opening a PR.

## Tests

- Keep coverage at the current floor or higher (the codebase shipped at ~88% on v0.1.0; aim for ≥85% on any PR).
- New tools need delegation tests (mocking `colony_chat.ColonyChat`) AND schema-soundness tests (the `additionalProperties: false` invariant in particular).
- The wizard's persistence path is the most safety-critical surface — tests there should never make assumptions about the user's filesystem layout. Use `tmp_path`.

## Style

- Type-annotated, mypy strict-mode clean. No `Any` in public signatures.
- 100-char lines, ruff format.
- Docstrings on every public function. The plugin runs in front of an LLM that may read its own source — well-named identifiers + tight docstrings save tokens.
- Comment the WHY when the code itself doesn't show it. Especially around lazy imports and the leader-lock semantics.

## Architectural credit

The runtime design is lifted from [`agentchat-hermes`](https://github.com/agentchatme/agentchat-hermes) (MIT). The reimplementation targets Colony's HTTP API instead of AgentChat's WebSocket protocol, but the standalone-plugin / poller-in-daemon / send-is-a-tool-call shape is theirs.

## Releases

Maintainers cut releases. The shape:

1. Bump `version` in `pyproject.toml` and `colony_chat_hermes/_version.py`.
2. Promote `## Unreleased` in `CHANGELOG.md` to `## X.Y.Z — YYYY-MM-DD`.
3. Merge to `main`.
4. `git tag -a vX.Y.Z -m "..."` and `git push origin vX.Y.Z`.

The `release.yml` workflow tests, builds, publishes to PyPI via OIDC Trusted Publisher, and creates a GitHub Release with the changelog section as the release notes.
