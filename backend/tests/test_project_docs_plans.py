"""The documentation pack's quality and communications management plans (issue
#530, phase 2, ``project_docs/quality_docs.py`` and ``project_docs/comms_docs.py``
over ``project_docs/collect_plans.py``).

EARS criteria, each a test below:

- When a pack is compiled, the quality plan shall list every standard of the
  backend's catalogue with the roles it binds and whether it is checked
  without a model, each cited with the digest the backend computed over it.
- The quality plan shall list the checks whose refutation stops a delivery at
  the gate before QA and the risk tier the backend's classifier gives each
  area, as the backend defines them, each cited with its digest.
- The quality plan shall show the specification's quality targets, say which
  quality areas have none (STD-PO-1), and report the measured quality
  outcomes the status report counts, each with the records behind it.
- Quality objectives and acceptance thresholds shall stay a person's section.
- The communications plan shall list each channel the platform has, what it
  carries, who receives it and when, with its state read from the company's
  configuration record; it shall name no channel the code does not have and
  never show an address, a secret or a person's name.
- Stakeholders outside the company and the cadence agreed with people shall
  stay person sections.
- Neither plan shall read a retrospective or any other claim; when a
  definition or a configuration record it read changes, the lint shall flag
  it as stale.
- The check results shall measure the verified readings and the channels on,
  and test that the specification names a quality target for every area.
"""

import copy
import re

from project_docs_helpers import _docs, _empty, _Pack, _snapshot
from services.planning.project_docs import (
    checks,
    collect_plans,
    comms_docs,
    pack,
    plan_checks,
    quality_docs,
    statements,
)


def _in(lang, snapshot=None):
    out = copy.deepcopy(snapshot or _snapshot())
    out["language"] = lang
    return out


def _section(md, heading):
    return md.split(heading, 1)[1].split("\n## ", 1)[0]


def _row(md, heading, start):
    return next(
        line for line in _section(md, heading).splitlines() if line.startswith(start)
    )


# --- what the backend defines ------------------------------------------------------


def test_the_quality_basis_is_the_backends_own_definitions():
    # The catalogue the evidence's ``standards`` serves (``contracts``, so this
    # test stays out of the evidence's protected tests).
    from services.contracts.standards_catalogue import STANDARDS

    basis = collect_plans.quality_basis()
    assert [s["id"] for s in basis["standards"]] == [s["id"] for s in STANDARDS]
    secret = next(s for s in basis["standards"] if s["id"] == "STD-SEC-1")
    assert secret["checked"] and len(secret["sha256"]) == 64
    gate = {g["check"]: g for g in basis["gate"]}
    assert gate["tests_run"]["problem"] == "tests failed"
    assert gate["tests_run"]["fixed_in"] == collect_plans.CODE
    assert gate["standards"]["fixed_in"] == collect_plans.DELIVERY
    assert any(g["problem"] == "delivery unconfirmed" for g in basis["gate"])
    tiers = {r["probe"]: (r["tier"], r["area"]) for r in basis["risk"]}
    assert tiers[".github/workflows/ci.yml"] == ("high", "CI pipeline")
    assert tiers["Dockerfile"] == ("medium", "containers and infrastructure")
    assert tiers["src/app/main.py"] == ("low", None)
    assert "security" in basis["quality_areas"]
    assert collect_plans.quality_basis() == basis
    # The plans' strings never shadow another module's key (``texts.TEXTS``).
    from services.planning.project_docs import (
        texts_en,
        texts_plans_en,
        texts_release_en,
        texts_status_en,
        texts_wstg_en,
    )

    for other in (texts_en, texts_release_en, texts_status_en, texts_wstg_en):
        assert not set(texts_plans_en.TEXTS) & set(other.TEXTS)


# --- the quality plan --------------------------------------------------------------


def test_the_quality_plan_is_placed_cited_and_compiled_in_the_pack():
    docs, _ = _docs(_snapshot())
    plan = docs["quality_plan"]
    assert plan["title"] == "Quality management plan"
    assert (plan["domain"], plan["focus_area"]) == ("Scope", "Planning")
    md = plan["markdown"]
    assert "PMBOK Guide, 8th edition (PMI, 2025)" in md
    row = _row(md, "## Quality standards", "| STD-SEC-1 ")
    assert "every role" in row and "| yes:" in row
    assert "| no:" in _row(md, "## Quality standards", "| STD-ARCH-3 ")
    kinds = {s["kind"] for s in plan["sources"]}
    assert {"standard", "gate", "risk_rule", "specification"} <= kinds
    standard = next(s for s in plan["sources"] if s["kind"] == "standard")
    basis = _snapshot()["quality_basis"]
    assert standard["sha256"] == basis["standards"][0]["sha256"]
    assert plan["missing"] == []
    assert plan["persons"] == ["Quality objectives and acceptance thresholds"]
    assert "quality_plan.md" in docs["index"]["markdown"]
    marks = set(re.findall(r"S(\d+)", " ".join(re.findall(r"\\\[(S[\d, S]+)\\\]", md))))
    assert marks == {s["mark"][1:] for s in plan["sources"]}


