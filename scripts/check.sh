#!/usr/bin/env bash
# One command for every check, for people and agents.
#
#   scripts/check.sh quick   the checks a change can break, while working
#   scripts/check.sh full    everything CI runs, once before pushing
#
# quick reads what changed against the base (committed since the merge base,
# staged, unstaged and untracked) and runs:
#   - a change outside frontend/: the backend tests it can break
#     (scripts/affected_tests.py --run -- -n 0) and ruff check / ruff format
#     --check on the changed Python files of backend/app and backend/tests;
#   - a change under frontend/: `npm run test:changed`, eslint on the changed
#     files of frontend/src and `npx tsc --noEmit`;
#   - a change to .github/workflows/: actionlint, when it is installed;
#   - always, being fast: the two dependency rules and the file-size ratchet.
# full runs the whole backend suite in parallel, ruff on app and tests, the
# LLM proxy's tests, the whole frontend (tsc, lint, test:run, build),
# actionlint and the rule checks.
#
# Each prints a summary with every step's status and time, and exits non-zero
# when a step failed. Dependencies are installed only when missing or stale:
# frontend/node_modules carries the hash of package-lock.json it was installed
# from, and a Poetry venv the hash of its lock file (.check-lock-sha256).
#
# Environment:
#   CHECK_BASE              ref to compare with (default origin/main, then main, then HEAD)
#   CHECK_PYTHON            the backend's interpreter (default: the Poetry venv of backend/)
#   CHECK_PROXY_PYTHON      the LLM proxy's interpreter (default: the Poetry venv of llm-proxy/)
#   CHECK_BACKEND_WORKERS   pytest-xdist workers for full (default auto; -n 2 on a shared machine)
#   CHECK_FILES             the changed files for quick, instead of asking git (space separated)
#   CHECK_DRY_RUN=1         print the steps the mode would run and run none of them
#   CHECK_REPORT            a file to write each step to, one per line as
#                           name<TAB>state<TAB>seconds (scripts/land.sh reads it)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

usage() {
  sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'
  echo "See the top of scripts/check.sh for what each mode runs and its variables."
}

MODE="${1:-}"
case "$MODE" in
  quick|full) ;;
  -h|--help|help) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac

DRY_RUN="${CHECK_DRY_RUN:-0}"
NAMES=()
STATES=()
TIMES=()
FAILED=0

now() { if [[ -n "${EPOCHREALTIME:-}" ]]; then echo "$EPOCHREALTIME"; else date +%s; fi; }
since() { awk -v a="$1" -v b="$(now)" 'BEGIN { printf "%.1f", b - a }'; }

record() {  # name, state, seconds
  NAMES+=("$1"); STATES+=("$2"); TIMES+=("$3")
  case "$2" in ok|skipped*|dry-run) ;; *) FAILED=1 ;; esac
}

# Run one step in a subshell (so `cd` stays inside it) and record how it went.
run_step() {
  local name="$1"; shift
  if [[ "$DRY_RUN" == 1 ]]; then
    echo "[dry-run] $name: $*"
    record "$name" dry-run 0.0
    return 0
  fi
  echo
  echo "==> $name"
  local started rc=0
  started="$(now)"
  ( "$@" ) || rc=$?
  if [[ $rc -eq 0 ]]; then record "$name" ok "$(since "$started")"
  else record "$name" "FAILED ($rc)" "$(since "$started")"; fi
}

skip_step() { echo "--- $1: skipped ($2)"; record "$1" "skipped: $2" 0.0; }

in_dir() { local dir="$1"; shift; cd "$dir" && "$@"; }

file_hash() {
  if command -v sha256sum >/dev/null 2>&1; then cat "$@" | sha256sum | cut -d' ' -f1
  else cat "$@" | shasum -a 256 | cut -d' ' -f1; fi
}

# ── Dependencies ─────────────────────────────────────────────────────────────

ensure_node_modules() {
  local stamp="frontend/node_modules/.check-lock-sha256" want
  want="$(file_hash frontend/package-lock.json)"
  if [[ -f "$stamp" && "$(cat "$stamp")" == "$want" ]]; then
    echo "frontend/node_modules matches package-lock.json"
    return 0
  fi
  echo "frontend/node_modules is missing or stale: npm ci"
  (cd frontend && npm ci --no-audit --no-fund) || return 1
  echo "$want" > "$stamp"
}

