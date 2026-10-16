import re
from typing import Any, Optional

from sqlmodel import Session

from models.agent_run import AgentRun, Verification
from services.agent_service import AgentService
from services.orchestration_metrics import (
    ACTION_AGENT_RUN_VERIFIED,
    VERDICT_REFUTED,
    VERDICT_UNVERIFIABLE,
    VERDICT_VERIFIED,
)

_PATH_PATTERN = re.compile(
    r"[A-Za-z0-9_./-]+\.(?:py|ts|tsx|js|jsx|json|ya?ml|md|toml|lock|txt|sql|css|html)"
)
_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
_TEST_PATTERNS = (
    re.compile(r"\b\d+\s+(?:tests?\s+)?pass(?:ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\ball tests pass\b", re.IGNORECASE),
    re.compile(r"\b(build|ci)\s+(?:is\s+)?(green|passing|passes|succeeds)\b", re.IGNORECASE),
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


def extract_claimed_paths(claims: Optional[dict]) -> list[str]:
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


def check_claimed_files_present(run: AgentRun) -> dict:
    paths = extract_claimed_paths(run.claims)
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


def extract_test_claims(claims: Optional[dict]) -> list[str]:
    matches = []
    seen = set()
    for text in _claim_strings(claims):
        if any(pattern.search(text) for pattern in _TEST_PATTERNS) and text not in seen:
            seen.add(text)
            matches.append(text)
    return matches


def check_claimed_tests_evidenced(
    run: AgentRun, evidence: Optional[dict]
) -> dict:
    test_claims = extract_test_claims(run.claims)
    if not test_claims:
        return {
            "name": "claimed_tests_evidenced",
            "outcome": "pass",
            "observation": "no test or build claims made",
        }

    if evidence is None or "ci" not in evidence:
        return {
            "name": "claimed_tests_evidenced",
            "outcome": VERDICT_UNVERIFIABLE,
            "observation": (
                f"agent claims {test_claims[0]!r}; no independent evidence supplied"
            ),
        }

    ci = evidence.get("ci")
    ci = ci if isinstance(ci, dict) else {}
    conclusion = ci.get("conclusion")
    source = ci.get("source")
    ref = ci.get("ref")
    if conclusion == "success":
        return {
            "name": "claimed_tests_evidenced",
            "outcome": "pass",
            "observation": (
                f"independent evidence from {source} reports {conclusion} ({ref})"
            ),
        }
    return {
        "name": "claimed_tests_evidenced",
        "outcome": VERDICT_REFUTED,
        "observation": (
            f"agent claims {test_claims[0]!r}; {source} reports {conclusion} ({ref})"
        ),
    }


def aggregate_verdict(checks: list[dict]) -> str:
    outcomes = {check["outcome"] for check in checks}
    if VERDICT_REFUTED in outcomes:
        return VERDICT_REFUTED
    if VERDICT_UNVERIFIABLE in outcomes:
        return VERDICT_UNVERIFIABLE
    return VERDICT_VERIFIED


def verify_agent_run(
    session: Session, run: AgentRun, evidence: Optional[dict]
) -> Verification:
    checks = [
        check_artifact_nonempty(run),
        check_claimed_files_present(run),
        check_claimed_tests_evidenced(run, evidence),
    ]
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
