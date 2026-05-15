#!/usr/bin/env python3
"""CI-friendly structural test.

Verifies _model_config + _betas semantics without requiring hermes-agent
to be installed. Loads the modules with the same namespace-parent
pattern Hermes' general PluginManager uses, so relative imports
(``from ._model_config import ...``) resolve correctly.

Exits 0 on success, 1 on failure.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


_PKG = "_hermes_claude_auth_test"


def _load(name: str, path: Path):
    full = f"{_PKG}.{name}"
    spec = importlib.util.spec_from_file_location(
        full,
        path,
        submodule_search_locations=[str(path.parent)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = _PKG
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    repo = Path(__file__).resolve().parent.parent

    ns = types.ModuleType(_PKG)
    ns.__path__ = [str(repo)]
    sys.modules[_PKG] = ns

    failures: list[str] = []

    def check(label: str, cond: bool, detail: str = "") -> None:
        status = "PASS" if cond else "FAIL"
        line = f"  [{status}] {label}"
        if detail:
            line += f" — {detail}"
        print(line)
        if not cond:
            failures.append(label)

    mc = _load("_model_config", repo / "_model_config.py")
    bt = _load("_betas", repo / "_betas.py")

    check("_model_config.CONFIG.cc_version == '2.1.112'", mc.CONFIG.cc_version == "2.1.112")
    check(
        "base_betas has all 6 entries",
        len(mc.CONFIG.base_betas) == 6 and "claude-code-20250219" in mc.CONFIG.base_betas,
    )
    check("haiku override present", "haiku" in mc.CONFIG.model_overrides)
    check(
        "get_model_override('claude-haiku-4-5') returns override",
        mc.get_model_override("claude-haiku-4-5") is not None,
    )
    check(
        "haiku override has disable_effort=True",
        mc.get_model_override("claude-haiku-4-5").disable_effort is True,
    )

    check(
        "LONG_CONTEXT_BETAS exact match",
        bt.LONG_CONTEXT_BETAS == ["context-1m-2025-08-07", "interleaved-thinking-2025-05-14"],
    )
    check(
        "is_long_context_error: 'You're out of extra usage'",
        bt.is_long_context_error("You're out of extra usage"),
    )
    check(
        "is_long_context_error: 'Extra usage is required for long context requests'",
        bt.is_long_context_error("Extra usage is required for long context requests"),
    )
    check(
        "is_long_context_error: 'long context beta is not yet available'",
        bt.is_long_context_error("long context beta is not yet available"),
    )
    check(
        "is_long_context_error: negative case",
        not bt.is_long_context_error("totally different error"),
    )

    check("supports_1m_context('claude-opus-4-7')", bt.supports_1m_context("claude-opus-4-7"))
    check("supports_1m_context('claude-sonnet-4-6')", bt.supports_1m_context("claude-sonnet-4-6"))
    check("NOT supports_1m_context('claude-haiku-4-5')", not bt.supports_1m_context("claude-haiku-4-5"))
    check("NOT supports_1m_context('claude-opus-4-5')", not bt.supports_1m_context("claude-opus-4-5"))
    check(
        "NOT supports_1m_context('claude-opus-4-20250514') (minor>99 → 0)",
        not bt.supports_1m_context("claude-opus-4-20250514"),
    )

    haiku_betas = bt.get_model_betas("claude-haiku-4-5-20251001")
    check(
        "get_model_betas(haiku) strips interleaved-thinking",
        "interleaved-thinking-2025-05-14" not in haiku_betas,
        f"got: {haiku_betas}",
    )
    check(
        "get_model_betas(haiku) keeps claude-code-20250219",
        "claude-code-20250219" in haiku_betas,
        f"got: {haiku_betas}",
    )

    opus_betas = bt.get_model_betas("claude-opus-4-7")
    check(
        "get_model_betas(opus-4-7) adds effort-2025-11-24",
        "effort-2025-11-24" in opus_betas,
        f"got: {opus_betas}",
    )

    print()
    if failures:
        print(f"Summary: {len(failures)} failed.")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("Summary: all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