# The interpreter of a Poetry project's venv, installed when missing or stale.
# Prints only the path on stdout; everything else goes to stderr.
poetry_python() {  # project dir, then requirements files to pip install into the venv
  local dir="$1"; shift
  local lock="$dir/poetry.lock" venv="" want
  want="$(file_hash "$lock" "$@" 2>/dev/null || file_hash "$lock")"
  if ! command -v poetry >/dev/null 2>&1; then
    echo "poetry is not on PATH; set CHECK_PYTHON / CHECK_PROXY_PYTHON to an interpreter" >&2
    return 1
  fi
  venv="$(cd "$dir" && poetry env info --path 2>/dev/null)" || venv=""
  if [[ -n "$venv" && -x "$venv/bin/python" && -f "$venv/.check-lock-sha256" \
        && "$(cat "$venv/.check-lock-sha256")" == "$want" ]]; then
    echo "$venv/bin/python"
    return 0
  fi
  echo "The venv of $dir is missing or stale: poetry install" >&2
  (cd "$dir" && POETRY_NO_INTERACTION=1 poetry install --no-interaction --no-root) >&2 || return 1
  venv="$(cd "$dir" && poetry env info --path)" || return 1
  local requirements
  for requirements in "$@"; do
    "$venv/bin/python" -m pip install -q -r "$requirements" >&2 || return 1
  done
  echo "$want" > "$venv/.check-lock-sha256"
  echo "$venv/bin/python"
}

BACKEND_PY=""
setup_backend_python() {
  if [[ "$DRY_RUN" == 1 ]]; then BACKEND_PY="${CHECK_PYTHON:-<backend venv>/bin/python}"; return 0; fi
  local started; started="$(now)"
  if [[ -n "${CHECK_PYTHON:-}" ]]; then
    BACKEND_PY="$CHECK_PYTHON"
  elif ! BACKEND_PY="$(poetry_python backend)"; then
    BACKEND_PY=""
    record "backend environment" FAILED "$(since "$started")"
    return 0
  fi
  echo "backend interpreter: $BACKEND_PY"
  record "backend environment" ok "$(since "$started")"
}

PROXY_PY=""
setup_proxy_python() {
  if [[ "$DRY_RUN" == 1 ]]; then PROXY_PY="${CHECK_PROXY_PYTHON:-<llm-proxy venv>/bin/python}"; return 0; fi
  local started; started="$(now)"
  if [[ -n "${CHECK_PROXY_PYTHON:-}" ]]; then
    PROXY_PY="$CHECK_PROXY_PYTHON"
  elif ! PROXY_PY="$(poetry_python llm-proxy llm-proxy/requirements-dev.txt)"; then
    PROXY_PY=""
    record "llm-proxy environment" FAILED "$(since "$started")"
    return 0
  fi
  echo "llm-proxy interpreter: $PROXY_PY"
  record "llm-proxy environment" ok "$(since "$started")"
}

NODE_READY=0
setup_node() {
  if [[ "$DRY_RUN" == 1 ]]; then NODE_READY=1; return 0; fi
  run_step "frontend dependencies" ensure_node_modules
  if [[ "${STATES[${#STATES[@]}-1]}" == ok ]]; then NODE_READY=1; fi
}

# ruff: CHECK_RUFF, else the one on PATH, else the backend venv's. CI pins a
# version (backend-tests.yml) because the default rule set moves between releases.
ruff_cmd() {
  if [[ -n "${CHECK_RUFF:-}" ]]; then echo "$CHECK_RUFF"
  elif command -v ruff >/dev/null 2>&1; then command -v ruff
  else echo "$BACKEND_PY -m ruff"; fi
}
warn_ruff_version() {
  local pinned have
  pinned="$(grep -o 'ruff==[0-9.]*' .github/workflows/backend-tests.yml | head -1 | cut -d= -f3)"
  have="$($(ruff_cmd) --version 2>/dev/null | awk '{print $2}')" || have=""
  if [[ -n "$pinned" && "$have" != "$pinned" ]]; then
    echo "note: ruff ${have:-not found} here, CI pins $pinned (pip install \"ruff==$pinned\")" >&2
  fi
}

# ── Rule checks (both modes) ─────────────────────────────────────────────────

rule_checks() {
  run_step "backend dependency rule" python3 -I scripts/check_architecture.py
  run_step "frontend layer rule" python3 -I scripts/check_frontend_architecture.py
  run_step "file sizes" python3 -I scripts/check_file_sizes.py
}

actionlint_step() {
  if command -v actionlint >/dev/null 2>&1; then
    run_step "actionlint" actionlint
  else
    skip_step "actionlint" "not installed"
  fi
}

# ── quick ────────────────────────────────────────────────────────────────────

resolve_base() {
  local ref
  for ref in ${CHECK_BASE:-} origin/main main; do
    if git rev-parse --verify --quiet "$ref^{commit}" >/dev/null; then echo "$ref"; return 0; fi
  done
  echo HEAD
}

