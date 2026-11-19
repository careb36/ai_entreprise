#!/usr/bin/env bash
# The best edit format per model (issue #689; #359, A.8): the reference
# specifications run once per edit format per model, after Aider's edit-format
# benchmark (method only, no code taken).
#
#   scripts/bench/edit_formats.sh [--out DIR] [--graders SHA] FORMATS [SPEC_DIR...]
#
# FORMATS is "each" or edit formats, comma-separated (auto,whole,search_replace;
# the names are the backend's, services/models/edit_format.py, and an unknown
# one stops here with exit 2, before anything starts). BENCH_MODELS names the
# models, comma-separated; each run asks for one with AGENT_MODEL, which the
# backend applies to every role. Empty: one model, labelled "default", whatever
# the stack routes (with the fake, the fake).
#
#   1. per model and format, scripts/bench/run.sh with BENCH_EDIT_FORMAT=<format>
#      (the backend's EDIT_FORMAT_BY_MODEL {"*": format}) and AGENT_MODEL=<model>;
#   2. scripts/bench/edit_format_card.py write: the first run's card with the
#      edit_formats block (schema v7), one row per model and format: its
#      outcome (pass, fail, unusable when no delivery used the format well,
#      never a pass; unmeasured with the reason), criteria passed (the
#      acceptance tests a person wrote), the deliveries well formed as the
#      backend read them and the daemon applied them, rework and tokens.
#
# The numbers inform a choice made later on real-model runs (#689): nothing
# here sets a default, and compare.py shows them without judging them.
#
# Each run is a whole run.sh (its own stack and fresh database, the same
# environment: BENCH_LLM_URL, BENCH_POLICY, BENCH_TOKEN_BUDGET per run, ...);
# --graders and SPEC_DIR are passed to every run. A run that fails (with the
# fake, also one that does not run green) stops here, exit 1.
#
# Output in DIR (default bench/scorecards/edit_formats): <stamp>.json and .md,
# the first run's stamp, and every run's own card under runs/<run id>/<n>/.
# With BENCH_WORKDIR set, each run works in BENCH_WORKDIR/<n>.
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
OUT_DIR="$REPO/bench/scorecards/edit_formats"
PASS=()
REQUEST=""
GOT_REQUEST=""
while [ $# -gt 0 ]; do
  case "$1" in
    --out=*) OUT_DIR="${1#--out=}"; shift ;;
    --out) [ $# -ge 2 ] || { echo "--out requires a value" >&2; exit 2; }; OUT_DIR="$2"; shift 2 ;;
    --graders=*) PASS+=("$1"); shift ;;
    --graders) [ $# -ge 2 ] || { echo "--graders requires a commit" >&2; exit 2; }; PASS+=("$1" "$2"); shift 2 ;;
    -h|--help) sed -n '2,/^set -u/p' "$0" | sed '$d'; exit 0 ;;
    *)
      if [ -z "$GOT_REQUEST" ]; then REQUEST=$1; GOT_REQUEST=1
      else PASS+=("$(cd "$1" 2>/dev/null && pwd || echo "$1")"); fi
      shift ;;
  esac
done
case "$OUT_DIR" in /*) ;; *) OUT_DIR="$PWD/$OUT_DIR" ;; esac
PY=${BENCH_PYTHON:-${E2E_PYTHON:-python}}
CARDS="$REPO/scripts/bench/edit_format_card.py"

[ -n "$GOT_REQUEST" ] || { echo "name the edit formats: each, or auto,whole,search_replace" >&2; exit 2; }
FORMATS=$("$PY" "$CARDS" parse --formats "$REQUEST") || exit 2
MODELS=default
if [ -n "${BENCH_MODELS:-}" ]; then
  MODELS=$("$PY" "$CARDS" parse --models "$BENCH_MODELS") || exit 2
fi

RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)
RUNS="$OUT_DIR/runs/$RUN_ID"
ARM_FLAGS=()
n=0
for model in $MODELS; do
  for fmt in $FORMATS; do
    n=$((n + 1))
    envs=(BENCH_EDIT_FORMAT="$fmt")
    [ "$model" = default ] || envs+=(AGENT_MODEL="$model")
    [ -z "${BENCH_WORKDIR:-}" ] || envs+=(BENCH_WORKDIR="$BENCH_WORKDIR/$n")
    echo "=== edit formats: $model, $fmt"
    env "${envs[@]}" "$REPO/scripts/bench/run.sh" --out "$RUNS/$n" ${PASS[@]+"${PASS[@]}"} \
      || { echo "BENCH EDIT FORMATS FAILED: the $model/$fmt run did not finish (its card, if any, in $RUNS/$n)"; exit 1; }
    cards=("$RUNS/$n"/*.json)
    [ ${#cards[@]} -eq 1 ] && [ -f "${cards[0]}" ] \
      || { echo "BENCH EDIT FORMATS FAILED: the $model/$fmt run left no single card in $RUNS/$n"; exit 1; }
    ARM_FLAGS+=(--arm "$model" "$fmt" "${cards[0]}")
  done
done
RESULT=$("$PY" "$CARDS" write ${ARM_FLAGS[@]+"${ARM_FLAGS[@]}"} --out-dir "$OUT_DIR") \
  || { echo "BENCH EDIT FORMATS FAILED: the card could not be written"; exit 1; }
echo "edit format card: $RESULT"
cat "${RESULT%.json}.md"
exit 0
