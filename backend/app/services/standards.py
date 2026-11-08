"""The quality standards every project of the factory is held to.

A fixed catalogue, by discipline (architecture, development, QA, security,
evidence and audit, documentation, operations), that no model and no request
edits: each standard has an id (``STD-SEC-1``), the roles it binds, the rule
in one paragraph, and whether the factory checks it deterministically. The
standards go into every prompt by role (``prompt_section``): the PO's draft,
the Architect's design, each task, QA's review. Where a standard can be
checked without a model, it is: ``check_delivery`` reads the files a
deliverable declares and records a ``standards`` check for the Verifier
(a secret in a file refutes the delivery; a code file delivered without a
test, a dependency left unpinned, a ``TODO`` left behind are findings a
reviewer sees), ``missing_quality_areas`` tells the specification check
which quality attributes a specification forgot, and the checks the loop
already runs (the risk tier, the acceptance tests, the architecture's
cycles, the audit chain) are listed as such. A company's procedures add to
the standards; they never replace them.
"""

from __future__ import annotations

import re
from typing import Any

from services import edit_blocks

ALL = "*"
PO, ARCHITECT, DEVELOPER, QA, SCRUM_MASTER = (
    "PO",
    "Architect",
    "Developer",
    "QA",
    "ScrumMaster",
)
CHECK_NAME = "standards"

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
        "roles": [DEVELOPER, ARCHITECT],
        "title": "Configured by environment, observable by default",
        "rule": (
            "Configuration comes from the environment with safe defaults; a service "
            "exposes a health check, logs in a structured form with no secret in it, "
            "and fails loudly rather than silently."
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

AREAS = tuple(dict.fromkeys(s["area"] for s in STANDARDS))

# The quality areas a specification is expected to say something about.
QUALITY_AREAS: dict[str, tuple[str, ...]] = {
    "security": ("secur", "auth", "privacy", "encrypt"),
    "performance": ("perform", "latenc", "respon", "throughput", "fast", "speed"),
    "reliability": ("reliab", "availab", "uptime", "resilien", "recover", "durab"),
    "accessibility": ("accessib", "wcag", "a11y", "screen reader"),
}


def catalogue() -> list[dict[str, Any]]:
    """The standards as the API serves them."""
    return [dict(s) for s in STANDARDS]


def for_role(role: str | None) -> list[dict[str, Any]]:
    """The standards that bind *role* (every role gets the ones for all)."""
    return [s for s in STANDARDS if ALL in s["roles"] or (role and role in s["roles"])]


def prompt_section(role: str | None, heading: str = "Quality standards") -> str:
    """The standards for *role* as a prompt section; never empty (``STD-SEC-1``
    and ``STD-AUD-1`` bind every role)."""
    lines = [
        f"\n\n## {heading}",
        (
            "The factory holds every project to these standards; they bind you "
            "before any task-specific instruction, and a company procedure adds "
            "to them, never relaxes them. The ones marked [checked] are verified "
            "without a model."
        ),
        "",
    ]
    for s in for_role(role):
        mark = " [checked]" if s["checked"] else ""
        lines.append(f"- {s['id']} {s['title']}{mark}: {s['rule']}")
    return "\n".join(lines)


def missing_quality_areas(spec: dict[str, Any]) -> list[str]:
    """The quality areas (``QUALITY_AREAS``) no quality attribute of *spec* names."""
    text = " ".join(
        f"{q.get('attribute') or ''} {q.get('target') or ''}"
        for q in spec.get("quality") or []
        if isinstance(q, dict)
    ).lower()
    return [
        area
        for area, words in QUALITY_AREAS.items()
        if not any(w in text for w in words)
    ]


# ---------------------------------------------------------------------------
# What a delivery is checked for
# ---------------------------------------------------------------------------

CODE_SUFFIXES = (
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".kt", ".rb",
    ".cs", ".php", ".swift", ".scala", ".c", ".cc", ".cpp", ".h",
)  # fmt: skip
_TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec|specs)(/|$)|(^|/)test_[^/]+$|[._-](test|spec)\.[a-z]+$|_test\.(go|rs|py)$|Tests?\.(java|kt|cs|swift)$"
)
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "a private key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    ),
    ("an AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("a GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("a Slack token", re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{10,}\b")),
    ("a Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("a Stripe key", re.compile(r"\b[sr]k_(?:live|test)_[0-9A-Za-z]{16,}\b")),
    ("an OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    (
        "a JWT",
        re.compile(
            r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"
        ),
    ),
    (
        "a password or secret assigned in code",
        re.compile(
            r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)\b\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"
        ),
    ),
)
_PLACEHOLDER = re.compile(
    r"(?i)^(?:x+|\*+|\.{3}|<[^>]+>|\$\{[^}]+\}|\{\{[^}]+\}\}|your[_-]|changeme|change_me|example|placeholder|dummy|test|secret|password|none|null|todo|redacted)",
)
# A marker left in a comment or a string, not an identifier (``todo_list``,
# ``is_todo``) and not a board column (``"todo"``).
_TODO = re.compile(r"(?<![\w'\"/.-])(TODO|FIXME|XXX|HACK)(?![\w'\"-])")


def _is_test(path: str) -> bool:
    return bool(_TEST_PATH.search(path))


def _is_code(path: str) -> bool:
    return path.lower().endswith(CODE_SUFFIXES) and not _is_test(path)


def _secret_value(match: re.Match[str]) -> str:
    text = match.group(0)
    quoted = re.search(r"['\"]([^'\"]+)['\"]\s*$", text)
    return quoted.group(1) if quoted else text


def secrets_in(path: str, content: str) -> list[str]:
    """What looks like a secret in *content*; placeholders and examples do not."""
    found: list[str] = []
    if path.endswith((".md", ".rst", ".txt")) and "example" in path.lower():
        return found
    lines_seen: set[int] = set()  # one finding per line, the first pattern's
    for what, pattern in _SECRET_PATTERNS:
        for match in pattern.finditer(content):
            value = _secret_value(match)
            if _PLACEHOLDER.match(value) or value.lower().startswith(
                ("os.environ", "process.env", "env(")
            ):
                continue
            line = content.count("\n", 0, match.start()) + 1
            if line in lines_seen:
                continue
            lines_seen.add(line)
            found.append(f"{what} at {path}:{line}")
            break
    return found


def todos_in(path: str, content: str) -> list[str]:
    found: list[str] = []
    for number, line in enumerate(content.splitlines(), 1):
        if _TODO.search(line):
            found.append(f"{path}:{number}")
            if len(found) >= 3:
                break
    return found


def unpinned_in(path: str, content: str) -> list[str]:
    """Dependencies declared without a version in a manifest the factory reads."""
    name = path.rsplit("/", 1)[-1]
    found: list[str] = []
    if name == "requirements.txt" or (
        name.startswith("requirements") and name.endswith(".txt")
    ):
        for line in content.splitlines():
            bare = line.split("#", 1)[0].strip()
            if not bare or bare.startswith(("-", "git+", "http")):
                continue
            if not re.search(r"[=<>~!]=|@|===", bare):
                found.append(bare.split("[", 1)[0])
    elif name == "package.json":
        import json

        try:
            data = json.loads(content)
            pairs = {}
            for section in ("dependencies", "devDependencies"):
                pairs.update(data.get(section) or {})
        except (ValueError, AttributeError):
            pairs = edit_blocks.json_dependency_pairs(content)  # An edit's fragment.
        for dep, version in pairs.items():
            v = str(version).strip()
            if v in ("", "*", "latest") or v.startswith((">", "<")):
                found.append(f"{dep}@{v or '*'}")
    return found[:10]


def check_files(files: list[dict[str, str]]) -> dict[str, Any]:
    """The ``standards`` check over the files a deliverable declares.

    Refuted on a secret (STD-SEC-1); otherwise pass, with the findings a
    reviewer should see: code without a test (STD-DEV-1), a TODO left behind
    (STD-DEV-2), an unpinned dependency (STD-SEC-3).
    """
    secrets: list[str] = []
    todos: list[str] = []
    unpinned: list[str] = []
    code = [f["path"] for f in files if _is_code(f["path"])]
    tests = [f["path"] for f in files if _is_test(f["path"])]
    for f in files:
        text = edit_blocks.scanned_text(f)
        secrets += secrets_in(f["path"], text)
        todos += todos_in(f["path"], text)
        unpinned += unpinned_in(f["path"], text)
    findings: list[dict[str, str]] = []
    if secrets:
        findings += [
            {"standard": "STD-SEC-1", "severity": "blocking", "detail": s}
            for s in secrets[:5]
        ]
    if code and not tests:
        findings.append(
            {
                "standard": "STD-DEV-1",
                "severity": "warning",
                "detail": f"code delivered without a test: {', '.join(code[:5])}",
            }
        )
    if todos:
        findings.append(
            {
                "standard": "STD-DEV-2",
                "severity": "warning",
                "detail": "left behind at " + ", ".join(todos[:5]),
            }
        )
    if unpinned:
        findings.append(
            {
                "standard": "STD-SEC-3",
                "severity": "warning",
                "detail": "unpinned: " + ", ".join(unpinned[:5]),
            }
        )
    if secrets:
        return {
            "name": CHECK_NAME,
            "outcome": "refuted",
            "observation": "STD-SEC-1: " + "; ".join(secrets[:3]),
            "findings": findings,
        }
    if not findings:
        return {
            "name": CHECK_NAME,
            "outcome": "pass",
            "observation": f"{len(files)} file(s) checked, nothing found",
            "findings": [],
        }
    return {
        "name": CHECK_NAME,
        "outcome": "pass",
        "observation": "; ".join(f"{f['standard']}: {f['detail']}" for f in findings),
        "findings": findings,
    }


def check_delivery(
    artifact: str | None, edited: list[dict[str, Any]] | None = None
) -> dict[str, Any] | None:
    """``check_files`` over the files an artifact declares and the ones the
    edit tool changed (*edited*, the daemon's read-back; a file the artifact
    declares itself keeps its block, as in the delivery); ``None`` without files."""
    from services.task_worker import parse_file_manifest

    files = parse_file_manifest(artifact or "")
    have = {f["path"] for f in files}
    files += [f for f in edited or [] if f.get("path") not in have]
    return check_files(files) if files else None
