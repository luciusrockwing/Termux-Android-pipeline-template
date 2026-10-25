# Termux/Android pipeline template

Build any GitHub project as a native **Termux** binary and publish it as a
`.deb` — from a repository that does not contain that project's source.

The build happens inside `termux/termux-docker:aarch64` on an `ubuntu-24.04-arm`
runner. Because the runner is already aarch64, the container's host triple *is*
`aarch64-linux-android`: a **native build, with no NDK and no cross-linker**,
using the same toolchain the target device runs.

Rust, Go, CMake, Meson, Python and Node are supported through config; the Rust
path is verified end to end on a real device.

## Use it

1. Click **Use this template** to create your own `OWNER/REPO` repository.
2. Keep upstream source out of it. This repository stays a small delta; CI
   clones upstream during the build.
3. Edit `pipeline.json` — project name, version, upstream repo, ref, license,
   build commands, smoke test. It ships with `OWNER/REPO` and `example`
   placeholders; replace them before dispatching CI.
4. Edit the `FORK_REPO` line at the top of `install.sh`.
5. Check it:
   ```bash
   python3 termux/ci/pipeline_config.py show
   python3 termux/ci/test_pipeline_config.py
   ```
6. Push to the default branch, then Actions → *release-android* → Run workflow.

Already own an upstream fork? Create the project repo from this template and
point `pipeline.json`'s `upstream` URL at that fork.

`[AGENTS.md](AGENTS.md)` is the full operating manual: every config key, the
rendering variables, per-language recipes, the patch mechanism, a
troubleshooting table, and the gotchas that cost real debugging time.

## What it produces

```
<project>_<version>_aarch64.deb
SHA256SUMS
BUILD_INFO          exact upstream commit, toolchain versions, build time
```

Users install with:

```bash
curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/install.sh | bash
```

## Layout

```
pipeline.json                  the only file you normally edit
install.sh                     user-facing installer
.github/workflows/
  release-android.yml          lint -> build -> release
termux/ci/                     the pipeline
termux/patches/                your delta against upstream
```

## Requirements

- A **public** GitHub repository. ARM64 hosted runners are free for public repos;
  a private fork is billed.
- Upstream must build with Termux's toolchain, possibly after patches in
  `termux/patches/`.
- aarch64 devices only. x86-64 Android emulators are not supported.

## Why a delta repo

The pipeline clones upstream at a pinned ref and applies patches at build time.
Your project repo stays a few kilobytes, upstream stays the single source of
truth, and "bump to the latest release" becomes a two-line edit instead of a
merge.

## License

MIT. See [`LICENSE`](LICENSE). Set `pipeline.json`'s `license` to the target
project's license before building a package.
