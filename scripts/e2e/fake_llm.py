"""
A fake LLM proxy for the end-to-end run: deterministic answers, no network.

It speaks the proxy's ``/v1/chat/completions`` shape and answers by the
prompt's section headers, so one run exercises the whole loop:

- a task prompt gets two files (a module and its test) as ``path=`` blocks;
  a documentation-like task gets the same, which is too short for the
  pre-review text checks, so it is sent back and finally blocked;
- the first review of each task asks for rework, the second approves;
- the ScrumMaster answers an escalation with "retry" and a brief, and
  proposes one procedure in the fixed format;
- the Architect answers every consultation with the same short brief;
- the Support agent triages every ticket as a high-severity bug;
- the Manager agent suggests accepting every delivery waiting for a person
  (low or high risk: the policy, not the model, keeps a high-risk one for
  the client) and escalates everything else;
- the PO drafts a specification (with a quality target without a number
  and one open question the first time, both fixed on the revision) and
  plans a sprint from it, one task per requirement with its criteria, the
  counter after the greeting;
- the Architect designs it: two modules, an interface, a decision and a
  convention (with one open question the first time, answered before the
  revision);
- the Developer of the counter reads the greeting's module through the
  workspace tools before delivering (one ``workspace__read`` call);
- the first delivery of ``INVENTED_TASK`` also writes a requirements file
  naming ``INVENTED_PACKAGE``, which the fake registry (``/pypi/<name>``)
  answers 404 like any name but pytest's, so the Verifier refutes it; the
  next delivery leaves the file out (issue #549);
- asked to compile a wiki page, it cites the first verified run the request
  lists and adds one statement without a source (``WIKI_UNCITED``), so the
  page waits for a person as a proposal (issue #445);
- QA, asked for browser checks (a project that serves pages), also writes
  one Playwright smoke test per criterion, ``e2e/req_1_1.spec.ts``;
- it also plays a company's own provider: ``/provider/v1/models`` lists two
  models for the key ``e2e-provider-key``, and a request that arrives with
  the company's connection (``X-Provider-Connection``) is answered only with
  the internal token, and written to ``FAKE_LLM_CONNECTIONS``.

Every prompt it receives is appended, one per line, to the file named by
``FAKE_LLM_PROMPTS`` so the run can assert what the agents were told.

It also checks the loop's order from the model's side of the boundary (an
idea from gentle-ai's deterministic agent tests): a QA review of a task must
follow a delivery of that task, one review per delivery at most, and must
show QA the files that delivery wrote. A request that breaks the order is
answered as usual and written to ``FAKE_LLM_VIOLATIONS``; the run fails if
that file is not empty.
"""

import base64
import json
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PADDING = (
    "The change is small, self-contained and follows the existing layout for "
    "source and tests so the project's test runner picks it up without any "
    "configuration change. Each function has a single responsibility, inputs "
    "are validated at the boundary, and the test covers the expected behaviour "
    "described in the task so a reviewer can check it quickly against the "
    "acceptance criteria written in the backlog item."
)
REVIEWED: set[str] = set()
REVIEWED_LOCK = threading.Lock()
PROMPT_LOG = os.environ.get("FAKE_LLM_PROMPTS", "fake_llm.prompts.log")
VIOLATIONS_LOG = os.environ.get("FAKE_LLM_VIOLATIONS", "fake_llm.violations.log")
CONNECTIONS_LOG = os.environ.get("FAKE_LLM_CONNECTIONS", "fake_llm.connections.log")
INTERNAL_TOKEN = os.environ.get("FAKE_LLM_INTERNAL_TOKEN", "")
PROVIDER_KEY = "e2e-provider-key"
# The module each requirement's implementation task writes (its slug), by the
# requirement's title, so QA's acceptance tests, written first, import what
# the Developer will deliver. The last three are the self-hosting run's
# specification of issue #535 (scripts/self_host/fixtures/).
ACCEPTANCE_MODULES = {
    "Greet by name": "add_greeting",
    "Count the greetings": "add_greeting_counter",
    "Filter expressions over an allowlist of fields": "add_task_filter",
    "The filtered list agrees with the board": "add_filtered_task_list",
    "Saved views, private or for the company": "add_saved_views",
}
# The self-hosting run's specification (scripts/self_host/): the PO plans one
# task per requirement, the filtered list after the filter.
SELF_HOST_SPEC = "Saved views of a sprint's tasks, filtered on the evidence"
SELF_HOST_PLAN = {
    "sprint_name": "Saved views 1",
    "goal": "Filter a sprint's tasks on the evidence, and keep the filter as a view",
    "tasks": [
        {"title": "Add task filter", "description": "parse_filter over the allowlist, with its tests.", "priority": "high", "assignee": "Developer", "covers": ["REQ-1.1", "REQ-1.2"]},
        {"title": "Add filtered task list", "description": "The list from the projections, with its tests.", "priority": "medium", "assignee": "Developer", "covers": ["REQ-2.1"], "depends_on": ["Add task filter"]},
        {"title": "Add saved views", "description": "The saved views and their access, with their tests.", "priority": "medium", "assignee": "Developer", "covers": ["REQ-3.1", "REQ-3.2"]},
    ],
    "team": ["Developer"],
    "team_reasons": {"Developer": "the filter and the views are code to write and test"},
}

