"""
A fake LLM proxy for the end-to-end run: deterministic answers, no network.

It speaks the proxy's ``/v1/chat/completions`` shape and answers by the
prompt's section headers, so one run exercises the whole loop:

- a task prompt gets two files (a module and its test) as ``path=`` blocks;
  a documentation-like task gets the same, which is too short for the
  pre-review text checks, so it is sent back and finally blocked;
- the first review of each task asks for rework, the second approves;
- the ScrumMaster answers an escalation with "retry" and a brief, and
  proposes one procedure in the fixed format.

Every prompt it receives is appended, one per line, to the file named by
``FAKE_LLM_PROMPTS`` so the run can assert what the agents were told.
"""

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


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "task"


def answer(prompt: str) -> str:
    with open(PROMPT_LOG, "a") as log:
        log.write(prompt.replace("\n", " ")[:6000] + "\n")
    if "You are the ScrumMaster. A task was blocked" in prompt:
        return (
            "DECISION: retry\n"
            "BRIEF: Write the full document, at least 180 words, as one path= block.\n"
            "WHY: the attempts were cut short, not wrong\n"
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
            first_review = key not in REVIEWED
            REVIEWED.add(key)
        if first_review:
            return "NEEDS WORK: add a test for empty input."
        return "APPROVED: the module and its test match the task."
    task = re.search(r"## Task: (.+)", prompt)
    if task:
        name = _slug(task.group(1))
        return (
            f"Implemented {task.group(1)}.\n\n"
            f"```python path=src/{name}.py\n"
            f'def {name}() -> str:\n    return "{name} done"\n```\n\n'
            f"```python path=tests/test_{name}.py\n"
            f"from src.{name} import {name}\n\n\n"
            f'def test_{name}():\n    assert {name}() == "{name} done"\n```\n\n'
            f"{PADDING}\n"
        )
    return "Keep the implementation minimal and covered by a unit test."


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
            return self._send(
                200,
                {"models": [{"id": "fake-model", "provider": "fake", "tier": "fast"}]},
            )
        return self._send(200, {"status": "ok"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        if self.path.startswith("/v1/chat/completions"):
            prompt = (body.get("messages") or [{}])[-1].get("content", "")
            return self._send(
                200,
                {
                    "choices": [
                        {"message": {"role": "assistant", "content": answer(prompt)}}
                    ],
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
