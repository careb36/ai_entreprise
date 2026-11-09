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
from services.standards_catalogue import (  # noqa: F401 - re-exported
    ALL,
    ARCHITECT,
    BACKEND,
    DATA,
    DESIGNER,
    DEVELOPER,
    DEVOPS,
    DISCIPLINES,
    FRONTEND,
    PO,
    QA,
    SCRUM_MASTER,
    STANDARDS,
)

CHECK_NAME = "standards"

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
    """The standards that bind *role* (every role gets the ones for all; a
    specialised developer every Developer standard too, ``DISCIPLINES``)."""
    from services import roles as catalogue

    name = catalogue.canonical(role) if role else ""
    bound = {name, *DISCIPLINES.get(name, ())} - {""}
    return [s for s in STANDARDS if ALL in s["roles"] or bound & set(s["roles"])]


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
            # An optional type annotation (``password: str = "…"``, quoted
            # parts included: ``Annotated[str, "db"]``, ``"str"``) and
            # string prefix (``f"…"``) before the quoted value.
            r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)\b\s*"
            r"(?::[^=\n]{1,80}?(?==))?[:=]\s*[bfru]{0,2}['\"][^'\"\s]{8,}['\"]"
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


# The prompt boundary fails closed: a line naming a secret-looking thing,
# then ``=`` or ``:``, then anywhere later a quoted run of 8 or more
# characters without a space that is no placeholder, is treated as holding
# a secret whatever syntax sits between (``Annotated[str, "db"] = "…"``,
# ``password: "str" = "…"``, a call, a dict). Only for what the model is
# shown; a delivery is refuted by ``secrets_in`` alone.
_SECRET_NAMED = re.compile(
    r"(?i)[\w$]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential)[\w$]*['\"]?\s*[:=]"
)
_QUOTED = re.compile(r"(['\"`])((?:\\.|(?!\1).)*?)\1")
_FROM_ENVIRONMENT = re.compile(r"(?i)os\.environ|getenv|process\.env|\benv\(")


def secret_line(line: str) -> bool:
    """Whether *line* may hold a secret, failing closed (``_SECRET_NAMED``):
    for the text shown to a model, never for a verdict."""
    named = _SECRET_NAMED.search(line)
    if not named or _FROM_ENVIRONMENT.search(line):
        return False
    for match in _QUOTED.finditer(line, named.end()):
        value = match.group(2)
        if (
            len(value) >= 8
            and not re.search(r"\s", value)
            and not _PLACEHOLDER.match(value)
        ):
            return True
    return False


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
