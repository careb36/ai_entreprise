"""The security test plan from OWASP WSTG (issue #539): the versioned catalogue,
the deterministic rule table, the statuses set only by an observation or a
person's recorded review, the pack's checklist and report with their licence
footer, and the tests named after a WSTG test read back from the daemon's run.
"""

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from services.evidence import wstg_tests
from services.planning import wstg_plan
from services.planning.project_docs import pack, security_docs, texts, wstg_status
from services.platform import wstg_catalogue
from test_project_docs import _empty, _snapshot

REPO = Path(__file__).resolve().parents[2]
BENCH = REPO / "bench" / "specs"
REFS = Path("/home/user/refs/wstg")
# The identifiers the security probe (issue #540) emits, verbatim from
# services/evidence/security_probe.py on its branch: the plan must cite the same.
PROBE_IDS = {
    "WSTG-v42-CONF-04",
    "WSTG-v42-CONF-06",
    "WSTG-v42-CONF-07",
    "WSTG-87cf4a4-CONF-12",
    "WSTG-87cf4a4-CONF-14",
    "WSTG-v42-SESS-02",
    "WSTG-v42-CLNT-07",
    "WSTG-v42-CLNT-09",
    "WSTG-v42-ERRH-01",
    "WSTG-v42-ERRH-02",
}


def _spec_snapshot(spec, **extra):
    return {"specification": {"id": 1, **spec}, **extra}


def _with_records(observations=(), reviews=(), lang="en"):
    snapshot = _snapshot()
    snapshot["language"] = lang
    snapshot["wstg_observations"] = list(observations)
    snapshot["wstg_reviews"] = list(reviews)
    return snapshot


def _probe(verification_id, observed_at, **statuses):
    return {
        "verification_id": verification_id,
        "run_id": verification_id + 100,
        "check": "security_probe",
        "tool": "security_probe (WSTG 87cf4a4)",
        "observed_by": "daemon",
        "artifact_sha256": "9" * 64,
        "observed_at": observed_at,
        "items": [
            {"id": i, "status": s, "observation": f"{i} {s}"}
            for i, s in statuses.items()
        ],
    }


def _review(review_id, wstg_id, result, created_at, note=""):
    return {
        "id": review_id,
        "wstg_id": wstg_id,
        "result": result,
        "reviewed_on": created_at[:10],
        "artifact": "reports/pentest.pdf",
        "note": note,
        "reviewer_role": "Manager",
        "created_at": created_at,
        "record_sha256": "a" * 64,
    }


# --- the catalogue ------------------------------------------------------------


def test_the_catalogue_is_versioned_licensed_and_matches_the_probe_ids():
    data = wstg_catalogue.catalogue()
    assert data["licence"] == "CC BY-SA 4.0"
    assert "OWASP" in data["attribution"] and data["commit"] in data["attribution"]
    assert re.fullmatch(r"[0-9a-f]{40}", data["commit"])
    tests = wstg_catalogue.tests()
    ids = [t["id"] for t in tests]
    assert len(ids) == len(set(ids)) == 117
    assert all(re.fullmatch(r"WSTG-(v42|[0-9a-f]{7})-[A-Z]{4}-\d\d", i) for i in ids), (
        ids
    )
    for t in tests:
        assert t["id"].startswith("WSTG-v42-") == t["in_v42"]
        assert t["upstream_id"] == f"WSTG-{t['short']}"
    assert PROBE_IDS <= set(ids)
    # 4.2 gave APIT-01 to GraphQL: the current API Reconnaissance is not that test.
    assert wstg_catalogue.versioned("APIT-01") == "WSTG-87cf4a4-APIT-01"


def test_every_spelling_of_an_id_resolves_to_the_versioned_one():
    for spelling in ("CONF-12", "conf-12", "WSTG-CONF-12", "WSTG-87cf4a4-CONF-12"):
        assert wstg_catalogue.versioned(spelling) == "WSTG-87cf4a4-CONF-12"
    assert wstg_catalogue.versioned("WSTG-v42-CONF-06") == "WSTG-v42-CONF-06"
    assert wstg_catalogue.versioned("CONF-99") is None
    assert wstg_catalogue.versioned("<script>") is None