changed_files() {  # one per line, deleted files included
  if [[ -n "${CHECK_FILES+set}" ]]; then
    local file
    for file in $CHECK_FILES; do echo "$file"; done
    return 0
  fi
  {
    git diff --name-only "$BASE...HEAD" 2>/dev/null || git diff --name-only "$BASE" HEAD
    git diff --name-only HEAD
    git ls-files --others --exclude-standard
  } | sort -u
}

quick() {
  BASE="$(resolve_base)"
  local changed backend=() python=() frontend=() lint=() workflows=0 file
  changed="$(changed_files)"
  while IFS= read -r file; do
    if [[ -z "$file" ]]; then continue; fi
    case "$file" in
      frontend/*) frontend+=("$file") ;;
      *) backend+=("$file") ;;
    esac
    case "$file" in
      backend/app/*.py|backend/tests/*.py)
        if [[ -f "$file" ]]; then python+=("${file#backend/}"); fi ;;
      frontend/src/*.ts|frontend/src/*.tsx|frontend/src/*.js|frontend/src/*.jsx)
        if [[ -f "$file" ]]; then lint+=("${file#frontend/}"); fi ;;
      .github/workflows/*|.github/actionlint.yaml) workflows=1 ;;
    esac
  done <<< "$changed"
  echo "quick: $(printf '%s\n' "$changed" | grep -c . || true) changed file(s) against $BASE"

  if [[ ${#backend[@]} -gt 0 ]]; then
    setup_backend_python
    if [[ -n "$BACKEND_PY" ]]; then
      local selection=(--base "$BASE")
      [[ -n "${CHECK_FILES+set}" ]] && selection=(--files "${backend[@]}")
      run_step "backend affected tests" "$BACKEND_PY" scripts/affected_tests.py "${selection[@]}" --run -- -n 0
    else
      skip_step "backend affected tests" "no backend environment"
    fi
    if [[ ${#python[@]} -gt 0 ]]; then
      warn_ruff_version
      run_step "ruff check (changed)" in_dir backend $(ruff_cmd) check "${python[@]}"
      run_step "ruff format (changed)" in_dir backend $(ruff_cmd) format --check "${python[@]}"
    fi
  fi

  if [[ ${#frontend[@]} -gt 0 ]]; then
    setup_node
    if [[ $NODE_READY == 1 ]]; then
      run_step "frontend changed tests" in_dir frontend env TEST_CHANGED_BASE="$BASE" npm run test:changed
      if [[ ${#lint[@]} -gt 0 ]]; then
        run_step "eslint (changed)" in_dir frontend npx eslint "${lint[@]}"
      fi
      run_step "tsc" in_dir frontend npx tsc --noEmit
    fi
  fi

  if [[ $workflows == 1 ]]; then actionlint_step; fi
  rule_checks
}

# ── full ─────────────────────────────────────────────────────────────────────

full() {
  local workers="${CHECK_BACKEND_WORKERS:-auto}"
  setup_backend_python
  if [[ -n "$BACKEND_PY" ]]; then
    run_step "backend tests (-n $workers)" in_dir backend env PYTHONPATH=.:app \
      "$BACKEND_PY" -m pytest tests -q -n "$workers" --dist loadgroup
    warn_ruff_version
    run_step "ruff check" in_dir backend $(ruff_cmd) check app tests
    run_step "ruff format" in_dir backend $(ruff_cmd) format --check app tests
  else
    skip_step "backend tests" "no backend environment"
  fi

  setup_proxy_python
  if [[ -n "$PROXY_PY" ]]; then
    run_step "llm-proxy tests" in_dir llm-proxy "$PROXY_PY" -m pytest -q
  else
    skip_step "llm-proxy tests" "no llm-proxy environment"
  fi

  setup_node
  if [[ $NODE_READY == 1 ]]; then
    run_step "tsc" in_dir frontend npx tsc --noEmit
    run_step "eslint" in_dir frontend npm run lint
    run_step "frontend tests" in_dir frontend npm run test:run
    run_step "frontend build" in_dir frontend npm run build
  fi

  actionlint_step
  rule_checks
}

summary() {
  local i
  echo
  echo "check.sh $MODE"
  for i in "${!NAMES[@]}"; do
    printf '  %-32s %-28s %7ss\n' "${NAMES[$i]}" "${STATES[$i]}" "${TIMES[$i]}"
  done
  printf '  %-32s %-28s %7ss\n' "total" "$([[ $FAILED == 0 ]] && echo ok || echo FAILED)" "$(since "$STARTED")"
  if [[ -n "${CHECK_REPORT:-}" ]]; then
    : > "$CHECK_REPORT"
    for i in "${!NAMES[@]}"; do
      printf '%s\t%s\t%s\n' "${NAMES[$i]}" "${STATES[$i]}" "${TIMES[$i]}" >> "$CHECK_REPORT"
    done
  fi
}

STARTED="$(now)"
"$MODE"
summary
exit "$FAILED"
