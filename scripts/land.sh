#!/usr/bin/env bash
# Land a branch on main locally, after the checks pass on the exact commit main gets.
#
#   scripts/land.sh <branch> [--push]   merge main and the branch, check, fast-forward main
#   scripts/land.sh --push              mirror main and refs/notes/verification to the remote
#   scripts/land.sh --verify <sha>      re-run the checks on <sha> and compare with its record
#
# Landing (issue #728): fetch main from the remote, merge it and the branch in a
# clean worktree (a merge commit, never a rebase; main is the first parent, so
# `git log --first-parent main` lists the landings as GitHub's merge button
# does), run the checks on that merge commit, and only when they pass write a
# verification record and fast-forward local main to it. A failure leaves main
# where it was and names the failing steps. One landing at a time (a lock under
# the git directory); when main moved while the checks ran, the merge is made
# again and checked again.
#
# The record is .git/fabrique/verifications/<sha>.json and the note of <sha>
# under refs/notes/verification. It is a CLAIM by the machine that ran the
# checks (docs/POSITIONING.md: an agent's report of its own run is a claim, not
# an observation): it names that host and account, and nothing signs it.
# `--verify` on another machine, or CI after the push, is what confirms it.
#
# Protected paths: a change that touches one (the list in
# backend/app/services/contracts/protected_paths.py with the evidence's tests
# from docs/architecture/contexts.json, both read from main, as the
# protected-paths workflow reads them from the base) is refused unless
# LAND_OWNER=1. That variable is a GUARD AGAINST MISTAKES, NOT A SECURITY
# BOUNDARY: anyone who can run this script can set it, or edit the script.
# The boundary is who may push main to the remote (docs/design/LOCAL_LANDING.md).
#
# Environment:
#   LAND_CHECK_CMD      the checks, a shell command run at the worktree's root
#                       (default: scripts/check.sh full)
#   LAND_OWNER=1        the owner lands a change to a protected path
#   LAND_MAIN           the trunk (default main)
#   LAND_REMOTE         the mirror (default origin; no such remote: local only)
#   LAND_OFFLINE=1      land against local main when the remote cannot be fetched
#   LAND_WORKTREE       the checking worktree (default <checkout>.land-worktree,
#                       beside the repository: never under .git, where Vite
#                       refuses to serve files and the frontend tests fail)
#   LAND_PYTHON         the interpreter that reads the protected list (default python3)
#   LAND_MAX_ATTEMPTS   merges tried while main keeps moving (default 3)
#
# Exit status: 0 landed, pushed or verified; 1 the checks failed, the merge
# conflicted or a verification differs; 2 usage or environment; 3 refused
# (protected path, main diverged from the remote, nothing to land, no record);
# 4 another landing holds the lock.
set -euo pipefail

usage() {
  sed -n '2,6p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  echo "See the top of scripts/land.sh for the record, the guard and its variables."
}

die() {  # status, message
  echo "land: $2" >&2
  exit "$1"
}

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
REPO="$(git rev-parse --show-toplevel 2>/dev/null)" || die 2 "not inside a git repository"
COMMON="$(cd "$REPO" && cd "$(git rev-parse --git-common-dir)" && pwd)"
STATE="$COMMON/fabrique"
RECORDS="$STATE/verifications"
LOGS="$STATE/logs"
LOCK="$STATE/land.lock"
MAIN="${LAND_MAIN:-main}"
REMOTE="${LAND_REMOTE:-origin}"
CHECK_CMD="${LAND_CHECK_CMD:-scripts/check.sh full}"
# Beside the main checkout, never under .git: Vite denies **/.git/** by
# default, so vitest in a worktree there loaded no setup file and every
# frontend suite failed (issue #766).
WORKTREE="${LAND_WORKTREE:-$(dirname "$COMMON").land-worktree}"
PY="${LAND_PYTHON:-python3}"
MAX_ATTEMPTS="${LAND_MAX_ATTEMPTS:-3}"
NOTES_REF="refs/notes/verification"
PROTECTED_MODULE="backend/app/services/contracts/protected_paths.py"
TEST_MAP="docs/architecture/contexts.json"
cd "$REPO"

