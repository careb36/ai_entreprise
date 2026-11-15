"""The company's architecture and its versions (issue #329, finding F3).

A company has one architecture. Each ``Architecture`` row is a version of
it: ``version`` counts the company's versions, approving one supersedes every
other approved version of the company (``document_approval``), and the
earlier versions stay readable. A version names the specification it was
drafted for (``specification_id``); its modules' ``covers`` are that
specification's requirements and ``earlier_covers`` keep, by specification
id, what they cover in the earlier specifications the architecture serves.

The Architect's revision for a new specification is the next version of the
same architecture (``start_revision``): the version's own covers move under
``earlier_covers`` and the new specification's covers start from what the
version already had for it, so no specification loses its covers. Taken from
Kiro's spec-driven flow (requirements, then a design, then tasks), where one
design document of a project is revised for each new spec rather than copied;
design only, no code.

Reads and pure helpers; nothing here commits.
"""

from __future__ import annotations

from typing import Any

from models import Architecture, Company, Specification
from sqlalchemy import func
from sqlmodel import Session, select

from services.planning import arch_check


def requirement_ids(spec: Specification) -> list[str]:
    return [
        r["id"] for r in spec.requirements or [] if isinstance(r, dict) and r.get("id")
    ]


def versions(session: Session, company_id: int) -> list[Architecture]:
    """Every version of the company's architecture, newest first."""
    return list(
        session.exec(
            select(Architecture)
            .where(Architecture.company_id == company_id)
            .order_by(Architecture.version.desc(), Architecture.id.desc())
        ).all()
    )


def current(session: Session, company_id: int) -> Architecture | None:
    """The company's current architecture: its newest approved version."""
    return session.exec(
        select(Architecture)
        .where(Architecture.company_id == company_id, Architecture.status == "approved")
        .order_by(Architecture.version.desc(), Architecture.id.desc())
    ).first()


def open_draft(session: Session, company_id: int) -> Architecture | None:
    """The draft version that revises the current one, if a person opened one."""
    base = current(session, company_id)
    if base is None:
        return None
    return session.exec(
        select(Architecture).where(
            Architecture.supersedes_id == base.id, Architecture.status == "draft"
        )
    ).first()


def next_version(session: Session, company_id: int) -> int:
    """The number the company's next version takes. The company row is locked
    until the commit, so two drafts started at once do not share a number."""
    session.exec(
        select(Company).where(Company.id == company_id).with_for_update()
    ).first()
    latest = session.exec(
        select(func.max(Architecture.version)).where(
            Architecture.company_id == company_id
        )
    ).one()
    return int(latest or 0) + 1


def covered_ids(arch: Architecture) -> list[int]:
    """The specifications the version covers: its own, then the earlier ones."""
    keys = {
        int(key)
        for m in arch.modules or []
        if isinstance(m, dict)
        for key in (m.get("earlier_covers") or {})
        if str(key).isdigit()
    }
    return [arch.specification_id, *sorted(keys - {arch.specification_id})]


def covers_specification(arch: Architecture, specification_id: int) -> bool:
    return specification_id in covered_ids(arch)


def for_specification(session: Session, spec: Specification) -> Architecture | None:
    """The version that holds for *spec*: the company's current one when it
    covers it, else the newest approved or superseded version that does (a
    specification of a company whose versions predate F3 keeps its own)."""
    base = current(session, spec.company_id)
    if base is not None and covers_specification(base, spec.id):
        return base
    for arch in versions(session, spec.company_id):
        if arch.status != "draft" and covers_specification(arch, spec.id):
            return arch
    return None


def lineage(session: Session, spec: Specification) -> set[int]:
    """*spec*'s id and the ids of every version of it that it supersedes."""
    ids: set[int] = set()
    row: Specification | None = spec
    while row is not None and row.id not in ids:
        ids.add(row.id)
        row = (
            session.get(Specification, row.supersedes_id) if row.supersedes_id else None
        )
    return ids


