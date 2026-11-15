"""Research tools: the roles that decide read official sources, and every
source is evidence.

The Architect choosing a library, the PO writing a quality target, SecOps
judging a dependency and TechWriter documenting an API decide with what their
model remembers up to its cut-off. These tools, offered in the same loop as
the workspace and MCP tools (``mcp_tools.chat_with_tools``), let them check:

- ``research__fetch``: one page, as text, bounded, from a host the company
  allows (``allowed_hosts``: the official documentation hosts of the common
  stacks, the registries and the advisory databases by default, plus what a
  Manager adds); anything else is refused and the refusal recorded;
- ``research__package``: a package's record at its registry (PyPI, npm,
  crates.io, the Go proxy) by the registries' own APIs: the latest version,
  when it was released, the last versions;
- ``research__advisories``: the known vulnerabilities of a package (and a
  version) from OSV.

Each call is an observation the backend made: the URL, when it was fetched,
the HTTP status, the sha256 and size of what came back and whether the host
was allowed, recorded like a workspace read and listed by the Verifier's
``sources`` check. What the model concludes from a source stays a claim.
No credential leaves the backend: the fetch carries none. A web search comes
from an MCP server the company configures, not from here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from config import settings

from services.evidence.sources import (  # noqa: F401 - re-exported
    check_sources,
    packages_in,
    sources_of,
)
from services.tools import mcp_tools
from services.tools.research_http import default_http
from services.tools.research_registries import (
    ECOSYSTEMS,
    OSV_ECOSYSTEMS,
    ResearchError,
    advisory_rows,
    package_record,
)
from services.tools.research_schemas import ADVISORIES, FETCH, PACKAGE, SCHEMAS, SERVER

logger = logging.getLogger(__name__)

MAX_REDIRECTS = 3
# The official documentation of the common stacks, the registries and the
# advisory databases. A company adds hosts; nothing here is removed by it.
DEFAULT_HOSTS = (
    "docs.python.org",
    "peps.python.org",
    "packaging.python.org",
    "pypi.org",
    "docs.djangoproject.com",
    "fastapi.tiangolo.com",
    "docs.pydantic.dev",
    "docs.sqlalchemy.org",
    "sqlmodel.tiangolo.com",
    "docs.pytest.org",
    "nodejs.org",
    "developer.mozilla.org",
    "react.dev",
    "vitejs.dev",
    "vite.dev",
    "www.typescriptlang.org",
    "registry.npmjs.org",
    "www.npmjs.com",
    "docs.npmjs.com",
    "crates.io",
    "docs.rs",
    "doc.rust-lang.org",
    "pkg.go.dev",
    "go.dev",
    "proxy.golang.org",
    "api.osv.dev",
    "osv.dev",
    "github.com",
    "docs.github.com",
    "learn.microsoft.com",
    "docs.oracle.com",
    "kubernetes.io",
    "docs.docker.com",
    "www.postgresql.org",
    "redis.io",
    "docs.nats.io",
    "www.rfc-editor.org",
    "owasp.org",
    "cheatsheetseries.owasp.org",
    "nvd.nist.gov",
    "cve.mitre.org",
)
_TAG = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_BLOCK = re.compile(
    r"</?(?:p|div|h[1-6]|li|ul|ol|tr|table|section|article|header|footer|pre|blockquote|br|hr|dt|dd)\b[^>]*>",
    re.IGNORECASE,
)
_TAGS = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n\s*\n+")


def research_roles() -> set[str]:
    return {r.strip().lower() for r in settings.RESEARCH_ROLES.split(",") if r.strip()}


def offered_to(role: str | None) -> bool:
    """Whether a role gets the research tools."""
    return (
        settings.RESEARCH_TOOLS_ENABLED
        and str(role or "").strip().lower() in research_roles()
    )


def allowed_hosts(extra: Any = None) -> set[str]:
    """The hosts a fetch may reach: the defaults, the deployment's
    ``RESEARCH_ALLOWED_HOSTS`` and the company's own list."""
    hosts = set(DEFAULT_HOSTS)
    hosts.update(
        h.strip().lower()
        for h in settings.RESEARCH_ALLOWED_HOSTS.split(",")
        if h.strip()
    )
    for host in extra or []:
        if isinstance(host, str) and host.strip():
            hosts.add(host.strip().lower())
    return hosts


_HOST = re.compile(
    r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$|^localhost$|^(\d{1,3}\.){3}\d{1,3}$"
)


def valid_host(host: str) -> bool:
    """An exact host name (or ``localhost`` / an IPv4 address for a mirror)."""
    return bool(host) and bool(_HOST.match(host))


