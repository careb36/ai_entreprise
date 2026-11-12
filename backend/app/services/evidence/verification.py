import re
from typing import Any

from models.agent_run import AgentRun, Verification
from sqlmodel import Session

from services.contracts.manifest import parse_file_manifest
from services.contracts.sandbox_report import check_sandbox
from services.contracts.tool_calls import summarize as summarize_tool_calls
from services.contracts.verdicts import (
    VERDICT_REFUTED,
    VERDICT_UNVERIFIABLE,
    VERDICT_VERIFIED,
)
from services.evidence import (
    app_check,
    context_read,
    criteria_tests,
    grounding,
    risk,
    secops_checks,
    sources,
    standards,
)
from services.evidence.console_run import check_console_run
from services.platform.agent_service import AgentService
from services.platform.orchestration_metrics import ACTION_AGENT_RUN_VERIFIED

_PATH_PATTERN = re.compile(
    # Longest extension first, and no continuation after it, so `a.tsx` is not
    # read as `a.ts` followed by a stray `x`.
    r"[A-Za-z0-9_./-]+\.(?:json|toml|lock|html|yaml|yml|tsx|jsx|py|ts|js|md|sql|css|txt)"
    r"(?![A-Za-z0-9_])"
)
_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
_TEST_PATTERNS = (
    re.compile(r"\b\d+\s+(?:tests?\s+)?pass(?:ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\ball tests pass\b", re.IGNORECASE),
    re.compile(
        r"\b(build|ci)\s+(?:is\s+)?(green|passing|passes|succeeds)\b", re.IGNORECASE
    ),
    re.compile(r"\bpytest\b.*\bpassed\b", re.IGNORECASE),
    re.compile(r"\bnpm (?:run )?test\b", re.IGNORECASE),
)


def _claim_strings(claims: Any):
    if isinstance(claims, dict):
        for value in claims.values():
            yield from _claim_strings(value)
    elif isinstance(claims, list):
        for value in claims:
            yield from _claim_strings(value)
    elif isinstance(claims, str):
        yield claims


def check_artifact_nonempty(run: AgentRun) -> dict:
    if run.artifact_size == 0:
        if run.claims:
            return {
                "name": "artifact_nonempty",
                "outcome": VERDICT_REFUTED,
                "observation": "claims present but artifact is empty (0 bytes)",
            }
        return {
            "name": "artifact_nonempty",
            "outcome": "pass",
            "observation": "no claims and no artifact",
        }
    return {
        "name": "artifact_nonempty",
        "outcome": "pass",
        "observation": f"artifact contains {run.artifact_size} bytes",
    }


def extract_claimed_paths(claims: dict | None) -> list[str]:
    paths = []
    seen = set()
    for text in _claim_strings(claims):
        without_urls = _URL_PATTERN.sub("", text)
        for match in _PATH_PATTERN.finditer(without_urls):
            path = match.group(0)
            if "/" in path and path not in seen:
                seen.add(path)
                paths.append(path)
    return paths


def check_claimed_files_present(run: AgentRun, evidence: dict | None = None) -> dict:
    # A file the workspace edit tool changed comes from the tool's record (an
    # observation, ``services/workspace_edits.py``), not from the answer's text.
    edited = context_read.files_edited((evidence or {}).get("tool_calls"))
    paths = [p for p in extract_claimed_paths(run.claims) if p not in edited]
    if not paths:
        return {
            "name": "claimed_files_present",
            "outcome": "pass",
            "observation": "no file paths claimed",
        }

    artifact = run.artifact_text or ""
    missing = [path for path in paths if path not in artifact]
    if missing:
        return {
            "name": "claimed_files_present",
            "outcome": VERDICT_REFUTED,
            "observation": f"claimed {', '.join(missing)}; absent from artifact",
        }
    return {
        "name": "claimed_files_present",
        "outcome": "pass",
        "observation": f"all {len(paths)} claimed file paths appear in artifact",
    }


def extract_test_claims(claims: dict | None) -> list[str]:
    matches = []
    seen = set()
    for text in _claim_strings(claims):
        if any(pattern.search(text) for pattern in _TEST_PATTERNS) and text not in seen:
            seen.add(text)
            matches.append(text)
    return matches


