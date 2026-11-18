"""The e2e run's ports are picked at run time, the web project's too.

The self-hosted runners share one machine, so two e2e runs can reach step 9c
(the web project) together. With a fixed ``E2E_WEB_PORT`` the second run's
application check found the port in use: the daemon refused to start the
greeting (``vscode-daemon/app_run.py``, ``port_in_use``), so no browser check
ran, no hold was placed, QA's approval closed it and the counter that depends
on it ran at once (run 43752094404). These tests read the workflow and the
script and hold them to a port picked when the run starts.

They also hold step 9e (issue #549, ``scripts/e2e/invented_package_step.sh``)
to what it needs: run.sh sources it, the fake model's first delivery of its
task names the package the step expects, the fake registry answers 404 for
it, no install would run for the file, and the step reads the Verifier's
observations, never the producer's claims.

And step 9f (issue #445, ``scripts/e2e/wiki_step.sh``): sourced after 9e,
it expects the page the fake model compiles for the backend's wiki request
and the file Sprint 8's task writes, and reads the sources the backend
resolved, the lint's reasons and the daemon's hashes.
"""

from __future__ import annotations

import importlib.util
import json
import re
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
E2E_WORKFLOW = REPO / ".github" / "workflows" / "e2e.yml"
RUN_SH = REPO / "scripts" / "e2e" / "run.sh"
INVENTED_STEP = REPO / "scripts" / "e2e" / "invented_package_step.sh"
FAKE_LLM = REPO / "scripts" / "e2e" / "fake_llm.py"


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


# --- step 9e: a package no registry has (issue #549) -------------------------


@pytest.fixture
def fake_llm(tmp_path):
    """A fresh copy of the fake model, its prompt log in *tmp_path*."""
    spec = importlib.util.spec_from_file_location("e2e_fake_llm_549", FAKE_LLM)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PROMPT_LOG = str(tmp_path / "prompts.log")
    module.VIOLATIONS_LOG = str(tmp_path / "violations.log")
    return module


def _paths(answer: str) -> list[str]:
    return re.findall(r"^```\S* path=(\S+)", answer, re.MULTILINE)


def test_run_sh_sources_the_step_after_the_self_hosting_and_before_the_history():
    text = RUN_SH.read_text()
    self_host = text.index('. "$REPO/scripts/e2e/self_host_step.sh"')
    step = text.index('. "$REPO/scripts/e2e/invented_package_step.sh"')
    history = text.index('echo "10.')
    assert self_host < step < history
    assert "9e." in text[: text.index("set -u")]


def test_the_step_expects_the_package_and_task_the_fake_model_delivers(fake_llm):
    text = INVENTED_STEP.read_text()
    assert f"INVENTED={fake_llm.INVENTED_PACKAGE} " in text
    assert f'\\"title\\": \\"{fake_llm.INVENTED_TASK}\\"' in text
    assert f"'{fake_llm.INVENTED_TASK}'.*{fake_llm.INVENTED_MANIFEST}" in text


def test_only_the_first_delivery_of_the_task_names_the_invented_package(fake_llm):
    prompt = f"## Task: {fake_llm.INVENTED_TASK}\nA function with a test."
    first, second = fake_llm.answer(prompt), fake_llm.answer(prompt)
    manifest = fake_llm.INVENTED_MANIFEST
    assert manifest in _paths(first)
    body = first.split(f"path={manifest}\n", 1)[1].split("```", 1)[0]
    assert body.split() == [fake_llm.INVENTED_PACKAGE]
    assert manifest not in _paths(second)
    assert fake_llm.LAST_PATHS[fake_llm.INVENTED_TASK] == _paths(second)
    # No other task delivers it.
    assert manifest not in _paths(fake_llm.answer("## Task: Add widget\nA function."))


def test_no_install_runs_for_the_file_the_grounded_check_reads(fake_llm):
    # Not a stack marker (stack_profiles.PROFILES): with no requirements.txt or
    # pyproject.toml in the project, no install runs and could refute first.
    name = fake_llm.INVENTED_MANIFEST
    assert name.startswith("requirements") and name.endswith(".txt")
    assert name not in (
        "requirements.txt",
        "pyproject.toml",
        "setup.py",
        "setup.cfg",
        "Pipfile",
    )


def test_the_fake_registry_answers_404_for_the_invented_package(fake_llm):
    server = ThreadingHTTPServer(("127.0.0.1", 0), fake_llm.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}/pypi"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # no proxy
    try:
        with pytest.raises(urllib.error.HTTPError) as missing:
            opener.open(f"{base}/{fake_llm.INVENTED_PACKAGE}/json", timeout=5)
        assert missing.value.code == 404
        with opener.open(f"{base}/pytest/json", timeout=5) as found:
            assert json.load(found)["info"]["name"] == "pytest"
    finally:
        server.shutdown()
        server.server_close()


def test_the_step_reads_the_verifiers_observations_not_the_producers_claims():
    text = INVENTED_STEP.read_text()
    # The backend's registry lookup, the grounded check and the gate's event.
    for read in (
        "e.get('registry_lookups')",
        "c['name'] == 'grounded'",
        "/v1/tasks/$RETRY/events",
        "e['actor'] == 'gate'",
        "m.get('author') == 'Verifier'",
    ):
        assert read in text, read
    # A claim never decides a check: no run's claims, no artifact text.
    for claim in ("claims", "artifact_text", "Implemented "):
        assert claim not in text, claim


