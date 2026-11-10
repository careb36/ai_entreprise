"""The PO drafts a specification from a person's brief.

The brief is a few sentences; the PO turns it into numbered requirements
with *given / when / then* criteria, quality attributes with a number and
constraints, in a fixed JSON shape a parser checks, and lists the questions
it could not decide. The draft is a claim: ``spec_check`` numbers it and
says what blocks its approval, a person answers the questions and edits,
and only a person approves. A revision is asked with the questions already
answered, so the PO writes the next draft from them.

No usable answer leaves the specification as it was.
"""

from __future__ import annotations

import json
import os
from typing import Any

from llm_proxy_client import LLMProxyClient
from logging_config import logger
from models import Agent, BusinessUnit
from models.specification import Specification
from sqlmodel import Session, select

from services.evidence import standards
from services.planning import spec_check
from services.planning.sprint_planner import _build_stack_summary
from services.tools import mcp_tools, research_tools

PO_ROLE = "PO"
DRAFT_MAX_TOKENS = 6000
DRAFT_TIMEOUT_SECONDS = 180.0
BRIEF_LIMIT = 6000


class SpecAuthorError(Exception):
    """The PO gave no usable draft (a failed call, or an answer off the format)."""


def _strip_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
    if cleaned.endswith("```"):
        cleaned = cleaned.rsplit("```", 1)[0]
    return cleaned.strip()


def _stack(value: Any) -> dict[str, Any] | None:
    """The detected stack as a dict; ``ProjectDiscovery`` stores it as JSON text."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    return value if isinstance(value, dict) and value else None


def business_unit_id(session: Session, company_id: int) -> int | None:
    """The company's business unit the proxy bills, or ``None`` when it has none."""
    unit = session.exec(
        select(BusinessUnit).where(BusinessUnit.company_id == company_id)
    ).first()
    return unit.id if unit else None


def po_profile(session: Session, company_id: int) -> tuple[str, str | None, str]:
    """The company's PO agent: its model (or the company's, or the role's default), system prompt and name."""
    from services.models import company_models

    po = session.exec(
        select(Agent).where(Agent.company_id == company_id, Agent.role == PO_ROLE)
    ).first()
    configured = (po.model if po else "") or ""
    return (
        company_models.resolve(session, company_id, PO_ROLE, configured),
        (po.system_prompt if po else None) or None,
        (po.name if po else "") or PO_ROLE,
    )


def ask(
    model: str,
    system_prompt: str | None,
    prompt: str,
    *,
    max_tokens: int = DRAFT_MAX_TOKENS,
    timeout: float = DRAFT_TIMEOUT_SECONDS,
    role: str = PO_ROLE,
    bu_id: int | None = None,
    company_id: int | None = None,
) -> str:
    """One question to an agent (the PO by default) through the proxy; no database involved.

    *bu_id* is the company's business unit: the proxy enforces the budget
    and accounts the usage by it.
    """
    messages = [{"role": "user", "content": prompt}]
    if system_prompt:
        messages.insert(0, {"role": "system", "content": system_prompt})
    client = _client(timeout, max_tokens, company_id)
    result = client.chat(messages=messages, model=model, role=role, bu_id=bu_id)
    return _content(result)


def _client(
    timeout: float, max_tokens: int, company_id: int | None = None
) -> LLMProxyClient:
    client = LLMProxyClient(
        base_url=os.getenv("LLM_PROXY_URL", "http://llm-proxy:5100")
    )
    client.timeout, client.max_tokens, client.max_retries = timeout, max_tokens, 0
    client.company_id = company_id
    return client


def _content(result: Any) -> str:
    if not isinstance(result, dict) or result.get("error"):
        raise SpecAuthorError(f"LLM call failed: {result!r}"[:500])
    choices = result.get("choices") or [{}]
    content = ((choices[0] or {}).get("message") or {}).get("content") or ""
    if not str(content).strip():
        raise SpecAuthorError("LLM call failed: empty response")
    return str(content).strip()


