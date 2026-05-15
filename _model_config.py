"""Static model configuration for the beta-stripping workaround.

Python port of ``opencode-claude-auth/src/model-config.ts``. The values
here are mirrored verbatim from the TypeScript source so that Hermes
sends the *exact same* ``anthropic-beta`` set that the OpenCode plugin
sends — which is known not to trigger Anthropic's Extra Usage gating
when authenticating with a Claude Code OAuth token.

Keep the dict-key insertion order stable. ``get_model_override`` does a
first-match-wins substring search, so more specific keys (e.g.
``"opus-4-6"``) must come before broader ones (e.g. ``"opus"``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class ModelOverride:
    exclude: Optional[List[str]] = None
    add: Optional[List[str]] = None
    disable_effort: bool = False


@dataclass(frozen=True)
class ModelConfig:
    cc_version: str
    base_betas: List[str]
    long_context_betas: List[str]
    model_overrides: Dict[str, ModelOverride] = field(default_factory=dict)


# Verbatim port of ``config`` in opencode-claude-auth/src/model-config.ts.
# Do not edit individual fields without first checking the TS source —
# any divergence here defeats the whole purpose of this module.
CONFIG = ModelConfig(
    cc_version="2.1.112",
    base_betas=[
        "claude-code-20250219",
        "oauth-2025-04-20",
        "interleaved-thinking-2025-05-14",
        "prompt-caching-scope-2026-01-05",
        "context-management-2025-06-27",
        "advisor-tool-2026-03-01",
    ],
    long_context_betas=[
        "context-1m-2025-08-07",
        "interleaved-thinking-2025-05-14",
    ],
    model_overrides={
        # Order matters — first-match-wins on substring.  Haiku must come
        # before any broader patterns because the haiku models also match
        # "4-5"/"4-6" substrings.
        "haiku": ModelOverride(
            exclude=["interleaved-thinking-2025-05-14"],
            disable_effort=True,
        ),
        "4-6": ModelOverride(add=["effort-2025-11-24"]),
        "4-7": ModelOverride(add=["effort-2025-11-24"]),
    },
)


def get_model_override(model_id: str) -> Optional[ModelOverride]:
    """Return the first matching override for ``model_id`` or ``None``.

    Matches the TS implementation: case-insensitive substring check
    against the model id, walking ``model_overrides`` in insertion order
    and returning the first hit.  Callers should list more specific keys
    before broader ones so the precedence works correctly.
    """
    if not isinstance(model_id, str) or not model_id:
        return None
    lower = model_id.lower()
    for pattern, override in CONFIG.model_overrides.items():
        if pattern.lower() in lower:
            return override
    return None
