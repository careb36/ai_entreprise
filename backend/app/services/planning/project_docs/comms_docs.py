"""The communications management plan of a pack (issue #530, phase 2).

Who is told what, through which channel and when, from what the platform does
and from the company's configuration of each channel (``communications``,
read by ``collect_plans``). The channels are exactly the ones the code has:

- always on, fixed by the platform: the board, the review inbox (what waits
  for a person), the documentation pack and its approval, and the audit log;
- configured by the company: the notification webhook (``CompanyWebhook``),
  the Slack and Teams connections for approvals in chat (``ChatConnection``),
  the client's brief by mail (a ``ManagerPolicy`` row, the Manager agent and
  the deployment's mail server) and the audit anchors
  (``AuditAnchorSettings``).

A configurable channel's state is read from its record and cited with the
digest the backend computed over it; the record never carries an address, a
secret, a credential or a name, so the plan cannot show one. A fixed channel
cites the factory rule that defines it. People are counted by company role.
The structure follows the PMBOK Guide, 8th edition (PMI, 2025), where
communications are planned within the Stakeholders performance domain's
Planning focus area; the wording is our own (``texts_plans_*``). Stakeholders
outside the company and the cadence agreed with people are person sections
(``statements``). The plan reads no retrospective or agent message.

Pure; no I/O.
"""

from __future__ import annotations

from typing import Any

from services.planning.project_docs import statements, texts
from services.planning.project_docs.control_docs import cell, rule_ref
from services.planning.project_docs.layout import PMBOK, build, missing_line, tr_of
from services.planning.project_docs.refs import Sources, digest, ref

KEY = "communications"
STAKEHOLDERS = "communications_plan.stakeholders"
CADENCE = "communications_plan.cadence"
BASE_INPUTS = ("members", KEY, statements.key_of("communications_plan"))
ROLES = ("Owner", "Manager", "Viewer")
PROVIDERS = ("slack", "teams")
# The channels outside the product whose state the company sets (``plan_checks``).
OUTSIDE = ("webhook", *PROVIDERS, "brief")
ROLES_DOC = "services/platform/teams"
BOARD_DOC = "services/tasks/board"
INBOX_DOC = "services/management/manager_inbox"
PACK_DOC = "routers/documentation"
AUDIT_DOC = "services/platform/agent_service"