LOCKED=0
PROTECTED=""
TMP="$(mktemp -d)"
cleanup() {
  if [[ $LOCKED == 1 ]]; then rm -rf "$LOCK"; fi
  rm -rf "$TMP"
}
trap cleanup EXIT

now() { if [[ -n "${EPOCHREALTIME:-}" ]]; then echo "$EPOCHREALTIME"; else date +%s; fi; }
since() { awk -v a="$1" -v b="$(now)" 'BEGIN { printf "%.1f", b - a }'; }
utc() { date -u +%Y-%m-%dT%H:%M:%SZ; }
host_name() { hostname 2>/dev/null || uname -n; }
sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
  else shasum -a 256 "$1" | cut -d' ' -f1; fi
}
has_remote() { git remote get-url "$REMOTE" >/dev/null 2>&1; }
commit_of() { git rev-parse --verify --quiet "$1^{commit}"; }

# ── The lock ─────────────────────────────────────────────────────────────────

acquire_lock() {
  mkdir -p "$STATE" "$RECORDS" "$LOGS"
  local pid="" host=""
  if ! mkdir "$LOCK" 2>/dev/null; then
    read -r pid host 2>/dev/null < "$LOCK/owner" || true
    # A landing on this host whose process is gone left the lock behind.
    if [[ -n "$pid" && "$host" == "$(host_name)" ]] && ! kill -0 "$pid" 2>/dev/null; then
      echo "land: removing the lock of landing $pid, which is no longer running" >&2
      rm -rf "$LOCK"
    fi
    mkdir "$LOCK" 2>/dev/null \
      || die 4 "another landing holds $LOCK (${pid:-unknown pid} on ${host:-unknown host}); wait for it"
  fi
  LOCKED=1
  echo "$$ $(host_name)" > "$LOCK/owner"
}

# ── main and the branch ──────────────────────────────────────────────────────

fetch_main() {
  has_remote || return 0
  if ! git fetch --quiet "$REMOTE" "+refs/heads/$MAIN:refs/remotes/$REMOTE/$MAIN"; then
    [[ "${LAND_OFFLINE:-0}" == 1 ]] \
      || die 2 "cannot fetch $MAIN from $REMOTE; LAND_OFFLINE=1 lands against local $MAIN only"
    echo "land: $REMOTE unreachable, landing against local $MAIN (LAND_OFFLINE=1)" >&2
  fi
}

# The commit the branch is merged with: local main, or the remote's when local
# main is behind it. Two lines of history that diverged are refused.
resolve_base() {
  local local_main remote_main=""
  local_main="$(commit_of "refs/heads/$MAIN")" || die 2 "there is no local branch $MAIN"
  if has_remote; then remote_main="$(commit_of "refs/remotes/$REMOTE/$MAIN")" || remote_main=""; fi
  if [[ -z "$remote_main" || "$remote_main" == "$local_main" ]]; then echo "$local_main"
  elif git merge-base --is-ancestor "$local_main" "$remote_main"; then echo "$remote_main"
  elif git merge-base --is-ancestor "$remote_main" "$local_main"; then echo "$local_main"
  else die 3 "local $MAIN and $REMOTE/$MAIN have diverged; reconcile them by hand (merge, never force)"
  fi
}

resolve_branch() {  # name -> commit, fetching it from the remote when it is not local
  local sha
  if sha="$(commit_of "refs/heads/$1")"; then echo "$sha"; return 0; fi
  if has_remote && git fetch --quiet "$REMOTE" "+refs/heads/$1:refs/remotes/$REMOTE/$1" 2>/dev/null \
      && sha="$(commit_of "refs/remotes/$REMOTE/$1")"; then
    echo "$sha"; return 0
  fi
  if sha="$(commit_of "$1")"; then echo "$sha"; return 0; fi
  die 2 "no branch or commit named $1"
}

