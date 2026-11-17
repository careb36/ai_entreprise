"""What the cycle checks before it starts and what it reads to decide
(``scripts/rsi/cycle.py``; REQ-2, REQ-5, REQ-20, REQ-4).

- **The specification** is read from B's objects (``git show B:<path>``),
  never from a working tree, and must name a ``target_measure`` present at B
  (REQ-2).
- **REQ-20.** The script has no database, so the findings' state is the
  platform's own answer to ``GET /v1/findings?company_id=<id>``, saved to a
  file by the person running the cycle (``--findings``) and kept with its
  sha256. The specification's sha256 is computed here from its canonical text
  with ``contracts/specification_text`` — the rule ``learning/findings``
  keys a finding by — and a finding with that sha256 and a ``revert`` outcome
  stops the cycle unless a person is named (``--retry-by``). Without the file
  the cycle does not start: an unknown state is not "no revert" (rule 7).
- **The protected match (REQ-5)** is computed from git: the files C changes
  against B, matched with B's copy of the list and of
  ``scripts/protected_paths_check.py`` (the CI check's own rule, loaded from
  B's tree), and an approval only from a code owner's review of C's sha read
  from GitHub's API, never from the pull request's body.
- **Graders pinned to B (REQ-4).** A scorecard whose ``graders.sha`` is not
  B's, or that carries no graders block, was not graded by B's tests.
- **B's medians and spread** for the cycle record, with
  ``scripts/bench/repeated.py``'s own reading (pairs, void pairs, complete
  runs, ``summary``).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
for _path in (ROOT / "backend" / "app", ROOT / "scripts" / "bench"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
import repeated  # scripts/bench/repeated.py, by path
from services.contracts.specification_text import specification_sha256
from services.evidence import fitness

REVERT = "revert"


class GateRefused(ValueError):
    """The cycle will not start, or a step's input is refused; says why."""


# ── The specification ────────────────────────────────────────────────────────


def read_spec(text: str, has_file: Callable[[str], bool]) -> dict[str, Any]:
    """The specification at B and its target measure (REQ-2)."""
    try:
        record = json.loads(text)
    except ValueError as exc:
        raise GateRefused(f"the specification is not JSON: {exc}") from exc
    if not isinstance(record, dict):
        raise GateRefused("the specification is not a JSON object")
    measure = record.get("target_measure")
    if not isinstance(measure, str) or not measure.strip():
        raise GateRefused("the specification names no target_measure (REQ-2)")
    if not has_file(measure):
        raise GateRefused(f"target_measure {measure} is not in B's tree")
    return {
        "target_measure": measure,
        "prompt_targeted": bool(record.get("prompt_targeted", False)),
        "sha256": specification_sha256(text),
    }


# ── REQ-20 ───────────────────────────────────────────────────────────────────


def load_findings(path: Path | None) -> list[dict[str, Any]]:
    """The findings of the platform's ``GET /v1/findings`` saved in *path*."""
    if path is None:
        raise GateRefused(
            "no --findings export: whether this specification was reverted "
            "is unknown, so the cycle does not start (REQ-20)"
        )
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GateRefused(f"--findings {path}: {exc}") from exc
    rows = data.get("findings") if isinstance(data, dict) else data
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise GateRefused(f"--findings {path}: not a list of findings")
    return rows


def _reverted(finding: dict[str, Any]) -> bool:
    return any(
        isinstance(o, dict) and o.get("decision") == REVERT
        for o in finding.get("outcomes") or []
    )


def findings_gate(
    spec_sha: str, rows: list[dict[str, Any]], retry_by: str | None
) -> dict[str, Any]:
    """REQ-20: whether a cycle may start for the specification *spec_sha*."""
    same = [r for r in rows if r.get("specification_sha256") == spec_sha]
    reverted = [r.get("id") for r in same if _reverted(r)]
    finding = next((r for r in same if not _reverted(r)), same[0] if same else None)
    gate = {
        "specification_sha256": spec_sha,
        "findings_read": len(rows),
        "reverted_findings": reverted,
        "finding": None
        if finding is None
        else {k: finding.get(k) for k in ("id", "source_ref", "title", "status")},
        "retry_by": retry_by,
    }
    if reverted and not (retry_by and retry_by.strip()):
        gate.update(allowed=False, reason="specification_reverted")
    elif reverted:
        gate.update(allowed=True, reason="person_asked")
    else:
        gate.update(allowed=True, reason="no_reverted_cycle")
    return gate


