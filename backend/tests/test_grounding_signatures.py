"""The symbol part of the ``grounded`` check (issue #444): a call to a project
function or class with arguments its signature does not take is a finding.

Here the daemon's ``workspace.signatures`` (``vscode-daemon/signatures.py``)
runs for real against ``tmp_path``: each parameter kind, constructors, a file
that does not parse, what it never reads, its bounds, and the answer over
NATS. The comparison is in ``test_grounding_signature_check.py``, the deliver
stage in ``test_grounding_signature_deliver.py``; the shared daemon in
``grounding_signatures_helpers``.
"""

from __future__ import annotations

import asyncio
import json

import grounding_signatures_helpers
import pytest
import signatures as daemon_signatures  # the daemon's module, on sys.path via conftest
from services.evidence import signatures as symbol_check

# The shared fixtures.
daemon = grounding_signatures_helpers.daemon


# ---------------------------------------------------------------------------
# The daemon: workspace.signatures
# ---------------------------------------------------------------------------

LIB = """
import functools
from dataclasses import dataclass


def every_kind(a, /, b, c=1, *args, d, e=2, **kw):
    return a


def plain(x, y, password="hunter2-very-secret"):
    return x


async def later(x, *, retries=3):
    return x


class Point:
    def __init__(self, x, y=0):
        self.x = x


class Bare:
    pass


class Child(Point):
    pass


@dataclass
class Record:
    name: str


@functools.lru_cache
def cached(x):
    return x


def twice(x):
    return x


twice = functools.partial(twice, 1)

if True:
    def conditional(x):
        return x
"""


def _ask(daemon, root, wanted):
    return daemon_signatures.signatures(
        str(root), {"wanted": wanted}, daemon._resolve_target_path
    )


@pytest.fixture()
def project(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "lib.py").write_text(LIB)
    return tmp_path


def test_the_daemon_reports_each_parameter_kind(daemon, project):
    names = ["every_kind", "plain", "later"]
    row = _ask(daemon, project, [{"path": "pkg/lib.py", "names": names}])["files"][0]
    assert row["status"] == "read" and len(row["sha256"]) == 64
    kind = row["names"]["every_kind"]
    assert kind["status"] == "found" and kind["kind"] == "function"
    assert kind["positional_only"] == ["a"]
    assert kind["positional"] == ["b", "c"]
    assert kind["positional_defaults"] == 1
    assert kind["varargs"] is True and kind["varkw"] is True
    assert kind["keyword_only"] == [
        {"name": "d", "default": False},
        {"name": "e", "default": True},
    ]
    assert row["names"]["later"]["keyword_only"] == [
        {"name": "retries", "default": True}
    ]
    # Only whether a default exists, never its value.
    assert "hunter2" not in json.dumps(row)


def test_the_daemon_reports_constructors_and_leaves_guesses_unknown(daemon, project):
    wanted = [
        "Point",
        "Bare",
        "Child",
        "Record",
        "cached",
        "twice",
        "conditional",
        "nowhere",
    ]
    names = _ask(daemon, project, [{"path": "pkg/lib.py", "names": wanted}])["files"][
        0
    ]["names"]
    assert names["Point"]["status"] == "found" and names["Point"]["kind"] == "class"
    assert names["Point"]["positional"] == ["x", "y"]
    assert names["Point"]["positional_defaults"] == 1
    assert names["Bare"]["status"] == "found" and names["Bare"]["positional"] == []
    assert names["Child"]["status"] == "unknown"  # inherited constructor
    assert names["Record"]["status"] == "unknown"  # a decorator writes __init__
    assert names["cached"]["status"] == "unknown"  # decorated
    assert names["twice"]["status"] == "unknown"  # rebound
    assert names["conditional"]["status"] == "unknown"  # not a plain definition
    assert names["nowhere"] == {"status": "missing"}


def test_the_daemon_says_when_a_file_does_not_parse(daemon, project):
    (project / "pkg" / "broken.py").write_text("def f(:\n")
    row = _ask(daemon, project, [{"path": "pkg/broken.py", "names": ["f"]}])["files"][0]
    assert row["status"] == "does not parse" and row["sha256"]


def test_the_daemon_never_reads_secrets_symlinks_out_or_vcs(daemon, project, tmp_path):
    (project / "secrets.py").write_text("def key(): pass\n")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.py"
    outside.write_text("def f(x): pass\n")
    (project / "out.py").symlink_to(outside)
    (project / ".git").mkdir()
    (project / ".git" / "hook.py").write_text("def f(x): pass\n")
    rows = _ask(
        daemon,
        project,
        [
            {"path": "secrets.py", "names": ["key"]},
            {"path": "out.py", "names": ["f"]},
            {"path": ".git/hook.py", "names": ["f"]},
            {"path": "../x.py", "names": ["f"]},
            {"path": "README.md", "names": ["f"]},
            {"path": "pkg/none.py", "names": ["f"]},
        ],
    )["files"]
    assert [row["status"] for row in rows] == [
        "refused",
        "refused",
        "refused",
        "refused",
        "refused",
        "unreadable",
    ]
    assert all("names" not in row for row in rows)


def test_the_daemon_refuses_a_request_over_its_bounds(daemon, project):
    from workspace_read import ReadError

    too_many_paths = [
        {"path": f"m{i}.py", "names": ["f"]}
        for i in range(daemon_signatures.MAX_PATHS + 1)
    ]
    with pytest.raises(ReadError) as paths:
        _ask(daemon, project, too_many_paths)
    assert paths.value.status == 400 and "paths" in paths.value.message
    too_many_names = [
        {"path": "pkg/lib.py", "names": [f"n{i}" for i in range(401)]},
    ]
    with pytest.raises(ReadError) as names:
        _ask(daemon, project, too_many_names)
    assert "names" in names.value.message
    assert daemon_signatures.MAX_PATHS == symbol_check.MAX_PATHS
    assert daemon_signatures.MAX_NAMES == symbol_check.MAX_NAMES


def test_the_daemon_answers_workspace_signatures_over_nats(daemon, project):
    assert (
        "workspace.signatures",
        daemon_signatures.handle_signatures,
    ) in daemon.signatures.HANDLERS
    replies = []

    class _Msg:
        def __init__(self, body):
            self.data = json.dumps(body).encode()
            self.reply = "inbox"

    async def reply(nc, msg, body):
        replies.append(body)

    async def run():
        for body in (
            {
                "workspace_root": str(project),
                "wanted": [{"path": "pkg/lib.py", "names": ["plain"]}],
            },
            {"wanted": []},
            {"workspace_root": str(project), "wanted": "nope"},
        ):
            await daemon_signatures.handle_signatures(
                None,
                _Msg(body),
                reply,
                daemon._request_body,
                daemon._resolve_target_path,
            )

    asyncio.run(run())
    assert replies[0]["files"][0]["names"]["plain"]["positional"] == [
        "x",
        "y",
        "password",
    ]
    assert replies[1]["error"]["status"] == 400
    assert replies[2]["error"]["status"] == 400
