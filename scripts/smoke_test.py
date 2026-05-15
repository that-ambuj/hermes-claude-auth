#!/usr/bin/env python3
"""Offline self-test for the hermes-claude-auth plugin.

Verifies that loading the plugin module:

  1. Registers the ``anthropic-subscription`` provider profile.
  2. Resolves all four aliases back to the same profile object.
  3. Sets the expected ``base_url``, ``api_mode``, ``fallback_models``,
     and ``default_aux_model`` on the profile.
  4. Monkey-patches ``agent.anthropic_adapter.build_anthropic_kwargs``
     (the patch carries a ``_hermes_claude_auth_wrapped`` marker).
  5. Exposes a ``register(ctx)`` entry point for Hermes' general
     plugin loader (``kind: standalone``).

No network is touched. Safe to run from any cwd. Exits 0 on success,
1 on any failure.

The plugin is loaded the same way Hermes' general PluginManager loads it
(``importlib.util.spec_from_file_location`` on the repo-root
``__init__.py``) so this test exercises the actual installed-plugin
import path.

Hermes' ``providers`` package is resolved from the on-disk hermes-agent
checkout. Lookup order:
  1. ``HERMES_AGENT_ROOT`` env var
  2. ``~/.hermes/hermes-agent`` (the installed location)
  3. ``~/personal/claude-code-subscription-hermes/hermes-agent`` (workspace fork)
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Optional


def _find_hermes_agent_root() -> Optional[Path]:
    candidates: list[Path] = []
    env = os.environ.get("HERMES_AGENT_ROOT")
    if env:
        candidates.append(Path(env).expanduser())
    home = Path.home()
    candidates.extend(
        [
            home / ".hermes" / "hermes-agent",
            home / "personal" / "claude-code-subscription-hermes" / "hermes-agent",
        ]
    )
    for path in candidates:
        if (path / "providers" / "__init__.py").is_file():
            return path
    return None


_NS_PARENT = "hermes_plugins"


def _load_plugin_module(plugin_dir: Path):
    import types

    init_file = plugin_dir / "__init__.py"
    if not init_file.is_file():
        raise FileNotFoundError(f"No __init__.py at {init_file}")

    if _NS_PARENT not in sys.modules:
        ns_pkg = types.ModuleType(_NS_PARENT)
        ns_pkg.__path__ = []
        ns_pkg.__package__ = _NS_PARENT
        sys.modules[_NS_PARENT] = ns_pkg

    module_name = f"{_NS_PARENT}.hermes_claude_auth_smoke_test"
    spec = importlib.util.spec_from_file_location(
        module_name,
        init_file,
        submodule_search_locations=[str(plugin_dir)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create module spec for {init_file}")
    module = importlib.util.module_from_spec(spec)
    module.__package__ = module_name
    module.__path__ = [str(plugin_dir)]
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    hermes_root = _find_hermes_agent_root()
    if hermes_root is None:
        print(
            "FAIL: could not find hermes-agent. Set HERMES_AGENT_ROOT to the "
            "directory containing providers/__init__.py.",
            file=sys.stderr,
        )
        return 1
    sys.path.insert(0, str(hermes_root))
    print(f"[info] hermes-agent at: {hermes_root}")

    plugin_dir = Path(__file__).resolve().parent.parent
    print(f"[info] plugin dir:     {plugin_dir}")

    failures: list[str] = []

    def check(label: str, condition: bool, detail: str = "") -> None:
        status = "PASS" if condition else "FAIL"
        line = f"  [{status}] {label}"
        if detail:
            line += f" — {detail}"
        print(line)
        if not condition:
            failures.append(label)

    try:
        module = _load_plugin_module(plugin_dir)
    except Exception as exc:
        print(f"FAIL: loading plugin module raised {type(exc).__name__}: {exc}")
        return 1

    from providers import get_provider_profile, list_providers

    profile = get_provider_profile("anthropic-subscription")
    check(
        "Provider registered under canonical name 'anthropic-subscription'",
        profile is not None,
        "" if profile is not None else "get_provider_profile returned None",
    )
    if profile is None:
        registered = sorted(p.name for p in list_providers())
        print(f"  [info] currently registered: {registered}")
        return 1

    for alias in ("claude-subscription", "claude-code-subscription", "cc-sub"):
        resolved = get_provider_profile(alias)
        check(
            f"Alias '{alias}' resolves to the subscription profile",
            resolved is profile,
            "" if resolved is profile else f"got {resolved.name if resolved else 'None'}",
        )

    check(
        "base_url == 'https://api.anthropic.com'",
        profile.base_url == "https://api.anthropic.com",
        f"actual: {profile.base_url!r}",
    )
    check(
        "api_mode == 'anthropic_messages'",
        profile.api_mode == "anthropic_messages",
        f"actual: {profile.api_mode!r}",
    )
    check(
        "'claude-opus-4-7' in fallback_models",
        "claude-opus-4-7" in profile.fallback_models,
        f"actual: {tuple(profile.fallback_models)}",
    )
    check(
        "default_aux_model == 'claude-haiku-4-5-20251001'",
        profile.default_aux_model == "claude-haiku-4-5-20251001",
        f"actual: {profile.default_aux_model!r}",
    )

    from agent import anthropic_adapter as _adapter

    wrapped = getattr(_adapter.build_anthropic_kwargs, "_hermes_claude_auth_wrapped", False)
    check(
        "agent.anthropic_adapter.build_anthropic_kwargs is wrapped",
        bool(wrapped),
        f"actual _hermes_claude_auth_wrapped attribute: {wrapped!r}",
    )

    register_fn = getattr(module, "register", None)
    check(
        "register(ctx) entry point exists for general plugin loader",
        callable(register_fn),
        "" if callable(register_fn) else f"got {register_fn!r}",
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
