"""
VS Code Daemon - Enterprise Edition (v2026.4)

Subscribes to:
- workspace.generate: Write files and open VS Code
- workspace.execute: Execute shell commands and return results (OpenHands style)
"""

import os
import json
import asyncio
import subprocess
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional
from uuid import uuid4

import nats

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("fabrique-daemon")

NATS_URL = os.getenv("NATS_URL", "nats://localhost:4222")
WORKSPACE_ROOT_DEFAULT = os.getenv("WORKSPACE_ROOT", os.path.expanduser("~/factory-workspaces"))
PROTECTED_ROOT_DOCS = {
    "README.md",
    "README.es.md",
    "CONTRIBUTING.md",
    "ARCHITECTURE.md",
    "TESTING_STRATEGY.md",
    "REFACTORING_PLAN.md",
    "DEPLOYMENT.md",
    "runbook.md",
    "runbook.es.md",
    "USER_GUIDE.md",
    "USER_GUIDE.es.md",
    "CHANGELOG.md",
}

def compute_prompt_hash(prompt: str) -> str:
    """Return a SHA-256 hex digest of the prompt for audit / tamper-detection."""
    return hashlib.sha256(prompt.encode()).hexdigest()


def create_commit_message(
    interaction_id: str,
    agent_id: str,
    prompt_hash: str,
    decision_signature: Optional[str] = None,
) -> str:
    """
    Build a structured git commit message that embeds governance metadata.

    The ``interaction_id`` and ``prompt_hash`` fields allow full traceability
    from a git commit back to the originating API call and decision record.
    """
    sig = decision_signature or "placeholder_signature"
    return f"""feat: AI generated code

interaction_id: {interaction_id}
agent_id: {agent_id}
prompt_hash: {prompt_hash}
decision_signature: {sig}"""

def confirm_workspace_path() -> str:
    """Ensure the default workspace root directory exists and return its path."""
    Path(WORKSPACE_ROOT_DEFAULT).mkdir(parents=True, exist_ok=True)
    return WORKSPACE_ROOT_DEFAULT


def _resolve_target_path(workspace_path: str, relative_path: str) -> Optional[Path]:
    """Resolve a payload path safely under the workspace root."""
    root = Path(workspace_path).resolve()
    candidate = (root / (relative_path or "")).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


def _is_protected_existing_doc(workspace_root: Path, target_path: Path) -> bool:
    """Protect curated root docs from blind overwrite by agent output."""
    if not target_path.exists() or target_path.is_dir():
        return False

    try:
        relative = target_path.relative_to(workspace_root)
    except ValueError:
        return False

    return len(relative.parts) == 1 and relative.name in PROTECTED_ROOT_DOCS


def write_files(workspace_path: str, files: list) -> Dict[str, Any]:
    """
    Write a list of files to the workspace directory, creating parent dirs as needed.

    Args:
        workspace_path: Absolute path to the workspace root.
        files:          List of dicts with ``path`` (relative) and ``content`` keys.

    Returns:
        List of ``{"path": <absolute>, "status": "written"}`` dicts.
    """
    results = []
    workspace_root = Path(workspace_path).resolve()
    for file_info in files:
        target_path = _resolve_target_path(workspace_path, file_info.get("path", ""))
        content = file_info.get("content", "")
        overwrite_existing = bool(file_info.get("overwrite_existing") or file_info.get("safe_replace"))

        if not target_path:
            results.append({"path": file_info.get("path", ""), "status": "rejected"})
            logger.warning("Rejected unsafe file path: %s", file_info.get("path", ""))
            continue

        if _is_protected_existing_doc(workspace_root, target_path) and not overwrite_existing:
            results.append({"path": str(target_path), "status": "skipped_protected"})
            logger.warning("Skipped protected document overwrite: %s", target_path)
            continue

        os.makedirs(target_path.parent, exist_ok=True)
        with open(target_path, "w") as f:
            f.write(content)
        results.append({"path": str(target_path), "status": "written"})
        logger.info(f"File written: {target_path}")
    return results


def _build_command_env() -> Dict[str, str]:
    """Provide a stable host-like environment for assistant CLI execution."""
    env = os.environ.copy()
    home = env.get("HOME") or str(Path.home())
    env["HOME"] = home

    nvm_root = Path(home) / ".nvm" / "versions" / "node"
    nvm_bins = sorted(nvm_root.glob("*/bin"), reverse=True) if nvm_root.exists() else []

    path_entries = [
        *(str(path) for path in nvm_bins),
        os.path.join(home, ".local", "bin"),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        env.get("PATH", ""),
    ]
    env["PATH"] = os.pathsep.join(entry for entry in path_entries if entry)
    return env


