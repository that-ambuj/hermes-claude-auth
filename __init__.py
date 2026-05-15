"""
hermes-claude-auth — Use a Claude Code subscription with Hermes Agent.

Registers an ``anthropic-subscription`` provider that talks directly to
api.anthropic.com using the OAuth credentials stored by ``claude login``
in ``~/.claude/.credentials.json``. The bundled
``hermes-agent/agent/anthropic_adapter.py`` already handles:

  - OAuth token detection by key prefix (``cc-``, ``sk-ant-`` (non-API),
    ``eyJ``) and routing to ``Authorization: Bearer`` instead of
    ``x-api-key`` (``_is_oauth_token`` at agent/anthropic_adapter.py:325).
  - Reading the access token from ``~/.claude/.credentials.json`` and the
    ``CLAUDE_CODE_OAUTH_TOKEN`` env var.
  - Refreshing expired access tokens against Anthropic's OAuth endpoint.
  - Injecting Claude Code identity + the required ``claude-code-20250219``
    and ``oauth-2025-04-20`` beta headers for OAuth-flagged requests.
  - Default-excluding the ``context-1m-2025-08-07`` beta header so
    accounts without long-context access don't hit Extra Usage 400s
    (agent/anthropic_adapter.py:248-255).
  - PascalCasing tool names on outgoing requests and stripping the
    ``mcp_`` prefix as needed.

This plugin's job is purely to expose that path as a DISTINCT named
provider entry point with a curated subscription-friendly model lineup,
so ``hermes --provider anthropic-subscription`` makes the subscription
path explicit and self-documenting.
"""

from providers import register_provider
from providers.base import ProviderProfile

from . import _patches as _hca_patches

_hca_patches.apply_patches()

# Curated Claude Code subscription model lineup. The trio at the top are
# the current primary models (Opus 4.7, Sonnet 4.6, Haiku 4.5); the
# remaining entries are dated/older snapshots kept as fallbacks. Sourced
# from hermes-agent's canonical catalog at
# ``hermes_cli/models.py`` (the "anthropic" key, lines 292-301).
_FALLBACK_MODELS = (
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
    "claude-opus-4-6",
    "claude-opus-4-5-20251101",
    "claude-sonnet-4-5-20250929",
)

# Design note — why plain ``ProviderProfile`` instead of subclassing the
# bundled ``AnthropicProfile``:
#
# The bundled ``plugins/model-providers/anthropic/__init__.py`` subclasses
# ``ProviderProfile`` only to override ``fetch_models`` so the live model
# catalog probe uses Anthropic's required ``x-api-key`` +
# ``anthropic-version`` headers (the base class sends ``Authorization:
# Bearer``, which Anthropic rejects on ``/v1/models``).
#
# For a subscription provider that authenticates via OAuth, the catalog
# probe is not meaningful anyway:
#   - An OAuth ``cc-`` token will not authenticate against the public
#     ``/v1/models`` listing.
#   - The static ``fallback_models`` tuple above is the source of truth
#     shown in ``/model`` picker UIs when live fetch returns ``None``.
#
# So inheriting the override buys nothing for v0 — the curated list is
# what users actually see. Using plain ``ProviderProfile`` keeps this
# plugin minimal and avoids importing a private subclass across plugins.
# If a future version wants to query Anthropic's catalog with the
# subscription token, swap in a subclass at that time.

profile = ProviderProfile(
    name="anthropic-subscription",
    aliases=("claude-subscription", "claude-code-subscription", "cc-sub"),
    display_name="Anthropic (Claude Code Subscription)",
    description=(
        "api.anthropic.com via Claude Code OAuth credentials. "
        "Avoids Extra Usage billing."
    ),
    signup_url="https://claude.com/code",
    api_mode="anthropic_messages",
    base_url="https://api.anthropic.com",
    # ``auth_type`` stays ``api_key`` because the bundled anthropic_adapter
    # detects OAuth at the token-PREFIX level (``_is_oauth_token`` in
    # agent/anthropic_adapter.py:325), NOT from the profile's ``auth_type``
    # field. Setting ``auth_type="oauth_external"`` here would route the
    # request away from the prefix-detection path that handles credential
    # reading + token refresh + OAuth beta header injection — breaking the
    # whole subscription flow.
    auth_type="api_key",
    # Env-var order matters: ``CLAUDE_CODE_OAUTH_TOKEN`` is listed first so
    # subscription users default to the OAuth path when both env vars are
    # set. When neither is set, the adapter's ``resolve_anthropic_token()``
    # falls through to reading ``~/.claude/.credentials.json``.
    env_vars=("CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY"),
    default_aux_model="claude-haiku-4-5-20251001",
    fallback_models=_FALLBACK_MODELS,
)

register_provider(profile)


def register(ctx):
    """Entry point for Hermes' general plugin loader (``kind: standalone``).

    The actual work — applying the ``agent.anthropic_adapter`` monkey-patches
    and registering the ``anthropic-subscription`` profile — happens at
    *module import time* via the side-effect statements above
    (``_hca_patches.apply_patches()`` and ``register_provider(profile)``).

    Hermes' general ``PluginManager`` first imports the module (firing the
    side effects) and then calls ``register(ctx)``; we expose this no-op
    so the manager does not log a 'no register() function' warning. The
    ``PluginContext`` argument is intentionally unused — this plugin needs
    no tools, hooks, slash commands, or skill registrations.
    """
    del ctx
