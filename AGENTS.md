# AGENTS.md

Operating instructions for a project repo that uses this pipeline. Read this
before changing anything.

## What this repository is

A **delta repo**. It does not contain the upstream project's source. It contains:

```
pipeline.json                  the only file you normally edit
install.sh                     the user-facing installer
.github/workflows/
  release-android.yml          manual dispatch -> .deb + SHA256SUMS + BUILD_INFO
termux/ci/                     the pipeline itself
termux/patches/                your delta against upstream (often empty)
```

CI clones upstream at the ref pinned in `pipeline.json`, applies
`termux/patches/*.patch`, builds it, and packages the result. The repo never
builds upstream in place, so it stays a few kilobytes regardless of how large
the upstream project is.

**The consequence to internalise:** this repo is disposable and upstream is the
source of truth. If you find yourself wanting to edit a file that came from
upstream, you do not edit it here — you either change `pipeline.json` or add a
patch. That is the whole design.

## Adopting the template for a project

1. Click **Use this template** and create `OWNER/REPO`. For an existing fork,
   replace its contents with this template's contents instead.
2. Keep only the template files: `pipeline.json`, `install.sh`, `.github/`,
   `termux/`, docs, and the license. Do not copy upstream source into the
   project repo.
3. Edit `pipeline.json` (see the reference below). Nothing else needs changing
   for the common case. Point `upstream` at the public upstream project or at
   your own upstream fork.
4. Edit the one `FORK_REPO="OWNER/REPO"` line at the top of `install.sh`.
5. Validate before dispatching:
   ```bash
   python3 termux/ci/pipeline_config.py show
   python3 termux/ci/test_pipeline_config.py
   ```
6. Push to the **default branch**. `workflow_dispatch` only works if the
   workflow file exists on the default branch — dispatching against any other
   branch does nothing, silently.
7. Actions → *release-android* → Run workflow.

Keeping upstream source out is the part people skip. A project repo carrying
upstream source has two build systems, and upstream's own CI can run on every
push and waste minutes.

## Making a release

| Input | Effect |
|---|---|
| *(blank)* | build and release the `version` pinned in `pipeline.json` |
| `version: 1.2.4` | override the released version only |
| `upstream_ref: main` | build that ref instead of the pin; tags the release `v<version>-termux.<run>` |
| `draft` | default `true` — nothing public until you say so |
| `prerelease` | ignored while `draft` is true |

The workflow refuses to overwrite an existing tag. If it fails with
`release v1.2.3 already exists`, bump `pipeline.json` rather than deleting the
release; deleting loses the checksums people already downloaded.

**Draft releases are invisible to `install.sh`.** A draft is for testing the
artifact on a real device. Publish only once you have.

## How the pipeline runs

```
lint      ubuntu-latest        ~20s    config valid, scripts parse, shellcheck clean
build     ubuntu-24.04-arm     ~16m    termux-docker:aarch64 -> compile + smoke test
release   ubuntu-24.04         ~10s    gh release create
```

The `lint` job is not decoration. It gates `build`, so a typo in
`pipeline.json` fails in twenty seconds on a cheap x64 runner instead of after
a sixteen-minute ARM build and a container image pull. Do not remove it.

### Why a container, and why ARM64 runners

Termux packages link against a patched Bionic sysroot rooted at
`/data/data/com.termux/files/usr`. No stock Ubuntu runner has one, so the build
runs inside `termux/termux-docker:aarch64`.

Because the runner is **already** aarch64, the container's host triple is
`aarch64-linux-android`. That is the entire reason there is **no NDK and no
cross-linker** here: it is a native build with the same toolchain the target
device uses. A desktop release matrix cross-compiles and gets a binary the
device rejects; this does not.

ARM64 hosted runners are free for public repositories. If you ever make the
fork private, this job starts billing roughly $0.005/min.

### Stage by stage

| Script | Runs on | Job |
|---|---|---|
| `pipeline_config.py` | both | the single source of config truth |
| `lib.sh` | both | shared helpers, sourced |
| `fetch-upstream.sh` | container | depth-1 clone at the pinned ref |
| `apply-patches.sh` | container | `git apply --3way` + `removeFiles` |
| `prepare-build-tree.sh` | container | the two above, in order |
| `build-in-container.sh` | container | toolchain, build, ELF check, smoke test |
| `build-on-runner.sh` | runner | prepares `/out`, runs the container |
| `package-deb.sh` | runner | `dpkg-deb` + `SHA256SUMS` |
| `refresh-patches.sh` | you | regenerate patches from an edited tree |

## `pipeline.json` reference

Validate with `python3 termux/ci/pipeline_config.py show` after every edit.
Unknown keys are **rejected**, not ignored — that is deliberate, so a typo
fails loudly instead of silently doing nothing.

### Identity — required

