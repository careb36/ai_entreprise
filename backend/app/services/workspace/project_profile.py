"""A delivery's stack profile: the daemon's snapshot with the company's overrides.

The commands a delivery's checks run (install, tests, start, e2e) come from
the stack profile (``stack_profiles``): detected from the project as the
daemon's snapshot shows it (``workspace.inspect``), then the deployment's and
the company's overrides (``Company.stack_profile``). Configuration, never
evidence. ``Context`` reads the company and the profile at most once per
delivery; the dependency install (``dependency_install``), the delivery's
checks (``services/loop/delivery_checks.py``), QA's browser checks and the lint
after an edit read it. ``Context.folders`` is the profile per folder of a
repository that holds several projects (``stack_roots``), read from the same
snapshot.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from config import settings

from services.workspace import stack_profiles, stack_roots, workspace_daemon

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
        self._detected: tuple[dict | None, dict] | None = None
        self._folders: list[dict[str, Any]] | None = None

    def company(self) -> dict[str, Any]:
        """``id``, ``overrides`` and ``installs`` of the sprint's company."""
        if self._company is None:
            self._company = read_company(self.sprint, self.engine)
        return self._company

    async def _detect(self) -> tuple[dict | None, dict]:
        if self._detected is None:
            self._detected = await detect_project(
                self.nats, self.evidence.get("workspace_root") or ""
            )
        return self._detected

    async def profile(self) -> dict[str, Any]:
        if self._profile is None:
            detected, _folders = await self._detect()
            self._profile = stack_profiles.resolve(
                detected, self.company().get("overrides")
            )
        return self._profile

    async def folders(self) -> list[dict[str, Any]]:
        """The folders with a profile of their own (``stack_roots``): none
        for a single-root project whose company names none."""
        if self._folders is None:
            overrides = self.company().get("overrides") or {}
            found: dict = {}
            # Folders are searched for only when the root has neither a test
            # command a person named nor a stack of its own (a stack the
            # company names counts): a single-root project, or one whose
            # company named one command for the whole repository, runs as
            # before. The folders the company names always count.
            if not self._named_test(overrides):
                root = await self.profile()
                if not root.get("stack"):
                    found = (await self._detect())[1]
            self._folders = stack_roots.resolve_folders(None, found, overrides)
        return self._folders

    @staticmethod
    def _named_test(overrides: dict[str, Any]) -> bool:
        if overrides.get("test") is not None:  # The company's word, even "off".
            return bool(str(overrides["test"]).strip())
        return bool(settings.DELIVERY_TEST_COMMAND.strip())

    def every_delivery(self) -> list[dict[str, str]]:
        """The commands the company runs on every delivery."""
        return stack_roots.every_delivery(self.company().get("overrides"))

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
    return (await detect_project(nats_client, workspace_root))[0]


def _detect_both(root: str) -> tuple[dict | None, dict]:
    profile = stack_profiles.detect(root)
    # Sub-folders are searched only for a root without a stack of its own.
    return profile, ({} if profile else stack_roots.detect_folders(root))


async def detect_project(
    nats_client: Any, workspace_root: str
) -> tuple[dict | None, dict]:
    """The root's profile and the folders with their own (``stack_roots``),
    as the daemon's snapshot shows the project."""
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
        return await asyncio.to_thread(_detect_both, workspace_root)

    def _in_snapshot() -> tuple[dict | None, dict]:
        with workspace_daemon.materialized_snapshot(
            project.get("snapshot") or {}
        ) as mirror:
            return _detect_both(mirror)

    return await asyncio.to_thread(_in_snapshot)
