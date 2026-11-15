"""The e2e run's ports are picked at run time, the web project's too.

The self-hosted runners share one machine, so two e2e runs can reach step 9c
(the web project) together. With a fixed ``E2E_WEB_PORT`` the second run's
application check found the port in use: the daemon refused to start the
greeting (``vscode-daemon/app_run.py``, ``port_in_use``), so no browser check
ran, no hold was placed, QA's approval closed it and the counter that depends
on it ran at once (run 43752094404). These tests read the workflow and the
script and hold them to a port picked when the run starts.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
E2E_WORKFLOW = REPO / ".github" / "workflows" / "e2e.yml"
RUN_SH = REPO / "scripts" / "e2e" / "run.sh"


def _e2e_step() -> str:
    data = yaml.safe_load(E2E_WORKFLOW.read_text())
    steps = data["jobs"]["e2e"]["steps"]
    return next(s["run"] for s in steps if "scripts/e2e/run.sh" in s.get("run", ""))


def test_the_workflow_picks_every_port_the_run_listens_on():
    run = _e2e_step()
    for name in ("E2E_BACKEND_PORT", "E2E_LLM_PORT", "E2E_WEB_PORT"):
        assert f'{name}="$(pick_port)"' in run, name
        assert re.search(rf"^\s*export .*\b{name}\b", run, re.MULTILINE), name


def test_the_script_has_no_fixed_web_port():
    text = RUN_SH.read_text()
    default = re.search(r"^WEB_PORT=\$\{E2E_WEB_PORT:-(.*)\}$", text, re.MULTILINE)
    assert default, "WEB_PORT must default from E2E_WEB_PORT"
    assert not default.group(1).strip().isdigit()
    assert "s.bind((" in default.group(1)


def test_the_script_says_when_the_web_port_is_taken():
    text = RUN_SH.read_text()
    before = text.index('echo "9c.')
    check = text.index("nothing else listens on the web project's port")
    stack_profile = text.index("a Manager says the project serves pages")
    assert before < check < stack_profile
