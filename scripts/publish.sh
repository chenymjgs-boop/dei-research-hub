#!/bin/bash
# Publish reports/site/ to the gh-pages branch of the project's GitHub remote.
#
# Strategy: use a detached `git worktree` so we never disturb the main branch.
#   1. Build the site fresh.
#   2. Create / re-use a worktree at .gh-pages-build pointing at gh-pages.
#   3. Mirror reports/site/ into it.
#   4. Commit + push.
#
# First-time setup is detected automatically:
#   - if no .git directory → init repo, ask user to set remote
#   - if no gh-pages branch on remote → create an orphan branch
#
# Requires: an existing GitHub remote called "origin" (or pass REMOTE=foo).

set -euo pipefail

PROJECT="/Users/marciachen/dei-research-assistant"
cd "$PROJECT"

REMOTE="${REMOTE:-origin}"
SITE_DIR="reports/site"
WORKTREE_DIR=".gh-pages-build"
COMMIT_MSG="${COMMIT_MSG:-publish: site update $(date '+%Y-%m-%d %H:%M')}"

# --- 1. Sanity / first-time setup ---
if [ ! -d ".git" ]; then
    echo "→ git repo not found; running 'git init'"
    git init -b main
fi

if ! git remote get-url "$REMOTE" >/dev/null 2>&1; then
    echo "✗ No git remote called '$REMOTE'."
    echo "  Add one first, e.g.:"
    echo "    git remote add origin git@github.com:<your-username>/<repo>.git"
    echo "    git push -u origin main"
    exit 1
fi

# --- 2. Make sure the site is built ---
echo "→ Building site"
source .venv/bin/activate
python main.py site

# --- 3. Get / create gh-pages branch on the remote ---
if ! git ls-remote --exit-code --heads "$REMOTE" gh-pages >/dev/null 2>&1; then
    echo "→ gh-pages branch not on remote; creating orphan branch"
    # Create an orphan branch with a placeholder commit, then push
    git worktree add --orphan -b gh-pages "$WORKTREE_DIR" >/dev/null 2>&1 || true
    (
        cd "$WORKTREE_DIR"
        # Clear anything that may have come from main HEAD
        git rm -rf . >/dev/null 2>&1 || true
        echo "DEI Research Hub" > README.md
        git add README.md
        git -c user.name="DEI Bot" -c user.email="bot@local" commit -m "init gh-pages"
        git push -u "$REMOTE" gh-pages
    )
    git worktree remove "$WORKTREE_DIR" --force
fi

# --- 4. Worktree-based publish ---
git fetch "$REMOTE" gh-pages

# Clean up stale worktree if a previous run was interrupted
if [ -d "$WORKTREE_DIR" ]; then
    git worktree remove "$WORKTREE_DIR" --force 2>/dev/null || rm -rf "$WORKTREE_DIR"
fi

git worktree add "$WORKTREE_DIR" gh-pages
(
    cd "$WORKTREE_DIR"
    # Wipe everything except .git so deletions in source propagate
    find . -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {} +
)

echo "→ Mirroring $SITE_DIR → $WORKTREE_DIR"
# rsync -a, but exclude any .git that may have crept in
rsync -a --exclude '.git' "$SITE_DIR/" "$WORKTREE_DIR/"

# Add a .nojekyll so GitHub Pages serves the files as-is (no Jekyll processing)
touch "$WORKTREE_DIR/.nojekyll"

(
    cd "$WORKTREE_DIR"
    git add -A
    if git diff --cached --quiet; then
        echo "→ No changes to publish"
    else
        git -c user.name="DEI Bot" -c user.email="bot@local" commit -m "$COMMIT_MSG"
        git push "$REMOTE" gh-pages
        echo "✓ Pushed to $REMOTE gh-pages"
    fi
)

git worktree remove "$WORKTREE_DIR" --force
echo "✓ Done. Public URL (after GitHub finishes building, usually ~30s):"
echo "    Check repo Settings → Pages, source should be 'gh-pages branch / root'"