| Key | Notes |
|---|---|
| `project` | dpkg name: lowercase, digits, `.` `+` `-` only |
| `version` | released version, no leading `v` |
| `description` | one line, becomes the deb `Summary` |
| `upstream` | `https://github.com/OWNER/REPO.git` — owner *and* repo both required |
| `upstreamRef` | tag, branch or 40-char sha |

### Identity — optional

| Key | Notes |
|---|---|
| `upstreamCommit` | 40-char lowercase sha. When the built ref equals `upstreamRef` and the resolved commit differs, **the build fails**. This is what makes a moved tag detectable. Blank disables the check. |
| `license` | target project's license identifier; shown in deb description |
| `homepage` | deb `Homepage` |
| `maintainer` | deb `Maintainer` |
| `debDescriptionExtra` | continuation lines for the deb `Description` |
| `extraDebFiles` | `"<src>:<dest-relative-to-PREFIX>"`, copied verbatim |
| `_comment` | ignored by validation; use it freely |

### Build

| Key | Notes |
|---|---|
| `language` | `rust` `go` `cmake` `meson` `python` `node` `custom` |
| `binaries` | `[{"name": "demo", "path": "target/{{profile}}/demo"}]`. Paths are relative to the build root and may not escape it. |
| `binary` | shorthand for a single binary whose path equals its name |
| `profile` | cargo profile; default `release`. Only used by `{{profile}}`. |
| `preBuildCommands` | run in the tree, before `buildCommands` |
| `buildCommands` | required, at least one |
| `buildEnv` | exported before the build; values rendered then shell-quoted |
| `smokeTest` | run after the ELF check, with the staged binaries on `PATH` |
| `elfCheck` | default `true`; set `false` for script-based artifacts |
| `toolchainPackages` | **overrides** the language preset's extras; base packages are always kept |
| `requiredTools` | **overrides** the language preset's tools; `git python3 ar` are always checked |
| `removeFiles` | deleted from the tree after patching; defaults to `["rust-toolchain.toml"]` for `rust`, empty otherwise |

### Rendering variables

Usable in `binaries[].path`, `buildCommands`, `preBuildCommands`, `smokeTest`,
`buildEnv` values, and `debDescriptionExtra`.

`{{project}}` `{{version}}` `{{profile}}` `{{buildRoot}}` `{{ref}}` `{{target}}`
`{{binary}}`

`{{binary}}` resolves to the **first** binary's absolute path, so
`"{{binary}} --version"` works without knowing where cargo dropped the output.

**An unknown variable is a hard error**, not an empty string. `{{versoin}}`
fails the build instead of producing `make VERSION=` and a confusing downstream
error.

### Language presets

Each preset supplies extra Termux packages and the tools asserted after install.

| `language` | adds | asserts |
|---|---|---|
| `rust` | clang lld cmake make ninja pkg-config **rust** | `cargo rustc` |
| `go` | golang make | `go` |
| `cmake` | clang lld cmake make ninja pkg-config | `cmake ninja` |
| `meson` | meson clang ninja pkg-config | `meson ninja` |
| `python` | python pip | `python3` |
| `node` | nodejs npm | `node` |
| `custom` | — | — (you list them) |

Always added: `git ca-certificates python binutils`.

### Recipes

**Go** — cross-compiles cleanly; no patching needed.
```json
"language": "go",
"binaries": [{ "name": "mytool", "path": "mytool" }],
"preBuildCommands": ["GOOS=android GOARCH=arm64 CGO_ENABLED=0 go mod download"],
"buildCommands": ["GOOS=android GOARCH=arm64 CGO_ENABLED=0 go build -trimpath -o {{binary}} ./cmd/mytool"],
"smokeTest": ["{{binary}} version"],
"elfCheck": true
```

**C/C++ via CMake** — needs an explicit Android toolchain file, and CGO-style
`Sysroot` juggling differs per project. This is the preset most likely to need
`toolchainPackages` and `buildEnv` tuned per project.
```json
"language": "cmake",
"binaries": [{ "name": "mytool", "path": "build/mytool" }],
"buildCommands": [
  "cmake -B build -G Ninja -DCMAKE_TOOLCHAIN_FILE=$PREFIX/share/cmake/android.toolchain.cmake -DCMAKE_BUILD_TYPE=Release",
  "cmake --build build"
],
"smokeTest": ["{{binary}} --help"],
"elfCheck": true
```

**Python** — the artifact is a wrapper script, so the ELF check must be off.
```json
"language": "python",
"binaries": [{ "name": "mytool", "path": "mytool" }],
"buildCommands": ["pip install ."],
"smokeTest": ["{{binary}} --help"],
"elfCheck": false
```

## The delta mechanism

Patches are plain `git diff` output in `termux/patches/`, applied in filename
order with `git apply --3way`. Full instructions, including how to generate
them, are in `termux/patches/README.md`.

`--3way` is chosen over `git am` because an upstream edit landing near one of
your hunks then falls back to a three-way merge instead of failing. It works on
a depth-1 clone, because for a patch generated against that ref the pre-image
blobs *are* the checked-out tree. A conflict aborts the release; it never
produces a half-patched build.