# ── The protected match ──────────────────────────────────────────────────────


def load_check(b_tree: Path) -> ModuleType:
    """B's copy of ``scripts/protected_paths_check.py``."""
    path = b_tree / "scripts" / "protected_paths_check.py"
    spec = importlib.util.spec_from_file_location("protected_paths_check_b", path)
    if spec is None or spec.loader is None or not path.is_file():
        raise GateRefused(f"B has no {path.relative_to(b_tree)}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def protected_match(
    b_tree: Path,
    repo: Path,
    b_sha: str,
    c_sha: str,
    reviews: Callable[[], list[dict]],
    author: str = "",
) -> dict[str, Any]:
    """``{sha, touched, approval}`` for ``decide.py --protected``, from git."""
    check = load_check(b_tree)
    try:
        module = check.load_protected(b_tree)
        patterns = check.protected_patterns(module, b_tree)
        rules = check.parse_codeowners(
            (b_tree / check.CODEOWNERS).read_text(encoding="utf-8")
        )
        changed = check.changed_files(b_sha, c_sha, repo)
        decision = check.decide(
            changed,
            patterns,
            module,
            rules,
            lambda: check.parse_reviews(reviews()),
            c_sha,
            author,
        )
    except RuntimeError as exc:  # check.CheckError, or the API's error
        raise GateRefused(f"the protected match could not be made: {exc}") from exc
    touched = [{"path": p.path, "pattern": p.pattern} for p in decision.paths]
    approved = [p for p in decision.paths if p.approved_by is not None]
    approval = None
    if approved:
        approval = {
            "sha": c_sha,
            "by": ", ".join(sorted({p.approved_by.login for p in approved})),
            "paths": sorted(p.path for p in approved),
        }
    return {
        "sha": c_sha,
        "changed_files": len(changed),
        "touched": touched,
        "approval": approval,
        "notes": decision.notes,
    }


# ── Graders pinned to B ──────────────────────────────────────────────────────


def pinned_problems(cards: list[tuple[Path, Any]], b_sha: str) -> list[str]:
    """Each card not graded by B's tests (REQ-4), named."""
    problems = []
    for path, card in cards:
        block = card.get("graders") if isinstance(card, dict) else None
        sha = block.get("sha") if isinstance(block, dict) else None
        if sha != b_sha:
            problems.append(f"{path.name}: graded by {sha!r}, not by B {b_sha}")
    return problems


# ── B's medians and spread ───────────────────────────────────────────────────


def _scope_summary(values: list[dict], k: int) -> dict[str, Any] | None:
    scope = {}
    for metric in (*fitness.GUARDED, "prompt_tokens_mean"):
        found = repeated.summary([v.get(metric) for v in values], k)
        if found is None and metric in fitness.GUARDED:
            return None
        if found is not None:
            scope[metric] = found
    return scope


def baseline_summary(
    b_cards: list[dict], c_cards: list[dict], k: int
) -> dict[str, Any]:
    """B's medians and spread over the counted pairs, and B's served models."""
    counted, void, unpaired = repeated.pairs(b_cards, c_cards)
    b_counted = [b for b, _ in counted]
    totals = _scope_summary(repeated._values(b_counted, "total"), k)
    names = sorted({s["name"] for card in b_counted for s in card["specs"]})
    specs = {}
    for name in names:
        scope = _scope_summary(repeated._values(b_counted, name), k)
        if scope is not None:
            specs[name] = scope
    models: dict[str, set[str]] = {}
    for card in b_counted:
        for role, names_served in repeated.served_models(card).items():
            models.setdefault(role, set()).update(names_served)
    return {
        "baseline": None if totals is None else {"totals": totals, "specs": specs},
        "models": {r: sorted(m) for r, m in sorted(models.items())},
        "pairs": {"counted": len(counted), "void": void, "unpaired": unpaired},
    }