def _dedup(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def rebase(
    modules: list[dict[str, Any]], own_id: int, target_id: int, target_lineage: set[int]
) -> list[dict[str, Any]]:
    """*modules* of a version drafted for *own_id*, as the start of a version
    for *target_id*: the own covers move under ``earlier_covers`` (or stay, when
    the target is a revision of the same specification), and the covers the
    version had for the target, or for a version of it, become its own."""
    if target_id == own_id:
        return [dict(m) for m in modules]
    out = []
    for m in modules:
        earlier = {str(k): list(v) for k, v in (m.get("earlier_covers") or {}).items()}
        covers: list[str] = []
        if own_id in target_lineage:
            covers += list(m.get("covers") or [])
        elif m.get("covers"):
            earlier[str(own_id)] = _dedup(earlier.get(str(own_id), []) + m["covers"])
        for key in list(earlier):
            if int(key) == target_id or int(key) in target_lineage:
                covers += earlier.pop(key)
        moved = {**m, "covers": _dedup(covers)}
        moved.pop("earlier_covers", None)
        if earlier:
            moved["earlier_covers"] = earlier
        out.append(moved)
    return out


def carry(base_modules: list[dict[str, Any]], modules: list[Any]) -> list[Any]:
    """*modules* as a person or the Architect wrote them, each keeping the
    earlier covers of the base module of the same name (or id) unless it names
    its own: a draft that rewrites the modules does not drop the covers of the
    earlier specifications."""
    by_key: dict[str, dict[str, Any]] = {}
    for m in base_modules:
        for key in (m.get("name"), m.get("id")):
            if key:
                by_key.setdefault(str(key).casefold(), m)
    out = []
    for m in modules:
        if isinstance(m, dict) and "earlier_covers" not in m:
            base = by_key.get(str(m.get("name") or "").casefold()) or by_key.get(
                str(m.get("id") or "").casefold()
            )
            if base is not None and base.get("earlier_covers"):
                m = {**m, "earlier_covers": dict(base["earlier_covers"])}
        out.append(m)
    return out


def view_for(
    content: dict[str, Any], own_id: int, specification_id: int
) -> dict[str, Any]:
    """*content* as one specification sees it: each module's ``covers`` are its
    covers in that specification, so a task's prompt maps its own criteria."""
    modules = []
    for m in content.get("modules") or []:
        covers = (
            m.get("covers")
            if specification_id == own_id
            else (m.get("earlier_covers") or {}).get(str(specification_id))
        )
        view = {k: v for k, v in m.items() if k != "earlier_covers"}
        view["covers"] = list(covers or [])
        modules.append(view)
    return {**content, "modules": modules}


def earlier_specifications(
    session: Session, arch: Architecture
) -> dict[str, dict[str, Any]]:
    """The earlier specifications the version covers, by id, as ``arch_check``
    reads them. A specification of another company is left out, so a cover of
    it is refused as unknown."""
    out: dict[str, dict[str, Any]] = {}
    for spec_id in covered_ids(arch)[1:]:
        spec = session.get(Specification, spec_id)
        if spec is None or spec.company_id != arch.company_id:
            continue
        out[str(spec_id)] = {
            "title": spec.title,
            "status": spec.status,
            "version": spec.version,
            "requirement_ids": requirement_ids(spec),
        }
    return out


def coverage(
    content: dict[str, Any],
    own: Specification | None,
    earlier: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Per specification the version covers, each requirement with the modules
    covering it, and the requirements no module covers."""
    rows = []
    specs: list[tuple[int, dict[str, Any]]] = []
    if own is not None:
        specs.append(
            (
                own.id,
                {
                    "title": own.title,
                    "status": own.status,
                    "version": own.version,
                    "requirement_ids": requirement_ids(own),
                },
            )
        )
    specs += [(int(k), v) for k, v in earlier.items()]
    for spec_id, spec in specs:
        view = view_for(content, own.id if own else -1, spec_id)
        requirements = [
            {
                "id": req,
                "modules": [
                    m["id"] for m in view["modules"] if req in (m.get("covers") or [])
                ],
            }
            for req in spec["requirement_ids"]
        ]
        rows.append(
            {
                "specification_id": spec_id,
                "title": spec["title"],
                "status": spec["status"],
                "version": spec["version"],
                "requirements": requirements,
                "uncovered": [r["id"] for r in requirements if not r["modules"]],
            }
        )
    return rows


def start_revision(
    session: Session,
    base: Architecture,
    target: Specification,
    actor: str,
    numbered: bool = True,
) -> Architecture:
    """The next version of the company's architecture, from *base*, for
    *target*; not added to the session. The covers of every specification
    *base* covers are kept (``rebase``). With *numbered* false the version is
    left at 0 for the caller to number (``next_version``) just before saving,
    so the company row is not locked while a model is asked."""
    return Architecture(
        company_id=base.company_id,
        specification_id=target.id,
        title=base.title,
        overview=base.overview,
        version=next_version(session, base.company_id) if numbered else 0,
        supersedes_id=base.id,
        modules=rebase(
            list(base.modules or []),
            base.specification_id,
            target.id,
            lineage(session, target),
        ),
        interfaces=list(base.interfaces or []),
        decisions=list(base.decisions or []),
        conventions=list(base.conventions or []),
        questions=[],
        drafted_by=actor,
    )


def summary(session: Session, arch: Architecture) -> dict[str, Any]:
    """One line of the version history."""
    spec = session.get(Specification, arch.specification_id)
    return {
        "id": arch.id,
        "version": arch.version,
        "status": arch.status,
        "title": arch.title,
        "specification_id": arch.specification_id,
        "specification_title": spec.title if spec else None,
        "specification_ids": covered_ids(arch),
        "supersedes_id": arch.supersedes_id,
        "drafted_by": arch.drafted_by,
        "approved_by": arch.approved_by,
        "approved_at": arch.approved_at.isoformat() if arch.approved_at else None,
        "created_at": arch.created_at.isoformat() if arch.created_at else None,
    }


def check(session: Session, arch: Architecture, spec: Specification) -> dict[str, Any]:
    """``arch_check.check`` over every specification the version covers."""
    content = {
        "title": arch.title,
        "overview": arch.overview,
        "modules": arch.modules or [],
        "interfaces": arch.interfaces or [],
        "decisions": arch.decisions or [],
        "conventions": arch.conventions or [],
        "questions": arch.questions or [],
    }
    return arch_check.check(
        content, requirement_ids(spec), earlier_specifications(session, arch)
    )
