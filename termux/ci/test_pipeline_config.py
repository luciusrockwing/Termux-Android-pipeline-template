#!/usr/bin/env python3
"""Tests for pipeline_config.py, the validator/renderer every ci script uses.

Run:  python3 termux/ci/test_pipeline_config.py

Stdlib only, no deps, so it runs in the Termux container and on a laptop.

The point of these tests is that someone adopting this template edits
pipeline.json in ways the shipped example never exercises: a typo in a key, a
binary path that escapes the build tree, a language nobody has a preset for.
Each of those must fail loudly instead of producing a subtly broken build.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import pipeline_config as pc  # noqa: E402

# A minimal config that passes validation. Individual tests break exactly one
# thing, so a failure names the rule that broke.
GOOD = {
    "project": "demo",
    "version": "1.2.3",
    "description": "A demo tool",
    "upstream": "https://github.com/owner/demo.git",
    "upstreamRef": "v1.2.3",
    "upstreamCommit": "0" * 40,
    "language": "rust",
    "binary": "demo",
    "buildCommands": ["make"],
    "smokeTest": ["{{binary}} --version"],
}


def with_changes(**changes) -> dict:
    out = dict(GOOD)
    out.update(changes)
    return out


class ConfigFixture(unittest.TestCase):
    """Helpers to write a config to disk and run the CLI against it."""

    def run_cli(self, config: dict, *args: str, env: dict | None = None):
        return subprocess.run(
            [sys.executable, str(HERE / "pipeline_config.py"), *args],
            capture_output=True,
            text=True,
            env=self.env_for(config, env),
        )

    def env_for(self, config: dict, extra: dict | None = None) -> dict:
        environ = dict(os.environ)
        environ.pop("PIPELINE_VERSION", None)
        environ.pop("PIPELINE_BUILD_ROOT", None)
        environ["PIPELINE_CONFIG"] = write_config(config)
        environ.update(extra or {})
        return environ

    def errors_for(self, config: dict) -> str:
        """Return the CLI's stderr, asserting the config was rejected."""
        result = self.run_cli(config, "validate")
        self.assertNotEqual(
            result.returncode, 0, f"expected rejection, got:\n{result.stdout}"
        )
        return result.stderr

    def assertRejected(self, config: dict, needle: str):
        problems = self.errors_for(config)
        self.assertIn(needle, problems, f"expected {needle!r} in:\n{problems}")

    def assertAccepted(self, config: dict) -> str:
        result = self.run_cli(config, "validate")
        self.assertEqual(result.returncode, 0, f"expected acceptance:\n{result.stderr}")
        return result.stdout.strip()


def write_config(config: dict) -> str:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    )
    with handle:
        json.dump(config, handle)
    return handle.name


def run_with_raw_config(text: str, *args: str):
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    )
    with handle:
        handle.write(text)
    environ = dict(os.environ, PIPELINE_CONFIG=handle.name)
    return subprocess.run(
        [sys.executable, str(HERE / "pipeline_config.py"), *args],
        capture_output=True,
        text=True,
        env=environ,
    )