# The worktree that has main checked out, if one has.
worktree_with_main() {
  local line path=""
  while IFS= read -r line; do
    case "$line" in
      "worktree "*) path="${line#worktree }" ;;
      "branch refs/heads/$MAIN") echo "$path"; return 0 ;;
    esac
  done < <(git worktree list --porcelain)
}

# ── Protected paths ──────────────────────────────────────────────────────────

# Prints "path<TAB>pattern" for each protected path the landing changes: the
# branch's own diff and what main actually receives. The list is main's.
protected_matches() {  # base, branch commit, merge commit
  local dir="$TMP/protected"
  mkdir -p "$dir"
  git show "$1:$PROTECTED_MODULE" > "$dir/protected_paths.py" 2>/dev/null || return 2
  git show "$1:$TEST_MAP" > "$dir/contexts.json" 2>/dev/null || return 2
  { git diff --no-renames --name-only -z "$1...$2"; git diff --no-renames --name-only -z "$1" "$3"; } \
    > "$dir/changed" || return 2
  "$PY" -I - "$dir/protected_paths.py" "$dir/contexts.json" "$dir/changed" <<'PY' || return 2
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("protected_paths_main", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with open(sys.argv[2], encoding="utf-8") as handle:
    tests = json.load(handle)["tests"]["files"]
with open(sys.argv[3], encoding="utf-8", newline="") as handle:
    paths = [p for p in handle.read().split("\0") if p]
for match in module.matches(paths, module.protected_patterns(tests)):
    print(f"{match.path}\t{match.pattern}")
PY
}

indent() { sed $'s/\t/  /; s/^/  /' <<< "$1"; }

guard_protected() {  # base, branch commit, merge commit; sets PROTECTED
  local rc=0
  PROTECTED="$(protected_matches "$1" "$2" "$3")" || rc=$?
  if [[ $rc -ne 0 ]]; then
    # A list that cannot be read refuses, as the protected-paths check does.
    [[ "${LAND_OWNER:-0}" == 1 ]] || die 3 "cannot read the protected list from $MAIN; refused"
    PROTECTED="(the protected list could not be read)"
  fi
  if [[ -n "$PROTECTED" ]]; then
    if [[ "${LAND_OWNER:-0}" != 1 ]]; then
      echo "land: this change touches protected paths (path, pattern):" >&2
      indent "$PROTECTED" >&2
      die 3 "refused: a protected change is landed by the owner (LAND_OWNER=1, a guard, not a security boundary)"
    fi
    echo "land: LAND_OWNER=1, landing a change to protected paths:" >&2
    indent "$PROTECTED" >&2
  fi
}

# ── The worktree, the merge and the checks ───────────────────────────────────

prepare_worktree() {  # commit
  if [[ -e "$WORKTREE/.git" ]]; then
    git -C "$WORKTREE" merge --abort >/dev/null 2>&1 || true
    git -C "$WORKTREE" checkout --quiet --detach --force "$1"
    git -C "$WORKTREE" reset --quiet --hard "$1"
  else
    [[ ! -e "$WORKTREE" ]] || die 2 "$WORKTREE exists and is not a worktree of this repository"
    git worktree prune
    git worktree add --quiet --detach "$WORKTREE" "$1"
  fi
  # Clean: nothing untracked or ignored survives but frontend/node_modules,
  # which check.sh reinstalls when its package-lock.json hash differs.
  git -C "$WORKTREE" clean -q -ffdx -e /frontend/node_modules/
}

make_merge() {  # base, branch commit, branch name; prints the merge commit
  prepare_worktree "$1" >&2
  if ! git -C "$WORKTREE" -c core.hooksPath=/dev/null merge --quiet --no-ff --no-edit \
      -m "Land $3 into $MAIN" -m "Merged by scripts/land.sh: $MAIN ${1:0:12} and $3 ${2:0:12}." \
      "$2" >&2; then
    git -C "$WORKTREE" merge --abort >/dev/null 2>&1 || true
    die 1 "$3 does not merge cleanly with $MAIN; merge $MAIN into it, resolve, and land again. $MAIN is untouched"
  fi
  git -C "$WORKTREE" rev-parse HEAD
}

run_checks() {  # commit, base for check.sh; sets CHECK_RC STARTED_AT FINISHED_AT DURATION LOG REPORT
  LOG="$LOGS/$1.$(date -u +%Y%m%dT%H%M%SZ).log"
  REPORT="$TMP/report.tsv"
  : > "$REPORT"
  STARTED_AT="$(utc)"
  local started
  started="$(now)"
  echo "land: running '$CHECK_CMD' on ${1:0:12} in $WORKTREE" >&2
  set +e
  (cd "$WORKTREE" && CHECK_REPORT="$REPORT" CHECK_BASE="$2" bash -c "$CHECK_CMD") 2>&1 | tee "$LOG"
  CHECK_RC=${PIPESTATUS[0]}
  set -e
  FINISHED_AT="$(utc)"
  DURATION="$(since "$started")"
}

failing_steps() {
  awk -F'\t' '$2 != "ok" && $2 != "dry-run" && $2 !~ /^skipped/ { print "  " $1 ": " $2 }' "$REPORT"
}

tool_versions() {
  local tool version
  for tool in git bash "$PY" node npm poetry ruff; do
    command -v "$tool" >/dev/null 2>&1 || continue
    version="$("$tool" --version 2>&1 | head -n 1)" || true
    printf '%s\t%s\n' "$(basename "$tool")" "${version:-unknown}"
  done
}

# ── The record ───────────────────────────────────────────────────────────────

write_record() {  # mode, commit, output file, base, branch name, branch commit
  tool_versions > "$TMP/tools.tsv"
  R_MODE="$1" R_COMMIT="$2" R_BASE="$4" R_BRANCH="$5" R_BRANCH_HEAD="$6" \
  R_TREE="$(git rev-parse "$2^{tree}")" R_PARENTS="$(git log -1 --format=%P "$2")" \
  R_COMMAND="$CHECK_CMD" R_RC="$CHECK_RC" R_STARTED="$STARTED_AT" R_FINISHED="$FINISHED_AT" \
  R_DURATION="$DURATION" R_REPORT="$REPORT" R_TOOLS="$TMP/tools.tsv" \
  R_LOG="${LOG#"$COMMON"/}" R_LOG_SHA="$(sha256 "$LOG")" \
  R_HOST="$(host_name)" R_SYSTEM="$(uname -srm)" R_USER="$(id -un 2>/dev/null || echo unknown)" \
  R_GIT_USER="$(git var GIT_COMMITTER_IDENT 2>/dev/null | sed 's/ <.*//' || true)" R_SCRIPT_BLOB="$(git hash-object "$SCRIPT")" \
  R_PROTECTED="${PROTECTED:-}" R_OWNER="${LAND_OWNER:-0}" \
  "$PY" -I - "$3" <<'PY'
import json, os, sys
e = os.environ
def rows(path):
    with open(path, encoding="utf-8") as handle:
        return [line.rstrip("\n").split("\t") for line in handle if line.strip()]
steps = [{"name": r[0], "status": r[1], "seconds": float(r[2])} for r in rows(e["R_REPORT"]) if len(r) == 3]
protected = [dict(zip(("path", "pattern"), line.split("\t"))) for line in e["R_PROTECTED"].splitlines() if line]
record = {
    "schema": "la-fabrique/verification-record/v1",
    "nature": "claim",
    "statement": (
        "The host and account below report that they ran the command on this commit with this "
        "result. It is a claim by that machine, not an independent observation, and it is not "
        "signed: confirm it with scripts/land.sh --verify on another machine, or with CI."
    ),
    "mode": e["R_MODE"],
    "commit": e["R_COMMIT"],
    "tree": e["R_TREE"],
    "parents": e["R_PARENTS"].split(),
    "main_before": e["R_BASE"] or None,
    "branch": {"name": e["R_BRANCH"], "head": e["R_BRANCH_HEAD"]} if e["R_BRANCH"] else None,
    "command": e["R_COMMAND"],
    "status": "passed" if e["R_RC"] == "0" else "failed",
    "exit_code": int(e["R_RC"]),
    "started_at": e["R_STARTED"],
    "finished_at": e["R_FINISHED"],
    "duration_seconds": float(e["R_DURATION"]),
    "steps": steps or None,
    "log": {"path": e["R_LOG"], "sha256": e["R_LOG_SHA"]},
    "host": {"name": e["R_HOST"], "system": e["R_SYSTEM"]},
    "account": {"user": e["R_USER"], "git_user_name": e["R_GIT_USER"] or None},
    "tools": {r[0]: r[1] for r in rows(e["R_TOOLS"]) if len(r) == 2},
    "script_blob": e["R_SCRIPT_BLOB"],
    "protected_paths": protected,
    "owner_override": e["R_OWNER"] == "1" and bool(protected),
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(record, handle, indent=2, sort_keys=True)
    handle.write("\n")
PY
}

# ── Commands ─────────────────────────────────────────────────────────────────

land() {  # branch name
  local name="$1" branch base local_main merge attempt=1 failing
  [[ "$name" != "$MAIN" ]] || die 2 "$MAIN is the trunk; name the branch to land on it"
  acquire_lock
  fetch_main
  branch="$(resolve_branch "$name")"
  while :; do
    base="$(resolve_base)"
    local_main="$(commit_of "refs/heads/$MAIN")"
    if git merge-base --is-ancestor "$branch" "$base"; then
      die 3 "$name (${branch:0:12}) is already in $MAIN; nothing to land"
    fi
    merge="$(make_merge "$base" "$branch" "$name")"
    guard_protected "$base" "$branch" "$merge"
    run_checks "$merge" "$base"
    if [[ $CHECK_RC -ne 0 ]]; then
      failing="$(failing_steps)"
      echo "land: the checks failed on ${merge:0:12} (exit $CHECK_RC); log: $LOG" >&2
      if [[ -n "$failing" ]]; then echo "land: failing steps:" >&2; echo "$failing" >&2; fi
      die 1 "$MAIN is untouched"
    fi
    fetch_main
    if [[ "$(resolve_base)" == "$base" && "$(commit_of "refs/heads/$MAIN")" == "$local_main" ]]; then
      break
    fi
    attempt=$((attempt + 1))
    [[ $attempt -le $MAX_ATTEMPTS ]] || die 1 "$MAIN kept moving during $MAX_ATTEMPTS attempts; land again"
    echo "land: $MAIN moved while the checks ran; merging again (attempt $attempt)" >&2
  done

  write_record land "$merge" "$RECORDS/$merge.json" "$base" "$name" "$branch"
  git notes --ref=verification add --force --file "$RECORDS/$merge.json" "$merge"

  local checkout
  checkout="$(worktree_with_main)"
  if [[ -n "$checkout" ]]; then
    [[ "$(commit_of "refs/heads/$MAIN")" == "$local_main" ]] || die 1 "$MAIN moved; land again"
    git -C "$checkout" merge --ff-only --quiet "$merge" \
      || die 1 "the record of ${merge:0:12} is written, but $checkout could not fast-forward (local changes in the way?); run: git -C '$checkout' merge --ff-only $merge"
  else
    git update-ref -m "land: $name" "refs/heads/$MAIN" "$merge" "$local_main"
  fi
  echo "land: $MAIN is now ${merge:0:12} ($name); record: $RECORDS/$merge.json"
}

push_mirror() {
  has_remote || die 2 "there is no remote named $REMOTE to mirror to"
  local tip refs
  tip="$(commit_of "refs/heads/$MAIN")" || die 2 "there is no local branch $MAIN"
  if [[ ! -f "$RECORDS/$tip.json" ]] && ! git notes --ref=verification show "$tip" >/dev/null 2>&1; then
    die 3 "$MAIN (${tip:0:12}) has no verification record; land changes with scripts/land.sh <branch>"
  fi
  refs=("refs/heads/$MAIN:refs/heads/$MAIN")
  if git rev-parse --verify --quiet "$NOTES_REF" >/dev/null; then refs+=("$NOTES_REF:$NOTES_REF"); fi
  # No force: a remote main or notes ref that moved on is a conflict for a person.
  git push --atomic --quiet "$REMOTE" "${refs[@]}" \
    || die 1 "$REMOTE refused the push; fetch, reconcile (git notes --ref=verification merge for the notes) and push again"
  echo "land: pushed $MAIN (${tip:0:12}) and $NOTES_REF to $REMOTE"
}

verify() {  # commit
  local sha claim mine rc=0
  sha="$(commit_of "$1")" || die 2 "no commit named $1"
  claim="$TMP/claim.json"
  if has_remote; then git fetch --quiet "$REMOTE" "$NOTES_REF:$NOTES_REF" 2>/dev/null || true; fi
  if [[ -f "$RECORDS/$sha.json" ]]; then cp "$RECORDS/$sha.json" "$claim"
  elif ! git notes --ref=verification show "$sha" > "$claim" 2>/dev/null; then
    die 3 "${sha:0:12} has no verification record to compare with (no file, no note in $NOTES_REF)"
  fi
  acquire_lock
  prepare_worktree "$sha" >&2
  PROTECTED=""
  run_checks "$sha" "$sha^1"
  mine="$RECORDS/$sha.verify.$(date -u +%Y%m%dT%H%M%SZ).json"
  write_record verify "$sha" "$mine" "" "" ""
  "$PY" -I - "$claim" "$mine" <<'PY' || rc=$?
import json, sys
with open(sys.argv[1], encoding="utf-8") as h:
    claim = json.load(h)
with open(sys.argv[2], encoding="utf-8") as h:
    mine = json.load(h)
def steps(r):
    return [(s["name"], s["status"]) for s in (r.get("steps") or [])]
differences = []
if claim.get("commit") != mine["commit"]:
    differences.append(f"commit: claimed {claim.get('commit')}, checked {mine['commit']}")
if claim.get("status") != mine["status"]:
    differences.append(f"status: claimed {claim.get('status')}, observed here {mine['status']}")
if steps(claim) != steps(mine):
    differences.append(f"steps: claimed {steps(claim)}, observed here {steps(mine)}")
host = (claim.get("host") or {}).get("name")
print(f"land: the record of {mine['commit'][:12]} claimed by {host}: {claim.get('status')}")
print(f"land: re-run on {mine['host']['name']}: {mine['status']} in {mine['duration_seconds']}s")
for line in differences:
    print(f"land: differs, {line}")
sys.exit(1 if differences or mine["status"] != "passed" else 0)
PY
  echo "land: this run's record: $mine"
  [[ $rc -eq 0 ]] || die 1 "the verification of ${sha:0:12} does not reproduce its record"
  echo "land: the record of ${sha:0:12} is reproduced"
}

# ── Arguments ────────────────────────────────────────────────────────────────

BRANCH="" PUSH=0 VERIFY=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --push) PUSH=1 ;;
    --verify) [[ $# -ge 2 ]] || { usage >&2; exit 2; }; VERIFY="$2"; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) usage >&2; exit 2 ;;
    *) [[ -z "$BRANCH" ]] || { usage >&2; exit 2; }; BRANCH="$1" ;;
  esac
  shift
done

if [[ -n "$VERIFY" ]]; then
  [[ -z "$BRANCH" && $PUSH == 0 ]] || { usage >&2; exit 2; }
  verify "$VERIFY"
elif [[ -n "$BRANCH" ]]; then
  land "$BRANCH"
  if [[ $PUSH == 1 ]]; then push_mirror; fi
elif [[ $PUSH == 1 ]]; then
  push_mirror
else
  usage >&2
  exit 2
fi
