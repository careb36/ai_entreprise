"""The docs-only write policy (``services/tools/docs_write_policy.py``, issue #766).

A documentation task reads all of the code and writes only documentation: the
edit tools of a docs-only toolbox refuse any other path before the daemon is
asked, with a reply that says what may be written.
"""

from __future__ import annotations

import pytest
from developer_tools_helpers import _call
from services.tools import developer_tools, docs_write_policy


class _Daemon:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, subject, payload, timeout):
        self.calls.append((subject, payload))
        return {"path": payload.get("path"), "sha256_before": "a", "sha256_after": "b"}


def _box(role):
    daemon = _Daemon()
    box = developer_tools.WorkspaceToolbox(
        daemon, "/proj", can_edit=True, edit_suffixes={".py", ".md", ".rst"}
    )
    box.lint_profile = None
    docs_write_policy.apply(box, role)
    return box, daemon


def test_a_documentation_task_refuses_a_python_edit():
    box, daemon = _box("TechWriter")
    obs, text = box.call(
        _call("workspace__edit", path="src/app.py", search="A", replace="B"), 1
    )
    assert obs["is_error"] and "documentation only" in text and "src/app.py" in text
    assert daemon.calls == [] and box.edits_made is False


@pytest.mark.parametrize(
    "path", ["docs/guide.md", "README.md", "pkg/README.rst", "CHANGES.md", "doc/x.rst"]
)
def test_a_documentation_task_edits_documentation(path):
    box, daemon = _box("TechWriter")
    box.call(_call("workspace__edit", path=path, search="A", replace="B"), 1)
    assert daemon.calls and daemon.calls[0][1]["path"] == path


def test_other_tasks_keep_their_writes():
    box, daemon = _box("Developer")
    assert box.docs_only is False
    box.call(_call("workspace__edit", path="src/app.py", search="A", replace="B"), 1)
    assert daemon.calls


def test_move_and_delete_are_refused_outside_the_docs_too():
    box, daemon = _box("TechWriter")
    box.can_move = True
    obs, text = box.call(
        _call("workspace__move", path="docs/a.md", start_line=1, end_line=2,
              to_path="src/b.py", to_line=1),
        1,
    )  # fmt: skip
    assert obs["is_error"] and "src/b.py" in text
    obs, _ = box.call(_call("workspace__delete", path="setup.py"), 1)
    assert obs["is_error"] and daemon.calls == []


def test_a_task_whose_kind_is_documentation_applies():
    assert docs_write_policy.applies(None, "documentation")
    assert docs_write_policy.applies("techwriter")
    assert not docs_write_policy.applies("QA", "implementation")
    assert not docs_write_policy.is_doc_path("src/docs_helper.py")
    assert not docs_write_policy.is_doc_path("documents/app.py")
    assert not docs_write_policy.is_doc_path("")
