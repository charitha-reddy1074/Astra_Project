#!/usr/bin/env bash
# Prepare the repository for upload to GitHub.
# This script initializes git (if needed), creates an initial commit,
# and uses the GitHub CLI (`gh`) to create a remote repository.

set -euo pipefail

if [ ! -d .git ]; then
  git init
  git add --all
  git commit -m "Initial import — sanitized"
else
  echo "Git repo already initialised"
fi

if command -v gh >/dev/null 2>&1; then
  echo "Creating repo via gh (interactive)"
  gh repo create --public
  git push -u origin main || git push -u origin master || true
else
  echo "GitHub CLI (gh) not found. Create a repo on GitHub and add remote manually."
  echo "Suggested commands:" 
  echo "  git remote add origin git@github.com:<user>/<repo>.git"
  echo "  git push -u origin main"
fi

echo "Done — ensure .env and data/outputs are not committed (see .gitignore)."
