#!/data/data/com.termux/files/usr/bin/bash
# Runs INSIDE termux/termux-docker:aarch64. Installs the toolchain that
# pipeline.json asks for, builds the pinned upstream ref with the fork's delta
# applied, smoke-tests the result, and hands the binaries to package-deb.sh.
#
# Mounts expected:
#   /workspace  this repo
#   /out        writable host dir for the artifact handoff
#
# Env inputs (read from /out/build-env.sh, written by build-on-runner.sh, because
# termux-docker's entrypoint strips inherited environment when it switches to
# the `system` user):
#   PIPELINE_VERSION       release version; defaults to pipeline.json
#   PIPELINE_UPSTREAM_REF  build this ref instead of the pinned one
#   PIPELINE_BUILD_JOBS    parallel codegen units (default 4)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$HERE/lib.sh"

if [ -f /out/build-env.sh ]; then
  # shellcheck disable=SC1091
  . /out/build-env.sh
fi

export PIPELINE_UPSTREAM_REF="${PIPELINE_UPSTREAM_REF:-}"
export PIPELINE_VERSION="${PIPELINE_VERSION:-$(cfg_get version)}"
export PIPELINE_VERSION="${PIPELINE_VERSION#v}"

PROJECT="$(cfg_get project)"
LANGUAGE="$(cfg_get language)"

die() { echo "error: $*" >&2; exit 1; }

echo "=== diag ==="
id || true
echo "HOME=$HOME PREFIX=${PREFIX:-unset}"
(touch /out/.write_test && rm -f /out/.write_test && echo "/out: WRITABLE") \
  || die "/out is not writable. termux-docker drops privileges regardless of --user; build-on-runner.sh chmods it 0777."
echo "==========="

# --- toolchain ---------------------------------------------------------------

# Pin the canonical mirror so package versions stay consistent across runs.
echo "deb https://packages.termux.dev/apt/termux-main stable main" \
  > "${PREFIX:-/data/data/com.termux/files/usr}/etc/apt/sources.list"

echo "=== installing toolchain (language=$LANGUAGE) ==="
mapfile -t PACKAGES < <(cfg getlist toolchainPackages)
apt update -y
# shellcheck disable=SC2086
apt install -y "${PACKAGES[@]}"

# An apt install that resolves to "nothing to do" still exits 0, so assert on
# the tools themselves rather than trusting the exit status. This exact check is
# what caught a missing `rust` package that exited 0 and failed 200 lines later
# with a bare `rustc: command not found`.
echo "=== verifying toolchain ==="
mapfile -t TOOLS < <(cfg getlist requiredTools)
for tool in "${TOOLS[@]}"; do
  command -v "$tool" >/dev/null 2>&1 \
    || die "required tool missing after apt install: $tool (see pipeline.json requiredTools)"
done
for tool in "${TOOLS[@]}"; do
  printf '  %-14s %s\n' "$tool" "$("$tool" --version 2>&1 | head -1)"
done

# The container IS aarch64, so its host triple is already aarch64-linux-android:
# no NDK and no cross-linker. rustc is the one tool that states a triple.
[ "$(uname -m)" = "aarch64" ] || die "expected an aarch64 container, got $(uname -m)"
if [ "$LANGUAGE" = "rust" ]; then
  HOST_TRIPLE="$(rustc -vV | sed -n 's/^host: //p')"
  [ "$HOST_TRIPLE" = "aarch64-linux-android" ] \
    || die "expected rustc host aarch64-linux-android, got $HOST_TRIPLE"
fi

# --- build -------------------------------------------------------------------

BUILD_ROOT="$HOME/build-$PROJECT"
echo "=== preparing $PROJECT $PIPELINE_VERSION (ref=${PIPELINE_UPSTREAM_REF:-<pinned>}) ==="
"$HERE/prepare-build-tree.sh" "$BUILD_ROOT" "$PIPELINE_UPSTREAM_REF"

cd "$BUILD_ROOT"
export PIPELINE_BUILD_ROOT="$BUILD_ROOT"

# Keep codegen bounded: the ARM runner is shared, and a fat-LTO + panic=abort
# release at full parallelism thrashes it.
export CARGO_BUILD_JOBS="${PIPELINE_BUILD_JOBS:-4}"

