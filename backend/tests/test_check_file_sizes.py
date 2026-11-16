"""The file-size ratchet (``scripts/check_file_sizes.py``).

Source files and test files are held to the same 600-line limit, each group
with its own baseline: a listed file may shrink but never grow, a new file may
not cross the limit, and ``--update`` only lowers entries and drops the ones
back under the limit. These tests run the script on the repository (it must
pass today) and on small git trees of their own.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_file_sizes.py"
_spec = importlib.util.spec_from_file_location("check_file_sizes", SCRIPT)
assert _spec and _spec.loader
check_file_sizes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_file_sizes)

LIMIT = check_file_sizes.LIMIT


def _write(root: Path, rel: str, lines: int) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x = 1\n" * lines)


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "scripts").mkdir()
    monkeypatch.setattr(check_file_sizes, "ROOT", tmp_path)
    monkeypatch.setattr(
        check_file_sizes, "BASELINE", tmp_path / "scripts" / "file_size_baseline.json"
    )
    monkeypatch.setattr(
        check_file_sizes,
        "TEST_BASELINE",
        tmp_path / "scripts" / "test_file_size_baseline.json",
    )
    return tmp_path


def _add(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)


def _baseline(root: Path, name: str, entries: dict[str, int]) -> None:
    (root / "scripts" / name).write_text(json.dumps(entries))


def test_the_repository_keeps_to_the_rule():
    result = subprocess.run(
        ["python3", str(SCRIPT)],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_the_baselines_only_list_files_over_the_limit():
    for name in ("file_size_baseline.json", "test_file_size_baseline.json"):
        entries = json.loads((REPO / "scripts" / name).read_text())
        for path, lines in entries.items():
            assert lines > LIMIT, f"{name}: {path} is listed at {lines}"
            assert (REPO / path).exists(), f"{name}: {path} does not exist"


def test_tests_and_sources_are_separate_groups(tree: Path):
    _write(tree, "backend/app/service.py", 3)
    _write(tree, "backend/tests/test_service.py", 3)
    _write(tree, "llm-proxy/tests/test_proxy.py", 3)
    _write(tree, "vscode-daemon/tests/test_daemon.py", 3)
    _write(tree, "frontend/src/pages/View.test.tsx", 3)
    _write(tree, "frontend/src/lib/util.test.ts", 3)
    _write(tree, "frontend/src/lib/util.ts", 3)
    _add(tree)
    assert check_file_sizes.source_files() == [
        "backend/app/service.py",
        "frontend/src/lib/util.ts",
    ]
    assert check_file_sizes.listed_test_files() == [
        "backend/tests/test_service.py",
        "frontend/src/lib/util.test.ts",
        "frontend/src/pages/View.test.tsx",
        "llm-proxy/tests/test_proxy.py",
        "vscode-daemon/tests/test_daemon.py",
    ]


def test_a_new_test_file_over_the_limit_fails(tree: Path, capsys):
    _write(tree, "backend/tests/test_big.py", LIMIT + 1)
    _write(tree, "backend/tests/test_small.py", LIMIT)
    _add(tree)
    assert check_file_sizes.main([]) == 1
    err = capsys.readouterr().err
    assert "backend/tests/test_big.py" in err
    assert "test_small.py" not in err


def test_a_listed_test_file_may_shrink_but_not_grow(tree: Path, capsys):
    _write(tree, "backend/tests/test_big.py", LIMIT + 10)
    _add(tree)
    _baseline(
        tree, "test_file_size_baseline.json", {"backend/tests/test_big.py": LIMIT + 20}
    )
    assert check_file_sizes.main([]) == 0
    assert "can be tightened" in capsys.readouterr().out

    _write(tree, "backend/tests/test_big.py", LIMIT + 21)
    assert check_file_sizes.main([]) == 1
    assert "grew from" in capsys.readouterr().err


def test_a_test_baseline_does_not_cover_a_source_file(tree: Path):
    _write(tree, "backend/app/big.py", LIMIT + 5)
    _add(tree)
    _baseline(tree, "test_file_size_baseline.json", {"backend/app/big.py": LIMIT + 5})
    assert check_file_sizes.main([]) == 1
    _baseline(tree, "file_size_baseline.json", {"backend/app/big.py": LIMIT + 5})
    assert check_file_sizes.main([]) == 0


def test_update_only_lowers_and_drops_entries(tree: Path):
    _write(tree, "backend/tests/test_shrunk.py", LIMIT + 5)
    _write(tree, "backend/tests/test_split.py", 10)
    _write(tree, "frontend/src/View.test.tsx", LIMIT + 3)
    _add(tree)
    _baseline(
        tree,
        "test_file_size_baseline.json",
        {
            "backend/tests/test_shrunk.py": LIMIT + 50,
            "backend/tests/test_split.py": LIMIT + 50,
            "backend/tests/test_gone.py": LIMIT + 50,
            "frontend/src/View.test.tsx": LIMIT + 3,
        },
    )
    assert check_file_sizes.main(["--update"]) == 0
    written = json.loads(
        (tree / "scripts" / "test_file_size_baseline.json").read_text()
    )
    assert written == {
        "backend/tests/test_shrunk.py": LIMIT + 5,
        "frontend/src/View.test.tsx": LIMIT + 3,
    }
    assert json.loads((tree / "scripts" / "file_size_baseline.json").read_text()) == {}


def test_update_refuses_while_a_test_file_breaks_the_rule(tree: Path):
    _write(tree, "backend/tests/test_new.py", LIMIT + 1)
    _add(tree)
    _baseline(tree, "test_file_size_baseline.json", {})
    assert check_file_sizes.main(["--update"]) == 1
    assert (tree / "scripts" / "test_file_size_baseline.json").read_text() == "{}"
