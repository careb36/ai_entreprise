"""Importing a company's standards from its repository (issue #700;
``docs/design/COMPANY_STANDARDS.md`` §4, §12 decisions 5 and 7).

1. **The ref.** The daemon reads ``.fabrique/standards.toml`` at the head of
   the company's **default branch** (``workspace.git.read_at``; the base
   branch, else ``origin/HEAD``, ``main``, ``master``), never a task branch,
   an integration branch or a delivery's worktree.
2. **The hash.** The sha256 is computed here over the bytes received; a hash
   in the file, the request or the reply is never read (rule 6).
3. **Validation.** ``contracts.company_standards.parse``, every refusal named.
4. **Guards and baselines.** At the same commit: every file under each guard
   (folders recursively, sorted, symbolic links refused, the guard's exclude
   globs applied) hashed here, one digest per guard; each ``baseline`` read
   as a JSON map of path to lines.
5. **Dry run.** The in-backend kinds evaluated once against that commit's
   tree as if every file had been delivered; a standard that fails there is
   ``fails_on_source``. ``command`` standards are ``not_run``: they execute
   in the delivery's sandbox only.
6. **The draft.** Stored by ``platform.company_standards_setting``; nothing in
   force changes.

No daemon: ``ImportUnavailable`` (a 409), and no draft is created.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from sqlmodel import Session

from services.contracts import company_standards as schema
from services.evidence import company_standards_check as judge
from services.platform import company_standards_setting as setting
from services.workspace import sandbox, workspace_daemon

SUBJECT = "workspace.git.read_at"
TIMEOUT_SECONDS = 30.0
DRY_RUN_MAX_FILES = 2000


class ImportUnavailable(Exception):
    """The file could not be read: no daemon, no project, or the daemon refused."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _bytes(entry: dict[str, Any]) -> bytes | None:
    if entry.get("status") != "present":
        return None
    try:
        return base64.b64decode(str(entry.get("content_b64") or ""), validate=True)
    except (binascii.Error, ValueError):
        return None


async def _ask(nats_client: Any, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        reply = await workspace_daemon.daemon_request(
            nats_client, SUBJECT, payload, TIMEOUT_SECONDS
        )
    except workspace_daemon.DaemonUnavailable as exc:
        raise ImportUnavailable(
            409, f"The workspace daemon did not answer: {exc}"
        ) from exc
    except workspace_daemon.DaemonError as exc:
        raise ImportUnavailable(
            409, f"The daemon could not read the file: {exc.message}"
        ) from exc
    if (
        not isinstance(reply, dict)
        or not reply.get("ok")
        or not isinstance(reply.get("commit"), str)
    ):
        raise ImportUnavailable(409, "The daemon's answer has no commit")
    return reply


def guard_hashes(
    specs: list[dict[str, Any]], folders: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Pure: each guard's per-file sha256 and digest, computed here, and the
    refusals (a guard missing, a symbolic link or an unreadable file in it)."""
    by_path = {f.get("path"): f for f in folders if isinstance(f, dict)}
    out: dict[str, Any] = {}
    refusals: list[dict[str, Any]] = []
    for spec in specs:
        folder = by_path.get(spec["path"]) or {"status": "missing", "files": []}
        files: dict[str, str] = {}
        for entry in folder.get("files") or []:
            path = str(entry.get("path") or "")
            if schema.excluded(path, spec.get("exclude") or []):
                continue
            data = _bytes(entry)
            if data is None:
                refusals.append(
                    {
                        "code": "guard_unreadable",
                        "message": f"guard {spec['path']}: {path} is {entry.get('status')}; a guard hashes regular files only",
                        "field": "guards",
                    }
                )
                continue
            files[path] = schema.sha256_hex(data)
        if folder.get("status") != "present" or not files:
            refusals.append(
                {
                    "code": "guard_missing",
                    "message": f"guard {spec['path']} has no file at the default branch's head",
                    "field": "guards",
                }
            )
            continue
        out[spec["path"]] = {
            "sha256": schema.guard_digest(files),
            "exclude": spec.get("exclude") or [],
            "files": files,
        }
    return out, refusals


def read_baselines(
    paths: list[str], files: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    by_path = {f.get("path"): f for f in files if isinstance(f, dict)}
    out: dict[str, Any] = {}
    refusals: list[dict[str, Any]] = []
    for path in paths:
        value, why = schema.baseline_refusal(_bytes(by_path.get(path) or {}))
        if why:
            refusals.append(
                {
                    "code": "invalid_baseline",
                    "message": f"{path}: {why}",
                    "field": "baseline",
                }
            )
        else:
            out[path] = value
    return out, refusals


def dry_run(
    standards: list[dict[str, Any]],
    tree: list[str],
    files: dict[str, bytes | None],
    baselines: dict[str, Any],
) -> dict[str, Any]:
    """Pure: every standard over the tree, as if every file had been delivered."""
    obs = judge.Observation(
        written=list(tree), added=list(tree), files=files, baselines=baselines
    )
    return judge.dry_run_summary(judge.evaluate(standards, obs))


async def import_draft(
    session: Session,
    nats_client: Any,
    company: Any,
    *,
    by: str,
    user_id: int | None,
    proposed_by: str | None = None,
):
    """Read, hash, validate, dry-run and store a draft (module docstring)."""
    root = sandbox.project_path(session, company.id)
    if not root:
        raise ImportUnavailable(409, "The company has no project folder to read")
    base = getattr(company, "git_base_branch", None) or ""
    first = await _ask(
        nats_client, {"workspace_root": root, "base": base, "paths": [schema.PATH]}
    )
    commit = first["commit"]
    entry = (first.get("files") or [{}])[0]
    raw = _bytes(entry)
    if raw is None:
        raise ImportUnavailable(
            409,
            f"{schema.PATH} is {entry.get('status') or 'missing'} at the head of the default branch ({commit[:12]})",
        )
    parsed = schema.parse(raw)
    standards = parsed.standards
    specs = schema.guard_specs(standards)
    baseline_paths = schema.baseline_paths(standards)
    second = await _ask(
        nats_client,
        {
            "workspace_root": root,
            "commit": commit,
            "paths": baseline_paths,
            "folders": [g["path"] for g in specs],
            "list": True,
        },
    )
    hashes, refusals = guard_hashes(specs, second.get("folders") or [])
    baselines, more = read_baselines(baseline_paths, second.get("files") or [])
    refusals += more
    tree = [p for p in second.get("tree") or [] if isinstance(p, str)]
    targets = judge.dry_run_targets(standards, tree)
    contents: dict[str, bytes | None] = {}
    if targets:
        third = await _ask(
            nats_client,
            {
                "workspace_root": root,
                "commit": commit,
                "paths": targets[:DRY_RUN_MAX_FILES],
            },
        )
        contents = {
            f.get("path"): _bytes(f)
            for f in third.get("files") or []
            if isinstance(f, dict)
        }
    summary = dry_run(standards, tree, contents, baselines)
    summary["tree_truncated"] = (
        bool(second.get("tree_truncated")) or len(targets) > DRY_RUN_MAX_FILES
    )
    return setting.create_draft(
        session,
        company.id,
        raw=raw,
        source_commit=commit,
        source_base=first.get("base"),
        parsed=parsed.as_dict(),
        guard_hashes=hashes,
        baselines=baselines,
        import_refusals=refusals,
        dry_run=summary,
        sandbox_seen=str(
            second.get("sandbox_required") or first.get("sandbox_required") or ""
        ),
        by=by,
        user_id=user_id,
        proposed_by=proposed_by,
    )
