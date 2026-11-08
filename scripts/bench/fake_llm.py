"""
A fake LLM proxy for the bench: deterministic answers, no network.

A sibling of ``scripts/e2e/fake_llm.py`` that plays a producer who gets each
reference specification right at the first attempt, so the circuit (post,
approve, plan, run, grade, score) runs green in CI without a model. Its
answers come from each specification's ``fake/`` folder, never from the
acceptance tests a person wrote (``acceptance/``), which grade the result:

- ``fake/plan.json``: the PO's plan (``sprint_name``, ``goal``, ``tasks``
  with ``covers`` and ``depends_on``), each task naming the ``files`` its
  delivery writes, and ``acceptance``: per requirement, the test file QA's
  test-writing task delivers;
- ``fake/files/<path>``: the content of each of those files.

It answers the PO's planning prompt for a specification by its title, QA's
test-writing task (``Acceptance tests for REQ-n: <title>``) and each
implementation task by its title, approves every review, and answers
anything else (the ScrumMaster's retrospective, say) with a short note.

Every answer reports its usage (``usage``), so the bench's token count runs
through the same ``usage_status: reported`` path as a real provider's.
Each prompt is appended, one per line, to ``FAKE_LLM_PROMPTS`` when set.
"""

import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PADDING = (
    "The change is small and self-contained, follows the layout the "
    "specification's constraints name for source and tests, and the tests "
    "cover the acceptance criteria this task covers, so the project's test "
    "runner picks them up from the project root without any configuration. "
    "Inputs are validated at the boundary and each function has a single "
    "responsibility, so a reviewer can check it quickly against the criteria."
)
SPECS_DIR = Path(os.environ.get("BENCH_SPECS", Path(__file__).resolve().parents[2] / "bench" / "specs"))
PROMPT_LOG = os.environ.get("FAKE_LLM_PROMPTS", "")
TITLE_LIMIT = 80  # services/acceptance.test_task cuts the test task's title here


def load(specs_dir: Path) -> tuple[dict, dict]:
    """(plans by specification title, deliveries by task title): each
    delivery is the list of (path, content) its answer writes."""
    plans: dict[str, dict] = {}
    deliveries: dict[str, list[tuple[str, str]]] = {}
    for spec_dir in sorted(p for p in specs_dir.iterdir() if (p / "fake" / "plan.json").is_file()):
        spec = json.loads((spec_dir / "spec.json").read_text(encoding="utf-8"))
        plan = json.loads((spec_dir / "fake" / "plan.json").read_text(encoding="utf-8"))
        files = spec_dir / "fake" / "files"

        def read(path: str, root: Path = files) -> tuple[str, str]:
            return path, (root / path).read_text(encoding="utf-8")

        plans[spec["title"]] = {
            "sprint_name": plan["sprint_name"],
            "goal": plan["goal"],
            "tasks": [{k: v for k, v in t.items() if k != "files"} for t in plan["tasks"]],
        }
        for task in plan["tasks"]:
            deliveries[task["title"]] = [read(p) for p in task["files"]]
        for index, requirement in enumerate(spec["requirements"], start=1):
            rid = f"REQ-{index}"
            title = f"Acceptance tests for {rid}: {requirement['title']}"[:TITLE_LIMIT]
            deliveries[title] = [read(plan["acceptance"][rid])]
    return plans, deliveries


PLANS, DELIVERIES = load(SPECS_DIR)


def _fence(path: str, content: str) -> str:
    language = "python" if path.endswith(".py") else "text"
    return f"```{language} path={path}\n{content.rstrip()}\n```"


def answer(prompt: str) -> str:
    if PROMPT_LOG:
        with open(PROMPT_LOG, "a") as log:
            log.write(prompt.replace("\n", " ")[:20000] + "\n")
    if ", the PO. Plan a sprint from the specification below" in prompt:
        title = re.search(r"## Specification: (.+)", prompt)
        plan = PLANS.get(title.group(1).strip()) if title else None
        return json.dumps(plan) if plan else '{"tasks": []}'
    if "You are reviewing" in prompt:
        return "APPROVED: the delivery matches the task and its criteria."
    task = re.search(r"## Task: (.+)", prompt)
    files = DELIVERIES.get(task.group(1).strip()) if task else None
    if files:
        blocks = "\n\n".join(_fence(path, content) for path, content in files)
        return f"Delivered {task.group(1).strip()}.\n\n{blocks}\n\n{PADDING}\n"
    return "Nothing to add: the sprint went as planned."


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
        if self.path.startswith("/v1/models"):
            return self._send(200, {"models": [{"id": "fake-model", "provider": "fake", "tier": "fast"}]})
        return self._send(200, {"status": "ok"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if not self.path.startswith("/v1/chat/completions"):
            return self._send(503, {"error": "not supported by the fake proxy"})
        messages = body.get("messages") or [{}]
        prompt = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
        text = answer(prompt or "")
        return self._send(
            200,
            {
                "choices": [{"message": {"role": "assistant", "content": text}}],
                # Deterministic and reported, like a provider's own figure.
                "usage": {
                    "prompt_tokens": len(prompt) // 4,
                    "completion_tokens": len(text) // 4,
                    "total_tokens": len(prompt) // 4 + len(text) // 4,
                },
                "served_model": "fake-model",
                "provider": "fake",
            },
        )


if __name__ == "__main__":
    port = int(os.environ.get("FAKE_LLM_PORT", "15200"))
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
