"""Reading a cycle runner's record into the backend (``docs/design/rsi-loop.md``,
PR 10 of section 8; REQ-26, REQ-27).

``scripts/rsi/cycle.py`` writes one JSON record per cycle (schema version 1,
``scripts/rsi/cycle_record.py``). A signed-in Owner imports it here. The
record is checked by the same rules the script applies
(``services/contracts/rsi_cycle_record.validate`` with
``services/evidence/fitness.GUARDED``), and a record that does not validate is
refused, every problem named and audited (``rsi_cycle_import_refused``).

The record is the runner's; the backend observes what it can. Each kept file
the backend can read under ``RSI_FILES_ROOT`` is hashed here, and a record
whose claimed sha256 differs is refused: no hash is accepted from the party
being recorded when it can be computed (rule 6). A file the backend cannot
read keeps its hash as a claim (``sha256_claimed``), never as an observation
(``sha256_observed`` stays empty).

A record names its cycle by ``cycle_id``. When the backend ran that cycle, the
record must be of the same B and its merged sha must be C (a moved head voids
the decision, REQ-17); the record and the kept files are attached and the
status is left to the person's recorded merge. Otherwise an ``imported``
cycle is created, merged as the record says, with no person named as its
merger.
"""

from __future__ import annotations

import hashlib
from datetime import UTC
from pathlib import Path
from typing import Any

from config import settings
from models import Finding
from models.rsi_cycle import KEPT_STATUSES, MERGED, RsiCycle
from sqlmodel import Session, select

from services.contracts import rsi_cycle_record
from services.evidence import fitness
from services.management import rsi_cycle

AUDIT_IMPORTED = "rsi_cycle_imported"
AUDIT_REFUSED = "rsi_cycle_import_refused"
READ_LIMIT = 50 * 1024 * 1024


def validate(record: Any) -> list[str]:
    """What is wrong with *record*: the script's rules, then ``kept_files``."""
    problems = rsi_cycle_record.validate(record, fitness.GUARDED)
    if isinstance(record, dict):
        problems += rsi_cycle_record.kept_files_problems(record.get("kept_files"))
    return problems


def _readable(root: Path | None, path: str) -> Path | None:
    """*path* under *root* when it is a regular file inside it; None otherwise."""
    if root is None:
        return None
    try:
        base = root.resolve(strict=True)
        candidate = (base / path).resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if base not in candidate.parents or not candidate.is_file():
        return None
    if candidate.stat().st_size > READ_LIMIT:
        return None
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def observe_kept_files(
    entries: list[dict[str, str]] | None, root: Path | None
) -> tuple[list[dict[str, Any]], list[str]]:
    """Each kept file with the hash the backend computed beside the claimed
    one, and the mismatches as problems."""
    kept, problems = [], []
    for index, entry in enumerate(entries or []):
        path = rsi_cycle_record.relative_path(entry["path"])
        claimed = entry["sha256"]
        file = _readable(root, path)
        observed = _sha256(file) if file else None
        if observed is not None and observed != claimed:
            problems.append(
                f"kept_files[{index}] {path}: the backend computed {observed}, "
                f"the record claims {claimed}"
            )
        kept.append(
            {"path": path, "sha256_observed": observed, "sha256_claimed": claimed}
        )
    return kept, problems


def _root(files_root: str | Path | None) -> Path | None:
    value = files_root if files_root is not None else settings.RSI_FILES_ROOT
    return Path(value) if value else None


def import_record(
    session: Session,
    company_id: int,
    record: Any,
    *,
    person: Any,
    files_root: str | Path | None = None,
) -> RsiCycle:
    """Import the runner's *record* for *company_id*, after validating it."""
    owner = rsi_cycle.owner_person(session, company_id, person)
    actor = f"human:{owner.email}"
    problems = validate(record)
    kept: list[dict[str, Any]] = []
    if not problems:
        kept, problems = observe_kept_files(record.get("kept_files"), _root(files_root))
    existing = None
    if not problems:
        existing = session.exec(
            select(RsiCycle).where(
                RsiCycle.company_id == company_id,
                RsiCycle.cycle_key == record["cycle_id"],
            )
        ).first()
        problems = _against(existing, record)
    if problems:
        session.rollback()
        rsi_cycle.audit(
            session,
            company_id,
            AUDIT_REFUSED,
            actor,
            {"problems": problems[:50]},
            existing,
        )
        raise rsi_cycle.CycleRefused(422, "bad_record", "; ".join(problems[:10]))
    cycle = existing or _new(session, company_id, record)
    cycle.record = record
    cycle.kept_files = kept
    cycle.updated_at = rsi_cycle.utcnow()
    session.add(cycle)
    session.flush()
    rsi_cycle.audit(
        session,
        company_id,
        AUDIT_IMPORTED,
        actor,
        {
            "user_id": owner.id,
            "merged_sha": record["merged_sha"],
            "created": existing is None,
            "kept_files": [[k["path"], k["sha256_observed"]] for k in kept],
        },
        cycle,
    )
    return cycle


def _against(existing: RsiCycle | None, record: dict[str, Any]) -> list[str]:
    """Why *record* cannot be the record of the backend's *existing* cycle."""
    if existing is None:
        return []
    problems = []
    if existing.status not in KEPT_STATUSES:
        problems.append(f"the cycle is {existing.status}: it kept nothing")
    if record["merged_sha"] != existing.candidate_sha:
        problems.append("merged_sha is not the cycle's C: the decision is void")
    if record.get("baseline_sha", existing.baseline_sha) != existing.baseline_sha:
        problems.append("baseline_sha is not the cycle's B")
    return problems


def _new(session: Session, company_id: int, record: dict[str, Any]) -> RsiCycle:
    merged_at = rsi_cycle_record.parse_when(record["merged_at"])
    finding = None
    if isinstance(record.get("finding_id"), str):
        finding = session.exec(
            select(Finding).where(
                Finding.company_id == company_id,
                Finding.source_ref == record["finding_id"],
            )
        ).first()
    return RsiCycle(
        company_id=company_id,
        cycle_key=record["cycle_id"],
        finding_id=finding.id if finding else None,
        source="imported",
        status=MERGED,
        decision="keep",
        baseline_sha=record.get("baseline_sha"),
        candidate_sha=record["merged_sha"],
        merged_sha=record["merged_sha"],
        merged_at=merged_at.astimezone(UTC).replace(tzinfo=None),
        pull_request=record.get("pull_request"),
    )
