#!/usr/bin/env bash
set -euo pipefail

# Poetry asks the system keyring on every operation; there is no secret to
# fetch here (keys come from the environment), and the keyring can hang on a
# container without a desktop session. Same guard the CI workflow uses.
export POETRY_NO_INTERACTION=1
export PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring

echo "📦 Installing Poetry…"
# The base image (FROM python:3.12-bookworm) does not ship Poetry.
pip install poetry >/dev/null

# Create virtualenvs inside each project so the interpreter path in
# devcontainer.json (`backend/.venv/bin/python`) resolves after install and
# VS Code picks it up for IntelliSense, tests and debugging.
poetry config virtualenvs.in-project true

echo "🐍 Backend: poetry install…"
(cd backend && poetry install --no-interaction)

echo "🐍 LLM Proxy: poetry install…"
# The proxy is its own Poetry project (its own pyproject.toml / lock).
(cd llm-proxy && poetry install --no-interaction --no-root)

echo "🎨 Frontend: npm ci…"
(cd frontend && npm ci)

echo "🧹 ruff (the version CI pins)…"
pip install "ruff==0.17.1" >/dev/null

# The first `docker compose up -d` needs the variables docker-compose
# interpolates from `.env`; copy the example if it doesn't exist yet so the
# infrastructure (Postgres, NATS, Neo4j, Jaeger) starts without friction.
if [[ ! -f .env ]]; then
  echo "📄 Copying .env.example → .env (fill in the real keys)"
  cp .env.example .env
fi

# Pre-seed github.com in known_hosts so the first `git push` over SSH does not
# stall on the "authenticity of host" prompt. Skipped when the host's ~/.ssh is
# mounted read-only (the host's own known_hosts is used directly then).
if [[ -d ~/.ssh && -w ~/.ssh ]]; then
  echo "🔑 Adding github.com to known_hosts…"
  ssh-keyscan github.com >> ~/.ssh/known_hosts 2>/dev/null || true
fi

# Fall back to a sensible git identity if the host's wasn't picked up, so
# commits made from the container don't fail with "Please tell me who you are".
if [[ -z "$(git config --get user.name 2>/dev/null)" ]]; then
  echo "👤 No git identity found; set one before committing:"
  echo "   git config --global user.name \"Your Name\""
  echo "   git config --global user.email \"you@example.com\""
fi

echo "✅ Environment ready. Infra services: \`docker compose up -d\`"
echo "   Backend:   cd backend && PYTHONPATH=.:app poetry run uvicorn app.main:app --reload --port 8000"
echo "   LLM proxy: cd llm-proxy && poetry run python app.py"
echo "   Frontend:  cd frontend && npm run dev"
echo "   To push:   see .devcontainer/README.md (SSH agent is forwarded)"