class TestPathResolution(unittest.TestCase):
    def test_config_path_defaults_to_this_checkout(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                Path(pc.config_path()), HERE.parent.parent / "pipeline.json"
            )

    def test_workspace_overrides_config_location(self):
        with mock.patch.dict(
            os.environ, {"WORKSPACE": "/custom/workspace"}, clear=True
        ):
            self.assertEqual(
                Path(pc.config_path()), Path("/custom/workspace/pipeline.json")
            )

    def test_explicit_config_path_has_priority(self):
        with mock.patch.dict(
            os.environ,
            {"PIPELINE_CONFIG": "/custom/pipeline.json", "WORKSPACE": "/ignored"},
            clear=True,
        ):
            self.assertEqual(
                Path(pc.config_path()), Path("/custom/pipeline.json")
            )

    def test_shared_library_defaults_workspace_to_this_checkout(self):
        repo_root = HERE.parent.parent
        result = subprocess.run(
            [
                "bash",
                "-c",
                'source "$1/termux/ci/lib.sh"; printf "%s" "$WORKSPACE"',
                "bash",
                str(repo_root),
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, str(repo_root))


class TestHappyPath(ConfigFixture):
    def tearDown(self):
        # Temp files are cheap but numerous; clean them so a full run does not
        # litter TMPDIR on a phone.
        for path in getattr(self, "_tempfiles", []):
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_minimal_config_is_valid(self):
        self.assertAccepted(GOOD)

    def test_shipped_template_config_is_valid(self):
        shipped = HERE.parent.parent / "pipeline.json"
        self.assertTrue(shipped.is_file(), f"missing {shipped}")
        with open(shipped, encoding="utf-8") as handle:
            data = json.load(handle)
        result = self.run_cli(data, "validate")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cli_finds_shipped_config_without_workspace_env(self):
        environ = dict(os.environ)
        environ.pop("PIPELINE_CONFIG", None)
        environ.pop("WORKSPACE", None)
        result = subprocess.run(
            [sys.executable, str(HERE / "pipeline_config.py"), "validate"],
            cwd=tempfile.gettempdir(),
            capture_output=True,
            text=True,
            env=environ,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_render_replaces_profile(self):
        config = with_changes(
            binaries=[{"name": "demo", "path": "target/{{profile}}/demo"}],
            profile="dist",
            buildCommands=["cargo build --profile {{profile}} --bin demo"],
        )
        result = self.run_cli(config, "rendershell", "buildCommands")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "cargo build --profile dist --bin demo")

    def test_profile_defaults_to_release(self):
        self.assertEqual(self.run_cli(GOOD, "get", "profile").stdout.strip(), "release")

    def test_binary_renders_absolute_with_build_root(self):
        config = with_changes(
            binaries=[{"name": "demo", "path": "target/{{profile}}/demo"}]
        )
        result = self.run_cli(
            config, "rendershell", "smokeTest", env={"PIPELINE_BUILD_ROOT": "/home/system/build"}
        )
        self.assertEqual(
            result.stdout.strip(), "/home/system/build/target/release/demo --version"
        )

    def test_binary_renders_relative_without_build_root(self):
        config = with_changes(
            binaries=[{"name": "demo", "path": "target/{{profile}}/demo"}]
        )
        self.assertEqual(
            self.run_cli(config, "rendershell", "smokeTest").stdout.strip(),
            "target/release/demo --version",
        )

    def test_command_lines_are_not_shell_quoted_into_one_token(self):
        """A whole command line must survive rendershell as executable syntax.

        shlex.quote-ing each line would wrap it in one token and bash would hunt
        for a file literally named "<cmd> --version".
        """
        config = with_changes(buildCommands=["env FOO=1 make", "make install"])
        result = self.run_cli(config, "rendershell", "buildCommands")
        self.assertEqual(result.stdout.strip(), "env FOO=1 make; make install")

    def test_version_from_environment_overrides_config(self):
        result = self.run_cli(GOOD, "get", "version", env={"PIPELINE_VERSION": "v9.9.9"})
        self.assertEqual(result.stdout.strip(), "9.9.9")

    def test_single_binary_shorthand_normalises_to_list(self):
        self.assertEqual(
            pc.resolve(GOOD)["binaries"], [{"name": "demo", "path": "demo"}]
        )

    def test_every_problem_is_reported_at_once(self):
        problems = self.errors_for(with_changes(project="", upstream="not-a-url"))
        self.assertIn("`project` is required", problems)
        self.assertIn("`upstream` must be an http(s) URL", problems)


class TestValidation(ConfigFixture):
    def test_project_must_be_a_lowercase_dpkg_name(self):
        self.assertRejected(with_changes(project="Demo"), "not a valid dpkg package name")
        self.assertRejected(with_changes(project="demo_tool"), "not a valid dpkg package name")
        self.assertRejected(with_changes(project=""), "`project` is required")
        self.assertAccepted(with_changes(project="demo-tool"))

    def test_description_must_be_one_line(self):
        self.assertRejected(
            with_changes(description="line one\nline two"), "must be a single line"
        )

    def test_upstream_must_name_owner_and_repo(self):
        self.assertRejected(
            with_changes(upstream="https://github.com/owner"), "must look like"
        )
        self.assertRejected(with_changes(upstream="git@github.com:o/r.git"), "http(s) URL")
        self.assertAccepted(with_changes(upstream="https://github.com/owner/demo"))

    def test_upstream_commit_must_be_a_full_lowercase_sha(self):
        self.assertRejected(with_changes(upstreamCommit="abc123"), "40-char lowercase sha")
        self.assertRejected(with_changes(upstreamCommit="A" * 40), "40-char lowercase sha")
        self.assertAccepted(with_changes(upstreamCommit=""))

    def test_binary_path_must_stay_inside_the_build_tree(self):
        self.assertRejected(
            with_changes(binaries=[{"name": "demo", "path": "/etc/passwd"}]),
            "must be relative to the build root",
        )
        self.assertRejected(
            with_changes(binaries=[{"name": "demo", "path": "../demo"}]),
            "must not escape it",
        )

    def test_duplicate_binary_names_rejected(self):
        self.assertRejected(
            with_changes(
                binaries=[{"name": "demo", "path": "a"}, {"name": "demo", "path": "b"}]
            ),
            "is a duplicate",
        )

    def test_build_commands_required(self):
        self.assertRejected(with_changes(buildCommands=[]), "at least one command")

    def test_binary_is_required(self):
        self.assertRejected(with_changes(binary=None), "is required")

    def test_unknown_key_is_reported(self):
        self.assertRejected(
            with_changes(bulidCommands=["make"]), "unknown key `bulidCommands`"
        )

    def test_comment_key_is_allowed(self):
        self.assertAccepted(with_changes(_comment=["notes"]))

    def test_unknown_language_is_rejected(self):
        self.assertRejected(with_changes(language="zig"), "not a known preset")

    def test_custom_language_accepts_explicit_packages(self):
        self.assertAccepted(
            with_changes(language="custom", toolchainPackages=["zig"], buildCommands=["zig build"])
        )

    def test_build_env_must_be_strings(self):
        self.assertRejected(with_changes(buildEnv={"JOBS": 4}), "`buildEnv` must be an object")
        self.assertAccepted(with_changes(buildEnv={"JOBS": "4"}))

    def test_unknown_render_variable_is_an_error_not_a_blank(self):
        result = self.run_cli(
            with_changes(buildCommands=["make VERSION={{versoin}}"]),
            "rendershell",
            "buildCommands",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown rendering variable", result.stderr)

    def test_malformed_json_is_reported(self):
        result = run_with_raw_config("{ not json ", "validate")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not valid JSON", result.stderr)

    def test_non_object_json_is_reported(self):
        result = run_with_raw_config("[1, 2, 3]", "validate")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must be a JSON object", result.stderr)

    def test_missing_config_file_is_reported(self):
        result = subprocess.run(
            [sys.executable, str(HERE / "pipeline_config.py"), "validate"],
            capture_output=True,
            text=True,
            env=dict(os.environ, PIPELINE_CONFIG="/nonexistent/pipeline.json"),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("config not found", result.stderr)


class TestLanguagePresets(ConfigFixture):
    def test_rust_preset_supplies_rust_and_cc_toolchain(self):
        resolved = pc.resolve(with_changes(language="rust"))
        self.assertIn("rust", resolved["toolchainPackages"])
        self.assertIn("cargo", resolved["requiredTools"])
        self.assertIn("rustc", resolved["requiredTools"])

    def test_every_preset_keeps_the_base_packages(self):
        for language in pc.PRESETS:
            config = with_changes(language=language, binary="demo", buildCommands=["true"])
            if language != "rust":
                config["binary"] = "demo"
            resolved = pc.resolve(config)
            for package in pc.BASE_PACKAGES:
                self.assertIn(package, resolved["toolchainPackages"], language)

    def test_explicit_packages_replace_preset_extras_but_keep_base(self):
        resolved = pc.resolve(with_changes(language="rust", toolchainPackages=["zig"]))
        self.assertIn("git", resolved["toolchainPackages"])
        self.assertIn("python", resolved["toolchainPackages"])
        self.assertIn("zig", resolved["toolchainPackages"])
        self.assertNotIn("rust", resolved["toolchainPackages"])

    def test_required_tools_always_include_the_harness(self):
        resolved = pc.resolve(GOOD)
        for tool in ("git", "python3", "ar"):
            self.assertIn(tool, resolved["requiredTools"])

    def test_preset_is_consulted_when_packages_are_absent(self):
        config = with_changes(language="go", toolchainPackages=None)
        config.pop("toolchainPackages")
        self.assertIn("golang", pc.resolve(config)["toolchainPackages"])


class TestDumpshell(ConfigFixture):
    def test_dumpshell_emits_sourceable_shell(self):
        result = self.run_cli(GOOD, "dumpshell")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertTrue(lines)
        for line in lines:
            self.assertTrue(line.startswith("export PIPELINE_"), line)
        # The whole block must parse as shell.
        subprocess.run(["bash", "-n"], input=result.stdout, text=True, check=True)


class TestBinariesSubcommand(ConfigFixture):
    def test_binaries_emits_tab_separated_name_and_absolute_path(self):
        config = with_changes(
            binaries=[{"name": "demo", "path": "target/{{profile}}/demo"}]
        )
        result = self.run_cli(
            config, "binaries", env={"PIPELINE_BUILD_ROOT": "/home/system/build"}
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        name, _, path = result.stdout.strip().partition("\t")
        self.assertEqual(name, "demo")
        self.assertEqual(path, "/home/system/build/target/release/demo")

    def test_binaries_emits_one_row_per_declared_binary(self):
        config = with_changes(
            binaries=[
                {"name": "demo", "path": "target/{{profile}}/demo"},
                {"name": "demo-helper", "path": "target/{{profile}}/demo-helper"},
            ]
        )
        result = self.run_cli(config, "binaries")
        rows = [line for line in result.stdout.splitlines() if line]
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[1].startswith("demo-helper\t"))

    def test_shorthand_binary_still_emits_a_row(self):
        result = self.run_cli(GOOD, "binaries")
        self.assertEqual(result.stdout.strip(), "demo\tdemo")


class TestBuildEnvSubcommand(ConfigFixture):
    def test_buildenv_emits_sourceable_exports(self):
        config = with_changes(buildEnv={"RELEASE_TAG": "v{{version}}", "JOBS": "4"})
        result = self.run_cli(config, "buildenv")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("export RELEASE_TAG=v1.2.3", result.stdout)
        self.assertIn("export JOBS=4", result.stdout)
        subprocess.run(["bash", "-n"], input=result.stdout, text=True, check=True)

    def test_buildenv_quotes_values_that_need_it(self):
        config = with_changes(buildEnv={"MSG": "hello world; rm -rf /"})
        result = self.run_cli(config, "buildenv")
        # A bare value here would execute when build-in-container.sh evals it.
        self.assertIn("'hello world; rm -rf /'", result.stdout)
        subprocess.run(["bash", "-n"], input=result.stdout, text=True, check=True)

    def test_buildenv_is_empty_when_unset(self):
        self.assertEqual(self.run_cli(GOOD, "buildenv").stdout.strip(), "")


class TestRemoveFiles(ConfigFixture):
    def test_rust_drops_the_rustup_toolchain_pin(self):
        self.assertEqual(pc.resolve(GOOD)["removeFiles"], ["rust-toolchain.toml"])

    def test_non_rust_keeps_it(self):
        config = with_changes(language="go")
        config.pop("toolchainPackages", None)
        self.assertEqual(pc.resolve(config)["removeFiles"], [])

    def test_explicit_remove_files_wins(self):
        config = with_changes(removeFiles=["Cargo.lock"])
        self.assertEqual(pc.resolve(config)["removeFiles"], ["Cargo.lock"])


class TestRemovedKeys(ConfigFixture):
    def test_deb_conflicts_is_no_longer_accepted(self):
        # It was declared in the schema but never consumed by package-deb.sh.
        # Dead config is worse than no config.
        self.assertRejected(with_changes(debConflicts=["other"]), "unknown key `debConflicts`")

if __name__ == "__main__":
    unittest.main(verbosity=2)
