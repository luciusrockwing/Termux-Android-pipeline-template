#!/data/data/com.termux/files/usr/bin/bash
# Fetch upstream at the pinned ref and apply the delta, producing a ready-to-build
# source tree.
#
# Usage: prepare-build-tree.sh <dest-dir> [ref]

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

DEST="${1:?usage: prepare-build-tree.sh <dest-dir> [ref]}"
REF="${2:-}"

"$HERE/fetch-upstream.sh" "$DEST" "$REF"
"$HERE/apply-patches.sh" "$DEST"