def ask_with_tools(
    model: str,
    system_prompt: str | None,
    prompt: str,
    *,
    max_tokens: int = DRAFT_MAX_TOKENS,
    timeout: float = DRAFT_TIMEOUT_SECONDS,
    role: str = PO_ROLE,
    bu_id: int | None = None,
    toolbox: Any = None,
    company_id: int | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """``ask`` with a toolbox (``mcp_tools.chat_with_tools``): the answer and
    the observations of every tool call the agent made on the way. Without a
    toolbox it is ``ask`` itself (the one place a test replaces)."""
    if toolbox is None:
        plain = ask(
            model,
            system_prompt,
            prompt,
            max_tokens=max_tokens,
            timeout=timeout,
            role=role,
            bu_id=bu_id,
            company_id=company_id,
        )
        return plain, []
    messages = [{"role": "user", "content": prompt}]
    if system_prompt:
        messages.insert(0, {"role": "system", "content": system_prompt})
    try:
        result, calls = mcp_tools.chat_with_tools(
            _client(timeout, max_tokens, company_id),
            messages,
            model,
            role,
            toolbox,
            bu_id=bu_id,
        )
    finally:
        toolbox.close()
    return _content(result), calls


DRAFT_FORMAT = """\
Answer with one JSON object and nothing else, in this exact shape:

{
  "title": "a short name for what is being built",
  "summary": "two to four sentences: what it is, for whom, and why",
  "requirements": [
    {
      "title": "one capability, as a short sentence",
      "description": "what is expected and why; be concrete, name the data and the actors",
      "criteria": [
        {"given": "the starting state", "when": "the action", "then": "the observable result"}
      ]
    }
  ],
  "quality": [
    {"attribute": "performance | availability | security | accessibility | ...", "target": "a number and its unit, e.g. p95 under 300 ms"}
  ],
  "constraints": ["a rule the implementation must keep, e.g. the language, a library, a platform"],
  "questions": ["what you could not decide from the brief; one question each, specific"]
}

Rules:
- Do not number anything; the ids are assigned afterwards.
- Every requirement needs at least one criterion, and every criterion an observable "then" a test can check.
- Every quality target carries a number. Never write "fast", "easy", "robust" or "etc." without one.
- Ask a question instead of guessing; do not invent features the brief does not imply.
- Keep the requirements that the brief states or clearly implies; 3 to 25 of them.
"""


def draft_prompt(
    po_name: str,
    brief: str,
    detected_stack: dict[str, Any] | None,
    existing: dict[str, Any] | None,
) -> str:
    """The question to the PO: a first draft, or a revision from the answers."""
    lines = [
        (
            f"You are {po_name}, the PO of an AI software factory. Write the specification "
            "the team will build from: the people and agents that implement it will read "
            "nothing else about the product, and every criterion you write becomes a test."
        ),
        "",
    ]
    if brief.strip():
        lines += ["## Brief from the person asking", brief.strip()[:BRIEF_LIMIT], ""]
    if stack := _stack(detected_stack):
        lines += ["## Detected stack of the project", _build_stack_summary(stack), ""]
    if existing:
        answered = [q for q in existing.get("questions") or [] if q.get("answer")]
        lines += [
            "## The current draft",
            (
                "Revise it: keep what the answers confirm, change what they correct, and "
                "ask again only what is still open."
            ),
            "",
            json.dumps(
                {
                    k: existing.get(k)
                    for k in (
                        "title",
                        "summary",
                        "requirements",
                        "quality",
                        "constraints",
                    )
                },
                ensure_ascii=False,
            )[:12000],
            "",
        ]
        if answered:
            lines += ["## Answers to your questions", ""]
            lines += [f"- Q: {q['question']}\n  A: {q['answer']}" for q in answered]
            lines.append("")
    lines.append(DRAFT_FORMAT)
    return "\n".join(lines)


def parse_draft(text: str) -> dict[str, Any] | None:
    """The draft in the fixed shape, normalized; ``None`` when unusable."""
    try:
        parsed = json.loads(_strip_fences(text))
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    spec = spec_check.normalize(parsed)
    if not spec["title"] or not spec["requirements"]:
        return None
    return spec


def merge_questions(
    existing: list[dict[str, Any]], proposed: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The answered questions stay; the PO's new ones follow, without repeats."""
    kept = [q for q in existing if q.get("answer")]
    seen = {q["question"].casefold() for q in kept}
    for q in proposed:
        key = q["question"].casefold()
        if key not in seen:
            seen.add(key)
            kept.append({"question": q["question"], "answer": None})
    return kept[: spec_check.MAX_QUESTIONS]


def prepare(
    session: Session,
    company_id: int,
    brief: str,
    detected_stack: dict[str, Any] | None = None,
    existing: Specification | None = None,
) -> dict[str, Any]:
    """Everything ``draft_from`` needs, read with *session*: plain values, so
    the call to the proxy can run in another thread without the session."""
    model, system_prompt, po_name = po_profile(session, company_id)
    current = (
        {
            "title": existing.title,
            "summary": existing.summary,
            "requirements": existing.requirements,
            "quality": existing.quality,
            "constraints": existing.constraints,
            "questions": existing.questions,
        }
        if existing is not None
        else None
    )
    return {
        "company_id": company_id,
        "model": model,
        "system_prompt": system_prompt,
        "prompt": draft_prompt(po_name, brief, detected_stack, current)
        + standards.prompt_section(PO_ROLE)
        + research_tools.prompt_section(PO_ROLE),
        "bu_id": business_unit_id(session, company_id),
        "questions": (existing.questions if existing is not None else []) or [],
        "research_hosts": research_tools.company_hosts(session, company_id),
    }


def draft_from(request: dict[str, Any]) -> dict[str, Any]:
    """The PO's draft for a ``prepare``d request, normalized; no database.

    Raises ``SpecAuthorError`` when nothing usable came back.
    """
    logger.info(
        "PO drafting a specification for company=%s model=%s",
        request["company_id"],
        request["model"],
    )
    answer, calls = ask_with_tools(
        request["model"],
        request["system_prompt"],
        request["prompt"],
        bu_id=request["bu_id"],
        toolbox=research_tools.toolbox_for_request(PO_ROLE, request),
        company_id=request["company_id"],
    )
    spec = parse_draft(answer)
    if spec is None:
        raise SpecAuthorError(
            "The PO's answer is not a specification in the expected format"
        )
    spec["questions"] = merge_questions(request["questions"], spec["questions"])
    # The sources consulted on the way: observations, kept with the draft.
    spec["sources"] = research_tools.sources_of(calls)
    return spec


def draft(
    session: Session,
    company_id: int,
    brief: str,
    detected_stack: dict[str, Any] | None = None,
    existing: Specification | None = None,
) -> dict[str, Any]:
    """``prepare`` then ``draft_from``, in one thread."""
    return draft_from(prepare(session, company_id, brief, detected_stack, existing))
