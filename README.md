# hermes-claude-auth

Use your Claude Code subscription with Hermes — no separate API key, no Extra Usage charges.

The Hermes-side analogue of [opencode-claude-auth](https://github.com/griffinmartin/opencode-claude-auth): reuse the OAuth credentials `claude login` wrote to `~/.claude/.credentials.json`, defend against Anthropic's Extra Usage billing gate.

## TL;DR

```bash
claude login                                                # once
hermes plugins install that-ambuj/hermes-claude-auth --enable
# restart your Hermes session
hermes -z "ping" --provider anthropic -m claude-haiku-4-5-20251001
```

## Install

### Via Hermes (recommended — no scripts, no symlinks)

```bash
hermes plugins install that-ambuj/hermes-claude-auth --enable
```

Hermes clones the repo into `~/.hermes/plugins/hermes-claude-auth/`, adds it to `plugins.enabled` in your config, and prints next steps. Restart your active Hermes session so the plugin loads.

### Via the web UI

If you run `hermes dashboard`, the Plugins page lets you install by entering `that-ambuj/hermes-claude-auth` and toggling enable.

### From a Git URL

```bash
hermes plugins install https://github.com/that-ambuj/hermes-claude-auth.git --enable
```

## Prerequisites

- Active Claude Code subscription (Pro / Max / Team).
- `claude login` completed — `~/.claude/.credentials.json` must exist.
- Hermes Agent `v0.13.x` or newer.

## Usage

```bash
# Haiku (cheapest; primary subscription model)
hermes -z "Say hi in three words" --provider anthropic -m claude-haiku-4-5-20251001

# Sonnet 4.6 (latest)
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

| Model                        | Class  | Notes                       |
| ---------------------------- | ------ | --------------------------- |
| `claude-opus-4-7`            | Opus   | Latest                      |
| `claude-sonnet-4-6`          | Sonnet | Latest                      |
| `claude-haiku-4-5-20251001`  | Haiku  | Default aux                 |
| `claude-opus-4-6`            | Opus   | Adds `effort-2025-11-24`    |
| `claude-opus-4-5-20251101`   | Opus   | Dated 4.5 snapshot          |
| `claude-sonnet-4-5-20250929` | Sonnet | Dated 4.5 snapshot          |

## Known limitation: `--provider anthropic-subscription` 404s

The plugin registers `anthropic-subscription` as a distinct named provider, but selecting it returns HTTP 404. This is a hardcoded provider-name check inside Hermes itself (`hermes_cli/runtime_provider.py:253`).

**Workaround:** use `--provider anthropic` (the bundled name). The plugin's patches affect the global bundled adapter, so the bundled `anthropic` provider transparently gets the subscription-friendly behavior.

## Debugging

Set `HERMES_CLAUDE_AUTH_DEBUG=1` to log every patch action to stderr and `~/.hermes/hermes-claude-auth.log`:

```
[hermes-claude-auth] patches applied (build_anthropic_kwargs, build_anthropic_client)
[hermes-claude-auth] override anthropic-beta for model='claude-haiku-4-5-20251001': ...
[hermes-claude-auth] excluded beta 'context-1m-2025-08-07' for model 'claude-opus-4-7' on Extra Usage retry
```

A different log path: `HERMES_CLAUDE_AUTH_LOG=/path/to/log`.

## Troubleshooting

| Symptom                                                | Fix                                                                                              |
| ------------------------------------------------------ | ------------------------------------------------------------------------------------------------ |
| `Plugin not enabled`                                   | Run `hermes plugins enable hermes-claude-auth` and restart your Hermes session.                  |
| `401 unauthorized` / auth failure                      | Token expired or missing. Run `claude login` and retry.                                          |
| `400 You're out of extra usage` (despite this plugin)  | Run with `HERMES_CLAUDE_AUTH_DEBUG=1`, inspect outgoing `anthropic-beta`, open an issue.         |
| `404` with `--provider anthropic-subscription`         | Known limitation. Use `--provider anthropic` instead.                                            |

## Uninstall

```bash
hermes plugins remove hermes-claude-auth
```

The plugin's behavior changes only apply while it's loaded — removing it restores Hermes' default bundled-adapter behavior on the next session.

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
- [Anthropic](https://www.anthropic.com/) — Claude, Claude Code, and the OAuth flow.

## License

MIT. See [LICENSE](./LICENSE).
