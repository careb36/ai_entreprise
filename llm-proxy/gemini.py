"""Gemini's request and answer, translated to and from the OpenAI shape.

The proxy speaks OpenAI's chat format to every provider; Gemini has its own.
This module builds its ``generateContent`` body (system instruction, contents,
``generationConfig`` with the caller's ``max_tokens`` as ``maxOutputTokens``,
the caller's ``tools`` as ``functionDeclarations``) and reads its answer back
into ``choices`` with the same ``finish_reason`` the OpenAI-compatible
providers report, so a caller can tell an answer cut at the limit
(``length``) from a complete one, and the same ``tool_calls`` shape, so the
backend's tool loop (``services/mcp_tools.py``) reads every provider alike.
"""

import json
from collections.abc import Callable

from upstream import UpstreamError
from usage import reported_count, with_usage

# Gemini's finishReason, as the OpenAI-compatible providers spell it.
FINISH_REASONS = {"MAX_TOKENS": "length", "STOP": "stop"}

# Gemini (2.5 and later, with thinking) puts a ``thoughtSignature`` on the
# part that carries a functionCall, and refuses a later request whose history
# replays that call without it. The signature travels with the call itself:
# it comes back inside the OpenAI tool_call as
# ``extra_content.google.thought_signature`` (the field Google's own
# OpenAI-compatible endpoint uses), the backend sends its history back as it
# received it, and the part gets it again here. No state in the proxy, so it
# survives restarts and replicas and never mixes two conversations. A call
# replayed with no signature at all (one another provider made, before a
# fallback) gets the validator-skip value Google documents for calls the
# model did not produce: https://ai.google.dev/gemini-api/docs/thought-signatures
SIGNATURE_SKIP = "skip_thought_signature_validator"


def _tool_declarations(tools: list | None) -> list[dict]:
    declarations = []
    for tool in tools or []:
        function = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(function, dict) or not function.get("name"):
            continue
        declaration = {"name": function["name"], "description": function.get("description") or ""}
        if isinstance(function.get("parameters"), dict):
            declaration["parameters"] = _without_schema_extras(function["parameters"])
        declarations.append(declaration)
    return declarations


def _without_schema_extras(schema):
    """Gemini's schema subset: no ``title``, ``$schema`` or ``additionalProperties``."""
    if isinstance(schema, dict):
        return {
            key: _without_schema_extras(value)
            for key, value in schema.items()
            if key not in {"title", "$schema", "additionalProperties", "default"}
        }
    if isinstance(schema, list):
        return [_without_schema_extras(item) for item in schema]
    return schema


def signature_of(tool_call: dict) -> str | None:
    """The thought signature an OpenAI tool_call carries back, if any."""
    extra = tool_call.get("extra_content")
    google = extra.get("google") if isinstance(extra, dict) else None
    signature = google.get("thought_signature") if isinstance(google, dict) else None
    if isinstance(signature, str) and signature:
        return signature
    # A call id from #411's format (``call_1::ts::<signature>``), for histories
    # written before the signature moved to ``extra_content``.
    cid = str(tool_call.get("id") or "")
    return cid.split("::ts::", 1)[1] or None if "::ts::" in cid else None


def _contents(messages: list) -> list[dict]:
    """OpenAI messages as Gemini contents; tool answers become functionResponse parts."""
    names_by_call_id: dict[str, str] = {}
    contents = []
    for m in messages:
        if m["role"] == "system":
            continue
        if m["role"] == "tool":
            name = names_by_call_id.get(str(m.get("tool_call_id")), "tool")
            contents.append(
                {
                    "role": "user",
                    "parts": [{"functionResponse": {"name": name, "response": {"content": m.get("content") or ""}}}],
                }
            )
            continue
        parts = []
        if m.get("content"):
            parts.append({"text": m["content"]})
        for call in m.get("tool_calls") or []:
            function = call.get("function") or {}
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments or "{}")
                except ValueError:
                    arguments = {"_raw": arguments}
            names_by_call_id[str(call.get("id"))] = function.get("name") or "tool"
            part = {"functionCall": {"name": function.get("name") or "tool", "args": arguments or {}}}
            part["thoughtSignature"] = signature_of(call) or SIGNATURE_SKIP
            parts.append(part)
        if parts:
            contents.append({"role": "model" if m["role"] == "assistant" else "user", "parts": parts})
    return contents


def _message_from(parts: list) -> dict:
    """The answer's text and function calls in OpenAI's message shape."""
    text = "".join(p.get("text") or "" for p in parts if isinstance(p, dict))
    tool_calls = []
    for part in parts:
        call = part.get("functionCall") if isinstance(part, dict) else None
        if not isinstance(call, dict) or not call.get("name"):
            continue
        tool_call = {
            "id": f"call_{len(tool_calls) + 1}",
            "type": "function",
            "function": {"name": call["name"], "arguments": json.dumps(call.get("args") or {})},
        }
        signature = part.get("thoughtSignature")
        if isinstance(signature, str) and signature:
            tool_call["extra_content"] = {"google": {"thought_signature": signature}}
        tool_calls.append(tool_call)
    message: dict = {"role": "assistant", "content": text or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def call_gemini(
    model: str,
    data: dict,
    timeout: float,
    *,
    post: Callable[..., tuple[int, dict]],
    base_url: str,
    api_key: str,
) -> tuple[dict, int]:
    """One ``generateContent`` call through *post* (the proxy's upstream POST)."""
    messages = data.get("messages", [])
    system = next((m["content"] for m in messages if m["role"] == "system"), None)
    generation: dict = {"temperature": data.get("temperature", 0.7)}
    if data.get("max_tokens"):
        generation["maxOutputTokens"] = int(data["max_tokens"])
    payload: dict = {"contents": _contents(messages), "generationConfig": generation}
    if system:
        payload["system_instruction"] = {"parts": [{"text": system}]}
    declarations = _tool_declarations(data.get("tools"))
    if declarations:
        payload["tools"] = [{"functionDeclarations": declarations}]
    status, res = post(
        "gemini", model, f"{base_url}/models/{model}:generateContent", {"x-goog-api-key": api_key}, payload, timeout
    )
    try:
        candidate = res["candidates"][0]
        parts = candidate["content"]["parts"]
        message = _message_from(parts)
        if message["content"] is None and not message.get("tool_calls"):
            raise KeyError("text")
    except (KeyError, IndexError, TypeError):
        reason = (res.get("promptFeedback") or {}).get("blockReason") or "no candidates"
        raise UpstreamError("gemini", model, status, f"response had no text ({reason})", retriable=False)
    finish = FINISH_REASONS.get(str(candidate.get("finishReason") or "").upper(), "other")
    if message.get("tool_calls"):
        finish = "tool_calls"
    result = {"choices": [{"message": message, "finish_reason": finish}], "model": model}
    meta = res.get("usageMetadata") or {}
    reported = reported_count(meta.get("totalTokenCount"))
    # The split prices a turn (backend ``services/run_cost.py``); thinking is billed as output.
    prompt, candidates = reported_count(meta.get("promptTokenCount")), reported_count(meta.get("candidatesTokenCount"))
    if prompt is not None and candidates is not None:
        result["usage"] = {"prompt_tokens": prompt, "completion_tokens": candidates + (reported_count(meta.get("thoughtsTokenCount")) or 0)}
    return with_usage(result, reported, messages, message["content"] or "")