def host_allowed(url: str, hosts: set[str]) -> tuple[bool, str]:
    """Whether *url* may be fetched, and its host. Only ``https``, except
    for a host the deployment or the company named itself (a local mirror,
    the e2e's fake registry)."""
    parts = urlsplit(str(url or "").strip())
    host = (parts.hostname or "").lower()
    if not host:
        return False, host
    named = host in hosts or any(host.endswith("." + h) for h in hosts)
    if parts.scheme == "https":
        return named, host
    if parts.scheme == "http":
        explicit = host in hosts and host not in DEFAULT_HOSTS
        return explicit, host
    return False, host


def operator_named(host: str) -> bool:
    """Whether the deployment itself named *host* in ``RESEARCH_ALLOWED_HOSTS``:
    the only hosts a fetch may reach on a private address (a local mirror,
    the e2e's fake registry). A default host or one on a company's list never
    may (WSTG-v42-INPV-19)."""
    named = {
        h.strip().lower()
        for h in settings.RESEARCH_ALLOWED_HOSTS.split(",")
        if h.strip()
    }
    return host.lower() in named


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def html_to_text(html: str) -> str:
    """The text of a page, scripts and styles dropped, tags removed, blanks folded."""
    text = _TAG.sub(" ", html)
    text = _BLOCK.sub("\n\n", text)
    text = _TAGS.sub(" ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
    )
    text = _SPACES.sub(" ", text)
    return _BLANKS.sub(
        "\n\n", "\n".join(line.strip() for line in text.splitlines())
    ).strip()


