#!/data/data/com.termux/files/usr/bin/bash
# Maintainer tool: regenerate termux/patches/*.patch from a modified build tree.
#
# Usage: refresh-patches.sh <build-tree> [output-dir]
#
#   <build-tree>   a tree produced by prepare-build-tree.sh that you have since
#                  edited in place
#   [output-dir]   where to write the patches (default: termux/patches)
#
# Produces plain `git diff` output numbered from 0001, which is exactly what
# apply-patches.sh feeds to `git apply --3way`. Untracked files are included as
# new-file diffs; remember to `git add -N` (or `git add`) new files in the build
# tree first, otherwise they are silently left out.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$HERE/lib.sh"

TREE="${1:?usage: refresh-patches.sh <build-tree> [output-dir]}"
OUT_DIR="${2:-$WORKSPACE/termux/patches}"

[ -d "$TREE/.git" ] || die "$TREE is not a git tree; run prepare-build-tree.sh first"

cd "$TREE"

# Fail loudly on leftovers rather than baking them into a patch.
if [ -n "$(git status --porcelain --ignored=no | grep '^??' || true)" ]; then
  echo "warning: untracked files present. `git add -N .` to include them:" >&2
  git status --porcelain | grep '^??' >&2
fi

mkdir -p "$OUT_DIR"
rm -f "$OUT_DIR"/*.patch

BASE="$(git rev-parse HEAD)"
DIFF="$(git diff --binary HEAD)"
if [ -z "$DIFF" ]; then
  echo "→ no changes in $TREE; nothing to refresh"
  exit 0
fi

PATCH="$OUT_DIR/0001-local-changes.patch"
printf '%s\n' "$DIFF" > "$PATCH"

echo "→ wrote $PATCH ($(wc -c < "$PATCH") bytes, base ${BASE:0:12})"
echo "  apply-patches.sh applies termux/patches/*.patch in name order;"
echo "  rename to 0002-..., 0003-... as the delta grows."
