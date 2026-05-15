# hermes-claude-auth installed

Next steps:

1. **Authenticate Claude Code** (if you haven't already):

   ```bash
   claude login
   ```

   This writes `~/.claude/.credentials.json` — the file this plugin reads.

2. **Restart your Hermes session** so the plugin loads and applies its
   monkey-patches to the bundled Anthropic adapter.

3. **Try it out**:

   ```bash
   hermes -z "Say hi in three words" --provider anthropic -m claude-haiku-4-5-20251001
   ```

   Use `--provider anthropic` (the bundled provider name). The plugin's
   patches make the bundled `anthropic` provider transparently use your
   subscription via the OAuth path — no API key required, no Extra Usage
   billing.

## Make it your default

Edit `~/.hermes/config.yaml`:

```yaml
model:
  provider: anthropic
  default: claude-sonnet-4-6
```

## Models

`claude-opus-4-7`, `claude-sonnet-4-6`, `claude-haiku-4-5-20251001` are the
current primary models. Older dated snapshots are also supported.

## Debug

```bash
HERMES_CLAUDE_AUTH_DEBUG=1 hermes -z "ping" --provider anthropic -m claude-haiku-4-5-20251001
```

Logs every patch action to stderr and `~/.hermes/hermes-claude-auth.log`.

## Known limitation

`--provider anthropic-subscription` (the distinct name) returns HTTP 404
due to a hardcoded provider-name check in Hermes itself
(`hermes_cli/runtime_provider.py:253`). Use `--provider anthropic` until
that lands upstream — the plugin's patches affect the global bundled
adapter so the subscription path works there transparently.

See [README](https://github.com/that-ambuj/hermes-claude-auth) for full
docs.
