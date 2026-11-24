"""The documentation checks: links resolve, cited paths exist (``evidence/doc_links``).

A delivery that changes Markdown under a docs folder or a README is judged
against what was observed: the daemon's listing of the delivered tree
(``project_files``) and the texts the daemon read back (the edit tool's files,
or an answer's file whose sha256 equals the daemon's read-back sha256). A
broken link or a missing path refutes, with the broken ones listed; what
cannot be decided is unverifiable; the gate sends a refuted one back.
"""

from __future__ import annotations

import hashlib

from services.evidence import delivery_gate, doc_links, markdown_refs
from verification_helpers import _ingest, _make_company, _payload, _verify

TREE = ["README.md", "docs/guide.md", "docs/api/index.md", "src/app.py", "src/util.py"]
GUIDE = """# Guide

## Install the app

See [the API](api/index.md#endpoints), [home](../README.md), [code](../src/app.py#L3)
and [below](#install-the-app). The entry point is `src/app.py`; helpers in `src/`.

[ref]: ./api/index.md

```bash
cat [not a link](nowhere.md) `src/fenced.py`
```
"""
API = "# API\n\n## Endpoints\n\nGET /orders\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _delivery(files: dict[str, str], tree=TREE, truncated=False):
    declared = [{"path": p, "content": c} for p, c in files.items()]
    evidence = {
        "workspace_writes": [
            {"path": p, "status": "written", "sha256": _sha(c)}
            for p, c in files.items()
        ],
        "project_files": {"files": list(tree), "truncated": truncated},
    }
    return evidence, declared


def _checks(files, **kw):
    evidence, declared = _delivery(files, **kw)
    return {c["name"]: c for c in doc_links.checks(evidence, declared)}


def test_good_links_and_paths_pass():
    checks = _checks({"docs/guide.md": GUIDE, "docs/api/index.md": API})
    links, paths = checks["docs_links"], checks["docs_code_paths"]
    assert links["outcome"] == "pass" and links["checked"] == 5, links
    assert paths["outcome"] == "pass" and paths["checked"] == 2, paths


def test_a_broken_link_refutes_with_the_link_listed():
    guide = GUIDE.replace("api/index.md#endpoints", "api/missing.md")
    links = _checks({"docs/guide.md": guide, "docs/api/index.md": API})["docs_links"]
    assert links["outcome"] == "refuted"
    assert links["broken"] == ["docs/guide.md:5 → api/missing.md (no such file)"]
    assert "api/missing.md" in links["observation"]


def test_a_broken_anchor_refutes_when_the_target_was_read_back():
    guide = GUIDE.replace("#endpoints", "#nope").replace("#install-the-app", "#gone")
    links = _checks({"docs/guide.md": guide, "docs/api/index.md": API})["docs_links"]
    assert links["outcome"] == "refuted" and len(links["broken"]) == 2
    assert all("no such heading" in b for b in links["broken"])


def test_an_anchor_into_a_document_not_read_back_is_unverifiable():
    links = _checks({"docs/guide.md": GUIDE})["docs_links"]  # index.md not delivered
    assert links["outcome"] == "unverifiable"
    assert "not read back" in links["unchecked"][0]


def test_a_cited_path_that_does_not_exist_refutes():
    guide = GUIDE.replace("`src/app.py`", "`src/main.py`")
    paths = _checks({"docs/guide.md": guide, "docs/api/index.md": API})[
        "docs_code_paths"
    ]
    assert paths["outcome"] == "refuted"
    assert paths["broken"] == ["docs/guide.md:6 `src/main.py` (no such path)"]


def test_a_bare_name_not_found_is_a_finding_and_names_are_not_paths():
    readme = "Copy `settings.toml` and call `app.run()` on `example.com`, see `v1.2.3`."
    paths = _checks({"README.md": readme})["docs_code_paths"]
    assert paths["outcome"] == "pass" and paths["checked"] == 1
    assert "settings.toml" in paths["findings"][0]


def test_text_not_read_back_by_the_daemon_is_not_judged():
    evidence, declared = _delivery({"docs/guide.md": GUIDE})
    evidence["workspace_writes"][0]["sha256"] = "0" * 64  # disk differs from the answer
    checks = {c["name"]: c for c in doc_links.checks(evidence, declared)}
    assert checks["docs_links"]["outcome"] == "unverifiable"
    assert "not read back" in checks["docs_links"]["observation"]


def test_the_edit_tools_read_back_text_counts():
    evidence = {
        "tool_edit_files": [{"path": "README.md", "content": "[x](docs/none.md)\n"}],
        "project_files": {"files": TREE, "truncated": False},
    }
    [links, _] = doc_links.checks(evidence, [])
    assert links["outcome"] == "refuted" and "docs/none.md" in links["broken"][0]


def test_a_cut_listing_or_none_never_refutes():
    guide = GUIDE.replace("api/index.md#endpoints", "api/missing.md")
    links = _checks({"docs/guide.md": guide}, truncated=True)["docs_links"]
    assert links["outcome"] == "unverifiable"
    evidence, declared = _delivery({"docs/guide.md": guide})
    del evidence["project_files"]
    assert {c["outcome"] for c in doc_links.checks(evidence, declared)} == {
        "unverifiable"
    }


def test_only_documentation_markdown_is_checked():
    assert doc_links.checks(*_delivery({"src/notes.md": "[x](nope.md)"})) == []
    assert doc_links.checks(*_delivery({"src/app.py": "x = '[x](nope.md)'"})) == []
    assert doc_links.is_documentation("README.md")
    assert doc_links.is_documentation("pkg/docs/usage.markdown")
    assert not doc_links.is_documentation("docs/diagram.png")


def test_hidden_and_external_targets_are_not_followed_or_refuted():
    readme = "[ci](.github/workflows/ci.yml) [web](https://example.com/x.md) [m](mailto:a@b.c)\n"
    links = _checks({"README.md": readme})["docs_links"]
    assert links["outcome"] == "unverifiable" and links["checked"] == 1
    assert not links["broken"]


def test_github_anchor_slugs():
    text = '# Hello, World!\n## Hello, World!\nSetext\n===\n<a id="Custom"></a>\n'
    assert markdown_refs.anchors(text) == {
        "hello-world",
        "hello-world-1",
        "setext",
        "custom",
    }


def test_the_gate_sends_back_broken_documentation():
    assert delivery_gate._GATE_PROBLEMS[doc_links.CHECK_LINKS]
    assert delivery_gate._GATE_PROBLEMS[doc_links.CHECK_CODE_PATHS]


def test_the_verifier_refutes_a_delivery_with_a_broken_link(
    sqlite_session, test_client, monkeypatch, tmp_path
):
    company_id = _make_company(sqlite_session)
    readme = "# Proj\n\nSee [the guide](docs/missing.md).\n"
    artifact = f"```markdown path=README.md\n{readme}```\n"
    run = _ingest(
        test_client,
        monkeypatch,
        tmp_path,
        _payload(company_id, artifact=artifact, claims={"files": ["README.md"]}),
    ).json()
    evidence, _ = _delivery({"README.md": readme})
    body = _verify(test_client, run["id"], company_id, evidence).json()
    checks = {c["name"]: c for c in body["checks"]}
    assert checks["docs_links"]["outcome"] == "refuted", checks["docs_links"]
    assert "docs/missing.md" in checks["docs_links"]["observation"]
    assert body["verdict"] == "refuted"
