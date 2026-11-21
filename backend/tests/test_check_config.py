"""One place for the backend's configuration (``scripts/check_config.py``).

Under ``backend/app`` only ``config.py`` reads the environment; the files
that still do are listed with their count, which may shrink but never grow;
and every field of ``Settings`` is named in ``docs/CONFIGURATION.md``. These
tests run the check on the repository (it must pass today) and its parts on
small sources of their own.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "check_config.py"
_spec = importlib.util.spec_from_file_location("check_config", SCRIPT)
assert _spec and _spec.loader
check_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_config)


def test_the_repository_keeps_to_the_rule():
    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPT)], check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_every_way_of_reading_the_environment_counts():
    source = (
        "import os\n"
        "from os import environ, getenv as ge\n"
        "a = os.getenv('A')\n"
        "b = os.environ.get('B')\n"
        "c = environ['C']\n"
        "d = ge('D')\n"
    )
    assert check_config.env_reads(source) == 4


def test_a_string_that_names_the_environment_is_not_a_read():
    source = 'MARKERS = ("os.environ", "process.env")\nimport os\nos.path.join("a")\n'
    assert check_config.env_reads(source) == 0


def test_a_new_file_or_a_new_read_fails_and_fewer_reads_ask_for_update():
    allowed = {"a.py": {"reads": 2, "why": "x"}, "gone.py": {"reads": 1, "why": "y"}}
    failures, lower = check_config.compare({"a.py": 3, "new.py": 1}, allowed)
    assert any(line.startswith("a.py:") for line in failures)
    assert any(line.startswith("new.py:") for line in failures)
    assert lower == ["gone.py"]

    failures, lower = check_config.compare({"a.py": 1}, allowed)
    assert failures == []
    assert sorted(lower) == ["a.py", "gone.py"]


def test_config_py_itself_is_not_scanned(tmp_path):
    (tmp_path / "config.py").write_text("import os\nos.getenv('X')\n")
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "m.py").write_text("import os\nos.getenv('Y')\n")
    assert check_config.scan(tmp_path) == {"svc/m.py": 1}


def test_a_setting_missing_from_the_document_is_found():
    source = (
        "class Settings(BaseSettings):\n"
        "    DOCUMENTED: int = 1\n"
        "    NOT_THERE: int = 2\n"
        "    class Config:\n"
        "        env_file = '.env'\n"
    )
    fields = check_config.settings_fields(source)
    assert fields == ["DOCUMENTED", "NOT_THERE"]
    assert check_config.undocumented(fields, "`DOCUMENTED` does X; NOT_THERE_ALSO") == [
        "NOT_THERE"
    ]
