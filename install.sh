#!/data/data/com.termux/files/usr/bin/env bash
# Installer for a project packaged by the Termux/Android pipeline.
#
#   curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/install.sh | bash
#
# Reads pipeline.json from the same repo to learn the project name, so renaming
# the project needs no change here.
#
# Env overrides:
#   PIPELINE_REPO    github repo, owner/name (default: the FORK_REPO below)
#   PIPELINE_VERSION exact version or tag, e.g. 1.2.3 or v1.2.3

set -euo pipefail

# >>> EDIT THIS when you adopt the template. <<<
FORK_REPO="OWNER/REPO"

REPO="${PIPELINE_REPO:-$FORK_REPO}"
VERSION="${PIPELINE_VERSION:-}"
BRANCH="main"

red() { printf '\033[0;31m%s\033[0m\n' "$*" >&2; }
grn() { printf '\033[0;32m%s\033[0m\n' "$*"; }
say() { printf '  %s\n' "$*"; }

# --- sanity ------------------------------------------------------------------

if [ -z "${PREFIX:-}" ] || [ ! -d "$PREFIX" ]; then
  red "This installer targets Termux. \$PREFIX is not set - are you running inside Termux?"
  exit 1
fi

if [ "$(uname -m)" != "aarch64" ]; then
  red "Unsupported architecture: $(uname -m) (only aarch64 is built)"
  exit 1
fi

for cmd in curl dpkg python; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    say "Installing missing dependency: $cmd"
    pkg install -y "$cmd" >/dev/null
  fi
done

# --- resolve project name ----------------------------------------------------

CONFIG_URL="https://raw.githubusercontent.com/$REPO/$BRANCH/pipeline.json"
PROJECT="$(curl -fsSL "$CONFIG_URL" 2>/dev/null | python3 -c \
  "import json,sys;print(json.load(sys.stdin)['project'])" 2>/dev/null || true)"
[ -n "$PROJECT" ] || PROJECT="${REPO##*/}"
say "Project: $PROJECT"

# --- resolve version ---------------------------------------------------------

API="https://api.github.com/repos/$REPO"

if [ -n "$VERSION" ]; then
  TAG="v${VERSION#v}"
else
  say "Resolving latest release from $REPO..."
  TAG="$(curl -fsSL "$API/releases/latest" | python3 -c \
    "import json,sys;print(json.load(sys.stdin)['tag_name'])" 2>/dev/null || true)"
  if [ -z "$TAG" ]; then
    # api.github.com may be blocked by the user's network.
    say "API unavailable, trying the web redirect fallback..."
    TAG="$(curl -fsSI -o /dev/null -w '%{redirect_url}' \
      "https://github.com/$REPO/releases/latest" 2>/dev/null \
      | command grep -oE '/tag/v[^/]+$' | command sed 's|^/tag/||' || true)"
  fi
  if [ -z "$TAG" ]; then
    red "Could not resolve the latest release. Set PIPELINE_VERSION explicitly."
    exit 1
  fi
fi

VERSION="${TAG#v}"
DEB="${PROJECT}_${VERSION}_aarch64.deb"
BASE="https://github.com/$REPO/releases/download/$TAG"

grn "Installing $PROJECT $TAG for Termux (aarch64)"

# --- download ----------------------------------------------------------------

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

say "Downloading $DEB"
curl -fL --progress-bar -o "$TMP/$DEB" "$BASE/$DEB"

say "Downloading SHA256SUMS"
curl -fsSL -o "$TMP/SHA256SUMS" "$BASE/SHA256SUMS"

say "Verifying checksum"
( cd "$TMP" && sha256sum -c --ignore-missing SHA256SUMS 2>&1 | command grep -F "$DEB" ) \
  || { red "Checksum failed"; exit 1; }

# --- install -----------------------------------------------------------------

# An older Termux bootstrap ships a dpkg built without libzstd, which then shells
# out to a `zstd` binary that is not in the bootstrap either, and the install dies
# with `member "control.tar" (zstd): No such file or directory`. Current releases
# are xz so this never triggers, but pinning an old PIPELINE_VERSION can still
# land a zstd deb. The ar member names are ASCII within the first ~200 bytes
# (control.tar.* starts at byte 72), so this needs no `ar` - binutils is not in
# the bootstrap. Matched with `case` rather than `head | grep -q`, which can
# report failure under `pipefail` when grep short-circuits and SIGPIPEs head.
# Best-effort: if detection fails, fall through and let dpkg report the problem.
DEB_HEADER="$(head -c 200 "$TMP/$DEB" | tr -d '\0')"
case "$DEB_HEADER" in
  *.tar.zst*)
    if ! command -v zstd >/dev/null 2>&1; then
      say "Archive is zstd-compressed; installing zstd"
      pkg install -y zstd >/dev/null 2>&1 || true
    fi
    ;;
esac

say "Installing via dpkg"
dpkg -i "$TMP/$DEB"

grn "Done. Installed into $PREFIX/bin"
for candidate in "$PREFIX"/bin/*; do
  [ -x "$candidate" ] && say "$(basename "$candidate")"
done
