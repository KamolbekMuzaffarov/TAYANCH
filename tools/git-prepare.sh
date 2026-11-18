#!/bin/bash
# Creates the local repository with the comment-stripping filter and makes the first commit.
set -e
cd "$(dirname "$0")/.."
if [ ! -d .git ]; then git init -q -b main; fi
git config filter.nocomments.clean 'python3 tools/gitfilter.py %f'
git config filter.nocomments.smudge cat
git config filter.nocomments.required true
git add -A
echo "files staged: $(git diff --cached --name-only | wc -l)"
echo "forbidden check:"; git diff --cached --name-only | grep -iE '(^|/)\.env|\.md$|\.db$|credentials|node_modules/|\.venv/|data/raw/|_backup/|^logs/' || echo "  none"
git commit -q -m "Tayanch: perinatal xavf monitoringi MVP" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" || true
git log --oneline -1
echo PREPARE_DONE
