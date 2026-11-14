"""Installing a delivery's dependencies before its tests, as the daemon reports it.

With ``DELIVERY_INSTALL_ENABLED``: when a dependency manifest the delivery
wrote differs from what the company's last successful install in that folder
saw (the sha256 in the daemon's write report against
``Company.dependency_installs``), the profile's install command runs through
``workspace.execute`` in the same sandbox as the tests, bounded by
``DELIVERY_INSTALL_TIMEOUT_SECONDS``, with the network as
``DELIVERY_INSTALL_NETWORK`` says whatever the sandbox's own setting (open by
default: a registry is needed). The daemon's reply is
``evidence["dependency_install"]``; the Verifier's ``dependencies_installed``
check (``services/evidence/app_check.py``) judges it. Only the manifests that
install covers (``stack_profiles.installed_by``: a detected command, its own
stack's manifests in the root; a named one, all) are recorded as installed,
and only after it exited 0, so a failed one is retried and a manifest no
install ran for (``not_covered``) is never taken as installed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from config import settings

from services.workspace import sandbox, stack_profiles
from services.workspace.project_profile import Context

logger = logging.getLogger(__name__)

OUTPUT_TAIL_CHARS = 2000
# What the backend adds to the daemon's own bounds before it stops waiting.
REPLY_MARGIN_SECONDS = 30.0


def changed_manifests(
    evidence: dict[str, Any], installs: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """The manifests the delivery wrote whose sha256 (the daemon's, read back
    from disk) differs from the last successful install's in that folder."""
    root = evidence.get("workspace_root") or ""
    seen = ((installs or {}).get(root) or {}) if isinstance(installs, dict) else {}
    changed = []
    for write in evidence.get("workspace_writes") or []:
        if not isinstance(write, dict) or write.get("status") != "written":
            continue
        path, digest = write.get("path"), write.get("sha256")
        if (
            not isinstance(path, str)
            or not digest
            or not stack_profiles.is_manifest(path)
        ):
            continue
        if seen.get(path) != digest:
            changed.append({"path": path, "sha256": digest, "previous": seen.get(path)})
    return changed


def _command_record(command: str, reply: Any) -> dict[str, Any]:
    reply = reply if isinstance(reply, dict) else {}
    output = f"{reply.get('stdout') or ''}{reply.get('stderr') or ''}"
    record: dict[str, Any] = {
        "command": command,
        "exit_code": reply.get("exit_code"),
        "timed_out": bool(reply.get("timed_out")),
        "output_tail": output[-OUTPUT_TAIL_CHARS:],
        "output_bytes": len(output),
        "output_truncated": len(output) > OUTPUT_TAIL_CHARS,
        "reason": reply.get("error"),
    }
    if reply.get("refused"):
        record["refused"] = True
    if record["timed_out"]:
        record["timeout_seconds"] = reply.get("timeout_seconds")
    if isinstance(reply.get("sandbox"), dict):
        record["sandbox"] = reply["sandbox"]
    return record


def _record_installed(
    engine: Any, company_id: Any, root: str, manifests: list[dict[str, Any]]
) -> None:
    from database import engine as default_engine
    from models.company import Company
    from sqlmodel import Session

    with Session(engine or default_engine) as session:
        company = session.get(Company, company_id)
        if company is None:
            return
        installs = dict(company.dependency_installs or {})
        seen = dict(installs.get(root) or {})
        seen.update({m["path"]: m["sha256"] for m in manifests})
        installs[root] = seen
        company.dependency_installs = installs
        session.add(company)
        session.commit()


async def install_if_needed(ctx: Context) -> None:
    """Install the dependencies when a manifest changed; the daemon's reply
    becomes ``evidence["dependency_install"]``. Nothing is recorded when no
    manifest changed."""
    if not settings.DELIVERY_INSTALL_ENABLED:
        return
    evidence = ctx.evidence
    changed = changed_manifests(evidence, ctx.company().get("installs"))
    if not changed:
        return
    profile = await ctx.profile()
    command = profile.get("install")
    covered = [m for m in changed if stack_profiles.installed_by(profile, m["path"])]
    base: dict[str, Any] = {
        "manifests": covered if command else changed,
        "stack": profile.get("stack"),
        "source": (profile.get("source") or {}).get("install"),
    }
    if command and len(covered) < len(changed):
        # Changed, but not what this install installs: never recorded as installed.
        base["not_covered"] = [m for m in changed if m not in covered]
    if not command:
        evidence["dependency_install"] = {
            **base,
            "command": None,
            "reason": "no install command for this project's stack",
        }
        return
    if not covered:
        paths = ", ".join(m["path"] for m in changed)
        evidence["dependency_install"] = {
            **base,
            "command": None,
            "reason": f"`{command}` in the project's root does not install {paths}",
        }
        return
    try:
        box = await asyncio.to_thread(
            sandbox.request_for_sprint, ctx.sprint, ctx.engine
        )
    except sandbox.PolicyUnavailable as e:
        evidence["dependency_install"] = {**base, "command": command, "reason": str(e)}
        return
    # The install's own setting, both ways: a registry may be needed, and a
    # lifecycle script must not reach the network when it is turned off.
    # ``purpose``: the daemon resets the project's dependency cache after a
    # failed install and quarantines it after three in a row (``sandbox_cache``).
    box = {
        **box,
        "network": bool(settings.DELIVERY_INSTALL_NETWORK),
        "purpose": "install",
    }
    timeout = float(settings.DELIVERY_INSTALL_TIMEOUT_SECONDS)
    try:
        reply = await ctx.nats.request(
            "workspace.execute",
            {
                "command": command,
                "workspace_root": evidence["workspace_root"],
                "sprint_id": ctx.sprint.get("id"),
                "sandbox": box,
                "timeout_seconds": timeout,
            },
            timeout=timeout + REPLY_MARGIN_SECONDS,
        )
    except Exception as e:  # noqa: BLE001 - A silent daemon leaves it unverified.
        evidence["dependency_install"] = {
            **base,
            "command": command,
            "reason": f"the workspace daemon did not reply ({type(e).__name__})",
        }
        return
    record = {**base, **_command_record(command, reply)}
    evidence["dependency_install"] = record
    if record["exit_code"] == 0 and not record["timed_out"]:
        company_id = ctx.company().get("id")
        if company_id is not None:
            try:
                await asyncio.to_thread(
                    _record_installed,
                    ctx.engine,
                    company_id,
                    evidence["workspace_root"],
                    covered,
                )
            except Exception as e:  # noqa: BLE001 - The next delivery installs again.
                logger.warning("Could not record the install: %s", e)
