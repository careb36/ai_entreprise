"""What a regression looks like in Slack (Block Kit) and Teams (an Adaptive Card).

Pure functions over the facts ``chat_regressions`` reads from the record: a
headline, the task or sprint's title, labelled facts, the checks that did
not pass and a link to the evidence. Information only: no button, no action
that changes state (Teams gets ``Action.OpenUrl`` links, as the approval
card does). Text from the record is data: escaped for Slack's ``mrkdwn`` and
capped; Teams gets it in plain ``TextBlock``s and ``FactSet``s.
"""

from __future__ import annotations

from typing import Any

from services.integrations.chat_messages import slack_escape

MAX_LINES = 6


def _cap(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def fallback_text(facts: dict[str, Any]) -> str:
    """The notification's plain text (Slack's ``text``)."""
    return _cap(f"{facts['headline']}: {facts['title']}", 300)


def slack_blocks(facts: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        f"*{slack_escape(label, 40)}*: {slack_escape(value, 200)}"
        for label, value in facts.get("facts") or []
    ]
    lines = [f"• {slack_escape(line, 120)}" for line in (facts.get("lines") or [])]
    blocks: list[dict[str, Any]] = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{slack_escape(facts['headline'], 120)}*\n"
                    f"{slack_escape(facts['title'], 150)}"
                ),
            },
        }
    ]
    if rows:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(rows)}}
        )
    if lines:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "*Checks not passed*\n" + "\n".join(lines[:MAX_LINES]),
                },
            }
        )
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"<{facts['evidence_url']}|"
                        f"{slack_escape(facts['evidence_label'], 80)}> · "
                        "information only: nothing to decide here"
                    ),
                }
            ],
        }
    )
    return blocks


def teams_card(facts: dict[str, Any]) -> dict[str, Any]:
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": _cap(facts["headline"], 120),
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
        },
        {"type": "TextBlock", "text": _cap(facts["title"], 150), "wrap": True},
    ]
    pairs = [
        {"title": _cap(label, 40), "value": _cap(value, 200)}
        for label, value in facts.get("facts") or []
    ]
    lines = list(facts.get("lines") or [])[:MAX_LINES]
    if lines:
        pairs.append(
            {"title": "Checks not passed", "value": _cap("; ".join(lines), 300)}
        )
    if pairs:
        body.append({"type": "FactSet", "facts": pairs})
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "type": "AdaptiveCard",
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "version": "1.4",
                    "body": body,
                    "actions": [
                        {
                            "type": "Action.OpenUrl",
                            "title": _cap(facts["evidence_label"], 60),
                            "url": facts["evidence_url"],
                        }
                    ],
                },
            }
        ],
    }
