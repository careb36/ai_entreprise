"""The backend's dependency rule between contexts (issue #469).

``scripts/check_architecture.py`` reads the imports of ``backend/app/services``
with ``ast`` and holds them to ``docs/architecture/contexts.json``. These
tests run it on the repository (it must pass with today's code thanks to its
allowlist) and on small trees of their own: an unassigned module, a new
edge against the rule, a cycle between contexts, a module moved into a
context folder, a shim left at the flat path, an allowlist that only
shrinks, and HTTP imported only by the listed adapter modules (a list that
only shrinks too), and every test file with a context in the test map, which
names only known contexts, existing files and known rules. They also keep the
records under ``docs/architecture`` approvable by the product's own checks
and ``docs/ARCHITECTURE.md`` equal to their rendering.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
from services.planning import arch_check, spec_check

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_architecture.py"
_spec = importlib.util.spec_from_file_location("check_architecture", SCRIPT)
assert _spec and _spec.loader
check_architecture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_architecture)


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    services = tmp_path / "services"
    for rel, text in files.items():
        path = services / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (services / "__init__.py").write_text("")
    return services


def _data(allowlist: list[str] | None = None, **contexts: dict) -> dict:
    return {"contexts": contexts, "allowlist": allowlist or []}


BASE = {
    "contracts": {"depends_on": [], "modules": ["shapes"]},
    "evidence": {"depends_on": ["contracts"], "modules": ["verify"]},
    "loop": {"depends_on": ["contracts", "evidence"], "modules": ["worker"]},
}
FILES = {
    "shapes.py": "VERIFIED = 'verified'\n",
    "verify.py": "from services.shapes import VERIFIED\n",
    "worker.py": "from services import verify\nimport services.shapes\n",
}


def test_the_repository_keeps_to_the_rule():
    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    data = check_architecture.load()
    analysis = check_architecture.analyse(data, tests=check_architecture.TESTS)
    assert analysis["problems"] == []
    # Every test file has a context (the map scripts/affected_tests.py reads).
    assert analysis["test_files"] == len(
        list(check_architecture.TESTS.glob("test_*.py"))
    )
    # Every flat module of services is in exactly one context; a module in a
    # context folder belongs to it by the folder.
    listed = [m for c in data["contexts"].values() for m in c["modules"]]
    assert len(listed) == len(set(listed))
    flat = {k for k in analysis["modules"] if "." not in k} - set(data["contexts"])
    assert flat == set(listed)
    # A shim left at the flat path is listed with its owner's context.
    for shim, context in analysis["shims"].items():
        assert analysis["owner"][shim] == context
    # The contracts context imports nothing else of services.
    assert not [
        (a, b)
        for a, b in analysis["edges"]
        if analysis["owner"][a] == "contracts" and analysis["owner"][b] != "contracts"
    ]
    # The evidence imports only contracts and platform.
    assert data["contexts"]["evidence"]["depends_on"] == ["contracts", "platform"]
    assert data["contexts"]["contracts"]["depends_on"] == []
    # The allowlist holds only edges that exist (it can only shrink).
    assert analysis["stale"] == []
    # HTTP only in the listed adapters, and every listed adapter still does
    # HTTP (the list can only shrink).
    assert analysis["http_new"] == []
    assert analysis["http_stale"] == []
    assert sorted(analysis["http"]) == sorted(check_architecture.http_adapters(data))


def test_a_tree_that_keeps_to_the_rule_passes(tmp_path):
    services = _tree(tmp_path, FILES)
    result = check_architecture.analyse(_data(**BASE), services)
    assert result["problems"] == []
    assert ("worker", "shapes") in result["edges"]
    assert ("worker", "verify") in result["edges"]


def test_an_unassigned_module_fails(tmp_path):
    services = _tree(tmp_path, {**FILES, "example.py": ""})
    problems = check_architecture.analyse(_data(**BASE), services)["problems"]
    assert any(p.startswith("example: in no context") for p in problems)


def test_a_module_listed_twice_or_missing_fails(tmp_path):
    services = _tree(tmp_path, FILES)
    contexts = json.loads(json.dumps(BASE))
    contexts["loop"]["modules"] += ["verify", "ghost"]
    problems = check_architecture.analyse(_data(**contexts), services)["problems"]
    assert any("verify: assigned to both evidence and loop" in p for p in problems)
    assert any("ghost: listed in loop, but no such module" in p for p in problems)


def test_an_edge_against_the_rule_fails_unless_allowlisted(tmp_path):
    files = {**FILES, "verify.py": "def f():\n    from services.worker import x\n"}
    services = _tree(tmp_path, files)
    result = check_architecture.analyse(_data(**BASE), services)
    assert result["new"] == ["verify -> worker"]
    assert any(
        p.startswith("verify -> worker: evidence may not import loop")
        for p in result["problems"]
    )
    allowed = check_architecture.analyse(_data(["verify -> worker"], **BASE), services)
    assert allowed["problems"] == []
    assert allowed["violations"] == ["verify -> worker"]


def test_a_cycle_between_contexts_fails(tmp_path):
    services = _tree(tmp_path, FILES)
    contexts = json.loads(json.dumps(BASE))
    contexts["contracts"]["depends_on"] = ["loop"]
    problems = check_architecture.analyse(_data(**contexts), services)["problems"]
    assert any("run in a circle" in p and "contracts" in p for p in problems)


def test_a_module_in_a_context_folder_belongs_to_it(tmp_path):
    files = {
        "shapes.py": "",
        "evidence/__init__.py": "",
        "evidence/verify.py": "from . import checks\nfrom services.shapes import X\n",
        "evidence/checks.py": "from services.worker import y\n",
        "worker.py": "from services.evidence.verify import run\n",
    }
    services = _tree(tmp_path, files)
    contexts = json.loads(json.dumps(BASE))
    contexts["evidence"]["modules"] = []
    result = check_architecture.analyse(_data(**contexts), services)
    assert result["owner"]["evidence.verify"] == "evidence"
    assert ("evidence.verify", "evidence.checks") in result["edges"]
    assert ("worker", "evidence.verify") in result["edges"]
    # An edge is named by the modules' stems, whichever folder holds them, so
    # an allowlisted edge keeps its name when its module moves (ADR 0016).
    assert result["new"] == ["checks -> worker"]
    assert any(
        p.startswith("checks -> worker: evidence may not import loop")
        for p in result["problems"]
    )
    moved = check_architecture.analyse(
        _data(["checks -> worker"], **contexts), services
    )
    assert moved["problems"] == []
    assert moved["stale"] == []

    stray = _tree(tmp_path / "other", {**FILES, "misc/__init__.py": ""})
    problems = check_architecture.analyse(_data(**BASE), stray)["problems"]
    assert any("services/misc/ is not a context" in p for p in problems)


SHIM = (
    "import sys\n"
    "from services.{ctx} import {mod} as _owner\n"
    "sys.modules[__name__] = _owner\n"
)


def test_one_stem_never_names_two_modules(tmp_path):
    # The allowlist names edges by stem: a second module with an allowlisted
    # stem must not inherit its exception (contracts may not import evidence).
    contexts = json.loads(json.dumps(BASE))
    contexts["loop"]["modules"] += ["probe"]
    allowlist = ["probe -> verify"]
    files = {
        **FILES,
        "contracts/__init__.py": "",
        "contracts/probe.py": "from services import verify\n",
        "loop/__init__.py": "",
        "loop/probe.py": "from services import verify\n",
    }
    problems = check_architecture.analyse(
        _data(allowlist, **contexts), _tree(tmp_path, files)
    )["problems"]
    assert any("probe: names contracts.probe and loop.probe" in p for p in problems)

    # A flat module beside a packaged one of the same stem must be its shim.
    files = {
        **FILES,
        "contracts/__init__.py": "",
        "contracts/verify.py": "VERIFIED = 'verified'\n",
    }
    problems = check_architecture.analyse(
        _data(**BASE), _tree(tmp_path / "flat", files)
    )["problems"]
    assert any(
        "verify: a module of its own beside contracts.verify" in p for p in problems
    )


def test_a_package_inside_a_context_folder_belongs_to_it(tmp_path):
    # ADR 0014: the task worker's stages live in services/loop/worker/.
    files = {
        "shapes.py": "",
        "verify.py": "from services.shapes import VERIFIED\n",
        "loop/__init__.py": "",
        "loop/runner.py": "from services.loop.worker import talk\n",
        "loop/worker/__init__.py": "",
        "loop/worker/talk.py": "from services import verify\nfrom . import intake\n",
        "loop/worker/intake.py": "from services.shapes import X\n",
    }
    contexts = json.loads(json.dumps(BASE))
    contexts["loop"]["modules"] = []
    services = _tree(tmp_path, files)
    result = check_architecture.analyse(_data(**contexts), services)
    assert result["problems"] == []
    assert result["owner"]["loop.worker"] == "loop"
    assert result["owner"]["loop.worker.talk"] == "loop"
    assert ("loop.runner", "loop.worker.talk") in result["edges"]
    assert ("loop.worker.talk", "verify") in result["edges"]
    assert ("loop.worker.talk", "loop.worker.intake") in result["edges"]

    # Held to the rule like any module of the context, named by its stem.
    files["loop/worker/talk.py"] = "from services import later\n"
    files["later.py"] = ""
    contexts["release"] = {"depends_on": ["loop"], "modules": ["later"]}
    problems = check_architecture.analyse(
        _data(**contexts), _tree(tmp_path / "rule", files)
    )["problems"]
    assert any(
        p.startswith("talk -> later: loop may not import release") for p in problems
    )

    # One stem names one module at any depth: a flat module beside a nested
    # one must be its shim, and two nested ones of one stem are refused.
    files = {**files, "talk.py": "X = 1\n", "loop/talk.py": ""}
    contexts["loop"]["modules"] = ["talk"]
    problems = check_architecture.analyse(
        _data(**contexts), _tree(tmp_path / "stems", files)
    )["problems"]
    assert any("talk: names loop.talk and loop.worker.talk" in p for p in problems)
    assert any("talk: a module of its own beside loop.talk" in p for p in problems)
    del files["loop/talk.py"]
    problems = check_architecture.analyse(
        _data(**contexts), _tree(tmp_path / "nested", files)
    )["problems"]
    assert any(
        "talk: a module of its own beside loop.worker.talk" in p for p in problems
    )
    files["talk.py"] = SHIM.format(ctx="loop.worker", mod="talk")
    result = check_architecture.analyse(
        _data(**contexts), _tree(tmp_path / "shim", files)
    )
    assert [p for p in result["problems"] if p.startswith("talk:")] == []
    assert result["shims"]["talk"] == "loop"


def test_a_shim_belongs_to_its_owners_context(tmp_path):
    files = {
        **FILES,
        "contracts/__init__.py": "",
        "contracts/shapes.py": "VERIFIED = 'verified'\n",
        "shapes.py": SHIM.format(ctx="contracts", mod="shapes"),
    }
    services = _tree(tmp_path, files)
    result = check_architecture.analyse(_data(**BASE), services)
    assert result["problems"] == []
    assert result["owner"]["contracts"] == "contracts"
    assert result["shims"] == {"shapes": "contracts"}
    assert ("shapes", "contracts.shapes") in result["edges"]

    contexts = json.loads(json.dumps(BASE))
    contexts["contracts"]["modules"] = []
    contexts["evidence"]["modules"] += ["shapes"]
    problems = check_architecture.analyse(_data(**contexts), services)["problems"]
    assert any(
        "shapes: a shim of contracts.shapes, but listed in evidence" in p
        for p in problems
    )


def test_update_only_shrinks_the_allowlist(tmp_path):
    services = _tree(tmp_path, FILES)
    data_file = tmp_path / "contexts.json"
    data_file.write_text(json.dumps(_data(["verify -> worker"], **BASE)))
    # The allowlisted edge is gone from the code: --update drops it.
    assert check_architecture.main([], data_file, services) == 0
    assert check_architecture.main(["--update"], data_file, services) == 0
    assert json.loads(data_file.read_text())["allowlist"] == []

    # A new edge against the rule: exit 1 and nothing written.
    (services / "verify.py").write_text("from services import worker\n")
    before = data_file.read_text()
    assert check_architecture.main(["--update"], data_file, services) == 1
    assert data_file.read_text() == before


def _with_adapters(adapters: list[str], **contexts: dict) -> dict:
    return {**_data(**contexts), "http_adapters": {"modules": adapters}}


def test_an_http_import_outside_the_adapters_fails(tmp_path):
    files = {
        **FILES,
        "loop/__init__.py": "",
        "loop/fetch.py": "def get():\n    import httpx\n",
        "loop/legacy.py": "from urllib import request\n",
        "loop/parse.py": "from urllib.parse import urlsplit\nimport urllib.error\n",
        "loop/client.py": "import requests\nfrom urllib.request import urlopen\n",
    }
    services = _tree(tmp_path, files)
    result = check_architecture.analyse(_with_adapters([], **BASE), services)
    # Imported anywhere in the module, a function's body too; urllib.parse
    # and urllib.error are not HTTP.
    assert result["http"] == {
        "loop.client": ["requests", "urllib.request"],
        "loop.fetch": ["httpx"],
        "loop.legacy": ["urllib.request"],
    }
    assert result["http_new"] == ["loop.client", "loop.fetch", "loop.legacy"]
    assert any(
        p.startswith("loop.fetch: imports httpx; HTTP belongs in an adapter")
        for p in result["problems"]
    )

    listed = ["loop.client", "loop.fetch", "loop.legacy"]
    allowed = check_architecture.analyse(_with_adapters(listed, **BASE), services)
    assert allowed["problems"] == []
    assert allowed["http_new"] == [] and allowed["http_stale"] == []

    ghost = check_architecture.analyse(
        _with_adapters([*listed, "loop.ghost"], **BASE), services
    )
    assert any(
        "loop.ghost: listed in http_adapters, but no such module" in p
        for p in ghost["problems"]
    )


def test_a_listed_adapter_without_http_is_reported_and_dropped(tmp_path, capsys):
    files = {
        **FILES,
        "loop/__init__.py": "",
        "loop/client.py": "import httpx\n",
        "loop/former.py": "import json\n",
    }
    services = _tree(tmp_path, files)
    data_file = tmp_path / "contexts.json"
    data = _with_adapters(["loop.client", "loop.former"], **BASE)
    data_file.write_text(json.dumps(data))
    result = check_architecture.analyse(data, services)
    assert result["problems"] == []
    assert result["http_stale"] == ["loop.former"]

    # Reported, so the list shrinks; --update drops it and never adds one.
    assert check_architecture.main([], data_file, services) == 0
    assert "The HTTP adapters can shrink" in capsys.readouterr().out
    assert check_architecture.main(["--update"], data_file, services) == 0
    assert json.loads(data_file.read_text())["http_adapters"]["modules"] == [
        "loop.client"
    ]

    # A new HTTP import: exit 1 and nothing written.
    (services / "loop" / "former.py").write_text("import aiohttp\n")
    before = data_file.read_text()
    assert check_architecture.main(["--update"], data_file, services) == 1
    assert data_file.read_text() == before


def _test_files(tmp_path: Path, *names: str) -> Path:
    tests = tmp_path / "tests"
    tests.mkdir()
    for name in names:
        (tests / name).write_text("def test_x():\n    pass\n")
    (tests / "conftest.py").write_text("")
    (tests / "helpers.py").write_text("")
    return tests


def _with_tests(**section: dict) -> dict:
    return {**_data(**BASE), "tests": section}


def test_a_test_map_that_names_every_test_passes(tmp_path):
    services = _tree(tmp_path, FILES)
    tests = _test_files(tmp_path, "test_worker.py", "test_rule.py", "test_doc.py")
    data = _with_tests(
        sources={"scripts/": "mentions", "vscode/": "loop", "x/": ["test_doc.py"]},
        always={"$comment": "runs whatever changed", "test_rule.py": "reads all"},
        files={"test_worker.py": ["loop", "evidence"], "test_doc.py": ["repo"]},
    )
    result = check_architecture.analyse(data, services, tests)
    assert result["problems"] == []
    # conftest.py and the helpers are no test files.
    assert result["test_files"] == 3
    # Without a tests folder the map is not checked (the small trees above).
    assert check_architecture.analyse(data, services)["test_files"] is None


def test_a_test_without_a_context_or_a_wrong_entry_fails(tmp_path):
    services = _tree(tmp_path, FILES)
    tests = _test_files(
        tmp_path, "test_worker.py", "test_new.py", "test_both.py", "test_empty.py"
    )
    data = _with_tests(
        sources={"scripts/": "nonsense", "bench/": ["mentions", "test_ghost.py"]},
        always={"test_both.py": "reads all", "test_gone.py": " "},
        files={
            "test_worker.py": ["loop", "billing"],
            "test_both.py": ["loop"],
            "test_empty.py": [],
            "test_old.py": ["evidence"],
        },
    )
    problems = check_architecture.analyse(data, services, tests)["problems"]
    expected = [
        "tests/test_new.py: no context; add it to tests.files",
        "tests/test_both.py: in both tests.files and tests.always",
        "tests/test_worker.py: unknown context billing",
        "tests/test_empty.py: names no context",
        "tests/test_old.py: listed in tests.files, but no such file",
        "tests/test_gone.py: listed in tests.always, but no such file",
        "tests/test_gone.py: in tests.always without its reason",
        "tests.sources scripts/: unknown rule nonsense",
        "tests.sources bench/: unknown rule test_ghost.py",
    ]
    for start in expected:
        assert any(p.startswith(start) for p in problems), start
    assert len(problems) == len(expected)

    # No map at all fails too, and main exits 1 with the tests folder given.
    bare = check_architecture.analyse(_data(**BASE), services, tests)["problems"]
    assert bare == ["no tests section in contexts.json: every test file needs one"]
    data_file = tmp_path / "contexts.json"
    data_file.write_text(json.dumps(data))
    assert check_architecture.main([], data_file, services, tests) == 1


@pytest.fixture(scope="module")
def records():
    spec = spec_check.normalize(
        json.loads((REPO / "docs/architecture/specification.json").read_text())
    )
    arch = arch_check.normalize(
        json.loads((REPO / "docs/architecture/architecture.json").read_text())
    )
    return spec, arch


def test_the_records_pass_the_products_own_checks(records):
    spec, arch = records
    assert spec_check.check(spec)["blocking"] == 0
    result = arch_check.check(arch, [r["id"] for r in spec["requirements"]])
    assert result["blocking"] == 0
    assert not [f for f in result["findings"] if f["code"] == "uncovered_requirement"]


def test_the_contexts_match_the_architecture(records):
    _, arch = records
    data = check_architecture.load()
    names = {m["id"]: m["name"] for m in arch["modules"]}
    for module in arch["modules"]:
        if module["name"] not in data["contexts"]:
            continue
        context = data["contexts"][module["name"]]
        assert context["path"] == module["path"]
        assert sorted(context["depends_on"]) == sorted(
            names[d] for d in module["depends_on"]
        )
    assert set(data["contexts"]) <= set(names.values())


def test_the_document_is_the_rendering_of_the_record(records):
    spec, arch = records
    expected = arch_check.as_markdown(arch, spec["title"], 1)
    assert (REPO / "docs" / "ARCHITECTURE.md").read_text() == expected