class ResearchToolbox:
    """The research tools for one task or draft, in the ``mcp_tools.Toolbox`` shape."""

    def __init__(
        self,
        *,
        hosts: set[str] | None = None,
        http: Any = None,
        result_max_chars: int | None = None,
    ) -> None:
        self.hosts = hosts if hosts is not None else allowed_hosts()
        self.http = http or default_http
        self.result_max_chars = result_max_chars or settings.MCP_RESULT_MAX_CHARS
        self.unavailable: dict[str, str] = {}
        self.max_rounds = settings.RESEARCH_TOOL_ROUNDS

    @property
    def tools(self) -> dict[str, dict[str, Any]]:
        return {
            s["function"]["name"]: {"server": SERVER, "read_only": True}
            for s in SCHEMAS
        }

    def schemas(self) -> list[dict[str, Any]]:
        return list(SCHEMAS)

    def call(
        self, tool_call: dict[str, Any], round_no: int
    ) -> tuple[dict[str, Any], str]:
        function = tool_call.get("function") or {}
        full_name = str(function.get("name") or "")
        tool = full_name.partition(mcp_tools.TOOL_NAME_SEP)[2]
        raw = function.get("arguments")
        try:
            arguments = (json.loads(raw) if isinstance(raw, str) else raw) or {}
            if not isinstance(arguments, dict):
                raise TypeError("arguments must be an object")
        except (ValueError, TypeError) as exc:
            observation = self._observation(
                tool, {"_raw": str(raw)[:500]}, round_no, True, 0, ""
            )
            observation["reason"] = f"malformed arguments: {exc}"
            return observation, f"Error: malformed arguments ({exc})."
        if full_name not in self.tools:
            observation = self._observation(tool, arguments, round_no, True, 0, "")
            observation["reason"] = "unknown tool"
            return observation, mcp_tools.NO_TOOL_NOTE
        started = time.monotonic()
        extra: dict[str, Any] = {}
        try:
            text = self._run(tool, arguments, extra)
            is_error = False
        except ResearchError as exc:
            text, is_error = f"Error: {str(exc)[:300]}", True
        except Exception as exc:  # noqa: BLE001 - The failure is the result the model sees.
            reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            text, is_error = f"Error: {reason[:300]}", True
        elapsed_ms = int((time.monotonic() - started) * 1000)
        observation = self._observation(
            tool, arguments, round_no, is_error, elapsed_ms, text
        )
        observation.update(extra)
        if len(text) > self.result_max_chars:
            observation["truncated"] = True
            text = text[: self.result_max_chars] + "\n[result cut here]"
        return observation, text

    # -- the tools --------------------------------------------------------

    def _get(
        self, url: str, extra: dict[str, Any], body: Any = None
    ) -> tuple[int, dict[str, str], bytes]:
        """A GET (or a POST with *body*) of an allowed URL, redirects followed
        while they stay allowed; the record on *extra*."""
        current = url
        for _hop in range(MAX_REDIRECTS + 1):
            allowed, host = host_allowed(current, self.hosts)
            extra.update(
                {
                    "url": current,
                    "host": host,
                    "allowed": allowed,
                    "fetched_at": datetime.now(UTC).isoformat(),
                }
            )
            if not allowed:
                extra["status"] = None
                raise ResearchError(
                    f"The host {host or current!r} is not on the company's list of sources; ask a Manager to add it."
                )
            status, headers, data = self.http(
                "POST" if body is not None else "GET",
                current,
                body,
                settings.RESEARCH_TIMEOUT_SECONDS,
                allow_private=operator_named(host),
            )
            extra.update(
                {"status": status, "sha256": _sha256(data), "bytes": len(data)}
            )
            if status in (301, 302, 303, 307, 308) and headers.get("location"):
                current = headers["location"]
                continue
            return status, headers, data
        raise ResearchError("Too many redirects.")

    def _run(self, tool: str, arguments: dict[str, Any], extra: dict[str, Any]) -> str:
        if tool == FETCH:
            url = str(arguments.get("url") or "").strip()
            if not url:
                raise ResearchError("A URL is required.")
            status, headers, data = self._get(url, extra)
            if status >= 400:
                raise ResearchError(f"The page answered {status}.")
            kind = (headers.get("content-type") or "").split(";")[0].strip().lower()
            if kind in ("application/json", "text/json"):
                text = json.dumps(
                    json.loads(data.decode("utf-8", errors="replace")),
                    indent=1,
                    ensure_ascii=False,
                )
            elif kind.startswith("text/html") or kind == "application/xhtml+xml":
                text = html_to_text(data.decode("utf-8", errors="replace"))
            elif kind.startswith("text/") or not kind:
                text = data.decode("utf-8", errors="replace")
            else:
                raise ResearchError(f"The page is {kind}, not text.")
            extra["content_type"] = kind
            if len(data) >= settings.RESEARCH_FETCH_MAX_BYTES:
                extra["cut_at_bytes"] = settings.RESEARCH_FETCH_MAX_BYTES
                text += "\n[page cut here]"
            return text or "(empty page)"
        if tool == PACKAGE:
            name, ecosystem = self._package_args(arguments)
            extra.update({"package": name, "ecosystem": ecosystem})
            record = package_record(self._get, name, ecosystem, extra)
            return json.dumps(record, indent=1, ensure_ascii=False)
        if tool == ADVISORIES:
            name, ecosystem = self._package_args(arguments)
            version = str(arguments.get("version") or "").strip() or None
            extra.update({"package": name, "ecosystem": ecosystem, "version": version})
            query: dict[str, Any] = {
                "package": {"name": name, "ecosystem": OSV_ECOSYSTEMS[ecosystem]}
            }
            if version:
                query["version"] = version
            status, _headers, data = self._get(
                settings.RESEARCH_OSV_URL.rstrip("/") + "/v1/query", extra, body=query
            )
            if status >= 400:
                raise ResearchError(f"OSV answered {status}.")
            found = (json.loads(data.decode("utf-8", errors="replace")) or {}).get(
                "vulns"
            ) or []
            rows = advisory_rows(found, name)
            extra["advisories"] = len(found)
            return json.dumps(
                {
                    "package": name,
                    "ecosystem": ecosystem,
                    "version": version,
                    "count": len(found),
                    "advisories": rows,
                },
                indent=1,
                ensure_ascii=False,
            )
        raise ResearchError("unknown tool")

    @staticmethod
    def _package_args(arguments: dict[str, Any]) -> tuple[str, str]:
        name = str(arguments.get("name") or "").strip()
        ecosystem = str(arguments.get("ecosystem") or "").strip().lower()
        if not name or any(ch in name for ch in " /\\?#") and ecosystem != "go":
            raise ResearchError("A package name is required.")
        if ecosystem not in ECOSYSTEMS:
            raise ResearchError(
                f"The ecosystem must be one of {', '.join(ECOSYSTEMS)}."
            )
        return name, ecosystem

    def _observation(
        self,
        tool: str,
        arguments: dict[str, Any],
        round_no: int,
        is_error: bool,
        elapsed_ms: int,
        text: str,
    ) -> dict[str, Any]:
        return {
            "server": SERVER,
            "tool": tool,
            "arguments": json.loads(json.dumps(arguments, default=str))
            if arguments
            else {},
            "round": round_no,
            "is_error": bool(is_error),
            "duration_ms": elapsed_ms,
            "result_sha256": _sha256(text.encode("utf-8")),
            "result_chars": len(text),
            "truncated": False,
            "read_only": True,
        }

    def close(self) -> None:
        pass

    def lookup_package(
        self, name: str, ecosystem: str, version: str | None = None
    ) -> dict[str, Any]:
        """One package's registry answer, recorded like a tool call (the Verifier's).

        ``exists`` is ``False`` only when the registry said 404, ``None`` when
        it could not be asked; ``requested_exists`` answers an exact version
        where the registry lists versions (never for the Go proxy, which
        answered ``@latest``). ``server`` is ``registry``, not the research
        server: the backend made this call, not the run.
        """
        extra: dict[str, Any] = {}
        started = time.monotonic()
        record: dict[str, Any] | None = None
        reason: str | None = None
        try:
            record = package_record(self._get, name, ecosystem, extra, version)
        except ResearchError as exc:
            reason = str(exc)[:300]
        except Exception as exc:  # noqa: BLE001 - The failure is the answer we record.
            reason = f"{type(exc).__name__}: {exc}"[:300]
        status = extra.get("status")
        observation = self._observation(
            "package",
            {"name": name, "ecosystem": ecosystem, "version": version},
            0,
            record is None,
            int((time.monotonic() - started) * 1000),
            "",
        )
        observation.update(
            {
                "server": "registry",
                "by": "verifier",
                "package": name,
                "ecosystem": ecosystem,
                "version": version,
                "exists": True if record else (False if status == 404 else None),
                "requested_exists": (record.get("has_requested") if record else None),
                "versions": record.get("versions") if record else [],
                "latest": record.get("latest") if record else None,
                "versions_complete": bool(record.get("versions_complete"))
                if record
                else False,
            }
        )
        for key in (
            "url",
            "host",
            "allowed",
            "fetched_at",
            "status",
            "sha256",
            "bytes",
        ):
            if key in extra:
                observation[key] = extra[key]
        if record and record.get("requested_yanked"):
            observation["requested_yanked"] = True
        if reason:
            observation["reason"] = reason
        return observation


