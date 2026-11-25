"""La Fabrique's own rules, given to its agents as company procedures.

The factory's standards are the same for every project; what is particular
to one repository goes in its company's procedures, which reach each prompt
by role and which the board measures (``services/learning/procedures.py``:
kept, or reverted when rework or blocked tasks got worse). The harness
writes these for the self-hosting company in its ``company`` step, so a new
company made for another checkout name has them too.

The first rule comes from self-hosting runs 6 to 8: every acceptance task's
first delivery added a backend test file without its entry in
``docs/architecture/contexts.json``, the architecture check (run on every
delivery) failed, and each task spent a rework learning it.
"""

from __future__ import annotations

from typing import Any

_REGISTER_TESTS = {
    "title": "Register every new backend test file in contexts.json",
    "body": (
        "scripts/check_architecture.py runs on every delivery and fails when a test "
        "file under backend/tests has no context. When you add a test file there, "
        "deliver in the same change its entry under \"tests\" -> \"files\" in "
        "docs/architecture/contexts.json, naming the contexts it exercises: start "
        "from `python3 scripts/affected_tests.py --suggest backend/tests/<file>`, "
        "and prefer the contexts its sibling tests of the same module use. Keep the "
        "entries in alphabetical order. A test file delivered without its entry is "
        "sent back."
    ),
}
# Run 9, task 57: the suggestion named ``evidence`` for an acceptance test of
# the task views, which made the file a protected path (CODEOWNERS and the
# shipped protected list, both reviewed by a person) and failed three tests.
_EVIDENCE_CONTEXT = {
    "title": "Name the evidence context only for a test that imports it",
    "body": (
        "A test file whose entry in docs/architecture/contexts.json names the "
        "\"evidence\" context becomes a protected path: it then needs a CODEOWNERS "
        "line and a regenerated backend/app/services/evidence/protected_list.json, "
        "which a person reviews, and tests/test_protected_paths.py fails without "
        "them. Name \"evidence\" only when the test imports services.evidence; "
        "otherwise drop it from what scripts/affected_tests.py --suggest proposes "
        "and keep the contexts the test really exercises (its sibling tests' ones)."
    ),
}
# The roles that write test files: QA writes the acceptance tests, the
# Developer the tests beside an implementation.
PROCEDURES = [
    {"role": role, **rule}
    for rule in (_REGISTER_TESTS, _EVIDENCE_CONTEXT)
    for role in ("QA", "Developer")
]


def ensure(api: Any, company_id: int) -> list[dict[str, Any]]:
    """Write each procedure the company lacks (a person's procedure is adopted
    at once); one already active or proposed (409) is left as it is."""
    seen = []
    for rule in PROCEDURES:
        try:
            created = api.post("/v1/procedures", {"company_id": company_id, **rule})
            seen.append({"role": rule["role"], "title": rule["title"], "id": created.get("id"), "created": True})
        except Exception as exc:  # noqa: BLE001 - only a 409 is expected; anything else is raised
            if getattr(exc, "status", None) != 409:
                raise
            seen.append({"role": rule["role"], "title": rule["title"], "id": None, "created": False})
    return seen
