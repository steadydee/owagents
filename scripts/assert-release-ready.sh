#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

branch="$(git branch --show-current)"
if [ "$branch" != "main" ]; then
  echo "Deployment blocked: current branch is '$branch'; live deployments require main." >&2
  exit 1
fi

if [ -n "$(git status --porcelain)" ]; then
  echo "Deployment blocked: the main worktree has uncommitted or untracked files." >&2
  git status --short >&2
  exit 1
fi

git fetch --quiet origin main

local_sha="$(git rev-parse HEAD)"
remote_sha="$(git rev-parse origin/main)"
if [ "$local_sha" != "$remote_sha" ]; then
  echo "Deployment blocked: local main does not exactly match origin/main." >&2
  echo "Local:  $local_sha" >&2
  echo "Remote: $remote_sha" >&2
  exit 1
fi

"$ROOT/scripts/check-no-secrets.sh"
echo "Release source verified: main@$local_sha"