def freshness_section(stack_versions: dict[str, str] | None = None) -> str:
    """The "## Sources" prompt section for a research role: the cut-off, the
    versions on disk, and the rule that a fact is checked before it is asserted."""
    lines = [
        "",
        "## Sources",
        (
            "Your knowledge has a cut-off date; versions, APIs and security facts "
            "move after it. The project's own versions are the ones on disk, "
            "listed below when known. Before you assert a version, an API or a "
            "security fact, check it at the source with the research tools "
            "(the registry for a package, the documentation for an API, OSV for "
            "a vulnerability) and say which source you rely on. A source you did "
            "not consult is a guess; say so."
        ),
    ]
    if stack_versions:
        lines += ["", "Versions on disk:"] + [
            f"- {k}: {v}" for k, v in sorted(stack_versions.items())[:40]
        ]
    return "\n".join(lines) + "\n"


def prompt_section(
    role: str | None, stack_versions: dict[str, str] | None = None
) -> str:
    """The sources section for a role that gets the tools; empty otherwise."""
    return freshness_section(stack_versions) if offered_to(role) else ""


def company_hosts(session: Any, company_id: int | None) -> list[str]:
    """The hosts the company added to the defaults (``Company.research_hosts``)."""
    if session is None or company_id is None:
        return []
    from models.company import Company

    company = session.get(Company, company_id)
    hosts = (company.research_hosts if company is not None else None) or []
    return [str(h).strip().lower() for h in hosts if isinstance(h, str) and h.strip()]


def toolbox_for_request(role: str, request: dict[str, Any]) -> ResearchToolbox | None:
    """The toolbox for a ``prepare``d draft request (no database): its
    ``research_hosts`` are the company's; ``None`` for a role without the tools."""
    if not offered_to(role):
        return None
    return ResearchToolbox(hosts=allowed_hosts(request.get("research_hosts") or []))


def toolbox_for(
    company_id: int | None, role: str | None, *, engine: Any = None
) -> ResearchToolbox | None:
    """The toolbox for a task of *role* in *company_id*, with the company's
    hosts read from *engine*; ``None`` when the role does not get the tools."""
    if not offered_to(role):
        return None
    extra: list[str] = []
    if company_id is not None and engine is not None:
        try:
            from sqlmodel import Session

            with Session(engine) as session:
                extra = company_hosts(session, company_id)
        except Exception as exc:  # noqa: BLE001 - The defaults still apply.
            logger.warning(
                "Could not read the research hosts of company=%s: %s", company_id, exc
            )
    return ResearchToolbox(hosts=allowed_hosts(extra))
