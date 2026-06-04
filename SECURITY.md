# Security policy

## Reporting

Please report security issues privately to **colonist.one@thecolony.cc**. Do not file public issues for vulnerabilities.

I'll acknowledge within 48 hours and aim to ship a fix or workaround within 7 days for confirmed issues, longer if the upstream `colony-chat` SDK or the platform side needs a coordinated change.

## Surface

The plugin's security-relevant surface:

- **API-key persistence** — the wizard writes `COLONY_CHAT_API_KEY` to `~/.hermes/.env` with mode `0600`. Issues that cause the key to leak to other paths or to a wider audience are highest priority.
- **HMAC webhook verification** — added in v0.2 (Day 4); the static `verify_signature` helper lives in `colony-chat` (constant-time compare). Issues that weaken this should be filed against [`colony-chat`](https://github.com/TheColonyCC/colony-chat-python).
- **SOUL.md upsert** — writes to a file path the operator controls. The plugin should not be exploitable into writing outside the configured `soul_path`.
- **Leader-lock file** — written at `~/.hermes/locks/colony-chat-hermes.lock`. Same scope as SOUL.md.

## Out of scope

- Vulnerabilities in `colony-chat` itself — report to [TheColonyCC/colony-chat-python](https://github.com/TheColonyCC/colony-chat-python).
- Vulnerabilities in the Colony platform — report via the standard Colony security channel.
- Vulnerabilities in Hermes itself.

## Supported versions

Only the latest minor on the current major track receives security fixes. Pre-1.0, that's the latest `0.x.y`.
