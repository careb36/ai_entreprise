"""The review comments a pull request or merge request got on its platform.

After a run's branch is published (``git_workspace.publish``), people review
it where it lives: on GitHub or GitLab. This module reads what they wrote
there, with the person's own ``gh`` or ``glab`` login on this machine, and
answers with one shape for both platforms, so the backend can bring the
comments back to the run they are about:

- ``line``: a comment on a line of a file (``path``, ``line``, ``side``);
- ``review``: a submitted review's text and its state (``APPROVED``,
  ``CHANGES_REQUESTED``, ``COMMENTED``);
- ``general``: a comment on the request as a whole.

Each item carries a stable ``external_id`` and the ``url`` of the comment,
so reading twice never duplicates anything. Nothing is written to the
platform.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

CLI_TIMEOUT_SECONDS = 60
MAX_ITEMS = 500
MAX_BODY_CHARS = 4000

_GITHUB = re.compile(r"^/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)/?$")
_GITLAB = re.compile(r"^/(?P<project>.+?)/-/merge_requests/(?P<iid>\d+)/?$")


class ReviewsError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _pages(text: str) -> list[Any]:
    """The items of a ``--paginate`` answer: JSON arrays written one after another."""
    decoder = json.JSONDecoder()
    items: list[Any] = []
    index, text = 0, text.strip()
    while index < len(text):
        page, index = decoder.raw_decode(text, index)
        items.extend(page if isinstance(page, list) else [page])
        while index < len(text) and text[index].isspace():
            index += 1
    return items


def _api(cli: str, args: list[str], cwd: Path) -> list[Any]:
    if shutil.which(cli) is None:
        raise ReviewsError(409, f"{cli} is not installed on the workspace daemon's computer")
    env = {**os.environ, "GH_PROMPT_DISABLED": "1", "NO_COLOR": "1", "GIT_TERMINAL_PROMPT": "0"}
    try:
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell.
            [cli, "api", *args], cwd=str(cwd), env=env, capture_output=True, text=True,
            timeout=CLI_TIMEOUT_SECONDS, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ReviewsError(504, f"{cli} api did not answer: {e}") from e
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise ReviewsError(409, f"{cli} api failed: {detail[-1] if detail else result.returncode}")
    try:
        return _pages(result.stdout)
    except ValueError as e:
        raise ReviewsError(502, f"{cli} api answered something that is not JSON: {e}") from e


def _text(value: Any) -> str:
    return str(value or "").strip()[:MAX_BODY_CHARS]


def _github(host: str, owner: str, repo: str, number: str, cwd: Path) -> list[dict[str, Any]]:
    base = ["--hostname", host, "--paginate"]
    prefix = f"repos/{owner}/{repo}"
    items: list[dict[str, Any]] = []
    for c in _api("gh", [*base, f"{prefix}/pulls/{number}/comments"], cwd):
        line = c.get("line") or c.get("original_line")
        items.append({
            "external_id": f"github:review_comment:{c.get('id')}",
            "kind": "line",
            "path": c.get("path"),
            "line": line if isinstance(line, int) and line > 0 else None,
            "side": "old" if c.get("side") == "LEFT" else "new",
            "body": _text(c.get("body")),
            "author": _text((c.get("user") or {}).get("login")),
            "url": c.get("html_url"),
            "state": None,
            "created_at": c.get("created_at"),
        })
    for r in _api("gh", [*base, f"{prefix}/pulls/{number}/reviews"], cwd):
        state = r.get("state")
        if state == "PENDING" or (not _text(r.get("body")) and state not in ("APPROVED", "CHANGES_REQUESTED")):
            continue
        items.append({
            "external_id": f"github:review:{r.get('id')}",
            "kind": "review",
            "path": None, "line": None, "side": "new",
            "body": _text(r.get("body")),
            "author": _text((r.get("user") or {}).get("login")),
            "url": r.get("html_url"),
            "state": state,
            "created_at": r.get("submitted_at"),
        })
    for c in _api("gh", [*base, f"{prefix}/issues/{number}/comments"], cwd):
        items.append({
            "external_id": f"github:issue_comment:{c.get('id')}",
            "kind": "general",
            "path": None, "line": None, "side": "new",
            "body": _text(c.get("body")),
            "author": _text((c.get("user") or {}).get("login")),
            "url": c.get("html_url"),
            "state": None,
            "created_at": c.get("created_at"),
        })
    return items


def _gitlab(host: str, project: str, iid: str, url: str, cwd: Path) -> list[dict[str, Any]]:
    endpoint = f"projects/{quote(project, safe='')}/merge_requests/{iid}/discussions"
    items: list[dict[str, Any]] = []
    for discussion in _api("glab", ["--hostname", host, "--paginate", endpoint], cwd):
        for note in (discussion or {}).get("notes") or []:
            if note.get("system"):
                continue
            position = note.get("position") or {}
            new_line, old_line = position.get("new_line"), position.get("old_line")
            on_line = bool(position) and (new_line or old_line)
            items.append({
                "external_id": f"gitlab:note:{note.get('id')}",
                "kind": "line" if on_line else "general",
                "path": (position.get("new_path") or position.get("old_path")) if on_line else None,
                "line": (new_line or old_line) if on_line else None,
                "side": "new" if new_line else "old",
                "body": _text(note.get("body")),
                "author": _text((note.get("author") or {}).get("username")),
                "url": f"{url.split('#')[0]}#note_{note.get('id')}",
                "state": None,
                "created_at": note.get("created_at"),
            })
    return items


def fetch(workspace_root: str, spec: dict[str, Any]) -> dict[str, Any]:
    """The comments on the request at ``spec['url']``, read with the person's CLI login."""
    url = str(spec.get("url") or "")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ReviewsError(422, f"not a pull request or merge request page: {url!r}")
    folder = Path(workspace_root).expanduser() if workspace_root else Path.home()
    cwd = folder if folder.is_dir() else Path.home()
    host = parts.hostname
    if (github := _GITHUB.match(parts.path)) and spec.get("provider") == "github":
        items = _github(host, github["owner"], github["repo"], github["number"], cwd)
    elif (gitlab := _GITLAB.match(parts.path)) and spec.get("provider") == "gitlab":
        items = _gitlab(host, gitlab["project"], gitlab["iid"], url, cwd)
    else:
        raise ReviewsError(422, f"not a GitHub pull request or GitLab merge request: {url!r}")
    items = [i for i in items if i["body"] or i["state"]]
    return {"ok": True, "url": url, "comments": items[:MAX_ITEMS], "truncated": len(items) > MAX_ITEMS}


