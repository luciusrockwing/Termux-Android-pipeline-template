#!/usr/bin/env python3
"""Read, validate and render pipeline.json for the Termux/Android pipeline.

Every shell script in termux/ci/ sources its configuration through this module
rather than hardcoding a project name, so one copy of the pipeline drives any
upstream project.

Subcommands
-----------
  get KEY           print a scalar ("" when absent)
  getlist KEY       print a list, one element per line
  render KEY        print a list with {{vars}} substituted, one command per line
  rendershell KEY   the same, joined into one `; `-separated shell chain
  binaries          print "<name>\t<absolute path>" per declared binary
  buildenv          print `export K='v'` lines for the buildEnv map
  dumpshell         print `export K='v'` lines for the whole config
  show              human-readable summary of the resolved config
  validate          validate and exit non-zero on any problem

Rendering variables
-------------------
  {{project}}    deb/artifact name, e.g. example
  {{version}}    version being released
  {{profile}}    cargo profile (config `profile`, default release)
  {{buildRoot}}  absolute path of the patched source tree (may be "")
  {{ref}}        upstream ref being built
  {{target}}     target triple, always aarch64-linux-android
  {{binary}}     name of the first declared binary

Global environment
------------------
  PIPELINE_CONFIG  path to pipeline.json (default: this checkout's pipeline.json)
  WORKSPACE        optional repo-root override (default: this checkout)
  PIPELINE_VERSION release version; falls back to the config's `version`
  PIPELINE_BUILD_ROOT  absolute path of the patched tree; used by {{buildRoot}}

Exit codes: 0 ok, 1 invalid config, 2 bad invocation/usage.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from typing import Any
from urllib.parse import urlparse

TARGET = "aarch64-linux-android"
DEFAULT_PROFILE = "release"

# Termux packages every build needs, regardless of language.
BASE_PACKAGES = ["git", "ca-certificates", "python", "binutils"]

# Per-language extras and the tools that must exist afterwards. An empty
# toolchainPackages in the config means "use the preset for `language`".
PRESETS: dict[str, dict[str, Any]] = {
    "rust": {
        "packages": ["clang", "lld", "cmake", "make", "ninja", "pkg-config", "rust"],
        "tools": ["cargo", "rustc"],
        "note": "rustup publishes no aarch64-linux-android *host* toolchain; pkg rust is the only rustc here",
    },
    "go": {
        "packages": ["golang", "make"],
        "tools": ["go"],
        "note": "pkg golang builds for GOOS=android GOARCH=arm64 with CGO off",
    },
    "cmake": {
        "packages": ["clang", "lld", "cmake", "make", "ninja", "pkg-config"],
        "tools": ["cmake", "ninja"],
        "note": "set CMAKE_TOOLCHAIN_FILE or -DCMAKE_SYSTEM_NAME=Android as the project requires",
    },
    "meson": {
        "packages": ["meson", "clang", "ninja", "pkg-config"],
        "tools": ["meson", "ninja"],
        "note": "meson needs a cross file; see the cmake entry",
    },
    "python": {
        "packages": ["python", "pip"],
        "tools": ["python3"],
        "note": "binary may be a script wrapper; set elfCheck false",
    },
    "node": {
        "packages": ["nodejs", "npm"],
        "tools": ["node"],
        "note": "bundle to a single script with pkg/nexe, or set elfCheck false",
    },
    "custom": {
        "packages": [],
        "tools": [],
        "note": "list every toolchainPackages and requiredTools yourself",
    },
}

KNOWN_KEYS = {
    "project", "version", "description", "license", "homepage", "maintainer",
    "upstream", "upstreamRef", "upstreamCommit", "binary", "binaries",
    "language", "profile", "toolchainPackages", "requiredTools",
    "buildCommands", "preBuildCommands", "buildEnv", "smokeTest",
    "elfCheck", "extraDebFiles", "_comment",
    "removeFiles",
    "debDescriptionExtra",
}

# dpkg package names: lowercase alnum, plus . + - starting with alnum.
PKG_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]*$")
BIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


class ConfigError(Exception):
    """Raised for any invalid configuration. Carries every problem found."""


def config_path() -> str:
    explicit = os.environ.get("PIPELINE_CONFIG")
    if explicit:
        return explicit
    workspace = os.environ.get("WORKSPACE")
    if workspace:
        return os.path.join(workspace, "pipeline.json")
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return os.path.join(repo_root, "pipeline.json")


def load() -> dict[str, Any]:
    path = config_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError:
        raise ConfigError(f"config not found: {path}")
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}")
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a JSON object")
    return data


def resolve_defaults(cfg: dict[str, Any]) -> dict[str, Any]:
    """Fill in derived values so downstream scripts never re-derive them."""
    out = dict(cfg)
    language = out.get("language", "rust")
    if language not in PRESETS:
        raise ConfigError(
            f"language {language!r} is not a known preset "
            f"({', '.join(sorted(PRESETS))}); use 'custom' and list your own packages"
        )
    preset = PRESETS[language]

    out["language"] = language
    out["profile"] = out.get("profile") or DEFAULT_PROFILE
    out["version"] = (
        os.environ.get("PIPELINE_VERSION") or out.get("version") or ""
    ).lstrip("v")

    # binaries: normalise the single-binary shorthand.
    binaries = out.get("binaries")
    if binaries is None:
        single = out.get("binary")
        binaries = [{"name": single, "path": single}] if single else []
    if not isinstance(binaries, list):
        raise ConfigError("`binaries` must be a list of {name, path} objects")
    out["binaries"] = binaries
    out["binary"] = binaries[0]["name"] if binaries else ""

    declared = out.get("toolchainPackages")
    if declared is None:
        out["toolchainPackages"] = BASE_PACKAGES + preset["packages"]
    else:
        # An explicit list replaces the preset's *extras* but never the base.
        merged = list(BASE_PACKAGES)
        for pkg in declared:
            if pkg not in merged:
                merged.append(pkg)
        out["toolchainPackages"] = merged

    out["requiredTools"] = ["git", "python3", "ar"] + list(
        out.get("requiredTools", preset["tools"])
    )

    for key in ("buildCommands", "preBuildCommands", "smokeTest"):
        out[key] = out.get(key) or []

    out["elfCheck"] = bool(out.get("elfCheck", True))
    out["buildEnv"] = out.get("buildEnv") or {}
    out["extraDebFiles"] = out.get("extraDebFiles") or []
    out["debDescriptionExtra"] = out.get("debDescriptionExtra") or []
    out["license"] = out.get("license") or ""
    out["homepage"] = out.get("homepage") or ""
    out["maintainer"] = out.get("maintainer") or "maintainers <noreply@users.noreply.github.com>"
    out["upstreamRef"] = out.get("upstreamRef") or ""
    out["upstreamCommit"] = out.get("upstreamCommit") or ""
    # A rust-toolchain.toml is a rustup-only pin. Termux provides its own rustc
    # and rustup publishes no aarch64-linux-android *host* toolchain, so such a
    # file can only misdirect a toolchain manager. Remove it by default for
    # rust and leave every other language alone.
    out["removeFiles"] = out.get("removeFiles") or (
        ["rust-toolchain.toml"] if out["language"] == "rust" else []
    )
    return out


def validate(cfg: dict[str, Any]) -> list[str]:
    """Return a list of human-readable problems. Empty means valid."""
    problems: list[str] = []

    def need_str(key: str) -> str:
        value = cfg.get(key)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"`{key}` is required and must be a non-empty string")
            return ""
        return value.strip()

    project = need_str("project")
    if project and not PKG_RE.match(project):
        problems.append(f"`project` {project!r} is not a valid dpkg package name")

    summary = need_str("description")
    if "\n" in summary:
        problems.append("`description` must be a single line; use `debDescriptionExtra` for more")
    if ":" in summary and not re.match(r"^[^:]+: [^:]", summary):
        problems.append("`description` must not start with or contain a bare colon")
    if summary and len(summary) > 200:
        problems.append("`description` exceeds 200 characters")

    for key in ("license", "homepage", "maintainer"):
        if key in cfg and not isinstance(cfg[key], str):
            problems.append(f"`{key}` must be a string")
    if cfg.get("homepage") and not str(cfg["homepage"]).startswith(("http://", "https://")):
        problems.append("`homepage` must be an http(s) URL")

    upstream = need_str("upstream")
    if upstream:
        parsed = urlparse(upstream)
        if parsed.scheme not in ("http", "https"):
            problems.append("`upstream` must be an http(s) URL")
        elif len([segment for segment in parsed.path.split("/") if segment]) < 2:
            # owner/repo are both required; a bare owner URL would fail much
            # later as an opaque git error.
            problems.append("`upstream` must look like https://github.com/owner/repo.git")

    need_str("upstreamRef")

    commit = cfg.get("upstreamCommit") or ""
    if commit and not SHA_RE.match(commit):
        problems.append(f"`upstreamCommit` must be a 40-char lowercase sha, got {commit!r}")

    if not cfg.get("version"):
        problems.append("`version` is required (or set PIPELINE_VERSION)")

    binaries = cfg.get("binaries") or []
    if not binaries:
        problems.append("`binary` or `binaries` is required")
    seen_names: set[str] = set()
    for index, entry in enumerate(binaries):
        where = f"`binaries[{index}]`"
        if not isinstance(entry, dict):
            problems.append(f"{where} must be an object with `name` and `path`")
            continue
        name = entry.get("name")
        path = entry.get("path")
        if not isinstance(name, str) or not BIN_RE.match(name):
            problems.append(f"{where}.name {name!r} is not a valid executable name")
        elif name in seen_names:
            problems.append(f"{where}.name {name!r} is a duplicate")
        else:
            seen_names.add(name)
        if not isinstance(path, str) or not path.strip():
            problems.append(f"{where}.path is required (relative to the build root)")
        elif path.startswith("/") or ".." in path.split("/"):
            problems.append(f"{where}.path must be relative to the build root and must not escape it")

    for key in ("buildCommands", "preBuildCommands", "smokeTest"):
        value = cfg.get(key) or []
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            problems.append(f"`{key}` must be a list of strings")
    if not cfg.get("buildCommands"):
        problems.append("`buildCommands` must contain at least one command")

    for key in ("toolchainPackages", "requiredTools", "extraDebFiles"):
        value = cfg.get(key) or []
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            problems.append(f"`{key}` must be a list of strings")

    env = cfg.get("buildEnv") or {}
    if not isinstance(env, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in env.items()
    ):
        problems.append("`buildEnv` must be an object of string keys to string values")

    if not isinstance(cfg.get("elfCheck", True), bool):
        problems.append("`elfCheck` must be true or false")

    for key in sorted(set(cfg) - KNOWN_KEYS):
        problems.append(f"unknown key `{key}` (typo? known keys: {', '.join(sorted(KNOWN_KEYS))})")

    return problems


def variables(cfg: dict[str, Any]) -> dict[str, str]:
    values = {
        "project": str(cfg.get("project", "")),
        "version": str(cfg.get("version", "")),
        "profile": str(cfg.get("profile", DEFAULT_PROFILE)),
        "buildRoot": os.environ.get("PIPELINE_BUILD_ROOT", ""),
        "ref": str(cfg.get("upstreamRef", "")),
        "target": TARGET,
        "binary": str(cfg.get("binary", "")),
    }
    # {{binary}} is the first declared binary's absolute path, so a smoke test
    # can be written as "{{binary}} --version" without knowing where cargo or
    # go actually dropped it. Render against the map without `binary` first, so
    # a path that itself references {{binary}} cannot recurse.
    binaries = cfg.get("binaries") or []
    build_root = values["buildRoot"]
    if binaries and isinstance(binaries[0], dict) and binaries[0].get("path"):
        try:
            relative = render(str(binaries[0]["path"]), values)
        except ConfigError:
            pass  # unrenderable path: leave {{binary}} as the bare name
        else:
            values["binary"] = os.path.join(build_root, relative) if build_root else relative
    return values


def render(text: str, variables: dict[str, str]) -> str:
    """Substitute {{name}}. An unknown variable is an error, not a silent blank."""
    missing: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            missing.append(name)
            return match.group(0)
        return variables[name]

    out = VAR_RE.sub(replace, text)
    if missing:
        raise ConfigError(
            f"unknown rendering variable(s) {sorted(set(missing))}; "
            f"available: {', '.join(sorted(variables))}"
        )
    return out


def resolve(cfg: dict[str, Any]) -> dict[str, Any]:
    """resolve_defaults + validate. Raises ConfigError listing every problem."""
    resolved = resolve_defaults(cfg)
    problems = validate(resolved)
    if problems:
        bullet = "\n".join(f"  - {problem}" for problem in problems)
        raise ConfigError(f"{config_path()} is not a valid pipeline config:\n{bullet}")
    return resolved


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    command = argv[1]

    try:
        cfg = resolve(load())
    except ConfigError as exc:
        if command == "validate":
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"error: {exc}", file=sys.stderr)
        return 1

    variables_map = variables(cfg)

    def render_all(key: str) -> list[str]:
        return [render(item, variables_map) for item in cfg.get(key, [])]

    if command == "get":
        key = argv[2] if len(argv) > 2 else ""
        value = cfg.get(key, "")
        print("" if value is None else value)
    elif command == "getlist":
        for item in cfg.get(argv[2] if len(argv) > 2 else "", []) or []:
            print(item)
    elif command == "render":
        for line in render_all(argv[2] if len(argv) > 2 else ""):
            print(line)
    elif command == "rendershell":
        # Each list element is already a complete shell command line written by
        # the config author, so join them raw. shlex.quote would wrap each line
        # into a single token and bash would look for a file literally named
        # "<cmd> --version".
        print("; ".join(render_all(argv[2] if len(argv) > 2 else "")))
    elif command == "binaries":
        # Tab-separated so a shell can read name and path in one pass even
        # when the path contains spaces. Paths are absolute when
        # PIPELINE_BUILD_ROOT is set, which build-in-container.sh does.
        build_root = variables_map["buildRoot"]
        for entry in cfg["binaries"]:
            relative = render(str(entry["path"]), variables_map)
            absolute = os.path.join(build_root, relative) if build_root else relative
            print(f"{entry['name']}\t{absolute}")
    elif command == "buildenv":
        # A dedicated subcommand because dumpshell flattens nested objects to
        # a Python repr, which is not shell source.
        for key, value in sorted(cfg["buildEnv"].items()):
            print(f"export {key}={shlex.quote(render(str(value), variables_map))}")
    elif command == "dumpshell":
        for key, value in sorted(cfg.items()):
            if isinstance(value, (str, int, float, bool)):
                print(f"export PIPELINE_{key.upper()}={shlex.quote(str(value))}")
    elif command == "show":
        print(f"project        {cfg['project']}")
        print(f"version        {cfg['version']}")
        print(f"language       {cfg['language']}  ({PRESETS[cfg['language']]['note']})")
        print(f"profile        {cfg['profile']}")
        print(f"upstream       {cfg['upstream']}")
        print(f"upstreamRef    {cfg['upstreamRef']}")
        print(f"upstreamCommit {cfg['upstreamCommit'] or '<unpinned>'}")
        for entry in cfg["binaries"]:
            print(f"binary         {entry['name']} <- {render(entry['path'], variables_map)}")
        print(f"packages       {' '.join(cfg['toolchainPackages'])}")
        print(f"requiredTools  {' '.join(cfg['requiredTools'])}")
        print(f"buildCommands  {len(cfg['buildCommands'])}")
        print(f"smokeTest      {variables_map['binary'] and ' '.join(render_all('smokeTest')) or '<none>'}")
        print(f"elfCheck       {str(cfg['elfCheck']).lower()}")
    elif command == "validate":
        print(f"ok: {config_path()} is valid ({cfg['project']} {cfg['version']})")
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    except BrokenPipeError:
        sys.exit(0)
    except IndexError:
        print("error: missing KEY argument", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)