def check_claimed_tests_evidenced(run: AgentRun, evidence: dict | None) -> dict:
    test_claims = extract_test_claims(run.claims)
    if not test_claims:
        return {
            "name": "claimed_tests_evidenced",
            "outcome": "pass",
            "observation": "no test or build claims made",
        }

    if not isinstance(evidence, dict) or "ci" not in evidence:
        return {
            "name": "claimed_tests_evidenced",
            "outcome": VERDICT_UNVERIFIABLE,
            "observation": (
                f"agent claims {test_claims[0]!r}; no CI evidence supplied"
            ),
        }

    ci = evidence.get("ci")
    if not isinstance(ci, dict):
        return {
            "name": "claimed_tests_evidenced",
            "outcome": VERDICT_UNVERIFIABLE,
            "observation": (
                f"agent claims {test_claims[0]!r}; CI evidence has no conclusion "
                "(run not finished or malformed)"
            ),
        }

    conclusion = ci.get("conclusion")
    source = ci.get("source")
    source = source if isinstance(source, str) and source else "CI"
    ref = ci.get("ref")
    ref = ref if isinstance(ref, str) and ref else "unknown ref"
    if conclusion is None:
        return {
            "name": "claimed_tests_evidenced",
            "outcome": VERDICT_UNVERIFIABLE,
            "observation": (
                f"agent claims {test_claims[0]!r}; CI evidence has no conclusion "
                "(run not finished or malformed)"
            ),
        }
    if conclusion == "success":
        return {
            "name": "claimed_tests_evidenced",
            "outcome": "pass",
            "observation": (
                f"independent evidence from {source} reports {conclusion} ({ref})"
            ),
        }
    if isinstance(conclusion, str) and conclusion in {"failure", "timed_out"}:
        return {
            "name": "claimed_tests_evidenced",
            "outcome": VERDICT_REFUTED,
            "observation": (
                f"agent claims {test_claims[0]!r}; {source} reports "
                f"{conclusion} ({ref})"
            ),
        }
    if not isinstance(conclusion, str):
        return {
            "name": "claimed_tests_evidenced",
            "outcome": VERDICT_UNVERIFIABLE,
            "observation": (
                f"agent claims {test_claims[0]!r}; {source} reports a malformed "
                f"conclusion ({ref}), which neither confirms nor refutes the claim"
            ),
        }
    return {
        "name": "claimed_tests_evidenced",
        "outcome": VERDICT_UNVERIFIABLE,
        "observation": (
            f"agent claims {test_claims[0]!r}; {source} reports {conclusion} "
            f"({ref}), which neither confirms nor refutes the claim"
        ),
    }


def evaluate_workspace_writes(
    claimed_paths: list[str], evidence: dict | None
) -> tuple[str, str]:
    """
    Compare the files an agent declared with what the workspace daemon observed.

    ``evidence["expected"]`` holds the sha256 the backend computed from the
    content it sent; ``evidence["workspace_writes"]`` is the daemon's per-file
    report, with the sha256 of the bytes it read back from disk.  Neither comes
    from the agent.  Returns ``(outcome, observation)``.
    """
    if not claimed_paths:
        return "pass", "no files declared"
    evidence = evidence or {}
    writes = evidence.get("workspace_writes")
    if writes is None:
        reason = evidence.get("reason") or "no report from the workspace daemon"
        return VERDICT_UNVERIFIABLE, f"{', '.join(claimed_paths)}: {reason}"

    expected = evidence.get("expected") or {}
    by_path = {w.get("path"): w for w in writes if isinstance(w, dict)}
    problems = []
    for path in claimed_paths:
        write = by_path.get(path)
        if write is None:
            problems.append(f"{path} not reported")
        elif write.get("status") != "written":
            reason = f": {write['error']}" if write.get("error") else ""
            problems.append(f"{path} {write.get('status') or 'not written'}{reason}")
        elif path in expected and write.get("sha256") != expected[path]:
            problems.append(f"{path} content on disk differs from what was sent")
    if problems:
        return VERDICT_REFUTED, "; ".join(problems)
    return "pass", f"{len(claimed_paths)} file(s) written: {', '.join(claimed_paths)}"


def check_files_written(run: AgentRun, evidence: dict | None) -> dict:
    claimed = (run.claims or {}).get("files") or []
    outcome, observation = evaluate_workspace_writes(
        [path for path in claimed if isinstance(path, str)], evidence
    )
    return {"name": "files_written", "outcome": outcome, "observation": observation}


