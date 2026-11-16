"""Another context imported only through its public modules (ADR 0018).

``scripts/check_architecture.py`` holds each import of another context's
module to that context's ``public`` list in ``docs/architecture/contexts.json``;
an internal import fails unless it is in ``interface_allowlist``, which only
shrinks. Split from ``test_check_architecture.py``, whose helpers these share.
"""

from __future__ import annotations

import json

from test_check_architecture import _tree, check_architecture

# Three contexts in folders: loop may import evidence, whose interface is
# ``verify``; ``checks`` is internal to evidence.
INTERFACE = {
    "contracts": {"depends_on": [], "public": ["*"], "modules": []},
    "evidence": {"depends_on": ["contracts"], "public": ["verify"], "modules": []},
    "loop": {"depends_on": ["contracts", "evidence"], "public": [], "modules": []},
}
INTERFACE_FILES = {
    "contracts/__init__.py": "",
    "contracts/shapes.py": "VERIFIED = 'verified'\n",
    "evidence/__init__.py": "",
    "evidence/verify.py": "from . import checks\nfrom services.contracts import shapes\n",
    "evidence/checks.py": "",
    "loop/__init__.py": "",
    "loop/worker.py": "from services.evidence.verify import run\nimport services.evidence\n",
}


def _interface(allowlist: list[str] | None = None, **public: list[str]) -> dict:
    contexts = json.loads(json.dumps(INTERFACE))
    for name, modules in public.items():
        contexts[name]["public"] = modules
    return {
        "contexts": contexts,
        "allowlist": [],
        "interface_allowlist": allowlist or [],
    }


def test_an_import_of_a_public_module_passes(tmp_path):
    services = _tree(tmp_path, INTERFACE_FILES)
    result = check_architecture.analyse(_interface(), services)
    assert result["problems"] == []
    assert result["internal"] == []
    # The context's package is public, "*" makes every module public, and a
    # context imports its own internals freely.
    assert ("loop.worker", "evidence") in result["edges"]
    assert ("evidence.verify", "evidence.checks") in result["edges"]
    assert result["public"]["contracts"] == {"contracts", "contracts.shapes"}
    assert result["public"]["evidence"] == {"evidence", "evidence.verify"}


def test_an_import_of_another_contexts_internal_module_fails(tmp_path):
    files = {
        **INTERFACE_FILES,
        "loop/runner.py": "from services.evidence import checks\n",
    }
    result = check_architecture.analyse(_interface(), _tree(tmp_path, files))
    assert result["internal_new"] == ["runner -> checks"]
    assert any(
        p.startswith("runner -> checks: evidence.checks is internal to evidence")
        for p in result["problems"]
    )
    # An edge against the dependency rule is reported by that rule only.
    files["evidence/checks.py"] = "from services.loop import worker\n"
    result = check_architecture.analyse(_interface(), _tree(tmp_path / "up", files))
    assert result["new"] == ["checks -> worker"]
    assert result["internal_new"] == ["runner -> checks"]

    # A context must name its interface, and only modules it holds.
    data = _interface(evidence=["verify", "ghost"])
    del data["contexts"]["loop"]["public"]
    problems = check_architecture.analyse(data, _tree(tmp_path / "ghost", files))[
        "problems"
    ]
    assert any(
        p.startswith("context loop: declares no public modules") for p in problems
    )
    assert any(
        "context evidence: public names ghost, but no such module" in p
        for p in problems
    )


def test_an_allowlisted_internal_import_passes_and_a_stale_one_is_dropped(
    tmp_path, capsys
):
    files = {
        **INTERFACE_FILES,
        "loop/runner.py": "from services.evidence import checks\n",
    }
    services = _tree(tmp_path, files)
    allowed = check_architecture.analyse(_interface(["runner -> checks"]), services)
    assert allowed["problems"] == []
    assert allowed["internal"] == ["runner -> checks"]
    assert allowed["internal_stale"] == []

    # Once checks is declared public, the entry is stale: reported, and
    # --update drops it.
    data_file = tmp_path / "contexts.json"
    data = _interface(["runner -> checks"], evidence=["verify", "checks"])
    data_file.write_text(json.dumps(data))
    assert check_architecture.analyse(data, services)["internal_stale"] == [
        "runner -> checks"
    ]
    assert check_architecture.main([], data_file, services) == 0
    assert "The interface allowlist can shrink" in capsys.readouterr().out
    assert check_architecture.main(["--update"], data_file, services) == 0
    assert json.loads(data_file.read_text())["interface_allowlist"] == []

    # A new internal import: exit 1 and nothing written.
    (services / "loop" / "runner.py").write_text("from services.evidence import x\n")
    (services / "evidence" / "x.py").write_text("")
    before = data_file.read_text()
    assert check_architecture.main(["--update"], data_file, services) == 1
    assert data_file.read_text() == before
