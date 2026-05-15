"""Beta-header management mirrored from opencode-claude-auth.

Python port of ``opencode-claude-auth/src/betas.ts``.  Owns:

  * The header set we ask Anthropic to apply on every OAuth-authenticated
    request (``get_model_betas``).
  * The per-model exclusion cache that the retry loop in ``_patches`` mutates
    when Anthropic returns the long-context / Extra-Usage 400 family.
  * The substring matcher that decides whether a response body is in fact
    the long-context error class.

The exclusion cache is mutated from the API-call wrapper, which Hermes
invokes from worker threads.  All access is therefore guarded by a
module-level ``Lock``.  The TS source is single-threaded so it does not
need the equivalent — Python does.
"""

from __future__ import annotations

import os
import re
import threading
from typing import Dict, List, Optional, Set

from ._model_config import CONFIG, get_model_override


LONG_CONTEXT_BETAS: List[str] = list(CONFIG.long_context_betas)


_LOCK = threading.Lock()
_excluded_betas: Dict[str, Set[str]] = {}
_last_beta_flags_env: Optional[str] = os.environ.get("ANTHROPIC_BETA_FLAGS")
_last_model_id: Optional[str] = None


def _read_required_betas_env() -> Optional[str]:
    return os.environ.get("ANTHROPIC_BETA_FLAGS")


def get_required_betas() -> List[str]:
    raw = _read_required_betas_env()
    if raw is None:
        return list(CONFIG.base_betas)
    return [s.strip() for s in raw.split(",") if s.strip()]


def get_excluded_betas(model_id: str) -> Set[str]:
    global _last_beta_flags_env, _last_model_id
    current_env = _read_required_betas_env()
    with _LOCK:
        if current_env != _last_beta_flags_env:
            _excluded_betas.clear()
            _last_beta_flags_env = current_env
        if _last_model_id is not None and _last_model_id != model_id:
            _excluded_betas.clear()
        _last_model_id = model_id
        return set(_excluded_betas.get(model_id, set()))


def add_excluded_beta(model_id: str, beta: str) -> None:
    with _LOCK:
        existing = _excluded_betas.get(model_id)
        if existing is None:
            existing = set()
            _excluded_betas[model_id] = existing
        existing.add(beta)


def reset_excluded_betas() -> None:
    global _last_model_id
    with _LOCK:
        _excluded_betas.clear()
        _last_model_id = None


_LONG_CONTEXT_ERROR_NEEDLES = (
    "Extra usage is required for long context requests",
    "long context beta is not yet available",
    "You're out of extra usage",
)


def is_long_context_error(response_body: str) -> bool:
    if not isinstance(response_body, str) or not response_body:
        return False
    return any(needle in response_body for needle in _LONG_CONTEXT_ERROR_NEEDLES)


def get_next_beta_to_exclude(model_id: str) -> Optional[str]:
    excluded = get_excluded_betas(model_id)
    for beta in LONG_CONTEXT_BETAS:
        if beta not in excluded:
            return beta
    return None


_VERSION_RE = re.compile(r"(opus|sonnet)-(\d+)-(\d+)")


def supports_1m_context(model_id: str) -> bool:
    if not isinstance(model_id, str):
        return False
    lower = model_id.lower()
    if "opus" not in lower and "sonnet" not in lower:
        return False
    match = _VERSION_RE.search(lower)
    if not match:
        return False
    try:
        major = int(match.group(2))
        minor = int(match.group(3))
    except ValueError:
        return False
    effective_minor = 0 if minor > 99 else minor
    return major > 4 or (major == 4 and effective_minor >= 6)


def is_enable_1m_context() -> bool:
    raw = os.environ.get("ANTHROPIC_ENABLE_1M_CONTEXT", "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def get_model_betas(model_id: str, excluded: Optional[Set[str]] = None) -> List[str]:
    betas: List[str] = list(get_required_betas())

    if is_enable_1m_context() and supports_1m_context(model_id):
        first_long = CONFIG.long_context_betas[0] if CONFIG.long_context_betas else None
        if first_long and first_long not in betas:
            betas.append(first_long)

    override = get_model_override(model_id)
    if override is not None:
        if override.exclude:
            for ex in override.exclude:
                while ex in betas:
                    betas.remove(ex)
        if override.add:
            for add in override.add:
                if add not in betas:
                    betas.append(add)

    if excluded:
        betas = [b for b in betas if b not in excluded]

    return betas
