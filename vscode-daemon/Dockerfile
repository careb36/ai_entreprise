FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    git \
    && rm -rf /var/lib/apt/lists/*

# nkeys signs the connection when the daemon uses a .creds file or an nkey
# seed (NATS_CREDS_FILE / NATS_NKEY_SEED, nats_connect.py).
RUN pip install --no-cache-dir nats-py==2.3.1 nkeys==0.2.1

COPY app_run.py code_graph.py command_policy.py console_run.py daemon.py daemon_status.py delivery.py delivery_journal.py delivery_snapshot.py file_diff.py file_edits.py file_hash.py git_base.py git_workspace.py host_folders.py local_merge.py nats_connect.py nats_scope.py platform_reviews.py project_lock.py repo_map.py sandbox.py sandbox_cache.py sandbox_image.py shell_exec.py signatures.py sprint_integration.py win_job.py workspace_edit.py workspace_read.py /app/

CMD ["python", "/app/daemon.py"]