def check_tests_run(evidence: dict) -> dict:
    """
    The project's tests as run by the workspace daemon after delivery.

    Exit 0 passes, any other exit code refutes. A command that hung refutes too,
    and says so: it was asked to finish and did not. Only a run that never
    happened (no test command, daemon silent, command error) is unverifiable —
    the code was not shown to work, which is different from it failing.

    When the daemon had to cut the output, the observation says it, so a
    truncated tail is never read as the whole story.
    """
    run = evidence.get("test_run") or {}
    command = run.get("command")
    target = f"`{command}`" if command else "tests"
    truncated = ""
    if run.get("output_truncated"):
        total = run.get("output_bytes")
        limit = run.get("output_limit")
        truncated = (
            f" [output truncated to the last {limit} bytes of {total}]"
            if isinstance(total, int) and isinstance(limit, int)
            else " [output truncated]"
        )
    if run.get("timed_out"):
        timeout = run.get("timeout_seconds")
        if isinstance(timeout, (int, float)) and not isinstance(timeout, bool):
            return {
                "name": "tests_run",
                "outcome": VERDICT_REFUTED,
                "observation": (
                    f"{target} did not finish within {timeout:g}s "
                    f"(timed out, killed){truncated}"
                ),
            }
        return {
            "name": "tests_run",
            "outcome": VERDICT_REFUTED,
            "observation": f"{target} timed out and was killed{truncated}",
        }
    exit_code = run.get("exit_code")
    if isinstance(exit_code, int) and not isinstance(exit_code, bool):
        if exit_code == 0:
            return {
                "name": "tests_run",
                "outcome": "pass",
                "observation": f"{target} passed{truncated}",
            }
        all_excused, excused = criteria_tests.excused_failures(evidence)
        if all_excused:
            # An acceptance test fails before its implementation, by design;
            # only those failed here (``services.evidence.acceptance``).
            return {
                "name": "tests_run",
                "outcome": "pass",
                "observation": (
                    f"{target} exited {exit_code}: only the acceptance tests of "
                    f"criteria not implemented yet failed ({', '.join(excused)})"
                    f"{truncated}"
                ),
            }
        tail = (run.get("output_tail") or "").strip()[-400:]
        return {
            "name": "tests_run",
            "outcome": VERDICT_REFUTED,
            "observation": f"{target} exited {exit_code}"
            + (f": {tail}" if tail else "")
            + truncated,
        }
    reason = run.get("reason") or "no exit status reported"
    return {
        "name": "tests_run",
        "outcome": VERDICT_UNVERIFIABLE,
        "observation": f"{target} not run: {reason}",
    }


def check_risk_tier(run: AgentRun, evidence: dict | None) -> dict:
    """
    How much the delivery needs a person (``services/risk.py``).

    A classification, not a verdict: its outcome is always ``pass`` and the
    tier travels in ``tier`` and ``reasons``. The paths are the ones the
    daemon reports written plus the ones the producer declared, which can
    only raise the tier.
    """
    claims = run.claims or {}
    writes = (evidence or {}).get("workspace_writes") or []
    written = [
        w.get("path")
        for w in writes
        if isinstance(w, dict) and w.get("status") == "written"
    ]
    declared = [p for p in claims.get("files") or [] if isinstance(p, str)]
    result = risk.classify(
        [*written, *declared, *extract_claimed_paths(claims)], claims.get("risk")
    )
    result = risk.raise_for_tool_calls(result, (evidence or {}).get("tool_calls"))
    result = risk.raise_for_unread_changes(
        result, context_read.check_context_read(evidence)
    )
    observation = result["tier"]
    if result["reasons"]:
        observation += " — " + "; ".join(result["reasons"])
    return {
        "name": "risk_tier",
        "outcome": "pass",
        "observation": observation,
        "tier": result["tier"],
        "reasons": result["reasons"],
    }


def check_tools_used(evidence: dict) -> dict:
    """
    The MCP tool calls the backend made for the producer (``mcp_tools``).

    A record, not a verdict: the outcome is always ``pass``. Each call in
    ``evidence["tool_calls"]`` names the server and tool, its arguments,
    whether it erred and the sha256 of what came back; the producer's account
    of what the tools told it stays in its claims.
    """
    calls = [c for c in evidence.get("tool_calls") or [] if isinstance(c, dict)]
    return {
        "name": "tools_used",
        "outcome": "pass",
        "observation": summarize_tool_calls(calls),
        "calls": calls,
    }


def aggregate_verdict(checks: list[dict]) -> str:
    outcomes = {check["outcome"] for check in checks}
    if VERDICT_REFUTED in outcomes:
        return VERDICT_REFUTED
    if VERDICT_UNVERIFIABLE in outcomes:
        return VERDICT_UNVERIFIABLE
    return VERDICT_VERIFIED


