FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    git \
    && rm -rf /var/lib/apt/lists/*

# nkeys signs the connection when the daemon uses a .creds file or an nkey
# seed (NATS_CREDS_FILE / NATS_NKEY_SEED, nats_connect.py).
RUN pip install --no-cache-dir nats-py==2.3.1 nkeys==0.2.1

COPY command_policy.py daemon.py delivery_journal.py delivery_snapshot.py file_diff.py file_edits.py file_hash.py git_workspace.py nats_connect.py nats_scope.py platform_reviews.py sandbox.py shell_exec.py sprint_integration.py workspace_edit.py workspace_read.py /app/

CMD ["python", "/app/daemon.py"]