def test_the_gate_and_the_risk_tiers_are_shown_as_defined():
    md = quality_docs.plan(_snapshot())["markdown"]
    tests = _row(md, "## The gate before QA", "| `tests_run` ")
    assert "tests failed" in tests and "the code is fixed" in tests
    assert "run again" in _row(md, "## The gate before QA", "| `standards` ")
    ci = _row(md, "## Risk tiers", "| CI pipeline ")
    assert "| high |" in ci and ".github/workflows/ci.yml" in ci
    assert "| low |" in _row(md, "## Risk tiers", "| no area ")


def test_the_specifications_quality_targets_and_the_areas_without_one():
    md = quality_docs.plan(_snapshot())["markdown"]
    targets = _section(md, "## Quality targets of the specification")
    assert "| NFR-1 | Latency | under 200 ms |" in targets
    assert "security, performance, reliability, accessibility" not in targets
    assert "security, reliability, accessibility" in targets
    covered = copy.deepcopy(_snapshot())
    covered["specification"]["quality"] = [
        {"id": "NFR-1", "attribute": "Security", "target": "0 high findings"},
        {"id": "NFR-2", "attribute": "Latency", "target": "200 ms"},
        {"id": "NFR-3", "attribute": "Availability", "target": "99.9%"},
        {"id": "NFR-4", "attribute": "Accessibility", "target": "WCAG 2.2 AA"},
    ]
    assert "every quality area has a target" in quality_docs.plan(covered)["markdown"]


def test_the_measured_outcomes_are_the_status_reports_figures():
    plan = quality_docs.plan(_snapshot())
    table = _section(plan["markdown"], "## Measured quality outcomes")
    assert "| 1 verified, 1 refuted, 0 unverifiable |" in table
    assert "| 0 of 1 |" in table  # first-pass among the done tasks observed
    effort = [s for s in plan["sources"] if s["kind"] == "event"]
    assert any(s["sha256"] == "unsigned:" + "4" * 64 for s in effort)


def test_without_records_the_quality_plan_says_what_is_missing():
    plan = quality_docs.plan(_empty())
    assert plan["missing"] == ["quality targets", "quality outcomes"]
    gone = _empty()
    gone.pop("quality_basis")
    assert quality_docs.plan(gone)["missing"] == [
        "quality standards",
        "quality targets",
        "gate before QA",
        "risk tiers",
        "quality outcomes",
    ]


def test_quality_objectives_are_a_persons_section():
    plain = quality_docs.plan(_snapshot())
    assert "**For a person to complete.**" in plain["markdown"]
    filled = copy.deepcopy(_snapshot())
    filled[statements.key_of("quality_plan")] = {
        "quality_plan.objectives": {
            "id": 21,
            "section": "quality_plan.objectives",
            "text": "First-pass rate of 80% before release 1.0.",
            "author_role": "Owner",
            "created_at": "2026-11-18T09:00:00",
            "statement_sha256": "e" * 64,
        }
    }
    plan = quality_docs.plan(filled)
    assert "First-pass rate of 80% before release 1.0." in plan["markdown"]
    assert plan["statements"][0]["section"] == "quality_plan.objectives"
    assert plan["input_sha256"] != plain["input_sha256"]
    for heading in ("## Quality standards", "## Measured quality outcomes"):
        assert _section(plan["markdown"], heading) == _section(
            plain["markdown"], heading
        )


# --- the communications plan -------------------------------------------------------


