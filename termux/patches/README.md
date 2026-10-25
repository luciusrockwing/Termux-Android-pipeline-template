# Patches

Drop `*.patch` files in **this** directory and `apply-patches.sh` will apply
every one of them, in filename order, with `git apply --3way`.

```
termux/patches/0001-termux-tls-roots.patch
termux/patches/0002-android-build-fixes.patch
```

The glob is `termux/patches/*.patch`, one level deep. Keep patch files directly
in this directory; subdirectories are ignored.

## What belongs here

Anything that makes upstream build for, or behave correctly on, Termux, when it
cannot be fixed by configuration alone:

- toolchain or build-system fixes that must be applied to the source tree
- Android/Termux platform workarounds (see the TLS note below)
- removal of files that are actively harmful under Termux

Version pinning does **not** belong here — that is `pipeline.json`.

## Generating them

```bash
# 1. Get a tree you can edit
bash termux/ci/prepare-build-tree.sh ~/build-myproject

# 2. Edit it, then stage any NEW files, or they are left out of the diff
cd ~/build-myproject
git add -N .          # or: git add <path>

# 3. Regenerate the patch
bash termux/ci/refresh-patches.sh ~/build-myproject
```

`refresh-patches.sh` writes plain `git diff` output, which is exactly what
`git apply --3way` consumes. Renumber as the delta grows: `0001-`, `0002-`, …

## Why `--3way` and not `git am`

`--3way` falls back to a three-way merge when an upstream edit lands near one of
your hunks, which is the difference between a bump being routine and a bump
needing a rewrite. It works on a depth-1 clone, because for a patch generated
against that ref the pre-image blobs *are* the checked-out tree.

A conflict aborts the release. It does not produce a half-patched build.

## Android TLS: the patch most projects eventually need

A Rust binary using `reqwest` 0.13 **will not** make HTTPS requests on Android
out of the box. It aborts at startup with:

```
thread 'main' panicked at rustls-platform-verifier-0.7.1/src/android.rs:90:
Expect rustls-platform-verifier to be initialized
```

The fix is to point the client at Termux's CA bundle, which costs no extra
dependencies and keeps `cargo build --locked` valid:

```rust
builder.tls_certs_only(certs.to_vec())   // + add_root_certificate per cert
```

with the bundle resolved as `$SSL_CERT_FILE` → `$PREFIX/etc/tls/cert.pem` →
`/data/data/com.termux/files/usr/etc/tls/cert.pem`. Termux sets `SSL_CERT_FILE`
in its shell profile, so the first branch normally wins.

`reqwest` 0.13 has no `webpki-roots` feature and no `SSL_CERT_FILE` support on
Android, so there is no configuration-only workaround. Implement the equivalent
in the target project's source tree, then regenerate a project-specific patch
with `termux/ci/refresh-patches.sh`.

Go, Java and Python do not have this problem — they ship their own roots or
read the system store.
