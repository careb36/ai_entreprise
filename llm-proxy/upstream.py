"""
Talking to a provider, and the error that comes back when it cannot answer.

Every provider call funnels through :func:`post_upstream`, so one place decides
what counts as a failure and whether the caller may try another provider. A
timeout is not retried elsewhere: the request's time budget is already spent.
"""

import requests


class UpstreamError(Exception):
    """A provider call that produced no usable answer."""

    def __init__(
        self,
        provider: str,
        model: str,
        status: int | None,
        message: str,
        retriable: bool | None = None,
    ):
        super().__init__(f"{provider} {status} {message}")
        self.provider = provider
        self.model = model
        self.status = status
        self.message = message
        self.attempts: list[dict] = []
        # 429 and 5xx may succeed elsewhere; so may a refused connection.
        if retriable is None:
            retriable = status is None or status == 429 or status >= 500
        self.retriable = retriable

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "message": self.message,
        }


def upstream_message(resp) -> str:
    """The provider's own error text, or the start of the body."""
    try:
        body = resp.json()
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
    text_body = (getattr(resp, "text", "") or "").strip()
    return text_body[:300] or f"HTTP {resp.status_code}"


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


def post_upstream(
    provider: str, model: str, url: str, headers: dict, payload: dict, timeout: float
) -> tuple[int, dict]:
    """POST to a provider; any non-2xx or unreadable body becomes an ``UpstreamError``."""
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.Timeout:
        raise UpstreamError(
            provider, model, None, f"timed out after {timeout:.0f}s", retriable=False
        )
    except requests.RequestException as exc:
        raise UpstreamError(provider, model, None, f"request failed: {exc}")
    if not 200 <= resp.status_code < 300:
        message = upstream_message(resp)
        # A provider without credit may still be answered by another one.
        retriable = True if credit_refusal(resp.status_code, message) else None
        raise UpstreamError(provider, model, resp.status_code, message, retriable=retriable)
    try:
        body = resp.json()
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
        message = upstream_message(resp)
        retriable = credit_refusal(resp.status_code, message)
        raise UpstreamError(
            provider, model, resp.status_code, message, retriable=retriable
        )
    return resp.status_code, body


def check_openai_answer(provider: str, model: str, status: int, result: dict) -> None:
    """Refuse an OpenAI-shaped 2xx body that carries no usable answer.

    No choices is an error no other provider would fix. An empty answer (no
    text and no tool call) is one a provider sometimes returns (DeepSeek does)
    and another may not, so it is retriable and the fallback runs. A tool call
    with no text is a real answer, and so is an empty one cut at the length
    limit (``finish_reason: length``): the caller reads that as truncated and
    asks again with room, which another provider would not change.
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
    if choice.get("finish_reason") == "length":
        return
    raise UpstreamError(provider, model, status, "empty response", retriable=True)
