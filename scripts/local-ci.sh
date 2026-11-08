#!/usr/bin/env bash
# CI local en paralelo: backend + frontend + proxy.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

run_backend() {
    cd "$ROOT/backend"
    PYTHONPATH=.:app poetry run ruff check app tests
    PYTHONPATH=.:app poetry run ruff format --check app tests
    PYTHONPATH=.:app poetry run pytest tests -q
}

run_frontend() {
    cd "$ROOT/frontend"
    npx tsc --noEmit
    npm run lint
    npm run test:run
    npm run build
}

run_proxy() {
    cd "$ROOT/llm-proxy"
    poetry run python -m pytest tests -q
}

echo "=== CI local (paralelo) ==="
run_backend & pid_b=$!
run_frontend & pid_f=$!
run_proxy & pid_p=$!

fail=0
wait $pid_b || fail=1
wait $pid_f || fail=1
wait $pid_p || fail=1

if [ $fail -ne 0 ]; then
    echo "CI local: FALLO"
    exit 1
fi
echo "CI local: OK"