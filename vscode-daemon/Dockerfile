FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    git \
    && rm -rf /var/lib/apt/lists/*

# The lock compiled from requirements.in, every package checked against its
# sha256: a changed or substituted file on the index fails the build. nkeys,
# in the lock, signs the connection when the daemon uses a .creds file or an
# nkey seed (NATS_CREDS_FILE / NATS_NKEY_SEED, nats_connect.py).
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /app/requirements.txt

COPY abilities.py app_run.py browser_probe.py code_graph.py command_policy.py console_run.py daemon.py daemon_status.py delivery.py delivery_journal.py delivery_snapshot.py file_diff.py file_edits.py file_hash.py forge_gate.py git_base.py git_diff.py git_env.py git_local.py git_sync.py git_workspace.py host_folders.py local_merge.py nats_connect.py nats_scope.py platform_reviews.py playwright_pin.py project_lock.py repo_map.py run_logs.py sandbox.py sandbox_cache.py sandbox_image.py security_probe.py shell_exec.py signatures.py sprint_integration.py win_job.py workspace_edit.py workspace_read.py /app/

COPY images/browser/probe-browser.js /app/images/browser/probe-browser.js

CMD ["python", "/app/daemon.py"]