def _declared_files(artifact: str | None) -> list[dict]:
    return parse_file_manifest(artifact or "")


def verify_agent_run(
    session: Session, run: AgentRun, evidence: dict | None
) -> Verification:
    checks = [
        check_artifact_nonempty(run),
        check_claimed_files_present(run, evidence),
        check_claimed_tests_evidenced(run, evidence),
    ]
    # A claim that files were written is only settleable against a daemon
    # report. Running the check on the claim alone (not on the presence of the
    # evidence key) means "no report" reads as unverifiable instead of as
    # "nothing to check" — a caller that posts evidence without the daemon's
    # per-file results must not get a verified run out of the omission.
    claimed_files = (run.claims or {}).get("files") or []
    delivered = _declared_files(run.artifact_text) + context_read.edited_texts(evidence)
    if claimed_files or (evidence and "workspace_writes" in evidence):
        checks.append(check_files_written(run, evidence))
    if evidence and "test_run" in evidence:
        checks.append(check_tests_run(evidence))
        # Where the tests ran, as the daemon reported it (contracts/sandbox_report.py);
        # a record, never the verdict.
        sandbox_check = check_sandbox(evidence)
        if sandbox_check:
            checks.append(sandbox_check)
    # The dependency install, the application started and probed, and its
    # e2e run (``services/app_check.py``), as the daemon reported them.
    checks.extend(app_check.checks(evidence))
    if evidence and evidence.get("acceptance"):
        # The acceptance tests per criterion (``services.evidence.acceptance``): what the
        # observed run said of this task's criteria, and that an implementation
        # task left the test files alone.
        for check in (
            criteria_tests.check_criteria(evidence),
            criteria_tests.check_untouched(
                [p for p in claimed_files if isinstance(p, str)], evidence
            ),
        ):
            if check:
                checks.append(check)
    if evidence and "tool_calls" in evidence:
        checks.append(check_tools_used(evidence))
        # Which of the files the delivery modified the Developer read first
        # (``services/context_read.py``); a record, never the verdict.
        context = context_read.check_context_read(evidence)
        if context:
            checks.append(context)
        # The sources the run consulted (``evidence/sources.py``) and
        # the dependencies it named without a registry lookup; a record.
        consulted = sources.check_sources(evidence, delivered)
        if consulted:
            checks.append(consulted)
    # Do the packages, versions and imports the delivery names exist?
    # (``services/evidence/grounding.py``): a package no registry has, a
    # version that does not exist or an import that names no observed file
    # refutes; the rest are findings.
    grounded_check = grounding.check_grounded(evidence, delivered)
    if grounded_check:
        checks.append(grounded_check)
    # A console producer's run (``services/producers/console_producer.py``): who ran,
    # how it ended and what the daemon saw it change (``evidence/console_run.py``).
    console_check = check_console_run(evidence)
    if console_check:
        checks.append(console_check)
    # The factory's standards over the files the deliverable declares
    # (``services.evidence.standards``): a secret refutes, the rest is for the reviewer.
    # The edit tool's files too: what the delivery wrote for them (an observation).
    standards_check = standards.check_delivery(
        run.artifact_text, context_read.edited_texts(evidence)
    )
    if standards_check:
        checks.append(standards_check)
    # SecOps scans (``services/secops_checks``): dependency audit, secret scan,
    # SAST. Only the ones that reported a result become checks.
    if evidence and any(
        k in evidence for k in ("dependency_audit", "secret_scan", "sast")
    ):
        checks.extend(secops_checks.check_all(evidence))
    checks.append(check_risk_tier(run, evidence))
    verdict = aggregate_verdict(checks)
    verification = Verification(
        company_id=run.company_id,
        agent_run_id=run.id,
        verdict=verdict,
        checks=checks,
        evidence=evidence,
    )
    session.add(verification)
    session.flush()

    audit_log = AgentService().audit_interaction(
        session=session,
        company_id=run.company_id,
        interaction_id=f"agent-run:{run.external_ref}",
        agent_id="verifier",
        action=ACTION_AGENT_RUN_VERIFIED,
        details={
            "company_id": str(run.company_id),
            "agent_run_id": run.id,
            "version": run.version,
        },
        result={"verdict": verdict, "checks": checks},
        decision_signature=None,
        cost_tokens=0,
    )
    verification.audit_log_id = audit_log.id
    run.status = verdict
    session.add(verification)
    session.add(run)
    session.commit()
    session.refresh(verification)
    return verification