@pytest.mark.skipif(not (REFS / "checklists").is_dir(), reason="no WSTG checkout")
def test_the_generator_reproduces_the_committed_catalogue(tmp_path):
    out = tmp_path / "catalogue.json"
    subprocess.run(
        [
            sys.executable,
            str(REPO / "scripts" / "wstg_catalogue.py"),
            str(REFS),
            "--out",
            str(out),
            "--commit",
            wstg_catalogue.commit(),
        ],
        check=True,
        capture_output=True,
    )
    assert json.loads(out.read_text()) == wstg_catalogue.catalogue()


# --- the rule table -------------------------------------------------------------


def test_every_rule_names_a_catalogue_test_and_the_probe_rows_are_the_probe_ids():
    probe_rows = set()
    for rule, signal, reason, tests in wstg_plan.RULES:
        assert rule.startswith("R-") and reason
        assert all(part in wstg_plan.SIGNAL_WORDS for part in signal.split("+"))
        for short, mode, by in tests:
            assert wstg_catalogue.by_short(short), short
            assert (mode, by) in {
                ("auto", "security_probe"),
                ("auto", "qa_test"),
                ("manual", "person"),
            }
            if by == "security_probe":
                probe_rows.add(wstg_catalogue.versioned(short))
        # Every rule has its text in both languages.
        for lang in texts.LANGUAGES:
            assert f"wstg.rule.{rule}" in texts.TEXTS[lang]
    assert probe_rows == PROBE_IDS


def test_the_bench_http_api_plan_names_the_expected_ids():
    expected = json.loads((BENCH / "03-http-api" / "wstg_expected.json").read_text())
    assert expected["catalogue_commit"] == wstg_catalogue.commit()
    spec = json.loads((BENCH / "03-http-api" / "spec.json").read_text())
    plan = wstg_plan.plan(_spec_snapshot(spec))
    rows = wstg_plan.applicable(plan)
    assert sorted(r["id"] for r in rows if r["mode"] == "auto") == sorted(
        expected["auto"]
    )
    assert sorted(r["id"] for r in rows if r["mode"] == "manual") == sorted(
        expected["manual"]
    )
    by_id = {r["id"]: r for r in plan["rows"]}
    for ident in expected["not_applicable_includes"]:
        assert not by_id[ident]["applicable"] and by_id[ident]["reason"]


@pytest.mark.parametrize("name", ["01-cli-wordcount", "02-lib-roman"])
def test_a_library_or_cli_has_no_web_test(name):
    spec = json.loads((BENCH / name / "spec.json").read_text())
    plan = wstg_plan.plan(_spec_snapshot(spec))
    assert plan["signals"] == {} and wstg_plan.applicable(plan) == []
    assert all(r["reason"] for r in plan["rows"])


def test_signals_are_read_in_spanish_from_frameworks_and_together():
    spec = {
        "title": "Portal de clientes",
        "summary": "Los clientes inician sesión con contraseña y suben archivos adjuntos.",
        "requirements": [
            {"id": "REQ-1", "title": "Pagos con tarjeta de crédito"},
            {"id": "REQ-2", "title": "Un administrador gestiona permisos"},
        ],
    }
    stack = {"frameworks": ["FastAPI", "graphene"], "databases": ["postgresql"]}
    plan = wstg_plan.plan(_spec_snapshot(spec, stack=stack))
    assert {
        "auth",
        "uploads",
        "payments",
        "roles",
        "api",
        "graphql",
        "database",
        "web",
    } <= set(plan["signals"])
    by_short = {r["short"]: r for r in plan["rows"]}
    # api+auth: object and function level authorization.
    assert by_short["APIT-02"]["applicable"]
    assert by_short["APIT-02"]["rules"] == ["R-API-AUTH"]
    assert by_short["SESS-02"]["observed_by"] == "security_probe"
    assert by_short["INJT-05"]["mode"] == "auto"
    assert by_short["BUSL-10"]["mode"] == "manual"
    where = plan["signals"]["auth"][0]
    assert where["kind"] == "text" and where["match"] == "inician sesión"
    # Deterministic.
    assert wstg_plan.plan(_spec_snapshot(spec, stack=stack)) == plan