def fit(body: dict[str, Any], max_bytes: int) -> dict[str, Any]:
    """*body* with as many comments as fit in one NATS message of *max_bytes*.

    A reply over the server's payload limit is never delivered, and the
    backend would only see a daemon that did not answer; the comments left
    out are marked as ``truncated``, like those past ``MAX_ITEMS``.
    """
    def size(b: dict[str, Any]) -> int:
        return len(json.dumps(b).encode())

    if max_bytes <= 0 or size(body) <= max_bytes:
        return body
    comments = list(body.get("comments") or [])
    low, high = 0, len(comments)
    while low < high:
        middle = (low + high + 1) // 2
        if size({**body, "comments": comments[:middle], "truncated": True}) <= max_bytes:
            low = middle
        else:
            high = middle - 1
    return {**body, "comments": comments[:low], "truncated": True}


async def handle_reviews(nc, msg, reply, request_body) -> None:
    """``workspace.git.reviews`` → the request's comments, or ``{"error": {status, message}}``."""
    import asyncio

    data = request_body(msg)
    try:
        body = await asyncio.to_thread(fetch, str(data.get("workspace_root") or ""), data)
    except ReviewsError as e:
        body = {"error": {"status": e.status, "message": e.message}}
    else:
        body = fit(body, int(getattr(nc, "max_payload", 0) or 0))
    await reply(nc, msg, body)