# The task whose first delivery names a package no registry has (issue #549,
# scripts/e2e/invented_package_step.sh). The file is not a requirements.txt
# and the project has no stack marker, so no install runs for it: the refusal
# is the Verifier's grounded check reading the backend's registry lookup.
INVENTED_TASK = "Add retry helper"
INVENTED_PACKAGE = "fabrique-e2e-invented-package"
INVENTED_MANIFEST = "requirements-dev.txt"

# The project wiki's compilation request (issue #445, services/learning/
# wiki_compile.build_request; scripts/e2e/wiki_step.sh). The fake cites the
# first verified run the request lists and adds one statement without a
# source, so the page is only proposed to a person, never put in force.
WIKI_PROMPT = "You maintain the project wiki."
WIKI_UNCITED = "The team agreed this design is the best one."

# Per task title: how many deliveries the fake wrote, how many reviews it was
# asked for, and the paths of the last delivery.
DELIVERED: dict[str, int] = {}
REVIEWS: dict[str, int] = {}
LAST_PATHS: dict[str, list[str]] = {}


# What separates the task branch from the attempt's message in QA's prompt
# (services/loop/worker/review_prompt.py).
BRANCH_MESSAGE = "--- THIS ATTEMPT'S MESSAGE (the agent's claim) ---"


def _violation(text: str) -> None:
    with open(VIOLATIONS_LOG, "a") as log:
        log.write(text + "\n")


def _check_review(title: str, prompt: str) -> None:
    """Called under REVIEWED_LOCK for every QA review prompt."""
    REVIEWS[title] = REVIEWS.get(title, 0) + 1
    if REVIEWS[title] > DELIVERED.get(title, 0):
        _violation(
            f"review {REVIEWS[title]} of {title!r} after {DELIVERED.get(title, 0)} deliveries: "
            "QA was asked to review work that was not delivered"
        )
        return
    # With the Git workflow QA reads the task branch first, as the daemon
    # read it (services/workspace/branch_view.py, "# Artifact: <path>
    # (<change>)"), then this attempt's message (the agent's claim). The
    # message must be the last delivery, and the branch must hold every
    # file that delivery wrote; the branch may hold earlier attempts' too.
    branch, marker, message = prompt.partition(BRANCH_MESSAGE)
    if not marker:
        branch, message = "", prompt
    shown = re.findall(r"# Artifact: (\S+)", message)
    wrote = LAST_PATHS.get(title, [])
    if shown != wrote:
        _violation(f"QA saw {shown} for {title!r}, but the delivery wrote {wrote}")
    held = set(re.findall(r"# Artifact: (\S+) \(", branch))
    if marker and not set(wrote) <= held:
        _violation(
            f"the task branch QA read for {title!r} lacks {sorted(set(wrote) - held)}, "
            "which the delivery wrote"
        )


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "task"


def smoke_tests(prompt: str, criteria: list[str]) -> list[tuple[str, str]]:
    """QA's browser smoke tests, one file per criterion, when the prompt asks
    for them (``services/browser_smoke.py``): the page's heading is shown."""
    if "## Browser checks to write" not in prompt:
        return []
    return [
        (
            f"e2e/{c.lower().replace('-', '_').replace('.', '_')}.spec.ts",
            "import { test, expect } from '@playwright/test';\n\n"
            f"test('{c} shows the greeting page', async ({{ page }}) => {{\n"
            "  await page.goto(process.env.APP_URL + '/');\n"
            "  await expect(page.locator('h1')).toHaveText('Greetings');\n"
            "});\n",
        )
        for c in criteria
    ]


