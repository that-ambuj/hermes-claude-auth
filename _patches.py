"""Monkey-patches that mirror opencode-claude-auth's beta + retry semantics
into Hermes' bundled ``agent.anthropic_adapter``.

Hook strategy (chosen after reading hermes-agent/agent/anthropic_adapter.py
and run_agent.py):

  1. ``build_anthropic_kwargs`` is wrapped because that function is the
     ONLY chokepoint that is called for both the non-streaming
     (``_anthropic_messages_create``) and streaming (``messages.stream``)
     code paths in run_agent.py.  Anthropic's SDK honours
     ``extra_headers["anthropic-beta"]`` as an override of the
     client-level ``default_headers`` set inside ``build_anthropic_client``,
     so injecting the OpenCode-style beta set there is enough to replace
     Hermes' default set without forking the bundled client builder.

  2. ``build_anthropic_client`` is wrapped because the client it returns
     is where ``messages.create`` / ``messages.stream`` actually live, and
     therefore where we can install the long-context retry loop without
     having to reach into the agent class in run_agent.py.  This keeps the
     plugin self-contained — patching is limited to two adapter-module
     symbols, no class references into run_agent.py.

Both wraps are no-ops for non-OAuth tokens (regular ``sk-ant-api*`` API
keys), so users who authenticate Hermes with a normal Anthropic API key
see exactly the bundled-adapter behaviour.

Idempotent: a module-level ``_PATCHED`` flag prevents double-wrapping if
``apply_patches`` is invoked from more than one importer.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import sys
import threading
from typing import Any, Dict, List, Optional

from ._betas import (
    LONG_CONTEXT_BETAS,
    add_excluded_beta,
    get_excluded_betas,
    get_model_betas,
    get_next_beta_to_exclude,
    is_long_context_error,
)
from ._model_config import CONFIG, get_model_override


_CLAUDE_CODE_IDENTITY = (
    "You are Claude Code, Anthropic's official CLI for Claude."
)
_BILLING_PREFIX = "x-anthropic-billing-header"
_BILLING_SALT = "59cf53e54c78"


_PATCHED: bool = False
_PATCH_LOCK = threading.Lock()


def _audit(message: str) -> None:
    line = f"[hermes-claude-auth] {message}\n"
    try:
        sys.stderr.write(line)
        sys.stderr.flush()
    except Exception:
        pass
    log_path = os.environ.get(
        "HERMES_CLAUDE_AUTH_LOG", os.path.expanduser("~/.hermes/hermes-claude-auth.log")
    )
    try:
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass


def _compute_oauth_beta_header(model: str) -> str:
    excluded = get_excluded_betas(model)
    betas = get_model_betas(model, excluded)
    return ",".join(betas)


def _extract_first_user_text(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    if isinstance(text, str) and text:
                        return text
        return ""
    return ""


def _compute_cch(message_text: str) -> str:
    return hashlib.sha256(message_text.encode("utf-8")).hexdigest()[:5]


def _compute_version_suffix(message_text: str, version: str) -> str:
    indices = (4, 7, 20)
    sampled = "".join(
        message_text[i] if i < len(message_text) else "0" for i in indices
    )
    payload = f"{_BILLING_SALT}{sampled}{version}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:3]


def _build_billing_header_value(
    messages: Any, version: str, entrypoint: str
) -> str:
    text = _extract_first_user_text(messages)
    suffix = _compute_version_suffix(text, version)
    cch = _compute_cch(text)
    return (
        f"{_BILLING_PREFIX}: "
        f"cc_version={version}.{suffix}; "
        f"cc_entrypoint={entrypoint}; "
        f"cch={cch};"
    )


def _system_entry_text(entry: Any) -> str:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        text = entry.get("text")
        if isinstance(text, str):
            return text
    return ""


def _repair_tool_pairs(messages: Any) -> Any:
    """Drop orphaned ``tool_use`` and ``tool_result`` blocks from message history.

    Python port of ``opencode-claude-auth/src/transforms.ts:32-87``
    (``repairToolPairs``). Anthropic returns HTTP 400 if a conversation
    contains a ``tool_use`` block with no matching ``tool_result`` (or
    vice versa) — which can happen mid-session if a tool call was
    interrupted, a session was resumed from a partial checkpoint, or
    Hermes' compression engine elided some content blocks.

    Identical semantics to the TS source:
      1. Collect every ``tool_use.id`` and every ``tool_result.tool_use_id``
      2. Compute orphan sets (uses with no matching result, and vice versa)
      3. Early-return the original list if both orphan sets are empty
      4. Otherwise: filter the orphan blocks out per-message, then drop
         any message whose ``content`` list ended up empty

    Pass-through (returns the input unchanged) for non-list inputs.
    """
    if not isinstance(messages, list):
        return messages

    tool_use_ids: set[str] = set()
    tool_result_ids: set[str] = set()
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_use":
                bid = block.get("id")
                if isinstance(bid, str):
                    tool_use_ids.add(bid)
            elif btype == "tool_result":
                tuid = block.get("tool_use_id")
                if isinstance(tuid, str):
                    tool_result_ids.add(tuid)

    orphaned_uses = tool_use_ids - tool_result_ids
    orphaned_results = tool_result_ids - tool_use_ids

    if not orphaned_uses and not orphaned_results:
        return messages

    repaired: List[Any] = []
    for msg in messages:
        if not isinstance(msg, dict):
            repaired.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            repaired.append(msg)
            continue
        filtered: List[Any] = []
        for block in content:
            if not isinstance(block, dict):
                filtered.append(block)
                continue
            btype = block.get("type")
            if btype == "tool_use":
                bid = block.get("id")
                if isinstance(bid, str) and bid in orphaned_uses:
                    continue
            elif btype == "tool_result":
                tuid = block.get("tool_use_id")
                if isinstance(tuid, str) and tuid in orphaned_results:
                    continue
            filtered.append(block)
        if not filtered:
            # Drop messages whose content list became empty (mirrors TS
            # final ``.filter(...)`` step at transforms.ts:83-86).
            continue
        new_msg = dict(msg)
        new_msg["content"] = filtered
        repaired.append(new_msg)
    return repaired


def _apply_oauth_transforms(kwargs: Dict[str, Any]) -> None:
    """Mirror opencode-claude-auth/src/transforms.ts on the already-built kwargs.

    Anthropic's OAuth path enforces a server-side system-prompt validator
    that returns "out of extra usage" when third-party (non-Claude-Code)
    system entries co-exist with the Claude Code identity prefix.  The
    same path also rejects lowercase tool names when many tools are
    present.  Hermes' bundled adapter does not do these mitigations, so
    we do them here.

    Operations (order matters):
      1. Inject an ``x-anthropic-billing-header`` system entry at position
         0 (deterministic Claude-Code signature; same algorithm as the
         OpenCode plugin).
      2. Move non-identity / non-billing system entries out of ``system[]``
         and prepend them as a text block on the first user message.
      3. PascalCase the first character after ``mcp_`` on every tool name
         (definitions AND ``tool_use`` blocks in message history), matching
         Claude Code's wire format.
      4. If the matched model override sets ``disable_effort``, drop
         ``output_config.effort`` and ``thinking.effort`` from the kwargs.
    """
    model = kwargs.get("model")
    cli_version = (
        os.environ.get("ANTHROPIC_CLI_VERSION", "").strip() or CONFIG.cc_version
    )
    entrypoint = os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").strip() or "sdk-cli"

    raw_system = kwargs.get("system")
    if isinstance(raw_system, str):
        system_list: List[Any] = (
            [{"type": "text", "text": raw_system}] if raw_system else []
        )
    elif isinstance(raw_system, list):
        system_list = list(raw_system)
    else:
        system_list = []

    system_list = [
        e
        for e in system_list
        if not (
            isinstance(e, dict)
            and isinstance(e.get("text"), str)
            and e["text"].startswith(_BILLING_PREFIX)
        )
    ]

    billing_value = _build_billing_header_value(
        kwargs.get("messages"), cli_version, entrypoint
    )
    system_list.insert(0, {"type": "text", "text": billing_value})

    kept: List[Any] = []
    moved_texts: List[str] = []
    for entry in system_list:
        text = _system_entry_text(entry)
        if text.startswith(_BILLING_PREFIX) or text.startswith(
            _CLAUDE_CODE_IDENTITY
        ):
            kept.append(entry)
        elif text:
            moved_texts.append(text)

    if moved_texts:
        messages = kwargs.get("messages")
        if isinstance(messages, list):
            for msg in messages:
                if isinstance(msg, dict) and msg.get("role") == "user":
                    prefix = "\n\n".join(moved_texts)
                    content = msg.get("content")
                    if isinstance(content, str):
                        msg["content"] = prefix + "\n\n" + content
                    elif isinstance(content, list):
                        msg["content"] = [
                            {"type": "text", "text": prefix},
                            *content,
                        ]
                    else:
                        msg["content"] = [{"type": "text", "text": prefix}]
                    break

    kwargs["system"] = kept

    def _pascal_after_mcp(name: str) -> str:
        if not isinstance(name, str) or not name.startswith("mcp_"):
            return name
        body = name[4:]
        if not body:
            return name
        return "mcp_" + body[0].upper() + body[1:]

    tools = kwargs.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if isinstance(tool, dict) and isinstance(tool.get("name"), str):
                tool["name"] = _pascal_after_mcp(tool["name"])

    messages = kwargs.get("messages")
    if isinstance(messages, list):
        for msg in messages:
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_use"
                    and isinstance(block.get("name"), str)
                ):
                    block["name"] = _pascal_after_mcp(block["name"])

    # Drop orphaned tool_use / tool_result blocks. Must run AFTER tool-name
    # PascalCasing (above) but BEFORE the Anthropic SDK serializes the
    # kwargs — otherwise multi-turn tool sessions hit 400s when prior turns
    # have unpaired blocks (e.g. from session resume, compression, or an
    # interrupted tool call). Mirrors opencode-claude-auth's
    # transforms.ts:254-256 placement.
    repaired_messages = _repair_tool_pairs(kwargs.get("messages"))
    if repaired_messages is not kwargs.get("messages"):
        kwargs["messages"] = repaired_messages

    if isinstance(model, str):
        override = get_model_override(model)
        if override is not None and override.disable_effort:
            output_config = kwargs.get("output_config")
            if isinstance(output_config, dict):
                output_config.pop("effort", None)
                if not output_config:
                    kwargs.pop("output_config", None)
            thinking = kwargs.get("thinking")
            if isinstance(thinking, dict):
                thinking.pop("effort", None)
                if not thinking:
                    kwargs.pop("thinking", None)


def _override_extra_headers(kwargs_out: Dict[str, Any], header_value: str) -> None:
    existing = kwargs_out.get("extra_headers")
    if isinstance(existing, dict):
        new_headers = dict(existing)
    else:
        new_headers = {}
    new_headers["anthropic-beta"] = header_value
    kwargs_out["extra_headers"] = new_headers


def _extract_body_text(exc: BaseException) -> str:
    parts = []
    msg = getattr(exc, "message", None)
    if isinstance(msg, str) and msg:
        parts.append(msg)
    body = getattr(exc, "body", None)
    if body is not None:
        if isinstance(body, (dict, list)):
            try:
                parts.append(json.dumps(body))
            except Exception:
                parts.append(repr(body))
        else:
            parts.append(str(body))
    response = getattr(exc, "response", None)
    if response is not None:
        text_method = getattr(response, "text", None)
        if callable(text_method):
            try:
                parts.append(str(text_method()))
            except Exception:
                pass
        elif isinstance(text_method, str):
            parts.append(text_method)
    parts.append(str(exc))
    return "\n".join(p for p in parts if p)


def _is_bad_request_400(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status == 400:
        return True
    response = getattr(exc, "response", None)
    if response is not None:
        response_status = getattr(response, "status_code", None)
        if isinstance(response_status, int) and response_status == 400:
            return True
    cls_name = type(exc).__name__
    return cls_name in {"BadRequestError"}


def _wrap_build_anthropic_kwargs(original):
    if getattr(original, "_hermes_claude_auth_wrapped", False):
        return original

    sig = inspect.signature(original)

    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)

        try:
            bound = sig.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            is_oauth_flag = bool(bound.arguments.get("is_oauth", False))
        except TypeError:
            is_oauth_flag = bool(kwargs.get("is_oauth", False))

        if not is_oauth_flag:
            return result

        if not isinstance(result, dict):
            return result

        model = result.get("model")
        if not isinstance(model, str) or not model:
            return result

        header_value = _compute_oauth_beta_header(model)
        if header_value:
            _override_extra_headers(result, header_value)
            if os.environ.get("HERMES_CLAUDE_AUTH_DEBUG"):
                _audit(
                    f"override anthropic-beta for model='{model}': {header_value}"
                )

        _apply_oauth_transforms(result)

        if os.environ.get("HERMES_CLAUDE_AUTH_DEBUG"):
            tool_count = (
                len(result.get("tools", []))
                if isinstance(result.get("tools"), list)
                else 0
            )
            sys_blocks = result.get("system")
            if isinstance(sys_blocks, list):
                sys_total = sum(
                    len(b.get("text", ""))
                    for b in sys_blocks
                    if isinstance(b, dict)
                )
                sys_count = len(sys_blocks)
            elif isinstance(sys_blocks, str):
                sys_total = len(sys_blocks)
                sys_count = 1
            else:
                sys_total = 0
                sys_count = 0
            _audit(
                f"post-transform kwargs: model={model} tools={tool_count} "
                f"system_entries={sys_count} system_chars={sys_total} "
                f"max_tokens={result.get('max_tokens')}"
            )
        return result

    wrapper._hermes_claude_auth_wrapped = True
    wrapper.__wrapped__ = original
    wrapper.__name__ = getattr(original, "__name__", "build_anthropic_kwargs")
    wrapper.__doc__ = getattr(original, "__doc__", None)
    return wrapper


def _try_retry_long_context(messages_obj, method_name: str, call_kwargs: Dict[str, Any]):
    model = call_kwargs.get("model")
    if not isinstance(model, str) or not model:
        return None

    next_beta = get_next_beta_to_exclude(model)
    if next_beta is None:
        return None

    add_excluded_beta(model, next_beta)
    _audit(
        f"excluded beta '{next_beta}' for model '{model}' on Extra Usage retry"
    )

    retry_kwargs = dict(call_kwargs)
    header_value = _compute_oauth_beta_header(model)
    if header_value:
        _override_extra_headers(retry_kwargs, header_value)

    original_method = getattr(messages_obj, "_hermes_claude_auth_original_" + method_name)
    return original_method(**retry_kwargs)


class _RetryingStreamManager:
    """Context-manager wrapper around ``client.messages.stream(**kwargs)``.

    The Anthropic SDK builds the HTTP request only on ``__enter__``, so we
    intercept that call: if it raises ``BadRequestError`` matching the
    long-context error class, we add the next ``LONG_CONTEXT_BETAS`` entry
    to the exclusion set, rebuild ``extra_headers``, and re-enter.  Up to
    ``len(LONG_CONTEXT_BETAS)`` attempts before giving up.
    """

    def __init__(self, original_stream, call_kwargs: Dict[str, Any]):
        self._original_stream = original_stream
        self._call_kwargs = call_kwargs
        self._manager = None

    def _attempt_enter(self, kwargs: Dict[str, Any]):
        manager = self._original_stream(**kwargs)
        entered = manager.__enter__()
        self._manager = manager
        return entered

    def __enter__(self):
        kwargs = dict(self._call_kwargs)
        try:
            return self._attempt_enter(kwargs)
        except Exception as exc:
            if not _is_bad_request_400(exc):
                raise
            body = _extract_body_text(exc)
            if os.environ.get("HERMES_CLAUDE_AUTH_DEBUG"):
                _audit(f"stream 400 body: {body[:400]}")
            if not is_long_context_error(body):
                raise
            last_exc = exc
            model = kwargs.get("model")
            if not isinstance(model, str) or not model:
                raise
            for _ in range(len(LONG_CONTEXT_BETAS)):
                next_beta = get_next_beta_to_exclude(model)
                if next_beta is None:
                    break
                add_excluded_beta(model, next_beta)
                _audit(
                    f"excluded beta '{next_beta}' for model '{model}' on Extra Usage retry"
                )
                header_value = _compute_oauth_beta_header(model)
                if header_value:
                    _override_extra_headers(kwargs, header_value)
                    if os.environ.get("HERMES_CLAUDE_AUTH_DEBUG"):
                        _audit(f"retry stream with anthropic-beta='{header_value}'")
                try:
                    return self._attempt_enter(kwargs)
                except Exception as retry_exc:
                    if not _is_bad_request_400(retry_exc):
                        raise
                    retry_body = _extract_body_text(retry_exc)
                    if not is_long_context_error(retry_body):
                        raise
                    last_exc = retry_exc
                    continue
            if os.environ.get("HERMES_CLAUDE_AUTH_DEBUG"):
                _audit(
                    f"stream retry exhausted for model='{model}'; final-error: {_extract_body_text(last_exc)[:300]}"
                )
            raise last_exc

    def __exit__(self, exc_type, exc, tb):
        if self._manager is None:
            return None
        return self._manager.__exit__(exc_type, exc, tb)


def _install_retry_on_messages_create(client: Any) -> None:
    messages_obj = getattr(client, "messages", None)
    if messages_obj is None:
        return
    if getattr(messages_obj, "_hermes_claude_auth_retry_installed", False):
        return

    original_create = getattr(messages_obj, "create", None)
    if callable(original_create):
        messages_obj._hermes_claude_auth_original_create = original_create

        def create_with_retry(**call_kwargs):
            try:
                return original_create(**call_kwargs)
            except Exception as exc:
                if not _is_bad_request_400(exc):
                    raise
                body = _extract_body_text(exc)
                if not is_long_context_error(body):
                    raise
                last_exc = exc
                for _ in range(len(LONG_CONTEXT_BETAS)):
                    try:
                        retry_result = _try_retry_long_context(
                            messages_obj, "create", call_kwargs
                        )
                    except Exception as retry_exc:
                        if not _is_bad_request_400(retry_exc):
                            raise
                        retry_body = _extract_body_text(retry_exc)
                        if not is_long_context_error(retry_body):
                            raise
                        last_exc = retry_exc
                        continue
                    if retry_result is None:
                        break
                    return retry_result
                raise last_exc

        create_with_retry._hermes_claude_auth_wrapped = True
        messages_obj.create = create_with_retry

    original_stream = getattr(messages_obj, "stream", None)
    if callable(original_stream):
        messages_obj._hermes_claude_auth_original_stream = original_stream

        def stream_with_retry(**call_kwargs):
            return _RetryingStreamManager(original_stream, call_kwargs)

        stream_with_retry._hermes_claude_auth_wrapped = True
        messages_obj.stream = stream_with_retry

    messages_obj._hermes_claude_auth_retry_installed = True


def _wrap_build_anthropic_client(original):
    if getattr(original, "_hermes_claude_auth_wrapped", False):
        return original

    def wrapper(api_key, *args, **kwargs):
        client = original(api_key, *args, **kwargs)
        try:
            from agent.anthropic_adapter import _is_oauth_token
        except Exception:
            return client
        try:
            if _is_oauth_token(api_key or ""):
                _install_retry_on_messages_create(client)
        except Exception:
            pass
        return client

    wrapper._hermes_claude_auth_wrapped = True
    wrapper.__wrapped__ = original
    wrapper.__name__ = getattr(original, "__name__", "build_anthropic_client")
    wrapper.__doc__ = getattr(original, "__doc__", None)
    return wrapper


def apply_patches() -> None:
    global _PATCHED
    with _PATCH_LOCK:
        if _PATCHED:
            return
        try:
            from agent import anthropic_adapter as _adapter
        except Exception as exc:
            _audit(f"could not import agent.anthropic_adapter: {exc}")
            return

        original_kwargs = getattr(_adapter, "build_anthropic_kwargs", None)
        if callable(original_kwargs):
            _adapter.build_anthropic_kwargs = _wrap_build_anthropic_kwargs(
                original_kwargs
            )

        original_client = getattr(_adapter, "build_anthropic_client", None)
        if callable(original_client):
            _adapter.build_anthropic_client = _wrap_build_anthropic_client(
                original_client
            )

        _PATCHED = True
        if os.environ.get("HERMES_CLAUDE_AUTH_DEBUG"):
            _audit(
                "patches applied (build_anthropic_kwargs, build_anthropic_client)"
            )


def is_applied() -> bool:
    return _PATCHED
