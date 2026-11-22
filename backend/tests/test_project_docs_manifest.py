"""The documentation pack's signed manifest (issue #694, phase 4 of
``docs/archive/design/PROJECT_DOCUMENTATION.md``).

The manifest lists every file of a pack with the sha256 the backend computed
over the bytes it would write, the pack's inputs digest and the compile time;
it is signed with the deployment's audit signing key (``AUDIT_SIGNING_KEY``,
``audit_signing``) when one is configured, and its digest enters the
company's audit chain when the pack is compiled, so a pack rewritten in the
database no longer matches the digest the chain holds. Verification refuses
a tampered, missing or unexpected file and a manifest whose signature does
not match.
"""

import hashlib
import json
from datetime import UTC, datetime

import pytest
from config import settings
from factories import enforcement  # noqa: F401 - the fixture, by name
from factories import member_headers as _member
from models import AuditLog, DocumentationPack
from services.planning.project_docs import pack as packs
from services.planning.project_docs import pack_manifest as manifests
from sqlmodel import select
from test_documentation_router import _company, _records, no_prose  # noqa: F401

# Built by concatenation so no credential-shaped literal sits in the file.
TEST_KEY = "manifest-test-" + "signing-" + "key"


def _pack(**fields):
    defaults = {
        "id": 7,
        "company_id": 3,
        "version": 2,
        "language": "en",
        "inputs_sha256": "c0ffee" * 10 + "abcd",
        "created_at": datetime(2026, 11, 19, 10, 30, 15, 123456, tzinfo=UTC),
        "documents": [
            {"key": "index", "markdown": "# Index\n"},
            {"key": "charter", "markdown": "# Charter\n\nPlanificación ñ\n"},
            {"key": "scope", "markdown": "# Scope\n"},
        ],
    }
    defaults.update(fields)
    return DocumentationPack(**defaults)


def _files(pack):
    return {f["path"]: f["content"].encode("utf-8") for f in packs.files_of(pack)}


@pytest.fixture
def signing_key(monkeypatch):
    monkeypatch.setattr(settings, "AUDIT_SIGNING_KEY", TEST_KEY)


@pytest.fixture
def no_signing_key(monkeypatch):
    monkeypatch.setattr(settings, "AUDIT_SIGNING_KEY", None)


# --- building ------------------------------------------------------------------


def test_the_manifest_lists_every_file_with_the_sha256_the_backend_computed():
    pack = _pack()
    built = manifests.build(pack)
    assert built["type"] == manifests.MANIFEST_TYPE and built["version"] == 1
    assert (built["company_id"], built["pack_id"], built["pack_version"]) == (3, 7, 2)
    assert built["inputs_sha256"] == pack.inputs_sha256
    assert built["compiled_at"] == "2026-11-19T10:30:15Z"
    paths = [f["path"] for f in built["files"]]
    assert paths == sorted(_files(pack))
    for entry in built["files"]:
        data = _files(pack)[entry["path"]]
        assert entry["sha256"] == hashlib.sha256(data).hexdigest()
        assert entry["bytes"] == len(data)


def test_the_same_pack_gives_the_same_manifest_bytes_whatever_the_stored_timezone():
    aware = _pack()
    # A database without time zones gives the moment back naive, in UTC.
    naive = _pack(created_at=datetime(2026, 11, 19, 10, 30, 15, 999))
    assert manifests.canonical(manifests.build(aware)) == manifests.canonical(
        manifests.build(naive)
    )
    assert (
        manifests.digest(manifests.build(aware))
        == hashlib.sha256(manifests.canonical(manifests.build(aware))).hexdigest()
    )


def test_a_file_the_pack_changed_changes_the_manifest_digest():
    before = manifests.digest(manifests.build(_pack()))
    changed = _pack(
        documents=[
            {"key": "index", "markdown": "# Index\n"},
            {"key": "charter", "markdown": "# Charter, edited\n"},
            {"key": "scope", "markdown": "# Scope\n"},
        ]
    )
    assert manifests.digest(manifests.build(changed)) != before


# --- signing and verifying -------------------------------------------------------


def test_with_the_audit_key_the_manifest_is_signed_and_verifies(signing_key):
    pack = _pack()
    built = manifests.build(pack)
    signature = manifests.sign(built)
    assert signature.startswith("hmac-sha256:")
    result = manifests.verify(built, signature, _files(pack))
    assert result["ok"] is True
    assert result["signature"] == "valid" and result["mode"] == "hmac-sha256"
    assert result["mismatched"] == [] and result["missing"] == []
    assert result["unexpected"] == []


def test_a_tampered_file_fails_verification(signing_key):
    pack = _pack()
    built = manifests.build(pack)
    signature = manifests.sign(built)
    files = _files(pack)
    files["docs/project/charter.md"] = b"# Charter\n\nSomething else\n"
    result = manifests.verify(built, signature, files)
    assert result["ok"] is False
    assert result["signature"] == "valid"
    assert result["mismatched"] == ["docs/project/charter.md"]


def test_a_missing_or_an_extra_file_fails_verification(signing_key):
    pack = _pack()
    built = manifests.build(pack)
    signature = manifests.sign(built)
    files = _files(pack)
    del files["docs/project/scope.md"]
    files["docs/project/extra.md"] = b"# Not in the pack\n"
    result = manifests.verify(built, signature, files)
    assert result["ok"] is False
    assert result["missing"] == ["docs/project/scope.md"]
    assert result["unexpected"] == ["docs/project/extra.md"]