def answer(prompt: str) -> str:
    with open(PROMPT_LOG, "a") as log:
        # Long enough for a task prompt with every section the checks read.
        log.write(prompt.replace("\n", " ")[:20000] + "\n")
    if prompt.startswith(WIKI_PROMPT):
        title = re.search(r'page "(.+?)" from the records', prompt)
        run = re.search(r"^- run (\d+) \(verified;", prompt, re.MULTILINE)
        cited = f"- A verified run of the sprint delivered this [run:{run.group(1)}]\n\n" if run else ""
        return f"# {title.group(1) if title else 'Page'}\n\n{cited}{WIKI_UNCITED}\n"
    if prompt.startswith("You are the Manager agent"):
        item = re.search(r"--- ITEM \((\w+)\) ---", prompt)
        category = item.group(1) if item else "unknown"
        decision = "accept" if category in ("accept_delivery", "accept_high_risk") else "escalate"
        return (
            f"DECISION: {decision}\n"
            f"CATEGORY: {category}\n"
            "REASON:\n"
            "QA approved it and the Verifier's verdict is in the item; the tier decides who accepts.\n"
            "END"
        )
    if ", the PO of an AI software factory. Write the specification" in prompt:
        revision = "## Answers to your questions" in prompt
        return json.dumps(
            {
                "title": "Greeting service",
                "summary": "A tiny service that greets people by name, for the e2e run.",
                "requirements": [
                    {
                        "title": "Greet by name",
                        "description": "The service answers a greeting that carries the caller's name.",
                        "criteria": [
                            {"given": "a name", "when": "greet is called with it", "then": "the answer contains the name"},
                            {"given": "an empty name", "when": "greet is called", "then": "the answer greets the world"},
                        ],
                    },
                    {
                        "title": "Count the greetings",
                        "description": "The service counts how many greetings it answered since it started.",
                        "criteria": [
                            {"given": "two greetings answered", "when": "the count is read", "then": "it reads 2"},
                        ],
                    },
                ],
                "quality": [
                    {"attribute": "performance", "target": "a greeting answers in under 50 ms" if revision else "fast"}
                ],
                "constraints": ["Python, standard library only"],
                "questions": [] if revision else ["Should the greeting be localized?"],
            }
        )
    if ", the Architect of an AI software factory. Design how the specification" in prompt:
        revision = "## Answers to your questions" in prompt
        return json.dumps(
            {
                "title": "Greeting service design",
                "overview": "One module greets, another counts; a function interface joins them.",
                "modules": [
                    {"name": "Greeter", "responsibility": "Builds the greeting text.", "path": "src/greeting.py", "depends_on": [], "covers": ["REQ-1"]},
                    {"name": "Counter", "responsibility": "Counts the greetings answered.", "path": "src/greeting_counter.py", "depends_on": ["Greeter"], "covers": ["REQ-2"]},
                ],
                "interfaces": [
                    {"name": "greet", "kind": "function", "module": "Greeter", "contract": "greet(name: str) -> str", "consumers": ["Counter"]}
                ],
                "decisions": [
                    {"title": "Standard library only", "context": "The constraint says so.", "decision": "No third-party package.", "consequences": "Nothing to install."}
                ],
                "conventions": ["Every module has a test file beside it."],
                "questions": [] if revision else ["Is the counter per process or shared?"],
            }
        )
    if ", the PO. Plan a sprint from the specification below" in prompt and SELF_HOST_SPEC in prompt:
        return json.dumps(SELF_HOST_PLAN)
    if ", the PO. Plan a sprint from the specification below" in prompt:
        return json.dumps(
            {
                "sprint_name": "Greeting 1",
                "goal": "Greet and count",
                "tasks": [
                    {"title": "Add greeting", "description": "greet(name) with its tests.", "priority": "high", "assignee": "Developer", "covers": ["REQ-1.1", "REQ-1.2"]},
                    {"title": "Add greeting counter", "description": "A counter with its test.", "priority": "medium", "assignee": "Developer", "covers": ["REQ-2.1"], "depends_on": ["Add greeting"]},
                ],
                "team": ["Developer"],
                "team_reasons": {"Developer": "the greeting is code to write and test"},
            }
        )
    if "You are the ScrumMaster. A task was blocked" in prompt:
        return (
            "DECISION: retry\n"
            "BRIEF: Write the full document, at least 180 words, as one path= block.\n"
            "WHY: the attempts were cut short, not wrong\n"
            "END"
        )
    if prompt.startswith("## Consultation request from"):
        return "Reuse the existing module layout and test the empty input first."
    if "You are the Reviewer agent" in prompt:
        return (
            "DECISION: approve\n"
            "CATEGORY: none\n"
            "REASON:\n"
            "The verifier confirmed the claims; nothing contradicts them.\n"
            "END"
        )
    if "You are the Support agent" in prompt:
        return (
            "SEVERITY: high\n"
            "CATEGORY: bug\n"
            "REPLY:\n"
            "Thanks for the report. We understood the board does not load; a "
            "person will follow up.\n"
            "END"
        )
    if "You are the ScrumMaster" in prompt:
        return (
            "PROCEDURE role=Developer title=Cover edge cases\n"
            "Add a test for empty and invalid input to every function you write.\n"
            "WHY: every task this sprint came back once for missing edge cases\n"
            "END"
        )
    if "You are reviewing" in prompt:
        task = re.search(r"for the task: '(.+?)'", prompt)
        key = task.group(1) if task else prompt[:80]
        with REVIEWED_LOCK:
            _check_review(key, prompt)
            first_review = key not in REVIEWED
            REVIEWED.add(key)
        if first_review:
            return "NEEDS WORK: add a test for empty input."
        return "APPROVED: the module and its test match the task."
    task = re.search(r"## Task: (.+)", prompt)
    if task and task.group(1).startswith("Acceptance tests for REQ-"):
        # QA writes one failing test per criterion, named after it, importing
        # the module the implementation task will write inside each test.
        title = task.group(1).strip()
        heading = re.match(r"Acceptance tests for (REQ-\d+): (.+)", title)
        req, module = heading.group(1), ACCEPTANCE_MODULES[heading.group(2).strip()]
        criteria = list(dict.fromkeys(re.findall(rf"\b({req}\.\d+):", prompt)))
        path = f"tests/test_{req.lower().replace('-', '_')}.py"
        tests = "".join(
            f"def test_{c.lower().replace('-', '_').replace('.', '_')}_holds():\n"
            f"    from src.{module} import {module}\n\n"
            f'    assert {module}() == "{module} done"\n\n\n'
            for c in criteria
        )
        smoke = smoke_tests(prompt, criteria)
        with REVIEWED_LOCK:
            DELIVERED[title] = DELIVERED.get(title, 0) + 1
            LAST_PATHS[title] = [path] + [p for p, _ in smoke]
        blocks = "".join(f"```ts path={p}\n{text}```\n\n" for p, text in smoke)
        return f"Acceptance tests of {req}.\n\n```python path={path}\n{tests}```\n\n{blocks}{PADDING}\n"
    if task:
        name = _slug(task.group(1))
        with REVIEWED_LOCK:
            title = task.group(1).strip()
            DELIVERED[title] = DELIVERED.get(title, 0) + 1
            LAST_PATHS[title] = [f"src/{name}.py", f"tests/test_{name}.py"]
            # The counter's first delivery rewrites an acceptance test: the
            # Verifier must refuse it, and the next delivery leaves it alone.
            edits_test = title == "Add greeting counter" and DELIVERED[title] == 1
            if edits_test:
                LAST_PATHS[title].append("tests/test_req_2.py")
            invents = title == INVENTED_TASK and DELIVERED[title] == 1
            if invents:
                LAST_PATHS[title].append(INVENTED_MANIFEST)
        return (
            f"Implemented {task.group(1)}.\n\n"
            f"```python path=src/{name}.py\n"
            f'def {name}() -> str:\n    return "{name} done"\n```\n\n'
            f"```python path=tests/test_{name}.py\n"
            f"from src.{name} import {name}\n\n\n"
            f'def test_{name}():\n    assert {name}() == "{name} done"\n```\n\n'
            + (
                "```python path=tests/test_req_2.py\ndef test_req_2_1_holds():\n    assert True\n```\n\n"
                if edits_test
                else ""
            )
            + (f"```text path={INVENTED_MANIFEST}\n{INVENTED_PACKAGE}\n```\n\n" if invents else "")
            + f"{PADDING}\n"
        )
    return "Keep the implementation minimal and covered by a unit test."