def test_a_word_inside_another_is_not_a_signal():
    spec = {"title": "Rapid restore", "summary": "The rest of the payroll."}
    assert wstg_plan.signals(_spec_snapshot(spec)) == {}


def test_a_model_may_only_propose_additions_as_claims():
    spec = json.loads((BENCH / "03-http-api" / "spec.json").read_text())
    plain = wstg_plan.plan(_spec_snapshot(spec))
    proposed = wstg_plan.plan(
        _spec_snapshot(spec),
        proposals=[
            {"id": "ATHN-03", "role": "SecOps", "reason": "a login may come"},
            # Already applicable: a proposal changes nothing.
            {"id": "WSTG-v42-CONF-06", "role": "SecOps", "reason": "drop it"},
            {"id": "NOPE-01", "role": "SecOps", "reason": "unknown"},
        ],
    )
    added = [r for r in proposed["rows"] if r.get("claim")]
    assert [r["short"] for r in added] == ["ATHN-03"]
    assert added[0]["mode"] == "manual" and added[0]["proposed_by"] == "SecOps"
    rest = [r for r in proposed["rows"] if not r.get("claim")]
    assert rest == [r for r in plain["rows"] if r["short"] != "ATHN-03"]


def test_qa_tasks_one_per_automatic_qa_row_named_by_token():
    plan = wstg_plan.plan(_snapshot())
    tasks = wstg_plan.security_test_tasks(plan, 50)
    qa_rows = [r for r in wstg_plan.applicable(plan) if r["observed_by"] == "qa_test"]
    assert [t["wstg_id"] for t in tasks] == [r["id"] for r in qa_rows]
    assert all(t["assignee"] == "QA" and t["covers"] == [] for t in tasks)
    first = tasks[0]
    token = wstg_plan.test_token(qa_rows[0]["short"])
    assert f"test_{token}_" in first["description"] and len(first["title"]) <= 80
    assert len(wstg_plan.security_test_tasks(plan, 2)) == 2


# --- statuses -------------------------------------------------------------------


def test_only_an_observation_or_a_review_sets_a_row_observed():
    snapshot = _with_records(
        observations=[
            _probe(
                12,
                "2026-11-03T12:00:00",
                **{
                    "WSTG-v42-CLNT-07": "refuted",
                    "WSTG-v42-CONF-07": "unverifiable",
                    "WSTG-87cf4a4-CONF-12": "pass",
                    "WSTG-87cf4a4-CONF-14": "finding",
                },
            ),
            # An item without a verification id is never an observation.
            {**_probe(0, "2026-11-04T00:00:00", **{"WSTG-v42-CONF-06": "pass"})},
        ],
        reviews=[
            _review(1, "WSTG-v42-INFO-02", "pass", "2026-11-05T10:00:00"),
            _review(
                2,
                "WSTG-v42-CRYP-01",
                "not_applicable",
                "2026-11-05T11:00:00",
                "no TLS: internal only",
            ),
        ],
    )
    snapshot["wstg_observations"][1]["verification_id"] = None
    rows = {r["id"]: r for r in wstg_status.rows(snapshot)}
    assert rows["WSTG-v42-CLNT-07"]["status"] == "observed-fail"
    assert rows["WSTG-87cf4a4-CONF-14"]["status"] == "observed-fail"
    assert rows["WSTG-87cf4a4-CONF-12"]["status"] == "observed-pass"
    assert rows["WSTG-v42-CONF-07"]["status"] == "not-run"
    assert rows["WSTG-v42-CONF-07"]["unverifiable"]
    assert rows["WSTG-v42-CONF-06"]["status"] == "not-run"
    assert rows["WSTG-v42-INFO-02"]["status"] == "observed-pass"
    assert rows["WSTG-v42-INFO-02"]["evidence"]["review_id"] == 1
    assert rows["WSTG-v42-CRYP-01"]["status"] == "not-applicable"
    assert rows["WSTG-v42-CONF-02"]["status"] == "needs-manual"
    assert rows["WSTG-v42-ATHN-03"]["status"] == "not-applicable"
    for row in rows.values():
        if row["status"].startswith("observed-"):
            ev = row["evidence"]
            assert ev and (ev.get("verification_id") or ev.get("review_id")), row
            if ev["kind"] == "verification":
                assert ev["run_id"] and ev["artifact_sha256"] and ev["tool"]
        assert row["status"] in wstg_status.STATUSES


