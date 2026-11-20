"""
Talking to a provider, and the error that comes back when it cannot answer.

Every provider call funnels through :func:`post_upstream`, so one place decides
what counts as a failure and whether the caller may try another provider. A
timeout is not retried elsewhere: the request's time budget is already spent.

Some failures carry a named ``reason`` the caller can act on, and the
message starts with it: ``upstream_timeout`` (no complete answer within the
budget, keep-alive lines included), ``empty_content`` (a 2xx answer with no
text and no tool call) and ``empty_content_reasoning_exhausted`` (the same,
cut at ``finish_reason: length`` after the model's reasoning, DeepSeek's
``reasoning_content``, used the whole ``max_tokens``). Reasoning text is
never passed off as the answer.
"""

import json
import time

import requests
from timeouts import CONNECT_TIMEOUT_SECONDS
from usage import openai_reported_count

UPSTREAM_TIMEOUT = "upstream_timeout"
EMPTY_CONTENT = "empty_content"
EMPTY_CONTENT_REASONING_EXHAUSTED = "empty_content_reasoning_exhausted"


class UpstreamError(Exception):
    """A provider call that produced no usable answer."""

    def __init__(
        self,
        provider: str,
        model: str,
        status: int | None,
        message: str,
        retriable: bool | None = None,
        reason: str | None = None,
    ):
        super().__init__(f"{provider} {status} {message}")
        self.provider = provider
        self.model = model
        self.status = status
        self.message = message
        self.reason = reason
        self.attempts: list[dict] = []
        # Tokens the provider reported for this failed call (an empty answer
        # still costs them); charged to the quota, never shown as usage.
        self.tokens = 0
        # 429 and 5xx may succeed elsewhere; so may a refused connection.
        if retriable is None:
            retriable = status is None or status == 429 or status >= 500
        self.retriable = retriable

    def as_dict(self) -> dict:
        record = {
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "message": self.message,
        }
        if self.reason:
            record["reason"] = self.reason
        if self.tokens:
            record["tokens"] = self.tokens
        return record


def failed_tokens(source) -> int:
    """Tokens the failed calls behind *source* reported: an ``UpstreamError``
    (every attempt it carries) or the ``fallback_from`` record of a served
    answer. Charged to the quota apart from the served answer's usage."""
    if isinstance(source, UpstreamError):
        attempts = source.attempts or [source.as_dict()]
        return sum(int(a.get("tokens") or 0) for a in attempts)
    if isinstance(source, dict):
        return int(source.get("failed_attempts_tokens") or 0)
    return 0


def fallback_record(primary: "UpstreamError") -> dict:
    """What a served answer says about the failure it fell back from."""
    record = primary.as_dict()
    spent = failed_tokens(primary)
    if spent:
        record["failed_attempts_tokens"] = spent
    return record


def error_body(exc: "UpstreamError", requested: str) -> dict:
    """The proxy's answer when no provider could answer."""
    body = {"error": "upstream_error", **exc.as_dict(), "requested_model": requested}
    if len(exc.attempts) > 1:
        body["attempts"] = exc.attempts
    return body


def upstream_message(status_code: int, text: str) -> str:
    """The provider's own error text, or the start of the body."""
    try:
        body = json.loads(text)
    except ValueError:
        body = None
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str) and err:
            return err
        if body.get("message"):
            return str(body["message"])
    text_body = (text or "").strip()
    return text_body[:300] or f"HTTP {status_code}"


# What a provider says when the account, not the request, is the problem.
# Whole phrases: "insufficient permissions" or a "load balancer" error is not
# a lack of credit, and neither is a plain 401 or 403 (a key or an access).
CREDIT_PHRASES = (
    "insufficient balance",
    "insufficient credit",
    "insufficient funds",
    "insufficient_quota",
    "insufficient quota",
    "credit balance",
    "out of credit",
    "no credit",
    "credits exhausted",
    "account balance",
    "balance is not enough",
    "arrearage",
    "payment required",
    "billing",
)


def credit_refusal(status: int | None, message: str | None) -> bool:
    """Whether the provider refused for lack of credit (402, or a message saying so)."""
    if status == 402:
        return True
    low = (message or "").lower()
    return any(phrase in low for phrase in CREDIT_PHRASES)


class _Overdue(Exception):
    """The call's deadline passed while its body was still arriving."""


def _timed_out(provider: str, model: str, timeout: float) -> UpstreamError:
    return UpstreamError(
        provider,
        model,
        None,
        f"{UPSTREAM_TIMEOUT}: no complete answer within {timeout:.0f}s",
        retriable=False,
        reason=UPSTREAM_TIMEOUT,
    )


