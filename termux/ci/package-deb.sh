#!/usr/bin/env bash
# Package the built binaries as a Termux .deb and emit SHA256SUMS.
#
# Usage: package-deb.sh <in-dir> <out-dir>
#
#   <in-dir>   directory holding bin/, BUILD_INFO and upstream-commit.txt,
#              as produced by build-in-container.sh
#   <out-dir>  where to write the .deb
#
# Run on the runner (Ubuntu), not in the Termux container: dpkg-deb on Ubuntu
# defaults to zstd compression, which some Termux bootstraps cannot unpack, so
# compression is pinned to xz explicitly.
#
# Env inputs:
#   WORKSPACE          optional repo-root override (default: this checkout)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$HERE/lib.sh"

IN_DIR="${1:?usage: package-deb.sh <in-dir> <out-dir>}"
OUT_DIR="${2:?usage: package-deb.sh <in-dir> <out-dir>}"

die() { echo "error: $*" >&2; exit 1; }

PROJECT="$(cfg_get project)"
VERSION="$(cfg_get version)"
VERSION="${VERSION#v}"
[ -n "$VERSION" ] || die "no version resolved (set PIPELINE_VERSION or pipeline.json version)"

DESCRIPTION="$(cfg_get description)"
MAINTAINER="$(cfg_get maintainer)"
HOMEPAGE="$(cfg_get homepage)"
LICENSE="$(cfg_get license)"
UPSTREAM="$(cfg_get upstream)"

[ -d "$IN_DIR/bin" ] || die "no bin/ directory in $IN_DIR (did the build run?)"
DEB="${PROJECT}_${VERSION}_aarch64.deb"

mkdir -p "$OUT_DIR"
ROOT="$(mktemp -d)"
trap 'rm -rf "$ROOT"' EXIT

PREFIX_REL="data/data/com.termux/files/usr"
DATA="$ROOT/$PREFIX_REL/bin"
mkdir -p "$ROOT/DEBIAN" "$DATA"
chmod 0755 "$ROOT"
# mktemp -d makes 0700; dpkg-deb rejects a control dir outside 0755-0775.
chmod 0755 "$ROOT/DEBIAN"

shopt -s nullglob
BINARIES=("$IN_DIR"/bin/*)
shopt -u nullglob
[ "${#BINARIES[@]}" -gt 0 ] || die "no binaries staged in $IN_DIR/bin"
for binary in "${BINARIES[@]}"; do
  install -Dm0755 "$binary" "$DATA/$(basename "$binary")"
done

# Optional extra files (shared libs, man pages) copied verbatim into $PREFIX.
# Each entry is "<source>:<destination-relative-to-PREFIX>".
mapfile -t EXTRA < <(cfg getlist extraDebFiles)
for entry in "${EXTRA[@]}"; do
  source_path="${entry%%:*}"
  destination="${entry#*:}"
  case "$destination" in
    /*) die "extraDebFiles destination must be relative to PREFIX: $destination" ;;
  esac
  install -Dm0644 "$IN_DIR/$source_path" "$ROOT/$PREFIX_REL/$destination"
done

# install -D creates parent directories under the current umask, which leaves
# them 0700, and dpkg then extracts ./data/... as drwx------. $PREFIX/bin
# already exists in Termux so this has never bitten in practice, but a package
# introducing a NEW directory under $PREFIX would install it unsearchable.
find "$ROOT/data" -type d -exec chmod 0755 {} +

# Installed-Size is in KiB, per dpkg's convention.
INSTALLED_SIZE="$(du -ks "$ROOT/data" | cut -f1)"

{
  echo "Package: $PROJECT"
  echo "Version: $VERSION"
  echo "Architecture: aarch64"
  echo "Maintainer: $MAINTAINER"
  echo "Installed-Size: $INSTALLED_SIZE"
  echo "Description: $DESCRIPTION"
  # Every continuation line must begin with a space or dpkg rejects the package.
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    echo " $line"
  done < <(cfg getlist debDescriptionExtra)
  # Debian has no standard License field, and Section means something else
  # (utils, net, ...), so the license rides in the description rather than
  # being mislabelled as a Section.
  [ -z "$LICENSE" ] || echo " License: $LICENSE"
  echo " Upstream: $UPSTREAM"
  [ -z "$HOMEPAGE" ] || echo " Homepage: $HOMEPAGE"
  echo " Built for Termux on aarch64-linux-android by a GitHub Actions pipeline."
} > "$ROOT/DEBIAN/control"

echo "→ building $DEB"
# -Zxz pins xz: Ubuntu's dpkg-deb defaults to zstd, and a zstd control.tar
# fails on Termux bootstraps whose dpkg was built without libzstd.
dpkg-deb --root-owner-group -Zxz --build "$ROOT" "$OUT_DIR/$DEB"

cp "$IN_DIR/BUILD_INFO" "$OUT_DIR/BUILD_INFO" 2>/dev/null || true

( cd "$OUT_DIR" && sha256sum "$DEB" > SHA256SUMS )
( cd "$OUT_DIR" && sha256sum -c SHA256SUMS )

ls -la "$OUT_DIR"
echo "→ packaged $DEB"