def test_the_newest_record_sets_the_status():
    fail_then_pass = _with_records(
        observations=[
            _probe(12, "2026-11-03T12:00:00", **{"WSTG-v42-CLNT-07": "refuted"}),
            _probe(13, "2026-11-06T12:00:00", **{"WSTG-v42-CLNT-07": "pass"}),
        ]
    )
    row = next(r for r in wstg_status.rows(fail_then_pass) if r["short"] == "CLNT-07")
    assert row["status"] == "observed-pass"
    assert row["evidence"]["verification_id"] == 13
    reviewed_after = copy.deepcopy(fail_then_pass)
    reviewed_after["wstg_reviews"] = [
        _review(3, "WSTG-v42-CLNT-07", "fail", "2026-11-07T09:00:00")
    ]
    row = next(r for r in wstg_status.rows(reviewed_after) if r["short"] == "CLNT-07")
    assert row["status"] == "observed-fail" and row["evidence"]["review_id"] == 3


# --- the pack's documents ----------------------------------------------------------


@pytest.mark.parametrize("lang", ["en", "es"])
def test_the_checklist_and_report_carry_the_licence_footer(lang):
    snapshot = _with_records(
        observations=[
            _probe(12, "2026-11-03T12:00:00", **{"WSTG-v42-CLNT-07": "refuted"})
        ],
        lang=lang,
    )
    documents, _ = pack.build_documents(snapshot)
    docs = {d["key"]: d for d in documents}
    footer = security_docs.footer(lang)
    for key in ("wstg_checklist", "security_report"):
        doc = docs[key]
        assert doc["licence"] == "CC BY-SA 4.0"
        assert doc["markdown"].rstrip().endswith(footer.rstrip())
        assert "CC BY-SA 4.0" in footer and wstg_catalogue.commit() in footer
        assert "https://creativecommons.org/licenses/by-sa/4.0/" in footer
    assert "OWASP Web Security Testing Guide" in docs["index"]["markdown"]
    # The other documents keep no licence of their own.
    assert "licence" not in docs["charter"]


def test_the_report_follows_chapter_five_and_its_findings_are_observations():
    snapshot = _with_records(
        observations=[
            _probe(
                12,
                "2026-11-03T12:00:00",
                **{"WSTG-v42-CLNT-07": "refuted", "WSTG-87cf4a4-CONF-12": "pass"},
            )
        ]
    )
    report = security_docs.report(snapshot)
    text = report["markdown"]
    headings = [line for line in text.splitlines() if line.startswith("#")]
    for heading in (
        "## 1. Introduction",
        "### 1.4 Limitations",
        "### 1.6 Disclaimer",
        "## 2. Executive summary",
        "## 3. Findings",
        "### 3.1 Findings summary",
        "### 3.2 Findings details",
        "## Appendices",
    ):
        assert heading in headings, heading
    assert "| F-1 | WSTG-v42-CLNT-07 |" in text
    assert (
        "WSTG-87cf4a4-CONF-12"
        not in text.split("## 3. Findings")[1].split("## Appendices")[0]
    )
    assert report["claims"] == []  # no prose was asked for
    prose = security_docs.report(snapshot, prose="All good, trust me.")
    assert prose["claims"] == [
        {"section": "executive summary prose", "role": "TechWriter"}
    ]
    assert "> **Claim.**" in prose["markdown"]
    # The prose never lands in a finding.
    findings = prose["markdown"].split("## 3. Findings")[1]
    assert "trust me" not in findings


