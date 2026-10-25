#!/data/data/com.termux/files/usr/bin/bash
# Apply the fork's delta to a freshly fetched upstream tree.
#
# Usage: apply-patches.sh <build-tree>
#
# Patches are plain `git diff` output applied with `git apply --3way`, so an
# upstream edit landing near one of our hunks falls back to a three-way merge
# instead of failing outright. A conflict aborts the release rather than
# producing a half-patched build. Regenerate with refresh-patches.sh.

set -euo pipefail

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

TREE="${1:?usage: apply-patches.sh <build-tree>}"
[ -d "$TREE" ] || die "no such build tree: $TREE"

cd "$TREE"

shopt -s nullglob
PATCHES=("$WORKSPACE"/termux/patches/*.patch)
shopt -u nullglob

if [ "${#PATCHES[@]}" -eq 0 ]; then
  echo "→ no patches in termux/patches, building unpatched upstream"
else
  for patch in "${PATCHES[@]}"; do
    name="$(basename "$patch")"
    echo "→ applying $name"
    git apply --3way "$patch" \
      || die "$name did not apply against this upstream ref;
       refresh it with termux/ci/refresh-patches.sh or drop it."
  done
  echo "→ applied ${#PATCHES[@]} patch(es)"
fi

# Config-driven cleanups. The default for rust drops rust-toolchain.toml, which
# is a rustup-only pin: Termux supplies its own rustc and rustup publishes no
# aarch64-linux-android *host* toolchain, so the file can only misdirect a
# toolchain manager. Set `removeFiles` in pipeline.json to override.
while IFS= read -r remove; do
  [ -n "$remove" ] || continue
  if [ -e "$remove" ]; then
    echo "→ removing $remove (removeFiles)"
    rm -rf "$remove"
  fi
done < <(cfg getlist removeFiles)

echo "→ tree is $(git rev-parse --short HEAD) + delta"
