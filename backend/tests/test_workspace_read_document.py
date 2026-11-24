"""``workspace__read_document``: a DOCX's text from the daemon (issue #766).

Documenting a whole project reads the documents already in it. The daemon's
``document_text`` runs for real on a project in ``tmp_path`` through a
synchronous NATS fake validated against ``workspace.read``'s schema: the model
sees the text, redacted and bounded; the observation carries the sha256 of the
file's bytes as the daemon read them; a PDF is refused with the reason; the
tool is offered only when every daemon announces ``document_text``.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import document_text  # the daemon's modules, on sys.path via conftest
import workspace_read
from developer_tools_helpers import _call
from services.tools import developer_tools, workspace_documents, workspace_edits
from services.workspace import daemon_liveness

SCHEMA = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "vscode-daemon/protocol/schemas/v1/workspace.read.json"
    ).read_text()
)
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _validator(part: str) -> Draft202012Validator:
    return Draft202012Validator({**SCHEMA, "$ref": f"#/$defs/{part}"})


def _docx(paragraphs: list[str]) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>',
        )
    return out.getvalue()


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "spec.docx").write_bytes(
        _docx(["The service keeps orders.", "password = 'hunter2hunter2'"])
    )
    (root / "docs" / "manual.pdf").write_bytes(b"%PDF-1.4\n")
    return root


def _resolve(root, relative):
    return Path(root) / relative


class _Daemon:
    def __init__(self, extracts: bool = True):
        self.calls: list[dict] = []
        self.extracts = extracts

    def __call__(self, subject, payload, timeout):
        assert subject == "workspace.read"
        self.calls.append(payload)
        _validator("request").validate(payload)
        reader = (
            document_text.read_document
            if self.extracts and payload.get("extract") == "text"
            else workspace_read.read_file
        )
        try:
            reply = reader(
                payload["workspace_root"],
                payload["path"],
                payload.get("start_line"),
                _resolve,
                payload.get("lines"),
            )
        except workspace_read.ReadError as exc:
            reply = {"error": {"status": exc.status, "message": exc.message}}
        _validator("reply").validate(reply)
        return developer_tools._checked(reply)


@pytest.fixture()
def heard():
    def hear(features):
        daemon_liveness.reset()
        daemon_liveness.observe_heartbeat(
            {
                "daemon_id": "d1",
                "abilities": {
                    "abilities_version": 1,
                    "subjects": ["workspace.read"],
                    "features": {"workspace.read": features},
                },
            }
        )

    yield hear
    daemon_liveness.reset()


def _box(project, daemon):
    box = developer_tools.WorkspaceToolbox(daemon, str(project))
    workspace_documents.offer(box)
    return box


def test_a_docx_is_read_redacted_with_the_files_sha256(project, heard):
    heard(["document_text"])
    box = _box(project, _Daemon())
    assert box.tools["workspace__read_document"]["read_only"]
    obs, text = box.call(_call("workspace__read_document", path="docs/spec.docx"), 1)
    data = (project / "docs" / "spec.docx").read_bytes()
    assert not obs["is_error"] and obs["read_only"]
    assert "The service keeps orders." in text
    assert "hunter2hunter2" not in text and "redacted" in text
    assert obs["sha256"] == hashlib.sha256(data).hexdigest()
    assert obs["bytes"] == len(data) and obs["format"] == "docx"
    assert obs["path"] == "docs/spec.docx"


def test_a_pdf_is_refused_with_the_reason(project, heard):
    heard(["document_text"])
    obs, text = _box(project, _Daemon()).call(
        _call("workspace__read_document", path="docs/manual.pdf"), 1
    )
    assert obs["is_error"] and "PDF text extraction is not available" in text


@pytest.mark.parametrize("path", ["../x.docx", ".env", "/etc/x.docx", "src/app.py"])
def test_a_path_outside_secret_or_not_a_document_is_refused_here(project, heard, path):
    heard(["document_text"])
    daemon = _Daemon()
    obs, _ = _box(project, daemon).call(_call("workspace__read_document", path=path), 1)
    assert obs["is_error"] and daemon.calls == []


def test_offered_only_when_announced_and_an_older_reply_is_not_text(project, heard):
    heard([])
    box = _box(project, _Daemon())
    assert "workspace__read_document" not in box.tools
    assert "workspace__read_document" not in workspace_edits.offered(box)["names"]
    heard(["document_text"])
    # A daemon that announced it but answered with the bytes: never shown as text.
    obs, text = _box(project, _Daemon(extracts=False)).call(
        _call("workspace__read_document", path="docs/spec.docx"), 1
    )
    assert obs["is_error"] and "did not extract" in text
