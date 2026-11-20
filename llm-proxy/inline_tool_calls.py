"""Tool calls a model wrote into its text instead of the ``tool_calls`` field.

DeepSeek's models sometimes answer a tool call in their own markup inside
``content`` (``<｜DSML｜tool_calls>``, ``<｜DSML｜invoke name="…">``,
``<｜DSML｜parameter name="…" string="true|false">…``) with no
``tool_calls``. The backend's tool loop reads only ``tool_calls``, so it took
that text for the final answer and delivered the first exploration step as
the task's work (sprint 3, tasks 12 and 16).

``normalize`` reads the markup back into the OpenAI shape when the request
offered tools: the calls go to ``tool_calls``, the prose before them stays
as ``content``, ``finish_reason`` becomes ``tool_calls`` and
``inline_tool_calls`` on the answer says it was translated. When the request
offered no tools the model asked for tools it does not have
(``exec_command``, ``Read``), and nothing can run them: ``normalize`` raises
``INLINE_TOOL_CALLS_WITHOUT_TOOLS`` and ``providers.call_provider`` asks once
more with ``NO_TOOLS_NOTE``.
"""

from __future__ import annotations

import json
import re

import upstream

INLINE_TOOL_CALLS_WITHOUT_TOOLS = "inline_tool_calls_without_tools"
NO_TOOLS_NOTE = (
    "This request offers no tools: do not write tool calls. Answer now with "
    "the complete result from what the request already gives you."
)

_BAR = r"[｜|]+"
_OPEN = rf"<{_BAR}DSML{_BAR}"
_CLOSE = rf"</{_BAR}DSML{_BAR}"
_MARKER = re.compile(rf"{_OPEN}(?:tool_calls|invoke)\b")
_BLOCK = re.compile(rf"{_OPEN}tool_calls>(.*?)(?:{_CLOSE}tool_calls>|$)", re.S)
_INVOKE = re.compile(rf'{_OPEN}invoke\s+name="([^"]+)"\s*>(.*?)(?:{_CLOSE}invoke>|$)', re.S)
_PARAM = re.compile(
    rf'{_OPEN}parameter\s+name="([^"]+)"(?:\s+string="(true|false)")?\s*>(.*?){_CLOSE}parameter>',
    re.S,
)


def has_markup(text: object) -> bool:
    return isinstance(text, str) and bool(_MARKER.search(text))


def _value(raw: str, is_string: str | None):
    if is_string == "true":
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def parse(text: str) -> tuple[str, list[dict]]:
    """``(prose, calls)``: the text without the markup, and each call in OpenAI shape."""
    calls: list[dict] = []
    for block in _BLOCK.findall(text):
        for name, body in _INVOKE.findall(block):
            arguments = {key: _value(raw, flag or None) for key, flag, raw in _PARAM.findall(body)}
            calls.append(
                {
                    "id": f"call_inline_{len(calls) + 1}",
                    "type": "function",
                    "function": {"name": name.strip(), "arguments": json.dumps(arguments)},
                }
            )
    prose = _BLOCK.sub("", text)
    prose = _MARKER.split(prose)[0].strip()
    return prose, calls


def normalize(provider: str, model: str, status: int, result: dict, request: dict) -> None:
    """Put tool calls written as text where the tool loop reads them, in place."""
    choices = result.get("choices") or []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    text = message.get("content")
    if message.get("tool_calls") or not has_markup(text):
        return
    prose, calls = parse(text)
    if not request.get("tools"):
        raise upstream.UpstreamError(
            provider,
            model,
            status,
            f"{INLINE_TOOL_CALLS_WITHOUT_TOOLS}: the answer writes {len(calls)} tool call(s) "
            "as text and the request offered no tools",
            retriable=True,
            reason=INLINE_TOOL_CALLS_WITHOUT_TOOLS,
        )
    if not calls:
        return
    message["tool_calls"] = calls
    message["content"] = prose or None
    choice["finish_reason"] = "tool_calls"
    result["inline_tool_calls"] = {"format": "dsml", "calls": len(calls)}