def _build_shell_command(command: str) -> list[str]:
    """Run commands through the user's shell when available instead of bare /bin/sh."""
    configured_shell = os.getenv("FABRIQUE_EXEC_SHELL")
    shell_path = configured_shell or ("/bin/zsh" if Path("/bin/zsh").exists() else "/bin/sh")
    if shell_path.endswith("zsh"):
        return [shell_path, "-dfc", command]
    if shell_path.endswith("bash"):
        return [shell_path, "--noprofile", "--norc", "-c", command]
    return [shell_path, "-c", command]


def _is_assistant_command(command: str) -> bool:
    normalized = (command or "").lower()
    return "copilot" in normalized or "claude" in normalized


def _summarize_command_result(command: str, result: Dict[str, Any]) -> str:
    command_preview = " ".join((command or "").strip().split())[:180]
    exit_code = result.get("exit_code", -1)
    output = (result.get("stdout") or result.get("stderr") or result.get("error") or "").strip()
    output_preview = output[:240]

    if result.get("success"):
        return f"Workspace command finished successfully (exit {exit_code}): {command_preview}"

    if output_preview:
        return f"Workspace command failed (exit {exit_code}): {output_preview}"

    return f"Workspace command failed (exit {exit_code}): {command_preview}"

def execute_shell_command(command: str, cwd: str) -> Dict[str, Any]:
    """
    Execute a shell command synchronously and capture all output.

    Inspired by OpenHands' real-execution model: runs the command with a
    120-second timeout, captures both stdout and stderr, and returns the
    exit code so the caller can decide whether execution succeeded.

    Args:
        command: Shell command string (passed to ``shell=True``).
        cwd:     Working directory for the command.

    Returns:
        Dict with keys ``success`` (bool), ``stdout``, ``stderr``,
        ``exit_code`` — or ``{"success": False, "error": ...}`` on exception.
    """
    logger.info(f"Executing: {command} in {cwd}")
    try:
        result = subprocess.run(
            _build_shell_command(command),
            cwd=cwd,
            env=_build_command_env(),
            capture_output=True,
            text=True,
            timeout=120
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "exit_code": result.returncode
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

async def handle_msg(nc, msg):
    """
    Unified NATS message handler for ``workspace.generate`` and ``workspace.execute``.

     ``workspace.generate`` flow:
     1. Write all files provided in the payload to the workspace directory.
     2. Optionally reply to the request-reply subject with a summary.

    ``workspace.execute`` flow:
    1. Run the requested shell command inside the workspace directory.
    2. Publish the command output to the Glassroom NATS subject so the frontend
       SSE stream receives it in real-time.
    3. Optionally reply with the full result dict.
    """
    subject = msg.subject
    data = json.loads(msg.data.decode())
    reply_to = msg.reply
    
    workspace_path = data.get("workspace_root", confirm_workspace_path())
    
    if subject == "workspace.generate":
        # Write files
        files = data.get("files", [])
        write_results = write_files(workspace_path, files)

        response = {"ok": True, "files": len(write_results), "path": workspace_path}
        if reply_to:
            await nc.publish(reply_to, json.dumps(response).encode())
            
    elif subject == "workspace.execute":
        cmd = data.get("command")
        result = execute_shell_command(cmd, workspace_path)
        
        sprint_id = data.get("sprint_id", "1")
        if not _is_assistant_command(cmd):
            await nc.publish(f"glassroom.{sprint_id}", json.dumps({
                "agent": "System",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "content": _summarize_command_result(cmd, result),
                "type": "status"
            }).encode())
        
        if reply_to:
            await nc.publish(reply_to, json.dumps(result).encode())

async def main():
    logger.info(f"Connecting to NATS at {NATS_URL}...")
    nc = await nats.connect(NATS_URL)
    
    # Subscriptions
    async def _callback(msg):
        await handle_msg(nc, msg)

    sub_gen = await nc.subscribe("workspace.generate", cb=_callback)
    sub_exec = await nc.subscribe("workspace.execute", cb=_callback)
    
    logger.info("Daemon is ready for generation and execution tasks.")
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
