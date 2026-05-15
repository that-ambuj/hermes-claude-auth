# hermes-claude-auth

Use your Claude Code subscription with Hermes — no separate API key, no Extra Usage charges.

This is the Hermes-side analogue of [opencode-claude-auth](https://github.com/griffinmartin/opencode-claude-auth): same goal (reuse the OAuth credentials `claude login` wrote to `~/.claude/.credentials.json`), same defense against Anthropic's Extra Usage billing gate, different host. The beta-header set and the long-context retry loop are ported verbatim from `opencode-claude-auth/src/{model-config,betas}.ts`.

## TL;DR

```bash
claude login                                                # once
hermes plugins install that-ambuj/hermes-claude-auth --enable
# restart your Hermes session
hermes -z "ping" --provider anthropic -m claude-haiku-4-5-20251001
# → no Extra Usage 400
```

## Install

### Via Hermes (recommended — no scripts, no symlinks)

```bash
hermes plugins install that-ambuj/hermes-claude-auth --enable
```

That's it. Hermes clones the repo into `~/.hermes/plugins/hermes-claude-auth/`, adds the plugin to `plugins.enabled` in your config, and prints next steps. Restart your active Hermes session so the plugin loads.

To uninstall:

```bash
hermes plugins remove hermes-claude-auth
```

### Via the web UI

If you run `hermes dashboard`, the Plugins page lets you install by entering `that-ambuj/hermes-claude-auth` and toggling enable. No terminal access required after install.

### From a Git URL (alternative shorthand)

```bash
hermes plugins install https://github.com/that-ambuj/hermes-claude-auth.git --enable
```

## What this plugin does

When Hermes loads it on startup, the plugin runs at module-import time and monkey-patches two functions in the bundled `agent.anthropic_adapter` — entirely in-memory, no Hermes source files are modified:

1. **`build_anthropic_kwargs`** is wrapped so every OAuth-authenticated request gets a fresh `extra_headers["anthropic-beta"]` value computed via [`_betas.py`](./_betas.py), which mirrors `opencode-claude-auth/src/betas.ts`:
   - **Default beta set** = `opencode-claude-auth`'s `baseBetas` (`claude-code-20250219`, `oauth-2025-04-20`, `interleaved-thinking-2025-05-14`, `prompt-caching-scope-2026-01-05`, `context-management-2025-06-27`, `advisor-tool-2026-03-01`).
   - **Per-model overrides** (from [`_model_config.py`](./_model_config.py)):
     - `haiku` models → strip `interleaved-thinking-2025-05-14` (the change that fixed Extra Usage 400 in the reproducer).
     - `opus-4-6` / `opus-4-7` / `sonnet-4-6` → add `effort-2025-11-24`.
   - **1M context** stays opt-in via `ANTHROPIC_ENABLE_1M_CONTEXT=true` (off by default to avoid Extra Usage).
   - **Tool name PascalCasing** (`bash` → `mcp_Bash`) on both tool definitions and `tool_use` blocks in message history.
   - **Orphaned `tool_use` / `tool_result` repair** — drops unpaired blocks that would cause Anthropic 400s on multi-turn tool sessions.
   - **System-prompt relocation** — non-billing-header / non-Claude-Code-identity system entries get moved to the first user message (Anthropic's OAuth path rejects third-party system entries alongside the Claude Code identity prefix).
   - **`x-anthropic-billing-header`** is computed (`cc_version` + `cc_entrypoint` + `cch` SHA-256 hash of the first user message) and injected as `system[0]`, matching what `opencode-claude-auth/src/signing.ts` produces.

2. **`build_anthropic_client`** is wrapped so the returned client's `messages.create` and `messages.stream` go through a retry loop: on `HTTP 400` with a body matching `is_long_context_error()` (matches the three substrings `"Extra usage is required for long context requests"`, `"long context beta is not yet available"`, `"You're out of extra usage"`), the next beta from `LONG_CONTEXT_BETAS = ["context-1m-2025-08-07", "interleaved-thinking-2025-05-14"]` is added to the session-level exclusion set and the call is retried with rebuilt headers. Up to `len(LONG_CONTEXT_BETAS)` retries, then the original error propagates.

Both wraps are no-ops for non-OAuth tokens — `_is_oauth_token()` gates the override — so regular `ANTHROPIC_API_KEY` users are unaffected.

The plugin also registers an `anthropic-subscription` `ProviderProfile` for discoverability (`hermes plugins`, `hermes model`). **See the "Known limitation" section below — the `--provider anthropic-subscription` route currently 404s due to a hardcoded provider-name check inside Hermes. Use `--provider anthropic` until that's resolved.**

## What Hermes does for you (no plugin code involved)

Everything else is already in Hermes' bundled `agent/anthropic_adapter.py`:

- **Credential reading** — `resolve_anthropic_token()` checks `CLAUDE_CODE_OAUTH_TOKEN` env var, then `~/.claude/.credentials.json` (the file `claude login` writes).
- **OAuth detection** — `_is_oauth_token()` matches `cc-`, `sk-ant-oat`, or `eyJ` prefixes and routes to `Authorization: Bearer`.
- **Token refresh** — when `expiresAt` is in the past, `refresh_anthropic_oauth_pure()` POSTs to `platform.claude.com/v1/oauth/token` (with `console.anthropic.com` fallback) and writes rotated tokens back to disk.
- **Claude Code user-agent + `x-app: cli` headers** — auto-injected on OAuth detection.
- **Response-stream `mcp_` tool-name reversal** — `strip_tool_prefix` kwarg on `normalize_response`, wired to `self._is_anthropic_oauth` at 5 call sites in `run_agent.py`.

## Prerequisites

- Active Claude Code subscription (Pro / Max / Team).
- `claude login` completed — `~/.claude/.credentials.json` must exist.
- Hermes Agent `v0.13.x` or newer.

## Usage

```bash
# Haiku (cheapest; primary subscription model)
hermes -z "Say hi in three words" --provider anthropic -m claude-haiku-4-5-20251001

# Sonnet 4.6 (latest, no date suffix)
hermes -z "Explain RAII briefly" --provider anthropic -m claude-sonnet-4-6

# Opus 4.7 (latest)
hermes -z "Refactor this code..." --provider anthropic -m claude-opus-4-7

# Interactive chat
hermes chat --provider anthropic -m claude-sonnet-4-6
```

To make the subscription path your default, edit `~/.hermes/config.yaml`:

```yaml
model:
  provider: anthropic
  default: claude-sonnet-4-6
```

## Supported models

Verified against Hermes' canonical catalog (`hermes_cli/models.py`, the `"anthropic"` key):

| Model                        | Class  | Notes                                          |
| ---------------------------- | ------ | ---------------------------------------------- |
| `claude-opus-4-7`            | Opus   | Latest Opus, no date suffix                    |
| `claude-sonnet-4-6`          | Sonnet | Latest Sonnet, no date suffix                  |
| `claude-haiku-4-5-20251001`  | Haiku  | Dated snapshot; default aux                    |
| `claude-opus-4-6`            | Opus   | Previous Opus, gets `effort-2025-11-24` beta   |
| `claude-opus-4-5-20251101`   | Opus   | Dated 4.5 snapshot                             |
| `claude-sonnet-4-5-20250929` | Sonnet | Dated 4.5 snapshot                             |

## Verification

```bash
# Offline smoke test (no network) — 10 checks including patch-applied verification
python3 scripts/smoke_test.py
```

Expected: `Summary: all checks passed.`

```bash
# Live round-trip — confirms patches actually prevent Extra Usage
hermes -z "Reply with exactly: HCA_OK" --provider anthropic -m claude-haiku-4-5-20251001
```

Expected: `HCA_OK` and exit code 0. NOT a `400 "out of extra usage"` error.

## Known limitation: `--provider anthropic-subscription` 404s

The plugin registers `anthropic-subscription` as a distinct named provider (with aliases `claude-subscription`, `claude-code-subscription`, `cc-sub`) so `hermes model` and `hermes --provider` autocomplete show it. But selecting it triggers:

```
API call failed after 3 retries: HTTP 404
```

**Root cause:** Hermes' `hermes_cli/runtime_provider.py:253` has a hardcoded `elif provider == "anthropic":` branch that sets `api_mode = "anthropic_messages"` and the correct base URL. Any provider name other than `anthropic` falls through to the default `chat_completions` transport, which POSTs `/v1/chat/completions` against `api.anthropic.com` and gets a bare nginx 404.

**Workaround:** use `--provider anthropic` (the bundled name). The patches in this plugin affect the global bundled adapter, so the bundled `anthropic` provider transparently gets the subscription-friendly beta set.

## v0.1 scope

- ✅ Beta-header replacement matching opencode-claude-auth's known-working set
- ✅ Per-model overrides (haiku strips `interleaved-thinking`, 4-6/4-7 add `effort-2025-11-24`)
- ✅ Long-context Extra Usage retry loop (mirrors opencode-claude-auth's `getNextBetaToExclude` flow)
- ✅ Tool name PascalCasing on requests
- ✅ Orphaned `tool_use` / `tool_result` block repair (mirrors `repairToolPairs` from `transforms.ts:32-87`)
- ✅ System-prompt relocation to first user message
- ✅ `x-anthropic-billing-header` injection
- ✅ Reads `~/.claude/.credentials.json` directly (no env var required — bundled adapter handles it)
- ✅ OAuth detection / Bearer routing / token refresh (inherited from bundled adapter)
- ✅ `ProviderProfile` registration for `hermes plugins` / `hermes model` discoverability
- ❌ `--provider anthropic-subscription` end-to-end (Hermes-internal hardcoded routing — see "Known limitation")
- ❌ macOS Keychain multi-account enumeration (planned for v0.2)
- ❌ Stainless SDK header mimicry beyond bundled adapter (deferred)

## Debugging

Set `HERMES_CLAUDE_AUTH_DEBUG=1` to log every patch action to stderr **and** to `~/.hermes/hermes-claude-auth.log`:

```
[hermes-claude-auth] patches applied (build_anthropic_kwargs, build_anthropic_client)
[hermes-claude-auth] override anthropic-beta for model='claude-haiku-4-5-20251001': claude-code-20250219,oauth-2025-04-20,prompt-caching-scope-2026-01-05,context-management-2025-06-27,advisor-tool-2026-03-01
[hermes-claude-auth] excluded beta 'context-1m-2025-08-07' for model 'claude-opus-4-7' on Extra Usage retry
```

A different log path: `HERMES_CLAUDE_AUTH_LOG=/path/to/log`.

## Troubleshooting

| Symptom                                                  | Fix                                                                                                                                          |
| -------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `Plugin not enabled`                                     | Run `hermes plugins enable hermes-claude-auth` and restart your Hermes session.                                                              |
| `401 unauthorized` / `auth failure`                      | Token expired or missing. Run `claude login` and retry.                                                                                      |
| `400 You're out of extra usage` (despite this plugin)    | Run with `HERMES_CLAUDE_AUTH_DEBUG=1` and check what `anthropic-beta` is being sent. If the retry loop is firing, narrow down which beta still triggers gating and open an issue. |
| `404` with `--provider anthropic-subscription`           | Known limitation (see above). Use `--provider anthropic` instead.                                                                            |
| `smoke_test.py` says "could not find hermes-agent"       | Set `HERMES_AGENT_ROOT=/path/to/hermes-agent` and re-run.                                                                                    |

## Uninstall

```bash
hermes plugins remove hermes-claude-auth
```

The patches only apply while the plugin is enabled and loaded. Removing the plugin restores Hermes' default bundled-adapter behavior on the next session.

## Develop

```bash
git clone https://github.com/that-ambuj/hermes-claude-auth.git
cd hermes-claude-auth
python3 scripts/smoke_test.py             # offline tests
python3 -m py_compile *.py scripts/*.py   # syntax check
```

To test changes against a real Hermes install, symlink your checkout:

```bash
ln -s "$(pwd)" "$HOME/.hermes/plugins/hermes-claude-auth"
hermes plugins enable hermes-claude-auth
```

## Credits

- [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) — host project.
- [opencode-claude-auth](https://github.com/griffinmartin/opencode-claude-auth) — direct inspiration and reference implementation. `_model_config.py` and `_betas.py` are line-for-line Python ports of its `model-config.ts` and `betas.ts`.
- [Anthropic](https://www.anthropic.com/) — Claude, Claude Code, the OAuth flow, and `~/.claude/.credentials.json`.

## License

MIT. See [LICENSE](./LICENSE).