def config(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    found = snapshot.get(KEY)
    return found if isinstance(found, dict) else None


def chat_of(snapshot: dict[str, Any], provider: str) -> dict[str, Any] | None:
    for row in (config(snapshot) or {}).get("chat") or []:
        if isinstance(row, dict) and row.get("provider") == provider:
            return row
    return None


def brief_reason(brief: dict[str, Any] | None) -> str | None:
    """Why the brief by mail is not sent, as a text key; ``None`` when it is."""
    brief = brief or {}
    if not brief.get("policy_id"):
        return "comms.brief_no_policy"
    if not brief.get("manager_agent"):
        return "comms.brief_no_agent"
    if not brief.get("mail_configured"):
        return "comms.brief_no_mail"
    return None


def outside_on(snapshot: dict[str, Any]) -> dict[str, bool]:
    """Which channels outside the product are on, from their records."""
    found = config(snapshot) or {}
    webhook = found.get("webhook") or {}
    on = {"webhook": bool(webhook.get("enabled") and webhook.get("events"))}
    for provider in PROVIDERS:
        on[provider] = bool((chat_of(snapshot, provider) or {}).get("enabled"))
    on["brief"] = brief_reason(found.get("brief")) is None
    return on


def _people(snapshot, src, tr) -> list[str]:
    members = snapshot.get("members") or {}
    mark = src.cite(
        ref("membership", "people", sha256=digest(members), label=tr("label_people")),
        rule_ref(ROLES_DOC, tr("comms.rule_roles")),
    )
    body = [
        tr("comms.people_heading"),
        "",
        tr("comms.people_note"),
        "",
        tr("comms.people_header"),
        "|---|---|---|---|",
    ]
    for role in ROLES:
        count = members.get(role) or 0
        body.append(f"| {role} | {count} | {tr('comms.role.' + role)} | {mark} |")
    return body


def _record_ref(kind: str, record: dict[str, Any], label: str) -> dict[str, Any]:
    return ref(kind, record.get("id") or kind, sha256=record.get("sha256"), label=label)


def _webhook(found, src, tr) -> tuple[str, str, str]:
    webhook = (found or {}).get("webhook")
    if not webhook:
        return (
            tr("comms.webhook_what", events=tr("comms.no_events")),
            tr("comms.not_configured"),
            "-",
        )
    events = [
        tr(f"comms.event.{e}")
        if f"comms.event.{e}" in texts.TEXTS["en"]
        else cell(e, 40)
        for e in webhook.get("events") or []
    ]
    state = tr("comms.on") if webhook.get("enabled") else tr("comms.off")
    what = tr("comms.webhook_what", events=", ".join(events) or tr("comms.no_events"))
    return what, state, src.cite(_record_ref("webhook", webhook, state))


def _chat(snapshot, provider, src, tr) -> tuple[str, str, str]:
    row = chat_of(snapshot, provider)
    if not row:
        return tr("comms.chat_who", count=0), tr("comms.not_configured"), "-"
    if not row.get("enabled"):
        state = tr("comms.off")
    elif row.get("test_ok") is True:
        state = tr("comms.test_passed")
    elif row.get("test_ok") is False:
        state = tr("comms.test_failed")
    else:
        state = tr("comms.never_tested")
    linked = row.get("linked_members")
    who = tr("comms.chat_who", count=linked if isinstance(linked, int) else 0)
    return who, state, src.cite(_record_ref("chat", row, f"{provider}: {state}"))


def _brief(found, src, tr) -> tuple[str, str, str]:
    brief = (found or {}).get("brief")
    if not brief:
        return tr("comms.brief_when", hours="?"), tr("comms.not_configured"), "-"
    hours = brief.get("interval_hours")
    shown = f"{hours:g}" if isinstance(hours, int | float) else "?"
    reason = brief_reason(brief)
    state = tr(reason) if reason else tr("comms.on")
    mark = src.cite(
        ref(
            "brief",
            brief.get("policy_id") or "deployment",
            sha256=brief.get("sha256"),
            label=tr("comms.label_brief"),
        )
    )
    return tr("comms.brief_when", hours=shown), state, mark


def _anchors(found, src, tr) -> tuple[str, str, str]:
    anchors = (found or {}).get("anchors")
    if not anchors:
        return tr("comms.anchors_when", minutes="?"), tr("comms.not_configured"), "-"
    minutes = anchors.get("cadence_minutes")
    when = tr(
        "comms.anchors_when", minutes=minutes if isinstance(minutes, int) else "?"
    )
    if anchors.get("enabled"):
        targets = [tr("comms.anchor_webhook")] if anchors.get("publish_webhook") else []
        targets += [tr("comms.anchor_tsa")] if anchors.get("tsa") else []
        state = tr(
            "comms.anchors_targets",
            targets=", ".join(targets) or tr("comms.anchor_none"),
        )
    else:
        state = tr("comms.off")
    return when, state, src.cite(_record_ref("anchors", anchors, state))


def _channels(snapshot, src, missing, lang, tr) -> list[str]:
    found = config(snapshot)
    always = tr("comms.always_on")
    rows: list[tuple[str, ...]] = []

    def fixed(name: str, doc: str, rule: str) -> None:
        rows.append(
            (
                tr(f"comms.{name}"),
                tr(f"comms.{name}_what"),
                tr(f"comms.{name}_who"),
                tr(f"comms.{name}_when"),
                always,
                src.cite(rule_ref(doc, tr(rule))),
            )
        )

    fixed("board", BOARD_DOC, "comms.rule_board")
    fixed("inbox", INBOX_DOC, "comms.rule_inbox")
    fixed("pack", PACK_DOC, "comms.rule_pack")
    what, state, mark = _webhook(found, src, tr)
    rows.append(
        (
            tr("comms.webhook"),
            what,
            tr("comms.webhook_who"),
            tr("comms.webhook_when"),
            state,
            mark,
        )
    )
    for provider in PROVIDERS:
        who, state, mark = _chat(snapshot, provider, src, tr)
        rows.append(
            (
                tr(f"comms.{provider}"),
                tr("comms.chat_what"),
                who,
                tr("comms.chat_when"),
                state,
                mark,
            )
        )
    when, state, mark = _brief(found, src, tr)
    rows.append(
        (
            tr("comms.brief"),
            tr("comms.brief_what"),
            tr("comms.brief_who"),
            when,
            state,
            mark,
        )
    )
    when, state, mark = _anchors(found, src, tr)
    rows.append(
        (
            tr("comms.anchors"),
            tr("comms.anchors_what"),
            tr("comms.anchors_who"),
            when,
            state,
            mark,
        )
    )
    fixed("audit", AUDIT_DOC, "comms.rule_audit")
    body = [tr("comms.channels_heading"), ""]
    if found is None:
        missing.append(tr("comms.section_config"))
        body += [missing_line(lang, tr("comms.no_config")), ""]
    body += [
        tr("comms.channels_note"),
        "",
        tr("comms.channels_header"),
        "|---|---|---|---|---|---|",
    ]
    body += ["| " + " | ".join(row) + " |" for row in rows]
    return body


def plan(snapshot: dict[str, Any]) -> dict[str, Any]:
    """The communications management plan from *snapshot*."""
    lang, tr = tr_of(snapshot)
    src, missing = Sources(lang), []
    body = [tr("comms.intro"), ""]
    body += _people(snapshot, src, tr)
    body += [""] + _channels(snapshot, src, missing, lang, tr)
    for section, heading in (
        (STAKEHOLDERS, "person.comms_stakeholders"),
        (CADENCE, "person.comms_cadence"),
    ):
        body += ["", f"## {tr(heading)}", ""]
        body += statements.lines(snapshot, section, src)
    body += ["", tr("comms.read_heading"), "", tr("comms.read")]
    return build(
        "communications_plan",
        PMBOK,
        body,
        src,
        snapshot,
        BASE_INPUTS,
        missing,
        persons=[tr("person.comms_stakeholders"), tr("person.comms_cadence")],
        statements=statements.filled(snapshot, "communications_plan"),
    )