def _top_level_comma(expression: str) -> bool:
    depth = 0
    for char in expression:
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            return True
    return False


def test_a_json_check_of_several_values_expects_them_space_separated():
    # run.sh's json() is print($1): "a, b" prints "a b", never a tuple. Step
    # 9e's chain check expected "('events', 'intact')" and failed on a task
    # whose events covered its thread and whose chain was intact (PR #652).
    # Step 9f's page() helper pipes into json() the same way.
    compared = re.compile(r'(?:json|page \S+) "([^"]*)"\)" = "([^"]*)"')
    seen = 0
    for script in sorted(RUN_SH.parent.glob("*.sh")):
        for expression, expected in compared.findall(script.read_text()):
            seen += 1
            if _top_level_comma(expression):
                assert not expected.startswith("("), (script.name, expression)
    assert seen > 30  # the pattern still finds the checks


# --- step 9f: the project wiki (issue #445, #555 F8) -------------------------

WIKI_STEP = REPO / "scripts" / "e2e" / "wiki_step.sh"
WIKI_COMPILE = REPO / "backend" / "app" / "services" / "learning" / "wiki_compile.py"


def test_run_sh_sources_the_wiki_step_after_9e_and_before_the_history():
    text = RUN_SH.read_text()
    invented = text.index('. "$REPO/scripts/e2e/invented_package_step.sh"')
    step = text.index('. "$REPO/scripts/e2e/wiki_step.sh"')
    history = text.index('echo "10.')
    assert invented < step < history
    assert "9f." in text[: text.index("set -u")]
    # It reads the ids the earlier steps and 9e set.
    used = set(re.findall(r"\$\{?([A-Z][A-Z0-9_]*)", WIKI_STEP.read_text()))
    before = text[:step] + INVENTED_STEP.read_text()
    for name in ("S4", "ARCH_ID", "RETRY", "RETRY_LAST", "WIDGET", "PROMPTS"):
        assert name in used, name
        assert re.search(rf"(?:^|;)\s*{name}=", before, re.MULTILINE), name


def test_the_fake_answers_the_backends_wiki_request_by_its_opening(fake_llm):
    # The request wiki_compile.build_request writes, and the run line the fake cites.
    source = WIKI_COMPILE.read_text()
    assert f"f\"{fake_llm.WIKI_PROMPT} Update the {{target['kind']}} page \"" in source
    assert 'f\'"{target["title"]}" from the records below.' in source
    assert "f\"- run {run['run_id']} (verified; task" in source


def test_the_fakes_wiki_page_cites_the_listed_run_and_one_statement_nothing(fake_llm):
    prompt = (
        f'{fake_llm.WIKI_PROMPT} Update the glossary page "Glossary" from the '
        "records below.\n"
        "## Verified runs of the sprint and the files they wrote\n"
        "- run 41 (verified; task 7: Add retry helper)\n"
        "  - file src/add_retry_helper.py sha256 " + "ab" * 32 + "\n"
    )
    answer = fake_llm.answer(prompt)
    assert answer.startswith("# Glossary\n")
    statements = [line for line in answer.splitlines() if line and line[0] != "#"]
    assert statements == [
        "- A verified run of the sprint delivered this [run:41]",
        fake_llm.WIKI_UNCITED,
    ]
    assert "[" not in fake_llm.WIKI_UNCITED  # cites nothing: the page is a proposal
    # Never a delivery or a review: no file block, and QA's order is untouched.
    assert not _paths(answer)
    assert not fake_llm.DELIVERED and not fake_llm.REVIEWS


def test_the_wiki_step_expects_what_the_fake_compiles_and_delivers(fake_llm):
    text = WIKI_STEP.read_text()
    assert f'WIKI_UNCITED="{fake_llm.WIKI_UNCITED}"' in text
    assert fake_llm.WIKI_PROMPT.replace(".", "\\.") in text
    # Sprint 8 writes the cited file again: the fake names the module by the task.
    assert '\\"title\\": \\"Add retry helper\\"' in text
    paths = _paths(fake_llm.answer("## Task: Add retry helper\nA function."))
    assert "src/add_retry_helper.py" in paths and "src/add_retry_helper.py" in text


def test_the_wiki_step_reads_observations_never_a_pages_account_of_itself():
    text = WIKI_STEP.read_text()
    for read in (
        "d['sources']",  # what the backend resolved each citation to
        "d['lint_reasons']",  # what the lint observed
        "'workspace daemon'",  # the daemon's hash of the file on disk
        "e.get('workspace_writes')",  # the daemon's report on the evidence
        "sha256sum",  # the file on disk, read by the run itself
    ):
        assert read in text, read
    # A hash no report has is refused, never accepted.
    assert "e2e-unobserved" in text and "= 422" in text
    # What the model compiled is checked as a proposal, never in force or in a prompt.
    assert "'proposed'" in text and "grep -c '(wiki:glossary, v'" in text