# buildEnv entries become shell exports before the build. pipeline_config
# renders {{vars}} and shell-quotes each value, so this is sourceable as-is.
while IFS= read -r line; do
  [ -n "$line" ] || continue
  echo "  $line"
  eval "$line"
done < <(cfg buildenv)

run_configured() {
  local key="$1" chain
  chain="$(cfg rendershell "$key")"
  [ -n "$chain" ] || { echo "→ no $key configured"; return 0; }
  echo "=== $key ==="
  echo "+ $chain"
  # Each list element is a complete shell command line from your own config;
  # this is the same trust level as the build itself.
  eval "$chain"
}

run_configured preBuildCommands
run_configured buildCommands

# --- collect artifacts -------------------------------------------------------

# Stage every declared binary under /out/bin. package-deb.sh reads from there, so
# the ELF check and the smoke test below exercise exactly the bytes that ship
# rather than a build-tree copy that could differ.
STAGE="/out/bin"
rm -rf "$STAGE"
mkdir -p "$STAGE"

echo "=== collecting binaries ==="
BINARIES="$(cfg binaries)"
[ -n "$BINARIES" ] || die "pipeline.json declared no binaries"

ELF_CHECK="$(cfg get elfCheck)"
while IFS=$'\t' read -r name path; do
  [ -n "$name" ] || continue
  [ -f "$path" ] || die "declared binary '$name' not found at $path after the build"
  install -Dm0755 "$path" "$STAGE/$name"
  printf '  %-20s %s\n' "$name" "$(du -h "$STAGE/$name" | cut -f1)"

  if [ "$ELF_CHECK" = "True" ] || [ "$ELF_CHECK" = "true" ]; then
    # ELF sanity without binutils: e_ident at 0, e_machine at 0x12.
    MAGIC="$(head -c 4 "$STAGE/$name" | od -An -tx1 | tr -d ' \n')"
    [ "$MAGIC" = "7f454c46" ] || die "$name is not an ELF file (magic=$MAGIC)"
    MACHINE="$(dd if="$STAGE/$name" bs=1 skip=18 count=2 2>/dev/null | od -An -tx1 | tr -d ' \n')"
    # b7 00 = EM_AARCH64. A cross-compiled desktop binary lands here as x86-64
    # (3e 00), the most common "it built but will not run" failure, so fail the
    # release rather than shipping it.
    [ "$MACHINE" = "b700" ] || die "$name has e_machine $MACHINE, expected b700 (AArch64)."
    echo "    ELF ok, e_machine=b700 (AArch64)"
  fi
done <<< "$BINARIES"

# --- smoke test --------------------------------------------------------------

# This container IS aarch64, so unlike a cross-built desktop matrix the artifact
# can actually be executed here. Put the staged binaries first on PATH so a smoke
# test may refer to them by bare name too.
if [ -n "$(cfg rendershell smokeTest)" ]; then
  echo "=== smoke test ==="
  export PATH="$STAGE:$PATH"
  while IFS= read -r command; do
    [ -n "$command" ] || continue
    echo "+ $command"
    eval "$command" || die "smoke test failed: $command"
  done < <(cfg render smokeTest)
else
  echo "=== smoke test: none configured, skipping ==="
fi

# --- provenance --------------------------------------------------------------

{
  echo "project:      $PROJECT"
  echo "version:      $PIPELINE_VERSION"
  echo "language:     $LANGUAGE"
  echo "upstream:     ${PIPELINE_UPSTREAM_REF:-$(cfg_get upstreamRef)}"
  echo "upstream_ref: $(cat /out/upstream-commit.txt 2>/dev/null || echo unknown)"
  echo "container:    $(uname -srm)"
  while IFS= read -r tool; do
    printf '%-14s %s\n' "$tool:" "$("$tool" --version 2>&1 | head -1)"
  done < <(cfg getlist requiredTools)
  echo "built:        $(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > /out/BUILD_INFO

echo "--- BUILD_INFO ---"
cat /out/BUILD_INFO
ls -la "$STAGE"
echo "=== done ==="
