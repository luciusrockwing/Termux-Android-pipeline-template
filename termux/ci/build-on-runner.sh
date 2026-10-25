#!/usr/bin/env bash
# Runs on the GitHub Actions runner. Prepares a world-writable /out dir on the
# host and runs termux/termux-docker:aarch64 with this repo mounted at /workspace
# and that dir mounted at /out. The build itself is build-in-container.sh.
#
# Env inputs:
#   GITHUB_WORKSPACE     set by Actions; the checked-out repo
#   PIPELINE_VERSION     optional; forwarded into the container
#   PIPELINE_UPSTREAM_REF optional; build a branch/commit instead of the pin
#   PIPELINE_BUILD_JOBS  optional; parallel codegen units
#
# Output:
#   $OUT_HOST/bin/<name>        the built binaries
#   $OUT_HOST/BUILD_INFO        build identity
#   $OUT_HOST/upstream-commit.txt  exact upstream commit built
#
# OUT_HOST is echoed to stdout LAST so a caller can capture it with `tail -n1`
# even while the container writes progress to the same stream.

set -euo pipefail

: "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE must be set}"

# Pin the container image. A floating tag means a future upstream image change
# can silently alter your build; override PIPELINE_IMAGE to test a new one.
IMAGE="${PIPELINE_IMAGE:-termux/termux-docker:aarch64}"

# /workspace is effectively read-only for the container's `system` user
# (termux-docker drops privileges regardless of --user), so mount a SECOND
# directory that the runner pre-creates world-writable.
mkdir -p "$GITHUB_WORKSPACE/../out"
OUT_HOST="$(cd "$GITHUB_WORKSPACE/../out" && pwd)"
chmod 0777 "$OUT_HOST"
echo "OUT_HOST=$OUT_HOST" >&2

# termux-docker's entrypoint strips inherited environment when it switches to
# the `system` user, so hand the values over through the writable /out mount.
cat > "$OUT_HOST/build-env.sh" <<EOF
export PIPELINE_VERSION='${PIPELINE_VERSION:-}'
export PIPELINE_UPSTREAM_REF='${PIPELINE_UPSTREAM_REF:-}'
export PIPELINE_BUILD_JOBS='${PIPELINE_BUILD_JOBS:-}'
EOF
chmod 0644 "$OUT_HOST/build-env.sh"

docker run --rm \
  -v "$GITHUB_WORKSPACE:/workspace" \
  -v "$OUT_HOST:/out" \
  -w /workspace \
  "$IMAGE" \
  bash /workspace/termux/ci/build-in-container.sh

echo "$OUT_HOST"