# The task whose Developer reads the project before delivering: the counter
# depends on the greeting, so the greeting's module is on disk by then.
READS_FIRST = "Add greeting counter"
READS_PATH = "src/add_greeting.py"


ARCHITECT_PROMPT = ", the Architect of an AI software factory. Design how the specification"
LOOKS_UP = {"name": "pytest", "ecosystem": "pypi"}
PYPI_RECORD = {
    "info": {"name": "pytest", "version": "9.0.1", "summary": "testing framework", "requires_python": ">=3.9"},
    "releases": {
        "9.0.0": [{"upload_time_iso_8601": "2026-09-15T00:00:00Z"}],
        "9.0.1": [{"upload_time_iso_8601": "2026-10-20T00:00:00Z"}],
    },
}


def tool_call_for(prompt: str, messages: list, tools: list) -> dict | None:
    """One ``workspace__read`` call before the counter's delivery, when the
    backend offered the workspace tools and no tool has answered yet; the
    fake then delivers as usual, so the run's evidence carries the read.
    Likewise one ``research__package`` lookup before the Architect's design,
    when the research tools are offered, so the draft keeps its source."""
    offered = {t.get("function", {}).get("name") for t in tools if isinstance(t, dict)}
    if any(m.get("role") == "tool" for m in messages):
        return None
    if "research__package" in offered and ARCHITECT_PROMPT in prompt:
        return {
            "id": "call_lookup_pytest",
            "type": "function",
            "function": {"name": "research__package", "arguments": json.dumps(LOOKS_UP)},
        }
    if "workspace__read" not in offered:
        return None
    task = re.search(r"## Task: (.+)", prompt)
    if not task or task.group(1).strip() != READS_FIRST:
        return None
    return {
        "id": "call_read_greeting",
        "type": "function",
        "function": {"name": "workspace__read", "arguments": json.dumps({"path": READS_PATH})},
    }


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass

    def do_GET(self):
        # A PyPI look-alike for the research tools (RESEARCH_PYPI_URL points here).
        if self.path == "/pypi/pytest/json":
            return self._send(200, PYPI_RECORD)
        if self.path.startswith("/pypi/"):
            return self._send(404, {"message": "Not Found"})
        if self.path == "/provider/v1/models":
            if self.headers.get("Authorization") != f"Bearer {PROVIDER_KEY}":
                return self._send(401, {"error": "invalid key"})
            models = [{"id": "deepseek-v4-flash"}, {"id": "e2e-unlisted-elsewhere"}]
            return self._send(200, {"object": "list", "data": models})
        if self.path.startswith("/v1/models"):
            return self._send(
                200,
                {"models": [{"id": "fake-model", "provider": "fake", "tier": "fast"}]},
            )
        return self._send(200, {"status": "ok"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path.startswith("/v1/chat/completions"):
            messages = body.get("messages") or [{}]
            prompt = next(
                (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
                "",
            )
            connection = self.headers.get("X-Provider-Connection")
            if connection:
                # The proxy honours a company's connection only with the shared
                # token; record what reached it, never the key itself.
                if not INTERNAL_TOKEN or self.headers.get("X-Internal-Token") != INTERNAL_TOKEN:
                    _violation("a company connection arrived without the internal token")
                    return self._send(403, {"error": "connection without the internal token"})
                conn = json.loads(base64.b64decode(connection))
                run = re.search(r"RUN (\S+)", prompt)
                with open(CONNECTIONS_LOG, "a") as log:
                    log.write(json.dumps({
                        "model": body.get("model"),
                        "base_url": conn.get("base_url"),
                        "key_ok": conn.get("api_key") == PROVIDER_KEY,
                        "run": run.group(1) if run else None,
                    }) + "\n")
            tool_call = tool_call_for(prompt, messages, body.get("tools") or [])
            if tool_call:
                message = {"role": "assistant", "content": None, "tool_calls": [tool_call]}
            else:
                message = {"role": "assistant", "content": answer(prompt)}
            return self._send(
                200,
                {
                    "choices": [{"message": message}],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 50,
                        "total_tokens": 60,
                    },
                    "served_model": "fake-model",
                    "provider": "fake",
                },
            )
        return self._send(503, {"error": "not supported by the fake proxy"})


if __name__ == "__main__":
    port = int(os.environ.get("FAKE_LLM_PORT", "15100"))
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
