#!/usr/bin/env bash
# Commits Rattle's persistent state (db, knowledge, dashboard) and pushes with retries.
# Usage: bash scripts/commit_state.sh "commit message"
set -uo pipefail

MSG="${1:-chore: update rattle state [skip ci]}"

git config --global user.name "github-actions[bot]"
git config --global user.email "github-actions[bot]@users.noreply.github.com"

for f in rattle.db rattle_knowledge.json index.html last_image.png last_voice.mp3; do
  [ -f "$f" ] && git add "$f"
done

if git diff --staged --quiet; then
  echo "No state changes to commit."
  exit 0
fi

git commit -m "$MSG"

for attempt in 1 2 3 4; do
  if git pull --rebase -X theirs origin "${GITHUB_REF_NAME:-main}" && git push; then
    echo "State pushed (attempt $attempt)."
    exit 0
  fi
  echo "Push failed (attempt $attempt), retrying..."
  git rebase --abort 2>/dev/null || true
  sleep $((attempt * 5))
done

echo "::error::Could not push state after 4 attempts."
exit 1
