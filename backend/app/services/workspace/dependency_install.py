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

What an install leaves depends on where it ran: packages installed on the
host are not in a container, and the reverse. So the record is kept per
sandbox mode (``install_key``): ``<root>`` for ``host`` (the key records had
before modes were told apart, all made in the default mode), ``<mode>:<root>``
for ``container`` and ``bwrap``. A company that moves from host to container
mode installs again at its next delivery that writes a manifest. The mode is
the one the company asks for; a daemon that forces another one
(``FABRIQUE_SANDBOX_REQUIRED``) is reported in the run's ``sandbox``, and the
record follows the company's request.

The install asks for its whole output too (``run_logs.py``): the record's
``full_log`` is what the backend read back and hashed.

In a repository with folders of their own (``stack_roots``), a manifest in a
folder is that folder's install's: ``cd <folder> && <install>``, kept in
``evidence["command_runs"]`` (``kind: "install"``) and judged by the
``required_commands`` check; the root's install keeps the manifests in no
folder, as before.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from config import settings

from services.workspace import run_logs, sandbox, stack_profiles, stack_roots
from services.workspace.folder_commands import execute
from services.workspace.project_profile import Context

logger = logging.getLogger(__name__)

OUTPUT_TAIL_CHARS = 2000
# What the backend adds to the daemon's own bounds before it stops waiting.
REPLY_MARGIN_SECONDS = 30.0


def install_key(root: str, mode: str | None) -> str:
    """Where an install's record is kept: per folder and sandbox mode."""
    mode = (mode or "host").strip().lower()
    return root if mode == "host" else f"{mode}:{root}"


def changed_manifests(
    evidence: dict[str, Any], installs: dict[str, Any] | None, mode: str = "host"
) -> list[dict[str, Any]]:
    """The manifests the delivery wrote whose sha256 (the daemon's, read back
    from disk) differs from the last successful install's in that folder and
    sandbox mode."""
    root = evidence.get("workspace_root") or ""
    key = install_key(root, mode)
    seen = ((installs or {}).get(key) or {}) if isinstance(installs, dict) else {}
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
    engine: Any, company_id: Any, key: str, manifests: list[dict[str, Any]]
) -> None:
    from database import engine as default_engine
    from models.company import Company
    from sqlmodel import Session

    with Session(engine or default_engine) as session:
        company = session.get(Company, company_id)
        if company is None:
            return
        installs = dict(company.dependency_installs or {})
        seen = dict(installs.get(key) or {})
        seen.update({m["path"]: m["sha256"] for m in manifests})
        installs[key] = seen
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
    try:
        box: dict[str, Any] | None = await asyncio.to_thread(
            sandbox.request_for_sprint, ctx.sprint, ctx.engine
        )
        unavailable = None
    except sandbox.PolicyUnavailable as e:
        box, unavailable = None, str(e)
    mode = (box or {}).get("mode") or "host"
    changed = changed_manifests(evidence, ctx.company().get("installs"), mode)
    if not changed:
        return
    folders = await ctx.folders()
    if folders:
        # A folder's manifests are its own install's (``stack_roots``).
        own = [m for m in changed if stack_roots.folder_of(m["path"], folders)]
        if own:
            await _install_folders(ctx, folders, own, box, unavailable, mode)
            changed = [m for m in changed if m not in own]
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
    if box is None:
        evidence["dependency_install"] = {
            **base,
            "command": command,
            "reason": unavailable,
        }
        return
    base["sandbox_mode"] = mode  # The key its record is kept under (install_key).
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
    full_log = run_logs.request_spec(ctx.nats)
    try:
        reply = await ctx.nats.request(
            "workspace.execute",
            {
                "command": command,
                "workspace_root": evidence["workspace_root"],
                "sprint_id": ctx.sprint.get("id"),
                "sandbox": box,
                "timeout_seconds": timeout,
                **run_logs.request_field(full_log),
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
    record["full_log"] = await run_logs.observe(ctx.nats, full_log, reply)
    evidence["dependency_install"] = record
    if record["exit_code"] == 0 and not record["timed_out"]:
        company_id = ctx.company().get("id")
        if company_id is not None:
            try:
                await asyncio.to_thread(
                    _record_installed,
                    ctx.engine,
                    company_id,
                    install_key(evidence["workspace_root"], mode),
                    covered,
                )
            except Exception as e:  # noqa: BLE001 - The next delivery installs again.
                logger.warning("Could not record the install: %s", e)


async def _install_folders(
    ctx: Context,
    folders: list[dict[str, Any]],
    changed: list[dict[str, Any]],
    box: dict[str, Any] | None,
    unavailable: str | None,
    mode: str,
) -> None:
    """Each folder's install for the manifests the delivery changed in it."""
    evidence = ctx.evidence
    if box is not None:
        box = {
            **box,
            "network": bool(settings.DELIVERY_INSTALL_NETWORK),
            "purpose": "install",
        }
    timeout = float(settings.DELIVERY_INSTALL_TIMEOUT_SECONDS)
    for folder in folders:
        name = folder["folder"]
        mine = [m for m in changed if stack_roots.folder_of(m["path"], folders) == name]
        if not mine:
            continue
        command = folder.get("install")
        covered = [m for m in mine if stack_roots.installed_by(folder, m["path"])]
        base = {"kind": "install", "folder": name, "required": True}
        if not command or not covered:
            paths = ", ".join(m["path"] for m in mine)
            reason = (
                f"`{command}` in {name}/ does not install {paths}"
                if command
                else f"no install command for {name}/"
            )
            evidence.setdefault("command_runs", []).append(
                {**base, "command": command, "exit_code": None, "manifests": mine}
                | {"reason": reason}
            )
            continue
        record, _output = await execute(
            ctx,
            "install",
            name,
            command,
            box,
            unavailable,
            timeout=timeout + REPLY_MARGIN_SECONDS,
            timeout_seconds=timeout,
        )
        record["manifests"] = covered
        record["source"] = (folder.get("source") or {}).get("install")
        if box is not None:
            record["sandbox_mode"] = mode
        evidence.setdefault("command_runs", []).append(record)
        company_id = ctx.company().get("id")
        if record["exit_code"] == 0 and not record["timed_out"] and company_id:
            try:
                await asyncio.to_thread(
                    _record_installed,
                    ctx.engine,
                    company_id,
                    install_key(evidence["workspace_root"], mode),
                    covered,
                )
            except Exception as e:  # noqa: BLE001 - The next delivery installs again.
                logger.warning("Could not record the install in %s/: %s", name, e)
