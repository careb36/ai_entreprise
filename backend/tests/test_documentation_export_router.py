"""The documentation export routes (issue #694, phase 4; ``routers/documentation_export.py``).

A Viewer downloads a stored pack as DOCX or PDF with the sha256 of the bytes
sent; the export's manifest lists that same digest beside the pack's files,
is signed, and carries the audit chain's record of the pack; an outsider is
refused, a missing pack is a 404, an unknown format a 404 (after the role check), and a renderer
whose library is missing a 503 that names it, while the other format keeps
working. No request field reaches the document: the routes take none.
"""

import hashlib
from datetime import UTC, datetime

import pytest
from config import settings
from factories import enforcement  # noqa: F401 - the fixture, by name
from factories import member_headers as _member
from models import DocumentationPack
from services.planning.project_docs import export_pdf
from services.planning.project_docs import pack_manifest as manifests
from test_documentation_router import _company, _records, no_prose  # noqa: F401
from test_project_docs_export import CHARTER, PDF_REASON

TEST_KEY = "export-test-" + "signing-" + "key"


@pytest.fixture
def signing_key(monkeypatch):
    monkeypatch.setattr(settings, "AUDIT_SIGNING_KEY", TEST_KEY)


def _stored(session, company):
    pack = DocumentationPack(
        company_id=company.id,
        version=1,
        status="draft",
        language="es",
        compiled_by="local demo",
        inputs_sha256="cd" * 32,
        created_at=datetime(2026, 11, 19, 10, 30, 15, tzinfo=UTC),
        documents=[
            {"key": "charter", "title": "Acta de constitución", "markdown": CHARTER}
        ],
        diagrams=[],
    )
    session.add(pack)
    session.flush()
    manifests.record(session, pack, "local demo")
    session.refresh(pack)
    return pack


@pytest.mark.usefixtures("enforcement", "signing_key")
def test_a_viewer_downloads_the_docx_and_its_manifest_matches_the_bytes(
    sqlite_session, test_client
):
    company, other = _company(sqlite_session), _company(sqlite_session)
    pack = _stored(sqlite_session, company)
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, other.id, "Owner")
    url = f"/v1/documentation/{pack.id}/export/docx"

    assert test_client.get(url, headers=outsider).status_code == 403
    assert test_client.get(url).status_code == 401
    got = test_client.get(url, headers=viewer)
    assert got.status_code == 200, got.text
    assert got.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert (
        f'filename="documentation-pack-{pack.id}-v1.docx"'
        in got.headers["content-disposition"]
    )
    sha256 = hashlib.sha256(got.content).hexdigest()
    assert got.headers["x-content-sha256"] == sha256

    listed = test_client.get(f"{url}/manifest", headers=viewer)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    built = body["manifest"]
    assert built["type"] == manifests.EXPORT_MANIFEST_TYPE
    entry = next(f for f in built["files"] if f["path"].startswith("export/"))
    assert entry["sha256"] == sha256 and entry["bytes"] == len(got.content)
    assert built["export"]["format"] == "docx"
    assert body["signature"].startswith("hmac-sha256:")
    assert body["manifest_sha256"] == manifests.digest(built)
    assert body["audit"]["recorded"] is True and body["audit"]["matches"] is True
    files = {
        "docs/project/charter.md": CHARTER.encode("utf-8"),
        entry["path"]: got.content,
    }
    assert manifests.verify(built, body["signature"], files)["ok"] is True


@pytest.mark.usefixtures("enforcement")
def test_a_missing_pack_or_an_unknown_format_is_a_404(sqlite_session, test_client):
    company = _company(sqlite_session)
    pack = _stored(sqlite_session, company)
    viewer = _member(sqlite_session, company.id, "Viewer")
    assert (
        test_client.get(
            "/v1/documentation/999999/export/pdf", headers=viewer
        ).status_code
        == 404
    )
    assert (
        test_client.get(
            f"/v1/documentation/{pack.id}/export/html", headers=viewer
        ).status_code
        == 404
    )


@pytest.mark.usefixtures("enforcement")
@pytest.mark.skipif(
    PDF_REASON is not None, reason=f"WeasyPrint unavailable: {PDF_REASON}"
)
def test_a_viewer_downloads_the_pdf(sqlite_session, test_client):
    company = _company(sqlite_session)
    pack = _stored(sqlite_session, company)
    viewer = _member(sqlite_session, company.id, "Viewer")
    got = test_client.get(f"/v1/documentation/{pack.id}/export/pdf", headers=viewer)
    assert got.status_code == 200, got.text
    assert got.headers["content-type"] == "application/pdf"
    assert got.content.startswith(b"%PDF-1.7")
    assert got.headers["x-content-sha256"] == hashlib.sha256(got.content).hexdigest()
    listed = test_client.get(
        f"/v1/documentation/{pack.id}/export/pdf/manifest", headers=viewer
    ).json()
    assert listed["manifest"]["export"]["conformance"] == ["PDF/A-3b", "PDF/UA-1"]
    entry = next(
        f for f in listed["manifest"]["files"] if f["path"].startswith("export/")
    )
    assert entry["sha256"] == got.headers["x-content-sha256"]


@pytest.mark.usefixtures("enforcement")
def test_without_weasyprint_the_pdf_is_a_503_that_names_why_and_docx_still_works(
    sqlite_session, test_client, monkeypatch
):
    monkeypatch.setattr(export_pdf, "_checked", True)
    monkeypatch.setattr(
        export_pdf, "_import_error", "WeasyPrint cannot load: no library called pango"
    )
    company = _company(sqlite_session)
    pack = _stored(sqlite_session, company)
    viewer = _member(sqlite_session, company.id, "Viewer")
    got = test_client.get(f"/v1/documentation/{pack.id}/export/pdf", headers=viewer)
    assert got.status_code == 503
    assert "pango" in got.json()["detail"]
    assert (
        test_client.get(
            f"/v1/documentation/{pack.id}/export/pdf/manifest", headers=viewer
        ).status_code
        == 503
    )
    assert (
        test_client.get(
            f"/v1/documentation/{pack.id}/export/docx", headers=viewer
        ).status_code
        == 200
    )
    formats = test_client.get("/v1/documentation/exports", headers=viewer).json()
    assert formats["formats"]["pdf"]["available"] is False
    assert formats["formats"]["docx"] == {"available": True, "reason": None}


@pytest.mark.usefixtures("no_prose")
def test_a_compiled_pack_exports_from_what_was_stored(sqlite_session, test_client):
    company = _company(sqlite_session)
    _records(sqlite_session, company)
    pack = test_client.post(f"/v1/companies/{company.id}/documentation", json={}).json()
    first = test_client.get(f"/v1/documentation/{pack['id']}/export/docx")
    assert first.status_code == 200, first.text
    # The same stored pack gives the same bytes.
    again = test_client.get(f"/v1/documentation/{pack['id']}/export/docx")
    assert again.headers["x-content-sha256"] == first.headers["x-content-sha256"]
    # Rewriting the stored pack changes the export: it is read from the row.
    stored = sqlite_session.get(DocumentationPack, pack["id"])
    documents = [dict(d) for d in stored.documents]
    documents[0]["markdown"] += "\nUna línea nueva.\n"
    stored.documents = documents
    sqlite_session.add(stored)
    sqlite_session.commit()
    changed = test_client.get(f"/v1/documentation/{pack['id']}/export/docx")
    assert changed.headers["x-content-sha256"] != first.headers["x-content-sha256"]