Patch files must live directly in `termux/patches/`. The glob is one level
deep, so subdirectories are ignored.

## Smoke tests

The container is aarch64, so the artifact can actually be **executed** there —
something a cross-built desktop matrix cannot do at all. A smoke test that
passes here has genuinely run.

Make it meaningful. `{{binary}} --version` proves the binary links and starts.
For anything that makes network requests, use a command needing no credentials.
For a network-enabled Rust project, prefer a credential-free command that makes
a real HTTPS request. This catches Android TLS failures that a version-only
smoke test cannot detect.

## Verified reference build

The Rust path was exercised end to end on a POCO F3 (Android 13, aarch64,
Termux) with a Rust CLI that performs HTTPS requests:

- `.deb` built by CI, checksum-verified, `dpkg -i` clean
- binary version command completed successfully
- credential-free HTTPS request succeeded using Termux CA roots
- `install.sh` re-run against the published release, installing from scratch

The Rust path is verified. Other presets follow their ecosystems' standard
cross-compilation and are **not** verified — treat the first build of a new
language as experimental.

## Gotchas that cost real debugging time

**`apt install` exits 0 when it resolves to "nothing to do".** A missing `rust`
package installed cleanly, exited 0, and failed 200 lines later with
`rustc: command not found`. The pipeline therefore asserts with `command -v` on
every entry in `requiredTools` rather than trusting exit status. If you add a
tool, add it to `requiredTools`, not just to `toolchainPackages`.

**`rustup` publishes no `aarch64-linux-android` host toolchain.** A
`rust-toolchain.toml` in the tree is a rustup-only pin that can only misdirect
a toolchain manager, which is why `removeFiles` drops it by default for `rust`.

**`termux-docker`'s entrypoint strips inherited environment** when it switches
to the `system` user. Inputs reach the container through
`/out/build-env.sh`, not through the environment.

**`/workspace` is read-only to the container's `system` user** regardless of
`--user`. `build-on-runner.sh` pre-creates a sibling `out/` directory and
`chmod 0777`s it.

**Termux sets `SSL_CERT_FILE`** in its shell profile, so a CA-bundle resolver
that checks it first resolves to `$PREFIX/etc/tls/certs/ca-certificates.crt`.

**A remote `gh release create` cannot read `versions.json` from a different
repo** without an explicit `GH_TOKEN` for that repo.

**A glob that matches nothing passes through literally under `set -e`.** The
publish step builds `DEBS=( ./*.deb )` and asserts `[ -e "${DEBS[0]}" ]`
explicitly, because otherwise the release would be created pointing at a file
literally named `*.deb`.

**`[ "$X" = y ] && cmd` as the last line of a script fails the job** under
`set -e` when the test is false. Draft/prerelease selection uses `if` blocks.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `rustc: command not found` | tool package not installed | add to `toolchainPackages`, and to `requiredTools` |
| `no binary named 'x'` at build end | wrong `binaries[].path` for your language | `python3 termux/ci/pipeline_config.py show` prints the resolved path |
| `e_machine is not AArch64` | cross-compiled the wrong target | the toolchain is bailing out to a desktop default; force the Android target in `buildCommands` |
| `xxx did not apply against this upstream ref` | upstream moved under a patch hunk | resolve in `~/build-<project>`, then `refresh-patches.sh` |
| `... resolved to X but pipeline.json pins Y` | upstream moved the tag | confirm upstream really moved, then update `upstreamCommit` deliberately |
| `code .H … Expect rustls-platform-verifier to be initialized` | Android TLS | see `termux/patches/README.md`; there is no config-only fix for reqwest 0.13 |
| `member "control.tar" (zstd)` | dpkg without libzstd | `package-deb.sh` pins `-Zxz`; `install.sh` installs `zstd` as a fallback |
| `/out is not writable` | container dropped privileges | `build-on-runner.sh` must have chmod'ed the host dir 0777 |
| dispatch does nothing | workflow absent from the default branch | push the workflow file to the default branch |
| `release vX already exists` | tag collision | bump `pipeline.json`; do not delete the release |

## Maintenance

**Bumping upstream.** Edit `upstreamRef`, run `fetch-upstream.sh` to get the new
commit, then update `upstreamCommit`. Dispatch with a blank version and confirm
`draft`. If a patch conflicts, resolve in the build tree and regenerate with
`refresh-patches.sh`.

**Keeping `.gitignore` honest.** `target/` must stay ignored. A rewritten
`.gitignore` that drops it once caused a commit to swallow 63,276 lines of local
build output. If you rewrite `.gitignore`, diff it against the previous version
before `git add -A`, and check the staged file count.

**Not covered.** There is no upstream watcher; new releases are picked up by
hand. There are no maintainer scripts in the `.deb`. Releases are created as
drafts by default and are invisible to `install.sh` until published.