def _read_within(resp, deadline: float) -> str:
    """The response body as text, read chunk by chunk until *deadline*."""
    chunks: list[bytes] = []
    try:
        # ``None``: each chunk as it arrives (a keep-alive line included), so
        # the deadline is checked between them.
        for chunk in resp.iter_content(chunk_size=None):
            if chunk:
                chunks.append(chunk)
            if time.monotonic() >= deadline:
                raise _Overdue()
    finally:
        resp.close()
    return b"".join(chunks).decode("utf-8", errors="replace")


def post_upstream(
    provider: str, model: str, url: str, headers: dict, payload: dict, timeout: float
) -> tuple[int, dict]:
    """POST to a provider; any non-2xx or unreadable body becomes an ``UpstreamError``.

    *timeout* is the call's whole budget, not a socket's: the body is read as
    it arrives and the call stops at the deadline even while the provider
    keeps the connection alive with empty lines (DeepSeek does,
    https://api-docs.deepseek.com/quick_start/rate_limit), where a plain read
    timeout would never fire.
    """
    deadline = time.monotonic() + timeout
    try:
        resp = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=(min(CONNECT_TIMEOUT_SECONDS, timeout), timeout),
            stream=True,
        )
        text = _read_within(resp, deadline)
    except _Overdue:
        raise _timed_out(provider, model, timeout) from None
    except requests.Timeout:
        raise _timed_out(provider, model, timeout) from None
    except requests.RequestException as exc:
        if time.monotonic() >= deadline:
            raise _timed_out(provider, model, timeout) from None
        raise UpstreamError(provider, model, None, f"request failed: {exc}") from None
    if not 200 <= resp.status_code < 300:
        message = upstream_message(resp.status_code, text)
        # A provider without credit may still be answered by another one.
        retriable = True if credit_refusal(resp.status_code, message) else None
        raise UpstreamError(provider, model, resp.status_code, message, retriable=retriable)
    try:
        body = json.loads(text)
    except ValueError:
        raise UpstreamError(
            provider, model, resp.status_code, "response body is not JSON", retriable=False
        )
    if not isinstance(body, dict):
        raise UpstreamError(
            provider,
            model,
            resp.status_code,
            "response body is not a JSON object",
            retriable=False,
        )
    if body.get("error"):
        message = upstream_message(resp.status_code, text)
        retriable = credit_refusal(resp.status_code, message)
        raise UpstreamError(
            provider, model, resp.status_code, message, retriable=retriable
        )
    return resp.status_code, body


def check_openai_answer(provider: str, model: str, status: int, result: dict) -> None:
    """Refuse an OpenAI-shaped 2xx body that carries no usable answer.

    No choices is an error no other provider would fix. A tool call with no
    text is a real answer. An answer with neither text nor a tool call is
    never a success, whatever the status: it is
    ``empty_content_reasoning_exhausted`` when it stopped at the length limit
    after reasoning (DeepSeek's ``reasoning_content``) used the budget, and
    ``empty_content`` otherwise (blank text, JSON mode's spaces, a cut with no
    reasoning). Both are retriable: another provider, or the same one with
    thinking off (``providers.py``), may answer. The reasoning is never
    returned as the answer.
    """
    choices = result.get("choices")
    if not choices:
        raise UpstreamError(
            provider, model, status, "response contained no choices", retriable=False
        )
    choice = choices[0] if isinstance(choices[0], dict) else {}
    message = choice.get("message")
    message = message if isinstance(message, dict) else {}
    text = message.get("content")
    if (isinstance(text, str) and text.strip()) or message.get("tool_calls"):
        return
    finish = choice.get("finish_reason") or "none"
    reasoning = message.get("reasoning_content")
    reasoned = len(reasoning) if isinstance(reasoning, str) and reasoning.strip() else 0
    if finish == "length" and reasoned:
        reason = EMPTY_CONTENT_REASONING_EXHAUSTED
        detail = (
            f"finish_reason=length; the reasoning ({reasoned} characters of "
            "reasoning_content) used the whole max_tokens and the answer is empty"
        )
    else:
        reason = EMPTY_CONTENT
        detail = f"finish_reason={finish}; the answer has no text and no tool call"
        if reasoned:
            detail += f" (reasoning_content: {reasoned} characters)"
    empty = UpstreamError(provider, model, status, f"{reason}: {detail}", retriable=True, reason=reason)
    empty.tokens = openai_reported_count(result) or 0
    raise empty
