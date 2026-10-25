# shellcheck shell=bash
# Shared helpers for the Termux/Android pipeline scripts. Source, don't execute:
#
#   . "$(dirname "$0")/lib.sh"

# Repo root. Default to the checkout containing this script so local and
# GitHub runner invocations need no environment setup. In the Termux container,
# the same derivation resolves to the /workspace mount.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WORKSPACE="${WORKSPACE:-$REPO_ROOT}"
export WORKSPACE

CONFIG_CLI="$WORKSPACE/termux/ci/pipeline_config.py"

# Every script reads its configuration through the one Python entry point, so
# there is a single place where the schema, the defaults and the rendering rules
# live. Never parse pipeline.json with ad-hoc grep/sed here.
cfg()      { python3 "$CONFIG_CLI" "$@"; }
cfg_get()  { cfg get "$1"; }

die() { echo "error: $*" >&2; exit 1; }

# True when the argument looks like a full 40-character git sha rather than a
# tag or branch name. `git clone -b` cannot take a sha, so those need init+fetch.
is_sha() {
  case "$1" in
    *[!0-9a-f]*) return 1 ;;
    "")          return 1 ;;
  esac
  [ "${#1}" -eq 40 ]
}