def test_a_manifest_rewritten_to_match_a_tampered_file_fails_on_its_signature(
    signing_key,
):
    pack = _pack()
    built = manifests.build(pack)
    signature = manifests.sign(built)
    forged_bytes = b"# Charter\n\nForged\n"
    files = _files(pack)
    files["docs/project/charter.md"] = forged_bytes
    forged = json.loads(json.dumps(built))
    for entry in forged["files"]:
        if entry["path"] == "docs/project/charter.md":
            entry["sha256"] = hashlib.sha256(forged_bytes).hexdigest()
            entry["bytes"] = len(forged_bytes)
    result = manifests.verify(forged, signature, files)
    assert result["mismatched"] == []
    assert result["signature"] == "invalid" and result["ok"] is False


def test_without_a_key_the_manifest_is_marked_unsigned_and_still_checks_files(
    no_signing_key,
):
    pack = _pack()
    built = manifests.build(pack)
    signature = manifests.sign(built)
    assert signature == "unsigned:" + manifests.digest(built)
    result = manifests.verify(built, signature, _files(pack))
    assert result["ok"] is True
    assert result["signature"] == "unsigned" and result["mode"] == "unsigned"
    files = _files(pack)
    files["docs/project/README.md"] = b"x"
    assert manifests.verify(built, signature, files)["ok"] is False


def test_a_keyed_signature_cannot_be_checked_without_the_key(monkeypatch):
    pack = _pack()
    built = manifests.build(pack)
    monkeypatch.setattr(settings, "AUDIT_SIGNING_KEY", TEST_KEY)
    signature = manifests.sign(built)
    monkeypatch.setattr(settings, "AUDIT_SIGNING_KEY", None)
    result = manifests.verify(built, signature, _files(pack))
    assert result["signature"] == "unverifiable" and result["ok"] is False


def test_an_unknown_signature_is_never_valid(signing_key):
    pack = _pack()
    built = manifests.build(pack)
    result = manifests.verify(built, "sha256:" + manifests.digest(built), _files(pack))
    assert result["signature"] == "invalid" and result["ok"] is False


# --- the routes ------------------------------------------------------------------


@pytest.mark.usefixtures("enforcement", "signing_key", "no_prose")
def test_compiling_records_the_manifest_digest_and_a_viewer_downloads_it(
    sqlite_session, test_client
):
    company, other = _company(sqlite_session), _company(sqlite_session)
    _records(sqlite_session, company)
    manager = _member(sqlite_session, company.id, "Manager")
    viewer = _member(sqlite_session, company.id, "Viewer")
    outsider = _member(sqlite_session, other.id, "Owner")
    pack = test_client.post(
        f"/v1/companies/{company.id}/documentation", json={}, headers=manager
    ).json()

    row = sqlite_session.exec(
        select(AuditLog).where(
            AuditLog.interaction_id == f"documentation-pack:{pack['id']}",
            AuditLog.action == manifests.AUDIT_ACTION,
        )
    ).one()
    assert row.chain_hash and row.company_id == company.id
    assert row.agent_id.startswith("human:")

    url = f"/v1/documentation/{pack['id']}/manifest"
    assert test_client.get(url, headers=outsider).status_code == 403
    got = test_client.get(url, headers=viewer)
    assert got.status_code == 200, got.text
    assert "attachment" in got.headers["content-disposition"]
    body = got.json()
    built = body["manifest"]
    assert built["pack_id"] == pack["id"]
    assert built["inputs_sha256"] == pack["inputs_sha256"]
    assert [f["path"] for f in built["files"]] == sorted(
        d["file"] for d in pack["documents"]
    )
    assert body["manifest_sha256"] == manifests.digest(built)
    assert row.details["manifest_sha256"] == body["manifest_sha256"]
    assert body["signature"].startswith("hmac-sha256:")
    assert body["audit"] == {
        "recorded": True,
        "matches": True,
        "audit_id": row.id,
        "chain_seq": row.chain_seq,
        "chain_hash": row.chain_hash,
        "manifest_sha256": row.details["manifest_sha256"],
    }
    # The files as the pack would write them verify against what was downloaded.
    stored = sqlite_session.get(DocumentationPack, pack["id"])
    assert manifests.verify(built, body["signature"], _files(stored))["ok"] is True


@pytest.mark.usefixtures("no_prose")
def test_a_pack_rewritten_in_the_database_no_longer_matches_the_chain(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    _records(sqlite_session, company)
    pack = test_client.post(f"/v1/companies/{company.id}/documentation", json={}).json()
    stored = sqlite_session.get(DocumentationPack, pack["id"])
    documents = [dict(d) for d in stored.documents]
    documents[1]["markdown"] = documents[1]["markdown"] + "\nA line nobody compiled.\n"
    stored.documents = documents
    sqlite_session.add(stored)
    sqlite_session.commit()

    body = test_client.get(f"/v1/documentation/{pack['id']}/manifest").json()
    assert body["audit"]["recorded"] is True and body["audit"]["matches"] is False
    assert body["audit"]["manifest_sha256"] != body["manifest_sha256"]


def test_a_pack_compiled_before_manifests_says_no_digest_was_recorded(
    sqlite_session, test_client
):
    company = _company(sqlite_session)
    pack = _pack(id=None, company_id=company.id)
    sqlite_session.add(pack)
    sqlite_session.commit()
    body = test_client.get(f"/v1/documentation/{pack.id}/manifest").json()
    assert body["audit"] == {"recorded": False, "matches": None}
    assert test_client.get("/v1/documentation/999999/manifest").status_code == 404
