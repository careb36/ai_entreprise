FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir nats-py==2.3.1

COPY daemon.py file_diff.py file_hash.py git_workspace.py nats_scope.py platform_reviews.py shell_exec.py workspace_read.py /app/

CMD ["python", "/app/daemon.py"]
