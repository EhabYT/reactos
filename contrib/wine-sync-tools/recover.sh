#!/bin/sh
# Recover this working tree after a sandbox reset.
#
# What a reset does, observed ten times: /tmp is emptied, untracked files in
# the tree survive, and the branch ref is moved back to the commit the session
# branched from while the working tree keeps its content. So the branch looks
# dirty against a stale HEAD and `git status` shows every synced file as
# modified, but nothing is actually lost - the content is on disk and on
# origin.
#
# The order matters. Check the working tree against origin BEFORE resetting:
# if content had been lost, resetting would hide the fact. Only reset once the
# diff shows nothing but untracked files.
#
# Usage: contrib/wine-sync-tools/recover.sh [branch]
set -e

BRANCH="${1:-arena/01a0d40c-reactos}"
cd "$(git rev-parse --show-toplevel)"

echo "== fetching origin"
git fetch -q origin "$BRANCH:refs/remotes/origin/$BRANCH"
TIP=$(git rev-parse "refs/remotes/origin/$BRANCH")
echo "   origin/$BRANCH is $(git rev-parse --short "$TIP")"

echo "== comparing the working tree with origin before touching anything"
DIFF=$(git diff --name-only "$TIP" || true)
if [ -n "$DIFF" ]; then
    echo "   files differing from origin:"
    echo "$DIFF" | sed 's/^/     /'
    # A file that differs only because it is untracked is not a loss; git
    # reports those as present in the index side. Anything else means the
    # working tree has content origin does not, which must be looked at by
    # hand rather than discarded by the reset below.
    LOST=""
    for f in $DIFF; do
        if [ -e "$f" ] && ! git ls-files --error-unmatch "$f" >/dev/null 2>&1; then
            continue            # untracked but present: fine
        fi
        LOST="$LOST $f"
    done
    if [ -n "$LOST" ]; then
        echo "== STOP: these exist in neither the working tree nor origin. Do not reset." >&2
        echo "  $LOST" >&2
        exit 1
    fi
    echo "   (all of them are untracked files that survived - nothing is lost)"
fi

echo "== resetting the branch to origin/$BRANCH"
git reset --hard -q "$TIP"
git log --oneline -1

echo "== re-cloning the Wine references (they live in /tmp, which a reset empties)"
# Two shallow clones in parallel; wine-10.0 is the merge base used for
# three-way merges and wine-11.18 is the target.
( git clone -q --depth 1 --branch wine-10.0 https://github.com/wine-mirror/wine.git /tmp/wine-10.0 \
    && echo "   wine-10.0  $(git -C /tmp/wine-10.0 rev-parse --short HEAD)" ) &
( git clone -q --depth 1 --branch wine-11.18 https://github.com/wine-mirror/wine.git /tmp/wine-11.18 \
    && echo "   wine-11.18 $(git -C /tmp/wine-11.18 rev-parse --short HEAD)" ) &
wait

echo "== done. Backlog:"
exec python3 contrib/wine-sync-tools/sync.py status
