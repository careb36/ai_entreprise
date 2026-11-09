"""A delivery's stack profile: the daemon's snapshot with the company's overrides.

The commands a delivery's checks run (install, tests, start, e2e) come from
the stack profile (``stack_profiles``): detected from the project as the
daemon's snapshot shows it (``workspace.inspect``), then the deployment's and
the company's overrides (``Company.stack_profile``). Configuration, never
evidence. ``Context`` reads the company and the profile at most once per
delivery; the dependency install (``dependency_install``), the delivery's
checks (``services/delivery_checks.py``), QA's browser checks and the lint
after an edit read it.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from config import settings

from services.workspace import stack_profiles, workspace_daemon

logger = logging.getLogger(__name__)


class Context:
    """One delivery's company settings and profile, read at most once."""

    def __init__(
        self, nats_client: Any, sprint: dict[str, Any], evidence: dict, engine: Any
    ):
        self.nats = nats_client
        self.sprint = sprint or {}
        self.evidence = evidence
        self.engine = engine
        self._company: dict[str, Any] | None = None
        self._profile: dict[str, Any] | None = None

    def company(self) -> dict[str, Any]:
        """``id``, ``overrides`` and ``installs`` of the sprint's company."""
        if self._company is None:
            self._company = read_company(self.sprint, self.engine)
        return self._company

    async def profile(self) -> dict[str, Any]:
        if self._profile is None:
            detected = await detect_profile(
                self.nats, self.evidence.get("workspace_root") or ""
            )
            self._profile = stack_profiles.resolve(
                detected, self.company().get("overrides")
            )
        return self._profile

    async def test_command(self) -> tuple[str | None, str]:
        """The delivery's test command and, without one, why.

        An empty ``test`` in the company's profile turns the tests off; a
        command the company or the deployment named runs as named; otherwise
        the detected profile's, which is absent when the project has no suite
        (npm's placeholder script, nothing for pytest to collect), and then
        the older detector (another runner's suite beside the root stack)."""
        own = self.company().get("overrides") or {}
        if "test" in own and own["test"] is not None and not str(own["test"]).strip():
            return None, "tests turned off by the company's stack profile"
        # A command a person named needs no look at the project.
        named = (
            str(own.get("test") or "").strip() or settings.DELIVERY_TEST_COMMAND.strip()
        )
        if named:
            return named, ""
        command = (await self.profile()).get("test")
        if not command:
            command = await workspace_daemon.detect_project_test_command(
                self.nats, self.evidence.get("workspace_root") or ""
            )
        return (command or None), "no test command found"


async def resolved(
    nats_client: Any, sprint: dict[str, Any], workspace_root: str, engine: Any
) -> dict[str, Any]:
    """The resolved profile of *workspace_root* for *sprint*'s company."""
    ctx = Context(nats_client, sprint, {"workspace_root": workspace_root}, engine)
    return await ctx.profile()


def read_company(sprint: dict[str, Any], engine: Any) -> dict[str, Any]:
    from database import engine as default_engine
    from models.company import Company
    from models.sprint import Sprint
    from sqlmodel import Session

    try:
        with Session(engine or default_engine) as session:
            company_id = sprint.get("company_id")
            if company_id is None and sprint.get("id") is not None:
                row = session.get(Sprint, sprint["id"])
                company_id = row.company_id if row is not None else None
            company = session.get(Company, company_id) if company_id else None
            return {
                "id": company_id,
                "overrides": dict(company.stack_profile or {}) if company else {},
                "installs": dict(company.dependency_installs or {}) if company else {},
            }
    except Exception as e:  # noqa: BLE001 - The detected profile still applies.
        logger.warning("Could not read the company's stack profile: %s", e)
        return {"id": sprint.get("company_id"), "overrides": {}, "installs": {}}


async def detect_profile(nats_client: Any, workspace_root: str) -> dict | None:
    """The profile of the project as the daemon's snapshot shows it."""
    try:
        project = await workspace_daemon.inspect_project(
            nats_client, workspace_root, snapshot=True
        )
    except (workspace_daemon.DaemonUnavailable, workspace_daemon.DaemonError):
        project = None
    except Exception as e:  # noqa: BLE001 - A fake or odd daemon reply.
        logger.info("Could not inspect %s for its profile: %s", workspace_root, e)
        project = None
    if not project or not project.get("is_dir"):
        return await asyncio.to_thread(stack_profiles.detect, workspace_root)

    def _in_snapshot() -> dict | None:
        with workspace_daemon.materialized_snapshot(
            project.get("snapshot") or {}
        ) as mirror:
            return stack_profiles.detect(mirror)

    return await asyncio.to_thread(_in_snapshot)
