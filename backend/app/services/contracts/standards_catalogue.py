"""The quality standards' catalogue (``services/evidence/standards.py`` reads it).

A fixed list by discipline that no model, request or procedure edits; kept
apart from the checks so each file stays readable.
"""

from __future__ import annotations

from typing import Any

ALL = "*"
PO, ARCHITECT, DEVELOPER, QA, SCRUM_MASTER = (
    "PO",
    "Architect",
    "Developer",
    "QA",
    "ScrumMaster",
)
BACKEND, FRONTEND, DEVOPS, DESIGNER, DATA = (
    "BackendDeveloper",
    "FrontendDeveloper",
    "DevOps",
    "Designer",
    "DataEngineer",
)
# The specialised coding roles are developers: every standard that binds the
# Developer binds them too, beside the ones of their own discipline. Nothing
# a role was held to is ever taken away.
DISCIPLINES: dict[str, tuple[str, ...]] = {
    BACKEND: (DEVELOPER,),
    FRONTEND: (DEVELOPER,),
    DEVOPS: (DEVELOPER,),
    DATA: (DEVELOPER,),
}


# Each entry: id, area, roles, title, rule, checked (how the factory checks
# it without a model, or None when the rule lives in the prompt alone).
STANDARDS: list[dict[str, Any]] = [
    # --- architecture -------------------------------------------------------
    {
        "id": "STD-ARCH-1",
        "area": "architecture",
        "roles": [ARCHITECT, DEVELOPER],
        "title": "Every decision is a recorded ADR",
        "rule": (
            "A decision a developer would otherwise take alone (storage, framework, "
            "boundaries, error handling, authentication) is written as an ADR with "
            "its context, the decision and its consequences, before code depends on it."
        ),
        "checked": "the architecture check warns when no decision is recorded",
    },
    {
        "id": "STD-ARCH-2",
        "area": "architecture",
        "roles": [ARCHITECT, DEVELOPER],
        "title": "Modules with one responsibility and explicit interfaces",
        "rule": (
            "Each module is responsible for one thing and says what it never does; "
            "modules talk through named interfaces with a contract a test can hold "
            "them to; no dependency runs in a circle."
        ),
        "checked": "the architecture check blocks a dependency cycle and a module without a responsibility",
    },
    {
        "id": "STD-ARCH-3",
        "area": "architecture",
        "roles": [DEVELOPER],
        "title": "Work inside the module boundaries",
        "rule": (
            "A task changes the module its requirements map to, and its tests and "
            "documentation; a change to another module or to an interface is a "
            "decision for the Architect, raised as a question, not made in passing."
        ),
        "checked": None,
    },
    # --- development --------------------------------------------------------
    {
        "id": "STD-DEV-1",
        "area": "development",
        "roles": [DEVELOPER],
        "title": "No code without a test",
        "rule": (
            "Every code file delivered comes with a test that exercises it, in the "
            "project's test framework, deterministic and runnable in CI; a bug fix "
            "comes with the test that would have caught it."
        ),
        "checked": "a delivery with a code file and no test file is a finding",
    },
    {
        "id": "STD-DEV-2",
        "area": "development",
        "roles": [DEVELOPER, QA],
        "title": "Nothing half done",
        "rule": (
            "A deliverable has no TODO, FIXME or placeholder left in it; what cannot "
            "be done in the task is said in the summary and raised as a question."
        ),
        "checked": "a TODO, FIXME, XXX or HACK left in a delivered file is a finding",
    },
    {
        "id": "STD-DEV-3",
        "area": "development",
        "roles": [DEVELOPER],
        "title": "Small, named, typed",
        "rule": (
            "Identifiers in English, functions that fit on a screen, types where the "
            "language has them, errors handled where they can be and reported with "
            "what to do; no dead code, no commented-out code."
        ),
        "checked": None,
    },
    # --- QA -----------------------------------------------------------------
    {
        "id": "STD-QA-1",
        "area": "qa",
        "roles": [QA, DEVELOPER],
        "title": "One acceptance test per criterion, written first",
        "rule": (
            "Every acceptance criterion of the specification has a test named after "
            "its id, written before the implementation and failing until it exists; "
            "the criterion is done when that test passes in a run the factory observed."
        ),
        "checked": "the criteria_tests check, and acceptance_tests_untouched when a test is rewritten",
    },
    {
        "id": "STD-QA-2",
        "area": "qa",
        "roles": [QA],
        "title": "Review against the criteria, not the prose",
        "rule": (
            "QA judges a deliverable against the acceptance criteria it covers, the "
            "architecture and these standards; an approval names what was checked, "
            "a rework request names what is missing and where."
        ),
        "checked": None,
    },
    {
        "id": "STD-QA-3",
        "area": "qa",
        "roles": [QA, DEVELOPER],
        "title": "Tests are deterministic and isolated",
        "rule": (
            "A test does not depend on the clock, the network, the order of other "
            "tests or shared mutable state; a flaky test is a bug, never retried into "
            "green."
        ),
        "checked": None,
    },
    # --- security -----------------------------------------------------------
    {
        "id": "STD-SEC-1",
        "area": "security",
        "roles": [ALL],
        "title": "No secret in the repository, ever",
        "rule": (
            "Keys, tokens, passwords and private keys live in the environment or a "
            "secret store, never in a file the repository tracks; configuration reads "
            "them by name."
        ),
        "checked": "a secret-looking value in a delivered file refutes the delivery",
    },
    {
        "id": "STD-SEC-2",
        "area": "security",
        "roles": [DEVELOPER, QA],
        "title": "Validate at the boundary, least privilege inside",
        "rule": (
            "Every input from outside (a request, a file, an environment variable, a "
            "model's answer) is validated and typed at the boundary; a component "
            "gets the permissions it needs and no more; authorization is checked on "
            "every route against the owner of the resource."
        ),
        "checked": None,
    },
    {
        "id": "STD-SEC-3",
        "area": "security",
        "roles": [DEVELOPER],
        "title": "Dependencies pinned and reviewed",
        "rule": (
            "A dependency is added with a pinned version and a lock file, from a "
            "registry the project already uses; a change to authentication, secrets, "
            "payments or the pipeline waits for a person."
        ),
        "checked": "an unpinned dependency in a manifest is a finding; the risk tier holds a high-risk delivery for a person",
    },
    # --- evidence and audit -------------------------------------------------
    {
        "id": "STD-AUD-1",
        "area": "audit",
        "roles": [ALL],
        "title": "Claims and observations never share a field",
        "rule": (
            "What an agent says it did is a claim; what the daemon, the tests and "
            "the checks saw is an observation. A summary lists the files written "
            "and the tests run as claims, so the factory can hold them to what it "
            "observed."
        ),
        "checked": "every delivery is verified against the daemon's report; the audit chain links every row",
    },
    {
        "id": "STD-AUD-2",
        "area": "audit",
        "roles": [SCRUM_MASTER, QA],
        "title": "Decisions are recorded with their reason",
        "rule": (
            "An approval, a rework request, a triage, a procedure adopted or "
            "reverted carries who decided and why, in a fixed format a parser "
            "checks; a decision without a reason is not one."
        ),
        "checked": "the history check refuses a task done without an approval or a person",
    },
    # --- documentation ------------------------------------------------------
    {
        "id": "STD-DOC-1",
        "area": "documentation",
        "roles": [DEVELOPER, ARCHITECT],
        "title": "A change to behaviour documents itself",
        "rule": (
            "A new or changed public interface, command, configuration variable or "
            "user-facing behaviour updates the documentation in the same delivery "
            "(README, the API reference, the changelog); the architecture document "
            "follows the approved architecture."
        ),
        "checked": "the approved architecture is written to docs/ARCHITECTURE.md with the daemon's hash",
    },
    {
        "id": "STD-DOC-2",
        "area": "documentation",
        "roles": [DEVELOPER],
        "title": "Code explains why, not what",
        "rule": (
            "A module starts with what it is for and what it never does; a comment "
            "says why something is done the way it is, never restates the code."
        ),
        "checked": None,
    },
    # --- operations ---------------------------------------------------------
    {
        "id": "STD-OPS-1",
        "area": "operations",
        "roles": [DEVELOPER, ARCHITECT, DEVOPS],
        "title": "Configured by environment, observable by default",
        "rule": (
            "Configuration comes from the environment with safe defaults; a service "
            "exposes a health check, logs in a structured form with no secret in it, "
            "and fails loudly rather than silently."
        ),
        "checked": None,
    },
    {
        "id": "STD-OPS-2",
        "area": "operations",
        "roles": [DEVOPS, ARCHITECT],
        "title": "Pipelines and infrastructure are reviewed code",
        "rule": (
            "CI pipelines, container files and infrastructure are code in the "
            "repository: pinned images and actions, least-privilege credentials "
            "taken from the environment, every step reproducible locally, and a "
            "rollback written down before the change ships."
        ),
        "checked": "the risk tier holds a CI pipeline change for a person (high) and marks container files medium",
    },
    # --- data ---------------------------------------------------------------
    {
        "id": "STD-DATA-1",
        "area": "data",
        "roles": [DATA, BACKEND, ARCHITECT],
        "title": "Schema changes are migrations, reversible and tested",
        "rule": (
            "A change to a schema is a versioned migration with its downgrade, "
            "written so it runs on a table that already holds rows (a default or "
            "a backfill for a required column), with a test that applies it; data "
            "is never changed by hand outside a migration."
        ),
        "checked": "the risk tier marks a migration medium",
    },
    # --- ux -----------------------------------------------------------------
    {
        "id": "STD-UX-1",
        "area": "ux",
        "roles": [DESIGNER, FRONTEND],
        "title": "Accessible by default",
        "rule": (
            "Every screen meets WCAG 2.2 AA: it works with the keyboard alone, "
            "names every control for a screen reader, keeps text contrast at 4.5:1 "
            "or more, never says something with colour alone, and holds at a "
            "phone's width; every user-facing string is translatable."
        ),
        "checked": None,
    },
    # --- product ------------------------------------------------------------
    {
        "id": "STD-PO-1",
        "area": "product",
        "roles": [PO],
        "title": "A specification names its quality attributes with a number",
        "rule": (
            "Beside the requirements, a specification says what it expects of "
            "security, performance, reliability and accessibility, each with a "
            "target a test can measure; a promise without a number is not a target."
        ),
        "checked": "the specification check warns on a quality area left out and on a target without a number",
    },
]