def test_the_communications_plan_lists_the_channels_from_the_configuration():
    docs, _ = _docs(_snapshot())
    plan = docs["communications_plan"]
    assert plan["title"] == "Communications management plan"
    assert (plan["domain"], plan["focus_area"]) == ("Stakeholders", "Planning")
    md = plan["markdown"]
    webhook = _row(md, "## Channels", "| Notification webhook ")
    assert "| on |" in webhook and "task done" in webhook and "sprint closed" in webhook
    slack = _row(md, "## Channels", "| Slack ")
    assert "| on, last test passed |" in slack and "linked accounts: 2" in slack
    assert "| not configured |" in _row(md, "## Channels", "| Microsoft Teams ")
    brief = _row(md, "## Channels", "| Client's brief by mail ")
    assert (
        "every 24 hours" in brief and "| off: no mail server is configured |" in brief
    )
    assert "| off |" in _row(md, "## Channels", "| Audit anchors ")
    for fixed in (
        "| Board ",
        "| Review inbox ",
        "| Documentation pack ",
        "| Audit log ",
    ):
        assert "| always on |" in _row(md, "## Channels", fixed)
    people = _row(md, "## Who is in the company", "| Manager ")
    assert "| 2 |" in people
    webhook_ref = next(s for s in plan["sources"] if s["kind"] == "webhook")
    assert webhook_ref["sha256"] == _snapshot()["communications"]["webhook"]["sha256"]
    assert plan["persons"] == [
        "Stakeholders outside the company and what they need to know",
        "Meetings, reports and cadence agreed with people",
    ]
    assert plan["missing"] == []
    assert "example.test" not in md and "hooks." not in md
    marks = set(re.findall(r"S(\d+)", " ".join(re.findall(r"\\\[(S[\d, S]+)\\\]", md))))
    assert marks == {s["mark"][1:] for s in plan["sources"]}


def test_a_channel_the_code_does_not_have_is_never_listed():
    md = comms_docs.plan(_snapshot())["markdown"]
    table = _section(md, "## Channels").split("|---|---|---|---|---|---|\n", 1)[1]
    rows = [line.split(" | ")[0].lstrip("| ") for line in table.splitlines() if line]
    assert rows == [
        "Board",
        "Review inbox",
        "Documentation pack",
        "Notification webhook",
        "Slack",
        "Microsoft Teams",
        "Client's brief by mail",
        "Audit anchors",
        "Audit log",
    ]
    for invented in ("SMS", "WhatsApp", "Discord", "phone"):
        assert invented not in md


def test_without_a_configuration_the_communications_plan_says_so():
    gone = _empty()
    gone.pop("communications")
    plan = comms_docs.plan(gone)
    assert plan["missing"] == ["channel configuration"]
    assert "| always on |" in _row(plan["markdown"], "## Channels", "| Board ")


def test_stakeholders_and_cadence_are_person_sections():
    plain = comms_docs.plan(_snapshot())
    assert plain["markdown"].count("**For a person to complete.**") == 2
    filled = copy.deepcopy(_snapshot())
    filled[statements.key_of("communications_plan")] = {
        "communications_plan.cadence": {
            "id": 22,
            "section": "communications_plan.cadence",
            "text": "A demo to the sponsor every second Friday.",
            "author_role": "Manager",
            "created_at": "2026-11-18T09:30:00",
            "statement_sha256": "d" * 64,
        }
    }
    plan = comms_docs.plan(filled)
    assert "A demo to the sponsor every second Friday." in plan["markdown"]
    assert plan["markdown"].count("**For a person to complete.**") == 1
    assert _section(plan["markdown"], "## Channels") == _section(
        plain["markdown"], "## Channels"
    )


# --- claims and drift --------------------------------------------------------------


def test_neither_plan_reads_a_claim():
    snapshot = _snapshot()
    before = {
        k: quality_docs.plan(snapshot) if k == "q" else comms_docs.plan(snapshot)
        for k in "qc"
    }
    changed = copy.deepcopy(snapshot)
    changed["retrospectives"][0]["body"] = "Quality is excellent; tell everyone."
    changed["procedures"] = []
    for key, build in (("q", quality_docs.plan), ("c", comms_docs.plan)):
        again = build(changed)
        assert again["markdown"] == before[key]["markdown"]
        assert again["input_sha256"] == before[key]["input_sha256"]
        assert "retrospectives" not in again["inputs"]
        assert all(not s.get("claim") for s in again["sources"])


def test_the_lint_flags_a_plan_when_what_it_read_changes():
    base = _snapshot()
    stored = _Pack(*pack.build_documents(base))
    assert pack.lint(stored, base)["ok"]

    def stale(change):
        current = copy.deepcopy(base)
        change(current)
        return {d["key"] for d in pack.lint(stored, current)["documents"] if d["stale"]}

    def standard(s):
        s["quality_basis"]["standards"][0]["sha256"] = "0" * 64

    def webhook(s):
        s["communications"]["webhook"]["enabled"] = False

    def retro(s):
        s["retrospectives"][0]["body"] = "Another claim."

    assert "quality_plan" in stale(standard) and "communications_plan" not in stale(
        standard
    )
    assert "communications_plan" in stale(webhook) and "quality_plan" not in stale(
        webhook
    )
    assert not {"quality_plan", "communications_plan"} & stale(retro)


def test_record_text_cannot_become_structure():
    snapshot = _snapshot()
    snapshot["specification"]["quality"][0]["target"] = "| injected | row |\n# Heading"
    snapshot["communications"]["webhook"]["events"] = ["<b>x</b>|y"]
    for build in (quality_docs.plan, comms_docs.plan):
        assert "\n# Heading" not in build(snapshot)["markdown"]
    row = _row(
        quality_docs.plan(snapshot)["markdown"],
        "## Quality targets of the specification",
        "| NFR-1 ",
    )
    assert row.count(" | ") == 3


