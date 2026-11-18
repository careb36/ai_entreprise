#!/usr/bin/env bash
# The bench's section ablation (issues #443 and #438): what each Developer
# prompt section is worth, measured by running the specifications without it.
#
#   scripts/bench/ablate.sh [--out DIR] [--graders SHA] SECTIONS [SPEC_DIR...]
#
# SECTIONS is "each" or optional prompt section names, comma-separated
# (memory,related_code; the names are the backend's, prompt_budget.py, and an
# unknown or required one stops here with exit 2, before anything starts).
#
#   1. the baseline: scripts/bench/run.sh as it is, nothing turned off;
#   2. one arm per section: run.sh again with BENCH_SECTIONS_OFF=<section>,
#      which the backend reads as PROMPT_SECTIONS_OFF (PR #648). With "each",
#      the optional sections the baseline's prompts carried (estimated tokens
#      above zero); the others are listed on the card as not run;
#   3. scripts/bench/ablation.py write: the baseline's card with an ablation
#      block (schema v4), per section the arm's numbers beside the baseline's
#      and the difference: criteria passed (the acceptance tests a person
#      wrote), rework requests, estimated prompt tokens and reported tokens,
#      over the specifications both ran in full.
#
# The numbers inform a decision the owner makes (#443): nothing here chooses
# a budget or turns a section off, and compare.py shows them without judging.
#
# Each run is a whole run.sh (its own stack and fresh database, the same
# environment: BENCH_LLM_URL, BENCH_POLICY, BENCH_TOKEN_BUDGET per run, ...);
# --graders and SPEC_DIR are passed to every run. A run that fails (with the
# fake, also one that does not run green) stops the ablation, exit 1.
#
# Output in DIR (default bench/scorecards/ablation): <stamp>.json and .md,
# the baseline's stamp, and every run's own card under arms/<run id>/<arm>/.
# With BENCH_WORKDIR set, each run works in BENCH_WORKDIR/<arm>.
set -u
REPO=$(cd "$(dirname "$0")/../.." && pwd)
OUT_DIR="$REPO/bench/scorecards/ablation"
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
ABLATION="$REPO/scripts/bench/ablation.py"

[ -n "$GOT_REQUEST" ] || { echo "name the sections to turn off: each, or memory,related_code,..." >&2; exit 2; }
REQUEST=$("$PY" "$ABLATION" parse --request "$REQUEST") || exit 2

RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)
ARMS="$OUT_DIR/arms/$RUN_ID"
CARD=""
run_arm() {  # run_arm <arm name> <sections off>: sets CARD to the run's card
  local arm=$1 off=$2 cards
  local envs=(BENCH_SECTIONS_OFF="$off")
  [ -z "${BENCH_WORKDIR:-}" ] || envs+=(BENCH_WORKDIR="$BENCH_WORKDIR/$arm")
  echo "=== ablation: $arm (prompt sections off: ${off:-none})"
  env "${envs[@]}" "$REPO/scripts/bench/run.sh" --out "$ARMS/$arm" ${PASS[@]+"${PASS[@]}"} \
    || { echo "BENCH ABLATION FAILED: the $arm run did not finish (its card, if any, in $ARMS/$arm)"; exit 1; }
  cards=("$ARMS/$arm"/*.json)
  [ ${#cards[@]} -eq 1 ] && [ -f "${cards[0]}" ] \
    || { echo "BENCH ABLATION FAILED: the $arm run left no single card in $ARMS/$arm"; exit 1; }
  CARD=${cards[0]}
}

run_arm baseline ""
BASELINE=$CARD
SECTIONS=$("$PY" "$ABLATION" plan --request "$REQUEST" --baseline "$BASELINE") \
  || { echo "BENCH ABLATION FAILED: the baseline's card could not be read"; exit 1; }
[ -n "$SECTIONS" ] || echo "the baseline's prompts carried no optional section: nothing to turn off"
ARM_FLAGS=()
for section in $SECTIONS; do
  run_arm "$section" "$section"
  ARM_FLAGS+=(--arm "$section=$CARD")
done
RESULT=$("$PY" "$ABLATION" write --baseline "$BASELINE" --request "$REQUEST" \
  ${ARM_FLAGS[@]+"${ARM_FLAGS[@]}"} --out-dir "$OUT_DIR") \
  || { echo "BENCH ABLATION FAILED: the ablation card could not be written"; exit 1; }
echo "ablation card: $RESULT"
cat "${RESULT%.json}.md"
exit 0
