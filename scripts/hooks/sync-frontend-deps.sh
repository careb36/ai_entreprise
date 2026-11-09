#!/usr/bin/env bash
# Reinstall the frontend's dependencies when a pull, merge or rebase changed
# frontend/package-lock.json, so a dependency someone else added (as
# react-markdown was) is never missing locally.
#
# Run by pre-commit at the post-merge stage (git merge, git pull) and the
# post-rewrite stage (git pull --rebase, git rebase), which git runs after the
# history has moved. Installed with:
#   pre-commit install --hook-type pre-commit --hook-type post-merge --hook-type post-rewrite
#
# It never fails the git command: post hooks cannot undo it anyway, so a
# missing npm or a failed install is only reported.
set -u

lock="frontend/package-lock.json"

# post-rewrite also fires for `git commit --amend`, which does not move
# ORIG_HEAD: comparing against it would rerun npm ci after every amend that
# follows a pull. pre-commit names the rewrite in PRE_COMMIT_REWRITE_COMMAND.
if [ "${PRE_COMMIT_REWRITE_COMMAND:-}" = "amend" ]; then
  exit 0
fi

# ORIG_HEAD is where HEAD was before the merge or rebase.
if ! git rev-parse -q --verify ORIG_HEAD >/dev/null; then
  exit 0
fi
if git diff --quiet ORIG_HEAD HEAD -- "$lock"; then
  exit 0
fi
if [ ! -f "$lock" ]; then
  exit 0
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "sync-frontend-deps: $lock changed but npm is not on PATH; run 'cd frontend && npm ci' yourself." >&2
  exit 0
fi

echo "sync-frontend-deps: $lock changed, running 'npm ci' in frontend/ ..."
if ! (cd frontend && npm ci --no-audit --no-fund); then
  echo "sync-frontend-deps: 'npm ci' failed; run it again in frontend/ before building or testing." >&2
fi
exit 0