def test_the_checklist_cites_every_observed_row_and_explains_every_other():
    snapshot = _with_records(
        observations=[
            _probe(12, "2026-11-03T12:00:00", **{"WSTG-87cf4a4-CONF-12": "pass"})
        ],
        reviews=[_review(1, "WSTG-v42-INFO-02", "pass", "2026-11-05T10:00:00")],
    )
    doc = security_docs.checklist(snapshot)
    kinds = {s["kind"] for s in doc["sources"]}
    assert {"verification", "review", "catalogue", "specification"} <= kinds
    review = next(s for s in doc["sources"] if s["kind"] == "review")
    assert review["sha256"] == "a" * 64 and "Manager" in review["label"]
    rows = [line for line in doc["markdown"].splitlines() if line.startswith("| WSTG-")]
    assert len(rows) == 117
    assert all(line.rstrip(" |").strip() for line in rows)
    assert doc["persons"]  # manual rows wait for a person


def test_an_empty_project_still_gets_both_documents():
    documents, _ = pack.build_documents(_empty())
    docs = {d["key"]: d for d in documents}
    assert docs["wstg_checklist"]["missing"]
    assert "No test has been observed failing" in docs["security_report"]["markdown"]


def test_drift_follows_the_wstg_records():
    snapshot = _with_records()
    documents, _ = pack.build_documents(snapshot)
    before = {d["key"]: d["input_sha256"] for d in documents}
    snapshot["wstg_reviews"] = [
        _review(1, "WSTG-v42-INFO-02", "pass", "2026-11-05T10:00:00")
    ]
    documents, _ = pack.build_documents(snapshot)
    after = {d["key"]: d["input_sha256"] for d in documents}
    changed = {k for k in before if before[k] != after[k]}
    assert changed == {"wstg_checklist", "security_report", "checks", "index"}


def test_the_security_check_is_measured_against_the_specifications_target():
    snapshot = _with_records(
        observations=[
            _probe(12, "2026-11-03T12:00:00", **{"WSTG-87cf4a4-CONF-12": "pass"})
        ]
    )
    from services.planning.project_docs import checks

    result = next(r for r in checks.run(snapshot) if r["id"] == "security")
    assert result["result"] == "measured" and result["domain"] == "Risk"
    assert result["observed"].startswith("1 of ")
    empty = next(r for r in checks.run(_empty()) if r["id"] == "security")
    assert empty["result"] == "not computable"


# --- tests named after a WSTG test, read from the daemon's run ----------------------


PYTEST_OUTPUT = """
tests/test_security.py::test_wstg_athz_02_viewer_cannot_delete PASSED
tests/test_security.py::test_wstg_athz_02_outsider_cannot_read FAILED
tests/test_security.py::test_wstg_inpv_05_old_id PASSED
tests/test_security.py::test_wstg_conf_06_trace_disabled PASSED
tests/test_security.py::TestWstgSess10::test_alg_none_refused PASSED
tests/test_other.py::test_unrelated PASSED
"""


def test_wstg_tests_are_read_from_the_runner_output():
    assert wstg_tests.results_from_output(PYTEST_OUTPUT) == {
        "ATHZ-02": "fail",
        "CONF-06": "pass",
    }
    evidence = {"test_run": {"command": "pytest -v", "exit_code": 1}}
    wstg_tests.record(evidence, PYTEST_OUTPUT)
    check = wstg_tests.check(evidence)
    assert check["name"] == "wstg_tests" and check["outcome"] == "pass"
    assert {i["id"]: i["status"] for i in check["items"]} == {
        "WSTG-v42-ATHZ-02": "refuted",
        "WSTG-v42-CONF-06": "pass",
    }
    # No WSTG test in the run: no check, nothing recorded.
    quiet = {"test_run": {"command": "pytest -v"}}
    wstg_tests.record(quiet, "tests/t.py::test_x PASSED\n")
    assert "wstg" not in quiet["test_run"] and wstg_tests.check(quiet) is None
    assert wstg_tests.check(None) is None


def test_a_claimed_wstg_result_is_never_read():
    # What a producer says about its own run is not test_run output.
    evidence = {"claims": {"wstg": {"ATHZ-02": "pass"}}, "test_run": {}}
    assert wstg_tests.check(evidence) is None
