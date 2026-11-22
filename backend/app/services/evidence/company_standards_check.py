"""Judging a company's standards from what was observed (issue #700;
``docs/archive/design/COMPANY_STANDARDS.md`` §3.1, §3.2, §5).

Each kind of the closed list (``services/contracts/company_standards.py``)
is evaluated here, once, as a pure function of observations: the paths the
daemon observed written (and those it observed added), the paths a producer
declared, the bytes the daemon read back, the baselines read at the
activated commit. No model is called, no network reached, no clock read
(rule 5), and nothing a producer says about a standard is read (rules 1
and 2).

Declared paths may add to a match (``paths``, ``when``, ``forbid_path``) and
never satisfy one (``then``). A file that could not be read back, a pattern
the RE2 binding cannot run, or a byte budget spent makes the standard
``unverifiable``, never ``pass`` (rule 3).

``command`` standards are run by the loop in the daemon's sandbox; until a
run is passed in ``commands``, their outcome is ``not_run``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from services.contracts import company_regex, protected_paths

CHECK_NAME = "company_standards"
OUTCOMES = ("pass", "fail", "skipped", "unverifiable", "not_run")
# Bytes of file content one standard reads, and one evaluation in all.
STANDARD_BUDGET = 4 * 1024 * 1024
TOTAL_BUDGET = 16 * 1024 * 1024
MAX_CITED = 20
# The kinds that read the bytes of the files they apply to.
_READS_BYTES = ("forbid_pattern", "max_lines")


@dataclass
class Observation:
    """What the platform observed of a delivery (or, for the dry run, of the
    default branch's tree as if every file had been delivered)."""

    written: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    declared: list[str] = field(default_factory=list)
    # path -> bytes read back; None when it could not be read.
    files: dict[str, bytes | None] = field(default_factory=dict)
    # baseline path -> {path: lines}; None when it could not be read.
    baselines: dict[str, dict[str, int] | None] = field(default_factory=dict)


class _Budget:
    def __init__(self, total: int):
        self.left = total

    def take(self, size: int) -> bool:
        if size > self.left:
            return False
        self.left -= size
        return True


def _hits(
    paths: Iterable[str], globs: list[str], exclude: list[str] | None = None
) -> list[str]:
    found = (
        [m.path for m in protected_paths.matches(list(paths), globs)] if globs else []
    )
    if exclude:
        out = {m.path for m in protected_paths.matches(found, exclude)}
        found = [p for p in found if p not in out]
    return found


def applies(standard: dict[str, Any], obs: Observation) -> bool:
    """Whether the delivery touched the standard's ``paths`` (none: always)."""
    globs = standard.get("paths") or []
    return not globs or bool(_hits([*obs.written, *obs.declared], globs))


def lines_of(data: bytes) -> int:
    """Pure: a file's line count, the last line counted without its newline."""
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def _entry(
    standard: dict[str, Any], outcome: str, **observation: Any
) -> dict[str, Any]:
    return {
        "id": standard.get("id"),
        "kind": standard.get("kind"),
        "severity": standard.get("severity"),
        "outcome": outcome,
        "observation": observation,
    }


def _read(obs: Observation, budget: _Budget, own: _Budget, path: str):
    """The bytes of *path*, or the reason the standard cannot be judged."""
    data = obs.files.get(path)
    if data is None:
        return None, {"reason": "unread", "path": path}
    if not own.take(len(data)) or not budget.take(len(data)):
        return None, {"reason": "budget", "path": path}
    return data, None


def _forbid_pattern(standard, obs: Observation, budget: _Budget):
    targets = _hits(obs.written, standard.get("files") or [], standard.get("except"))
    if not targets:
        return _entry(standard, "pass", files=[])
    try:
        compiled = company_regex.compile(standard.get("pattern") or "")
    except company_regex.Unavailable:
        return _entry(standard, "unverifiable", reason=company_regex.UNAVAILABLE)
    except ValueError as exc:
        return _entry(
            standard, "unverifiable", reason="pattern_refused", detail=str(exc)
        )
    own, cited = _Budget(STANDARD_BUDGET), []
    for path in targets:
        data, problem = _read(obs, budget, own, path)
        if problem:
            return _entry(standard, "unverifiable", **problem)
        for line in company_regex.matching_lines(compiled, data, MAX_CITED):
            cited.append({"path": path, "line": line})
    if cited:
        return _entry(standard, "fail", matches=cited[:MAX_CITED], files=targets)
    return _entry(standard, "pass", files=targets)


def _max_lines(standard, obs: Observation, budget: _Budget):
    targets = _hits(obs.written, standard.get("files") or [], standard.get("except"))
    limit = int(standard.get("limit") or 0)
    baseline_path = standard.get("baseline")
    baseline = obs.baselines.get(baseline_path) if baseline_path else {}
    own, over = _Budget(STANDARD_BUDGET), []
    for path in targets:
        data, problem = _read(obs, budget, own, path)
        if problem:
            return _entry(standard, "unverifiable", **problem)
        count = lines_of(data)
        if count <= limit:
            continue
        if baseline is None:
            return _entry(
                standard, "unverifiable", reason="baseline_unread", path=baseline_path
            )
        allowed = baseline.get(path)
        if allowed is not None and count <= allowed:
            continue
        over.append({"path": path, "lines": count, "baseline": allowed})
    if over:
        return _entry(standard, "fail", over=over[:MAX_CITED], limit=limit)
    return _entry(standard, "pass", files=targets, limit=limit)


def _require_together(standard, obs: Observation):
    pool = obs.added if standard.get("on") == "added" else [*obs.written, *obs.declared]
    when = _hits(pool, standard.get("when") or [])
    if not when:
        return _entry(standard, "pass", when=[])
    then = _hits(obs.written, standard.get("then") or [])
    if then:
        return _entry(standard, "pass", when=when[:MAX_CITED], then=then[:MAX_CITED])
    return _entry(
        standard, "fail", when=when[:MAX_CITED], then=[], missing=standard.get("then")
    )


def _forbid_path(standard, obs: Observation):
    hits = _hits(
        [*obs.written, *obs.declared],
        standard.get("files") or [],
        standard.get("except"),
    )
    if hits:
        return _entry(standard, "fail", paths=hits[:MAX_CITED])
    return _entry(standard, "pass", paths=[])


def evaluate_one(
    standard: dict[str, Any],
    obs: Observation,
    budget: _Budget | None = None,
    commands: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Pure: one standard's outcome over *obs*."""
    budget = budget or _Budget(TOTAL_BUDGET)
    if not applies(standard, obs):
        return _entry(standard, "skipped", reason="paths_not_touched")
    kind = standard.get("kind")
    if kind == "forbid_pattern":
        return _forbid_pattern(standard, obs, budget)
    if kind == "max_lines":
        return _max_lines(standard, obs, budget)
    if kind == "require_together":
        return _require_together(standard, obs)
    if kind == "forbid_path":
        return _forbid_path(standard, obs)
    if kind == "command":
        run = (commands or {}).get(standard.get("id") or "")
        if run is None:
            return _entry(standard, "not_run", reason="run_by_the_loop_at_delivery")
        return _command(standard, run)
    return _entry(standard, "unverifiable", reason="unknown_kind")


def _command(standard: dict[str, Any], run: dict[str, Any]) -> dict[str, Any]:
    """A command's run as the daemon reported it and the backend hashed it."""
    code = run.get("exit_code")
    sandbox = str((run.get("sandbox") or {}).get("mode") or "")
    if (
        run.get("refused")
        or run.get("timed_out")
        or not isinstance(code, int)
        or isinstance(code, bool)
    ):
        return _entry(standard, "unverifiable", reason="no_exit_status", run=run)
    if sandbox not in ("container", "bwrap"):
        return _entry(standard, "unverifiable", reason="host_sandbox", run=run)
    if code in (126, 127):
        return _entry(standard, "unverifiable", reason="not_runnable", run=run)
    return _entry(standard, "pass" if code == 0 else "fail", run=run)


def evaluate(
    standards: list[dict[str, Any]],
    obs: Observation,
    commands: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Pure: every standard's outcome, in the file's order, one shared budget."""
    budget = _Budget(TOTAL_BUDGET)
    return [evaluate_one(s, obs, budget, commands) for s in standards]


def dry_run_summary(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """What the Owner sees before activating: the standards that fail on the
    tree they were imported from (``fails_on_source``), those that could not
    be judged, and the command standards the dry run does not execute."""
    by = {o: [e["id"] for e in entries if e["outcome"] == o] for o in OUTCOMES}
    return {
        "fails_on_source": by["fail"],
        "blocking_fails_on_source": [
            e["id"]
            for e in entries
            if e["outcome"] == "fail" and e.get("severity") == "blocking"
        ],
        "unverifiable": by["unverifiable"],
        "not_run": by["not_run"],
        "passed": by["pass"],
        "skipped": by["skipped"],
        "entries": entries,
    }


def dry_run_targets(standards: list[dict[str, Any]], tree: list[str]) -> list[str]:
    """The files of *tree* whose bytes the dry run needs: those a
    ``forbid_pattern`` or ``max_lines`` standard reads, in tree order."""
    wanted: set[str] = set()
    for standard in standards:
        if standard.get("kind") in _READS_BYTES:
            wanted.update(
                _hits(tree, standard.get("files") or [], standard.get("except"))
            )
    return [p for p in tree if p in wanted]
