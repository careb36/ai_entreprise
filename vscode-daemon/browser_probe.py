"""Is there a browser Playwright can launch where the smoke tests run? (#315)

Before QA's browser smoke tests, ``workspace.app.check`` runs the probe
``images/browser/probe-browser.js`` (adapted from AixleHQ/flow's, see its
header) when the request says ``browser_probe: true``: with ``node -e`` in
the project's folder (host mode) or inside the application check's container
(container mode), where the e2e command runs next. The probe launches the
browser headless, paints a page and reads it back, and prints one line the
daemon reads here. The reply's ``browser_probe`` is the daemon's observation:

- ``available``: whether a browser launched (``true`` / ``false``);
- ``from``: ``image`` when the probe used the browser sandbox image's own
  pinned Playwright (``FABRIQUE_PLAYWRIGHT_DIR``), ``project`` when it used
  the project's (whose ``node_modules`` agent-written code may have shaped);
- ``playwright``, ``browser``: the versions the probe found, ``reason`` when
  none launched, ``exit_code``, ``observed_by: daemon``.

A probe that could not run at all (no ``node``, the script missing from this
install) says so in ``reason`` with ``available: false``; never a guess.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import sandbox

SCRIPT = Path(__file__).resolve().parent / "images" / "browser" / "probe-browser.js"
MARK = "fabrique-browser-probe: "
# The probe's own watchdog is 60 s; the daemon's bound covers a slow launch.
PROBE_SECONDS = 90
TAIL_CHARS = 1000


def script() -> str | None:
    try:
        return SCRIPT.read_text(encoding="utf-8")
    except OSError:
        return None


def _unavailable(reason: str, **extra: Any) -> dict[str, Any]:
    return {"available": False, "reason": reason, "observed_by": "daemon", **extra}


def read(run: dict[str, Any]) -> dict[str, Any]:
    """The observation from the probe's run (``sandbox.run_bounded``'s result)."""
    text = f"{run.get('stdout') or ''}"
    code = run.get("exit_code")
    for line in reversed(text.splitlines()):
        if line.startswith(MARK):
            try:
                said = json.loads(line[len(MARK):])
            except ValueError:
                break
            if isinstance(said, dict) and isinstance(said.get("available"), bool):
                keep = ("available", "from", "package", "playwright", "browser", "reason")
                out = {k: said[k] for k in keep if k in said}
                # The exit status decides with the line: a browser that launched exits 0.
                if out["available"] and code != 0:
                    out = {**out, "available": False, "reason": f"the probe exited {code} after reporting a browser"}
                return {**out, "exit_code": code, "observed_by": "daemon"}
            break
    output = f"{text}{run.get('stderr') or ''}".strip()
    if run.get("timed_out"):
        return _unavailable(f"the probe did not finish within {PROBE_SECONDS}s", exit_code=None,
                            output_tail=output[-TAIL_CHARS:])
    if code == 127 or "node: not found" in output or "executable file not found" in output:
        return _unavailable("node is not installed where the smoke tests run", exit_code=code,
                            output_tail=output[-TAIL_CHARS:])
    return _unavailable(f"the probe gave no answer (exit {code})", exit_code=code, output_tail=output[-TAIL_CHARS:])


def on_host(cwd: str, env: dict[str, str]) -> dict[str, Any]:
    """The probe in the project's folder on the daemon's machine."""
    source = script()
    if source is None:
        return _unavailable(f"the probe script is missing from this daemon ({SCRIPT.name})")
    node = shutil.which("node", path=env.get("PATH") or os.environ.get("PATH"))
    if not node:
        return _unavailable("node is not installed on the daemon's machine", exit_code=None)
    return read(sandbox.run_bounded([node, "-e", source], PROBE_SECONDS, cwd=cwd, env=env, own_group=True))


def in_container(runtime: str, name: str, env_args: list[str]) -> dict[str, Any]:
    """The probe inside the running application check's container."""
    source = script()
    if source is None:
        return _unavailable(f"the probe script is missing from this daemon ({SCRIPT.name})")
    return read(sandbox.run_bounded([runtime, "exec", *env_args, name, "node", "-e", source], PROBE_SECONDS))
