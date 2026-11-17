"""GitHub's API for the cycle runner (``scripts/rsi/cycle.py``; REQ-8, REQ-17, REQ-18).

Every call goes through ``request``, the one function that reaches the
network, so the tests replace it and make no call. What the cycle reads:

- the check-runs of a commit, by its sha (``GET
  /repos/{owner}/{repo}/commits/{sha}/check-runs``, every page), never the
  pull request's body or comments (REQ-8);
- the head sha of a pull request, before and after the measurements (REQ-17:
  a head that moved voids the decision);
- the reviews of a pull request, for the code owners' approval of a protected
  path at C's sha (``scripts/protected_paths_check.py`` decides from them).

What it may write: closing a pull request (``PATCH .../pulls/{n}`` with
``{"state": "closed"}``) on ``revert`` or ``unmeasured``, only when a person
asks for it with ``--close-pr`` (REQ-18). There is no merge, approval or push
here, and none is ever added: a person merges (REQ-17, ``merge_self_change``).

The token is read from the environment (``GITHUB_TOKEN``, else ``GH_TOKEN``)
and is never written to a file or an output, nor passed to a command the
cycle runs (``cycle_steps.WITHHELD_ENV``): those run the build under test.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

DEFAULT_API = "https://api.github.com"
PER_PAGE = 100
MAX_PAGES = 30
TIMEOUT_SECONDS = 30


class GitHubError(RuntimeError):
    """A call that failed or answered something the cycle cannot read."""


def token() -> str:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""


def api_root() -> str:
    return os.environ.get("GITHUB_API_URL", DEFAULT_API).rstrip("/")


def request(
    method: str, url: str, body: dict[str, Any] | None = None
) -> tuple[int, Any]:
    """The adapter: one HTTPS call, its status and its JSON (None when empty)."""
    if not url.startswith("https://"):
        raise GitHubError(f"refusing a non-https API URL: {url}")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    call = urllib.request.Request(url, data=data, method=method)
    call.add_header("Accept", "application/vnd.github+json")
    call.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        call.add_header("Content-Type", "application/json")
    secret = token()
    if secret:
        call.add_header("Authorization", f"Bearer {secret}")
    try:
        with urllib.request.urlopen(call, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except (urllib.error.URLError, OSError) as exc:
        raise GitHubError(f"{method} {url} failed: {exc}") from exc
    try:
        return status, json.loads(raw) if raw else None
    except ValueError as exc:
        raise GitHubError(f"{method} {url} did not answer JSON") from exc


def _get(url: str) -> Any:
    status, payload = request("GET", url)
    if status != 200:
        raise GitHubError(f"GET {url} answered {status}")
    return payload


def check_runs(repository: str, sha: str) -> tuple[dict[str, Any], list[str]]:
    """Every check-run of *sha*, as GitHub's listing (``total_count``,
    ``check_runs``), and the URLs read. Pages until the listing is whole."""
    runs: list[dict] = []
    urls: list[str] = []
    total = None
    for page in range(1, MAX_PAGES + 1):
        url = (
            f"{api_root()}/repos/{repository}/commits/{sha}/check-runs"
            f"?per_page={PER_PAGE}&page={page}"
        )
        urls.append(url)
        payload = _get(url)
        if not isinstance(payload, dict) or not isinstance(
            payload.get("check_runs"), list
        ):
            raise GitHubError(f"GET {url} did not answer a check-run listing")
        total = payload.get("total_count")
        batch = payload["check_runs"]
        runs.extend(batch)
        if len(batch) < PER_PAGE or (isinstance(total, int) and len(runs) >= total):
            break
    return {"total_count": total, "check_runs": runs}, urls


def pull_head(repository: str, number: int) -> tuple[str, str]:
    """The pull request's head sha, and the URL read."""
    url = f"{api_root()}/repos/{repository}/pulls/{number}"
    payload = _get(url)
    head = (payload or {}).get("head") if isinstance(payload, dict) else None
    sha = head.get("sha") if isinstance(head, dict) else None
    if not isinstance(sha, str) or len(sha) != 40:
        raise GitHubError(f"GET {url} names no head sha")
    return sha, url


def reviews(repository: str, number: int) -> list[dict]:
    """Every review of the pull request, page by page."""
    found: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        url = (
            f"{api_root()}/repos/{repository}/pulls/{number}/reviews"
            f"?per_page={PER_PAGE}&page={page}"
        )
        batch = _get(url)
        if not isinstance(batch, list):
            raise GitHubError(f"GET {url} did not answer a list")
        found.extend(batch)
        if len(batch) < PER_PAGE:
            return found
    raise GitHubError(f"more than {MAX_PAGES * PER_PAGE} reviews")


def close_pull(repository: str, number: int) -> int:
    """Close the pull request unmerged (REQ-18); the status GitHub answered."""
    url = f"{api_root()}/repos/{repository}/pulls/{number}"
    status, _ = request("PATCH", url, {"state": "closed"})
    return status
