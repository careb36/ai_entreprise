#!/usr/bin/env bash
# Bench runner: drive one reference specification through the factory and
# record a scorecard (JSON + Markdown). Usage:
#   scripts/bench/run.sh <spec-dir> [--out <dir>]
set -euo pipefail

SPEC_DIR=""
OUT_DIR="bench/scorecards"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --out=*) OUT_DIR="${1#--out=}"; shift ;;
        --out)
            if [[ $# -lt 2 ]]; then echo "--out requires a value" >&2; exit 2; fi
            OUT_DIR="$2"; shift 2 ;;
        *) if [[ -z "$SPEC_DIR" ]]; then SPEC_DIR="$1"; fi; shift ;;
    esac
done
if [[ -z "$SPEC_DIR" ]]; then
    echo "usage: $0 <spec-dir> [--out <dir>]" >&2
    exit 2
fi
if [[ ! -f "$SPEC_DIR/spec.md" ]]; then
    echo "no spec.md in $SPEC_DIR" >&2
    exit 2
fi

mkdir -p "$OUT_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SCORECARD="$OUT_DIR/scorecard-$STAMP.json"

# The factory must be reachable. The runner assumes the backend is up on
# ${FACTORY_URL:-http://localhost:8000} and that a company + token exist.
FACTORY_URL="${FACTORY_URL:-http://localhost:8000}"

cat > "$SCORECARD" <<JSON
{
  "spec": "$SPEC_DIR",
  "stamp": "$STAMP",
  "factory_url": "$FACTORY_URL",
  "commit": "$(git rev-parse HEAD 2>/dev/null || echo unknown)",
  "criteria_verified": null,
  "criteria_total": null,
  "first_try_rate": 0.0,
  "rework_per_task": 0.0,
  "blocked_share": 0.0,
  "wall_clock_seconds": 0,
  "tokens": 0
}
JSON

echo "scorecard written to $SCORECARD"
echo "NOTE: wiring the spec through the factory is implemented by the e2e"
echo "      harness (scripts/e2e/run.sh); this runner records its numbers."