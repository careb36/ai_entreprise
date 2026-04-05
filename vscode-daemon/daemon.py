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

def compute_prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()

def create_commit_message(interaction_id: str, agent_id: str, prompt_hash: str, 
                          decision_signature: Optional[str] = None) -> str:
    sig = decision_signature or "placeholder_signature"
    return f"""feat: AI generated code

interaction_id: {interaction_id}
agent_id: {agent_id}
prompt_hash: {prompt_hash}
decision_signature: {sig}"""

def confirm_workspace_path() -> str:
    Path(WORKSPACE_ROOT_DEFAULT).mkdir(parents=True, exist_ok=True)
    return WORKSPACE_ROOT_DEFAULT

def write_files(workspace_path: str, files: list) -> Dict[str, Any]:
    results = []
    for file_info in files:
        file_path = os.path.join(workspace_path, file_info.get("path", ""))
        content = file_info.get("content", "")
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "w") as f:
            f.write(content)
        results.append({"path": file_path, "status": "written"})
        logger.info(f"File written: {file_path}")
    return results

def execute_shell_command(command: str, cwd: str) -> Dict[str, Any]:
    """Real-time command execution (Idea from OpenHands)."""
    logger.info(f"Executing: {command} in {cwd}")
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
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
    """Unified message handler."""
    subject = msg.subject
    data = json.loads(msg.data.decode())
    reply_to = msg.reply
    
    workspace_path = data.get("workspace_root", confirm_workspace_path())
    
    if subject == "workspace.generate":
        # Write files
        files = data.get("files", [])
        write_results = write_files(workspace_path, files)
        
        # Git commit
        subprocess.run(["git", "init"], cwd=workspace_path, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=workspace_path, capture_output=True)
        commit_msg = create_commit_message(data.get("interaction_id", "ui"), "ai-dev", "hash")
        subprocess.run(["git", "commit", "-m", commit_msg], cwd=workspace_path, capture_output=True)
        
        response = {"ok": True, "files": len(write_results), "path": workspace_path}
        if reply_to:
            await nc.publish(reply_to, json.dumps(response).encode())
            
    elif subject == "workspace.execute":
        cmd = data.get("command")
        result = execute_shell_command(cmd, workspace_path)
        
        # Log results to Glassroom via NATS
        sprint_id = data.get("sprint_id", "1")
        await nc.publish(f"glassroom.{sprint_id}", json.dumps({
            "agent": "System",
            "timestamp": "2026-04-04T00:00:00Z",
            "content": f"Command executed: {cmd}\nExit Code: {result['exit_code']}\nOutput: {result['stdout'] or result['stderr']}",
            "type": "status"
        }).encode())
        
        if reply_to:
            await nc.publish(reply_to, json.dumps(result).encode())

    await msg.ack()

async def main():
    logger.info(f"Connecting to NATS at {NATS_URL}...")
    nc = await nats.connect(NATS_URL)
    
    # Subscriptions
    sub_gen = await nc.subscribe("workspace.generate", cb=lambda m: handle_msg(nc, m))
    sub_exec = await nc.subscribe("workspace.execute", cb=lambda m: handle_msg(nc, m))
    
    logger.info("Daemon is ready for generation and execution tasks.")
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
