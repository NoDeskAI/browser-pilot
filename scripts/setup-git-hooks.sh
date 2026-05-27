#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"

git config core.hooksPath scripts/git-hooks
chmod +x scripts/git-hooks/commit-msg

echo "Git hooks enabled: core.hooksPath=scripts/git-hooks"
