"""What an approval looks like in Slack (Block Kit) and Teams (an Adaptive Card).

Pure functions over the approval point ``chat_approvals.current_point``
reads: the task, the Verifier's verdict and the checks that did not pass as
recorded, the risk hold's note or the block's reason (the inbox's text) and
links to the evidence. A risk hold offers Approve and Send back; a triage
block offers Send back only, as the board does. Nothing an agent
claimed about its own work is shown. Text from the record (a title, a check's
name) is data: escaped for Slack's ``mrkdwn`` and capped; Teams gets it in
plain ``TextBlock``s and ``FactSet``s, never as markdown links.
"""

from __future__ import annotations

import hashlib
from typing import Any

APPROVE = "fabrique_approve"
SEND_BACK = "fabrique_send_back"
MAX_TEXT = 300
MAX_CHECKS = 6
TRIAGE = "triage"  # chat_approvals.TRIAGE; this module imports nothing of it


def _triage(point: dict[str, Any]) -> bool:
    return point.get("kind") == TRIAGE


def heading(point: dict[str, Any]) -> str:
    if _triage(point):
        return "A task is blocked and waits for a person"
    return "A delivery waits for a person's acceptance"


def _note_label(point: dict[str, Any]) -> str:
    return "Why it is blocked (as recorded)" if _triage(point) else "Hold"


def _cap(value: Any, limit: int = MAX_TEXT) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def slack_escape(value: Any, limit: int = MAX_TEXT) -> str:
    """*value* as literal text in Slack ``mrkdwn``: no links, mentions or markup injected."""
    return (
        _cap(value, limit)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def head_tag(head: str) -> str:
    """A short, colon-free tag of the hold's chain hash, for a button's value."""
    return hashlib.sha256(head.encode()).hexdigest()[:16]


def button_value(message_id: int, head: str, run_version: int) -> str:
    """What a Slack button carries: which approval, and what it was posted for."""
    return f"{message_id}:{head_tag(head)}:{run_version}"


def parse_button_value(value: Any) -> tuple[int, str, int] | None:
    parts = str(value or "").split(":")
    if len(parts) != 3 or not parts[0].isdigit() or not parts[2].isdigit():
        return None
    return int(parts[0]), parts[1], int(parts[2])


def _check_lines(point: dict[str, Any]) -> list[str]:
    return [
        f"{c['name']}: {c['outcome']}"
        for c in point.get("failing_checks", [])[:MAX_CHECKS]
    ]


def slack_blocks(point: dict[str, Any], message_id: int, short_code: str) -> list[dict]:
    checks = _check_lines(point)
    checks_text = (
        "\n".join(f"• {slack_escape(line, 120)}" for line in checks)
        if checks
        else "none recorded"
    )
    value = button_value(message_id, point["head"], point["run_version"])
    send_back = {
        "type": "button",
        "action_id": SEND_BACK,
        "style": "danger",
        "text": {"type": "plain_text", "text": "Send back"},
        "value": value,
    }
    approve = {
        "type": "button",
        "action_id": APPROVE,
        "style": "primary",
        "text": {"type": "plain_text", "text": "Approve"},
        "value": value,
    }
    note = slack_escape(point["hold_note"], 200)
    if _triage(point):
        note = (
            f"*{_note_label(point)}:* {note} · Send back with a reason: "
            f"/fabrique sendback {short_code} followed by the reason"
        )
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*{heading(point)}*\n"
                    f"<{point['task_url']}|{slack_escape(point['title'], 150)}> · "
                    f"{slack_escape(point['sprint_name'], 80)}"
                ),
            },
        },
        {
            "type": "section",
            "fields": [
                {
                    "type": "mrkdwn",
                    "text": f"*Verdict (Verifier)*\n{slack_escape(point['verdict'], 40)}",
                },
                {
                    "type": "mrkdwn",
                    "text": f"*Checks not passed*\n{checks_text}",
                },
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"{note} · "
                        f"<{point['run_url']}|Run {point['run_id']} v{point['run_version']} evidence> · "
                        f"approval {short_code}"
                    ),
                }
            ],
        },
        {
            "type": "actions",
            "elements": [send_back] if _triage(point) else [approve, send_back],
        },
    ]


def slack_outcome_blocks(title: str, outcome_text: str, run_url: str) -> list[dict]:
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{slack_escape(title, 150)}*\n{slack_escape(outcome_text, 300)}",
            },
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"<{run_url}|Evidence>"}],
        },
    ]


def teams_card(point: dict[str, Any], short_code: str) -> dict[str, Any]:
    checks = _check_lines(point) or ["none recorded"]
    if _triage(point):
        how = f"To send it back to work, mention the La Fabrique webhook: sendback {short_code} followed by the reason."
    else:
        how = (
            f"To decide, mention the La Fabrique webhook: approve {short_code} "
            f"or sendback {short_code} followed by the reason."
        )
    body = [
        {
            "type": "TextBlock",
            "text": heading(point),
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
        },
        {"type": "TextBlock", "text": _cap(point["title"], 150), "wrap": True},
        {
            "type": "FactSet",
            "facts": [
                {"title": "Sprint", "value": _cap(point["sprint_name"], 80)},
                {"title": "Verdict (Verifier)", "value": _cap(point["verdict"], 40)},
                {"title": "Checks not passed", "value": _cap("; ".join(checks), 300)},
                {"title": _note_label(point), "value": _cap(point["hold_note"], 200)},
                {
                    "title": "Run",
                    "value": f"{point['run_id']} v{point['run_version']}",
                },
            ],
        },
        {
            "type": "TextBlock",
            "text": how,
            "wrap": True,
            "isSubtle": True,
        },
    ]
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
                            "title": "Open the evidence",
                            "url": point["run_url"],
                        },
                        {
                            "type": "Action.OpenUrl",
                            "title": "Open the task",
                            "url": point["task_url"],
                        },
                    ],
                },
            }
        ],
    }