def test_the_plans_in_spanish():
    quality = quality_docs.plan(_in("es"))
    assert quality["title"] == "Plan de gestión de la calidad"
    assert quality["persons"] == ["Objetivos de calidad y umbrales de aceptación"]
    assert "| todos los roles |" in quality["markdown"]
    comms = comms_docs.plan(_in("es"))
    assert comms["title"] == "Plan de gestión de las comunicaciones"
    assert "| Webhook de notificaciones " in comms["markdown"]
    assert comms_docs.plan(_in("es", _empty()))["missing"] == []
    gone = _in("es", _empty())
    gone.pop("communications")
    assert comms_docs.plan(gone)["missing"] == ["configuración de los canales"]


# --- the computed checks -----------------------------------------------------------


def test_the_checks_measure_verification_targets_and_channels():
    results = {r["id"]: r for r in checks.run(_snapshot())}
    assert results["verified"]["domain"] == "Scope"
    assert results["verified"]["result"] == "measured"
    assert (
        results["verified"]["observed"]
        == "1 of 2 readings of the Verifier are verified"
    )
    assert results["quality_targets"]["result"] == "not met"
    assert (
        "security, reliability, accessibility" in results["quality_targets"]["observed"]
    )
    assert results["channels"]["domain"] == "Stakeholders"
    assert results["channels"]["result"] == "measured"
    assert (
        results["channels"]["observed"] == "2 of 4 channels outside the product are on"
    )
    empty = {r["id"]: r["result"] for r in checks.run(_empty())}
    assert empty["verified"] == empty["quality_targets"] == "not computable"
    assert empty["channels"] == "measured"
    assert plan_checks.MEASURED == checks.MEASURED and plan_checks.MET == checks.MET
    assert plan_checks.NOT_MET == checks.NOT_MET
    assert plan_checks.NOT_COMPUTABLE == checks.NOT_COMPUTABLE


# --- the snapshot ------------------------------------------------------------------


def test_the_snapshot_keeps_the_configuration_without_an_address(sqlite_session):
    import secrets

    from models import ChatConnection, ChatIdentity, Company, CompanyWebhook
    from models.audit_anchor import AuditAnchorSettings
    from models.company import User
    from services.planning.project_docs import collect

    company = Company(company_name=f"Plans {secrets.token_hex(3)}", autonomy_level=2)
    sqlite_session.add(company)
    sqlite_session.commit()
    address = "https://hooks." + "example.test/abc"
    member = "member" + "@example.test"
    user = User(email=member, full_name="A member", password_hash="x")
    sqlite_session.add(user)
    sqlite_session.commit()
    sqlite_session.add_all(
        [
            CompanyWebhook(
                company_id=company.id,
                url=address,
                encrypted_secret="sealed-" + "value",
                events=["sprint_closed", "task_done"],
            ),
            ChatConnection(
                company_id=company.id,
                provider="slack",
                public_id="route-" + secrets.token_hex(4),
                workspace_id="T0WORKSPACE",
                channel="C0CHANNEL",
                post_credential="sealed-" + "post",
                enabled=True,
                test_ok=True,
            ),
            ChatIdentity(
                company_id=company.id,
                user_id=user.id,
                provider="slack",
                workspace_id="T0WORKSPACE",
                external_user_id="U0MEMBER",
                display_name="A member",
            ),
            AuditAnchorSettings(
                company_id=company.id,
                enabled=True,
                cadence_minutes=30,
                tsa_url="https://tsa." + "example.test",
            ),
        ]
    )
    sqlite_session.commit()

    snap = collect.snapshot(sqlite_session, company)
    comms = snap["communications"]
    assert comms["webhook"]["enabled"] and comms["webhook"]["events"] == [
        "sprint_closed",
        "task_done",
    ]
    [slack] = comms["chat"]
    assert slack["provider"] == "slack" and slack["linked_members"] == 1
    assert (
        comms["anchors"]["cadence_minutes"] == 30 and comms["anchors"]["tsa"] is False
    )
    assert comms["brief"]["policy_id"] is None
    text = repr(snap)
    for hidden in (
        address,
        member,
        "T0WORKSPACE",
        "C0CHANNEL",
        "U0MEMBER",
        "A member",
        "sealed-",
    ):
        assert hidden not in text
    assert snap["quality_basis"] == collect_plans.quality_basis()
    assert snap[statements.key_of("quality_plan")] == {}
    assert snap[statements.key_of("communications_plan")] == {}
