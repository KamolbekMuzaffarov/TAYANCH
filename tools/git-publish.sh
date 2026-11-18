#!/bin/bash
# Commits current work and publishes it to a PRIVATE GitHub repository named Tayanch.
set -e
cd "$(dirname "$0")/.."
gh auth status -h github.com 2>&1 | grep -E "Logged in|account" | head -2
git add -A
git commit -q -m "${COMMIT_MSG:-Yangilanish}" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" || true
if git remote get-url origin >/dev/null 2>&1; then
  git push -u origin main
else
  gh repo create Tayanch --private --source . --remote origin --push
fi
echo "VISIBILITY: $(gh repo view --json visibility -q .visibility)"
echo "URL: $(gh repo view --json url -q .url)"
git log --oneline -3
echo PUBLISH_DONE
