#!/data/data/com.termux/files/usr/bin/bash
# Clone the upstream project at a known-good ref into a build tree.
#
# Usage: fetch-upstream.sh <dest-dir> [ref]
#
# The ref defaults to the one pinned in pipeline.json (`upstreamRef`). Pass a
# branch, tag or commit as $2 to build something other than the pin.
#
# Depth-1 on purpose. A full-history clone of a large repo cost minutes and
# bought nothing: `git apply --3way` can still find pre-image blobs, because
# for a patch generated against this ref they *are* the checked-out tree. Where
# a build genuinely needs tags (a `git describe --tags` in build.rs), clone the
# repo without --depth-1 in your own tree or fetch tags after the clone.

set -euo pipefail

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

DEST="${1:?usage: fetch-upstream.sh <dest-dir> [ref]}"
REF="${2:-}"

UPSTREAM="$(cfg_get upstream)"
PINNED_REF="$(cfg_get upstreamRef)"
PINNED_SHA="$(cfg_get upstreamCommit)"

[ -n "$REF" ] || REF="$PINNED_REF"
[ -n "$REF" ] || die "no ref given and pipeline.json has no upstreamRef"
[ -n "$UPSTREAM" ] || die "pipeline.json has no upstream"

PROJECT="$(cfg_get project)"
echo "→ fetching $PROJECT $REF from $UPSTREAM"

rm -rf "$DEST"

# `git clone -b` accepts a tag or branch but not a raw commit sha, so a sha goes
# through init + fetch instead.
if is_sha "$REF"; then
  git init -q "$DEST"
  git -C "$DEST" remote add origin "$UPSTREAM"
  git -C "$DEST" fetch --depth 1 --quiet origin "$REF"
  git -C "$DEST" checkout --detach --quiet FETCH_HEAD
else
  git clone --depth 1 --branch "$REF" --quiet "$UPSTREAM" "$DEST"
fi

# Record the exact commit so BUILD_INFO can print it and a moved tag is
# detectable rather than silent. An unpinned upstreamCommit simply skips the
# check; the sha is still reported.
RESOLVED="$(git -C "$DEST" rev-parse HEAD)"
echo "→ upstream $REF = $RESOLVED"

if [ -n "$PINNED_SHA" ]; then
  if [ "$REF" = "$PINNED_REF" ] && [ "$RESOLVED" != "$PINNED_SHA" ]; then
    die "$REF resolved to $RESOLVED but pipeline.json pins $PINNED_SHA;
       upstream may have moved the tag. Update upstreamCommit deliberately."
  fi
fi

# Only meaningful inside the container; skipped for local verification runs.
if [ -d /out ] && [ -w /out ]; then
  echo "$RESOLVED" > /out/upstream-commit.txt
fi
