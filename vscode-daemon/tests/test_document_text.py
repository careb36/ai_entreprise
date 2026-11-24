"""A document's text through ``workspace.read`` with ``extract: "text"`` (``document_text.py``)."""

from __future__ import annotations

import asyncio
import hashlib
import io
import zipfile
from pathlib import Path

import pytest

import abilities
import document_text
import workspace_read
from workspace_read import ReadError

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _docx(body: str, prolog: str = "") -> bytes:
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>{prolog}'
           f'<w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>')
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", xml)
    return out.getvalue()


BODY = (
    '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Install</w:t></w:r></w:p>'
    '<w:p><w:r><w:t>Run the </w:t></w:r><w:r><w:t>installer</w:t><w:tab/><w:t>then</w:t></w:r></w:p>'
    '<w:p><w:r><w:t>first</w:t><w:br/><w:t>second</w:t></w:r></w:p>'
)


def _resolve(root: str, relative: str):
    return Path(root) / relative


@pytest.fixture()
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "spec.docx").write_bytes(_docx(BODY))
    (root / "docs" / "manual.pdf").write_bytes(b"%PDF-1.4\n%binary\n")
    (root / "secrets.docx").write_bytes(_docx(BODY))
    return root


def test_a_docx_reads_as_numbered_lines_with_the_files_sha256(project):
    reply = document_text.read_document(str(project), "docs/spec.docx", None, _resolve)
    data = (project / "docs" / "spec.docx").read_bytes()
    assert reply["sha256"] == hashlib.sha256(data).hexdigest() and reply["bytes"] == len(data)
    assert reply["format"] == "docx" and reply["extracted"] and not reply["binary"]
    assert reply["text"].splitlines() == [
        "    1| # Install", "    2| Run the installer\tthen", "    3| first", "    4| second",
    ]
    assert reply["lines"] == 4 and not reply["truncated"]


def test_a_window_pages_the_text(project):
    reply = document_text.read_document(str(project), "docs/spec.docx", 3, _resolve, 1)
    assert reply["text"] == "    3| first" and reply["truncated"]


def test_pdf_is_refused_with_the_reason_for_now(project):
    with pytest.raises(ReadError) as caught:
        document_text.read_document(str(project), "docs/manual.pdf", None, _resolve)
    assert caught.value.status == 415 and "PDF" in caught.value.message


@pytest.mark.parametrize("path,status", [
    ("../outside.docx", 400), ("secrets.docx", 403), ("docs/none.docx", 404),
])
def test_the_read_rules_apply(project, path, status):
    with pytest.raises(ReadError) as caught:
        document_text.read_document(str(project), path, None, _resolve)
    assert caught.value.status == status


def test_a_dtd_or_a_broken_archive_is_refused(project):
    bomb = '<!DOCTYPE d [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;">]>'
    (project / "docs" / "bomb.docx").write_bytes(_docx("<w:p><w:r><w:t>&b;</w:t></w:r></w:p>", bomb))
    (project / "docs" / "fake.docx").write_bytes(b"not a zip")
    for name in ("bomb.docx", "fake.docx"):
        with pytest.raises(ReadError) as caught:
            document_text.read_document(str(project), f"docs/{name}", None, _resolve)
        assert caught.value.status == 415


def test_the_read_subject_routes_extract_and_announces_it(project):
    assert "document_text" in abilities.announcement()["features"]["workspace.read"]
    replies = []

    async def reply(_nc, _msg, body):
        replies.append(body)

    for request in (
        {"workspace_root": str(project), "path": "docs/spec.docx", "extract": "text"},
        {"workspace_root": str(project), "path": "docs/spec.docx"},
    ):
        asyncio.run(workspace_read.handle_read(None, None, reply, lambda _m, r=request: r, _resolve))
    assert replies[0]["extracted"] and "# Install" in replies[0]["text"]
    assert replies[1]["binary"] and "extracted" not in replies[1]
