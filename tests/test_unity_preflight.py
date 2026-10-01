"""
Tests for scripts/common/unity_preflight.py -- the local Unity environment
preflight (exact editor version from ProjectVersion.txt, platform modules,
machine-wide install lock).

The real Unity CLI / Hub are replaced by stateful fakes:
  tests/fixtures/fake_unity_cli.py   standalone `unity` CLI (--json envelopes)
  tests/fixtures/fake_unity_hub.py   Unity Hub headless CLI (modules.json)
Each fake is reached through a small shim (.sh on POSIX, .cmd on Windows)
passed via --unity-cli / --unity-hub, so no PATH manipulation is needed.
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import unity_preflight as up

REPO_ROOT = Path(__file__).parent.parent
FIXTURES_DIR = Path(__file__).parent / "fixtures"
SCRIPT = REPO_ROOT / "scripts" / "common" / "unity_preflight.py"
FAKE_CLI = FIXTURES_DIR / "fake_unity_cli.py"
FAKE_HUB = FIXTURES_DIR / "fake_unity_hub.py"

VERSION = "6000.0.26f1"
CHANGESET = "ccb7c73d2c02"
OTHER_VERSION = "6000.3.9f1"
ANDROID_FULL = ["android", "android-sdk-ndk-tools", "android-open-jdk-17.0.9+9"]

PREFLIGHT_ENV_VARS = ("UNITY_EDITOR", "UNITY_CLI", "UNITY_HUB", "UNITY_PREFLIGHT_CLI",
                      "UNITY_PREFLIGHT_LOCK_DIR", "GITHUB_OUTPUT")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_shim(directory: Path, name: str, target: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        shim = directory / f"{name}.cmd"
        shim.write_text(f'@"{sys.executable}" "{target}" %*\n', encoding="utf-8")
    else:
        shim = directory / name
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{target}" "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
    return shim


def make_project(root: Path, version: str = VERSION, changeset: str = CHANGESET,
                 standalone_backend=None, crlf: bool = False) -> Path:
    project = root / "project"
    settings = project / "ProjectSettings"
    settings.mkdir(parents=True, exist_ok=True)
    lines = [f"m_EditorVersion: {version}"]
    if changeset:
        lines.append(f"m_EditorVersionWithRevision: {version} ({changeset})")
    newline = "\r\n" if crlf else "\n"
    (settings / "ProjectVersion.txt").write_bytes((newline.join(lines) + newline).encode())
    if standalone_backend is not None:
        (settings / "ProjectSettings.asset").write_text(
            "PlayerSettings:\n"
            "  productName: Sample\n"
            "  scriptingBackend:\n"
            "    Android: 1\n"
            f"    Standalone: {standalone_backend}\n"
            "  il2cppCompilerConfiguration: {}\n",
            encoding="utf-8",
        )
    return project


class Machine:
    """A fake machine: CLI shims, editor state, invocation log, lock dir."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.state = root / "state.json"
        self.editors = root / "editors"
        self.log = root / "invocations.log"
        self.lock_dir = root / "locks"
        self.cli = make_shim(root / "bin", "unity", FAKE_CLI)
        self.hub = make_shim(root / "bin", "unityhub", FAKE_HUB)
        self.state.write_text(json.dumps({"editors": {}}), encoding="utf-8")

    def add_editor(self, version: str, modules=(), hub: bool = False) -> Path:
        state = json.loads(self.state.read_text(encoding="utf-8"))
        state["editors"][version] = {"architecture": "x86_64", "modules": list(modules)}
        self.state.write_text(json.dumps(state), encoding="utf-8")
        exe = self.editors / version / "Editor" / ("Unity.exe" if os.name == "nt" else "Unity")
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_text("fake editor\n", encoding="utf-8")
        if hub:
            offered = ANDROID_FULL + ["ios", "webgl", "linux-il2cpp", "linux-mono", "linux-server"]
            (self.editors / version / "modules.json").write_text(json.dumps(
                [{"id": m, "selected": m in modules} for m in offered]), encoding="utf-8")
        return exe

    def env(self, mode: str = "success", delay: float = 0, extra=None) -> dict:
        env = os.environ.copy()
        for name in PREFLIGHT_ENV_VARS:
            env.pop(name, None)
        env.update({
            "FAKE_UNITY_STATE": str(self.state),
            "FAKE_UNITY_ROOT": str(self.editors),
            "FAKE_UNITY_LOG": str(self.log),
            "FAKE_UNITY_MODE": mode,
            "FAKE_UNITY_INSTALL_DELAY": str(delay),
        })
        env.update(extra or {})
        return env

    def argv(self, project: Path, *args: str, cli: str = "unity") -> list:
        return [sys.executable, str(SCRIPT), "--project", str(project),
                "--cli", cli, "--unity-cli", str(self.cli), "--unity-hub", str(self.hub),
                "--lock-dir", str(self.lock_dir), *args]

    def run(self, project: Path, *args: str, cli: str = "unity", mode: str = "success",
            extra_env=None, timeout: int = 60) -> subprocess.CompletedProcess:
        return subprocess.run(self.argv(project, *args, cli=cli), capture_output=True,
                              text=True, timeout=timeout, env=self.env(mode, extra=extra_env))

    def calls(self) -> list:
        if not self.log.is_file():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines() if line]

    def install_calls(self) -> list:
        result = []
        for argv in self.calls():
            args = [a for a in argv if a not in ("--no-banner", "--non-interactive", "--json",
                                                 "--", "--headless")]
            if args and args[0] in ("install", "install-modules") and "--list" not in args:
                result.append(args)
        return result


def parse_env(stdout: str) -> dict:
    result = {}
    for line in stdout.splitlines():
        if "=" in line:
            (pair,) = shlex.split(line)
            key, value = pair.split("=", 1)
            result[key] = value
    return result


@pytest.fixture
def machine(tmp_path):
    return Machine(tmp_path / "machine")


# ---------------------------------------------------------------------------
# Version detection
# ---------------------------------------------------------------------------

class TestProjectVersion:
    def test_exact_version_and_changeset(self, tmp_path):
        assert up.read_project_version(make_project(tmp_path)) == (VERSION, CHANGESET)

    def test_crlf_file(self, tmp_path):
        assert up.read_project_version(make_project(tmp_path, crlf=True)) == (VERSION, CHANGESET)

    def test_no_revision_line_gives_empty_changeset(self, tmp_path):
        assert up.read_project_version(make_project(tmp_path, changeset="")) == (VERSION, "")

    def test_missing_file_is_actionable(self, tmp_path, machine):
        project = tmp_path / "not-a-unity-project"
        project.mkdir()
        proc = machine.run(project, "--platform", "Android")
        assert proc.returncode == up.EXIT_USAGE
        assert "Unity project version could not be determined" in proc.stderr
        assert str(Path("ProjectSettings") / "ProjectVersion.txt") in proc.stderr
        assert str(project.resolve()) in proc.stderr
        assert proc.stdout == ""
        assert machine.install_calls() == []

    def test_malformed_version(self, tmp_path):
        project = make_project(tmp_path, version="6000.0", changeset="")
        with pytest.raises(up.PreflightError) as exc:
            up.read_project_version(project)
        assert exc.value.exit_code == up.EXIT_USAGE
        assert "m_EditorVersion: 6000.0" in str(exc.value)

    def test_project_in_subfolder_of_worktree_is_detected(self, tmp_path, machine):
        # Repo layout where the Unity project is not the repository root.
        worktree = tmp_path / "worktree"
        project = make_project(worktree / "MyGame")
        (worktree / "tools" / "docs").mkdir(parents=True)
        machine.add_editor(VERSION, [])
        proc = machine.run(worktree)
        assert proc.returncode == 0, proc.stderr
        assert parse_env(proc.stdout)["UNITY_PROJECT_PATH"] == str(project.resolve())

    def test_several_projects_are_never_guessed(self, tmp_path, machine):
        worktree = tmp_path / "worktree"
        make_project(worktree / "GameA")
        make_project(worktree / "GameB")
        proc = machine.run(worktree)
        assert proc.returncode == up.EXIT_USAGE
        assert "GameA" in proc.stderr and "GameB" in proc.stderr
        assert machine.calls() == []


# ---------------------------------------------------------------------------
# Platform and module mapping
# ---------------------------------------------------------------------------

class TestPlatformMapping:
    def test_aliases_resolve_to_toolkit_names(self):
        assert up.resolve_platforms(["android", "IOS", "webgl,Linux64", "windows"]) == \
            ["Android", "iOS", "WebGL", "Linux64", "Windows64"]

    def test_unknown_platform_lists_valid_names(self):
        with pytest.raises(up.PreflightError) as exc:
            up.resolve_platforms(["switch"])
        assert exc.value.exit_code == up.EXIT_USAGE
        for name in up.CANONICAL_PLATFORMS:
            assert name in str(exc.value)

    def test_android_requires_sdk_ndk_and_versioned_jdk(self):
        reqs = up.required_modules("Android", False, "linux")
        assert [r.label() for r in reqs] == ["android", "android-sdk-ndk-tools", "android-open-jdk*"]

    @pytest.mark.parametrize("backend,expected", [("1", "linux-il2cpp"), ("0", "linux-mono")])
    def test_linux_module_follows_scripting_backend(self, tmp_path, backend, expected):
        project = make_project(tmp_path, standalone_backend=backend)
        il2cpp = up.read_standalone_il2cpp(project)
        assert [r.module for r in up.required_modules("Linux64", il2cpp, "windows")] == [expected]

    def test_backend_defaults_to_mono_without_settings(self, tmp_path):
        assert up.read_standalone_il2cpp(make_project(tmp_path)) is False

    def test_host_native_mono_is_builtin(self):
        assert up.required_modules("Windows64", False, "windows") == []
        assert up.required_modules("Linux64", False, "linux") == []
        assert [r.module for r in up.required_modules("Windows64", True, "windows")] == ["windows-il2cpp"]

    def test_prefix_resolves_to_offered_versioned_id(self):
        reqs = [up.Requirement("Android", "android-open-jdk", True)]
        assert up.resolve_module_ids(reqs, set(ANDROID_FULL), VERSION) == ["android-open-jdk-17.0.9+9"]

    def test_module_not_offered_is_prerequisite_error(self):
        with pytest.raises(up.PreflightError) as exc:
            up.resolve_module_ids([up.Requirement("WebGL", "webgl")], {"android"}, VERSION)
        assert exc.value.exit_code == up.EXIT_PREREQ
        assert "WebGL" in str(exc.value) and VERSION in str(exc.value)

    def test_json_extracted_after_progress_noise(self):
        text = "Adding module x as dependency of y.\n\n{\n  \"success\": true\n}\n"
        assert up.extract_json(text) == {"success": True}


# ---------------------------------------------------------------------------
# Unity CLI backend
# ---------------------------------------------------------------------------

class TestUnityCliBackend:
    def test_installed_editor_and_modules_are_reused(self, tmp_path, machine):
        exe = machine.add_editor(VERSION, ANDROID_FULL)
        proc = machine.run(make_project(tmp_path), "--platform", "Android")
        assert proc.returncode == 0, proc.stderr
        out = parse_env(proc.stdout)
        assert out["UNITY_READY"] == "true"
        assert out["UNITY_VERSION"] == VERSION
        assert out["UNITY_CHANGESET"] == CHANGESET
        assert Path(out["UNITY_EDITOR"]) == exe
        assert out["UNITY_EDITOR_SOURCE"] == "existing"
        assert out["UNITY_INSTALLED"] == ""
        assert out["UNITY_CLI_BACKEND"] == "unity"
        assert machine.install_calls() == []
        assert not (machine.lock_dir / up.LOCK_FILE_NAME).exists(), "read-only run must not lock"

    def test_missing_editor_is_installed_with_changeset_and_modules(self, tmp_path, machine):
        proc = machine.run(make_project(tmp_path), "--platform", "Android")
        assert proc.returncode == 0, proc.stderr
        (call,) = machine.install_calls()
        assert call[:2] == ["install", VERSION]
        assert call[call.index("--changeset") + 1] == CHANGESET
        assert "--child-modules" in call and "--accept-eula" in call
        assert call[call.index("-m") + 1:] == ["android", "android-sdk-ndk-tools"]
        out = parse_env(proc.stdout)
        assert out["UNITY_EDITOR_SOURCE"] == "installed"
        assert out["UNITY_INSTALLED"] == "editor,android,android-sdk-ndk-tools"
        assert "MISSING" in proc.stderr and "READY" in proc.stderr

    def test_other_installed_version_is_never_used(self, tmp_path, machine):
        machine.add_editor(OTHER_VERSION, ANDROID_FULL)
        proc = machine.run(make_project(tmp_path), "--platform", "Android")
        assert proc.returncode == 0, proc.stderr
        out = parse_env(proc.stdout)
        assert VERSION in out["UNITY_EDITOR"]
        assert OTHER_VERSION not in out["UNITY_EDITOR"]
        assert [c[1] for c in machine.install_calls()] == [VERSION]

    def test_missing_platform_module_is_installed(self, tmp_path, machine):
        machine.add_editor(VERSION, [])
        proc = machine.run(make_project(tmp_path), "--platform", "Android")
        assert proc.returncode == 0, proc.stderr
        (call,) = machine.install_calls()
        assert call[0] == "install-modules"
        assert call[call.index("-m") + 1:] == ANDROID_FULL
        assert "Android support:\n  MISSING" in proc.stderr
        assert parse_env(proc.stdout)["UNITY_EDITOR_SOURCE"] == "existing"

    def test_only_missing_child_module_is_installed(self, tmp_path, machine):
        machine.add_editor(VERSION, ["android", "android-sdk-ndk-tools"])
        proc = machine.run(make_project(tmp_path), "--platform", "Android")
        assert proc.returncode == 0, proc.stderr
        (call,) = machine.install_calls()
        assert call[call.index("-m") + 1:] == ["android-open-jdk-17.0.9+9"]

    def test_second_run_is_idempotent(self, tmp_path, machine):
        project = make_project(tmp_path)
        first = machine.run(project, "--platform", "Android")
        second = machine.run(project, "--platform", "Android")
        assert first.returncode == 0 and second.returncode == 0, first.stderr + second.stderr
        assert len(machine.install_calls()) == 1
        assert parse_env(first.stdout)["UNITY_INSTALLED"] != ""
        assert parse_env(second.stdout)["UNITY_INSTALLED"] == ""
        assert parse_env(second.stdout)["UNITY_EDITOR"] == parse_env(first.stdout)["UNITY_EDITOR"]

    def test_noisy_install_output_is_tolerated(self, tmp_path, machine):
        proc = machine.run(make_project(tmp_path), "--platform", "Android", mode="noisy")
        assert proc.returncode == 0, proc.stderr

    def test_install_failure_propagates_cli_error(self, tmp_path, machine):
        proc = machine.run(make_project(tmp_path), "--platform", "Android", mode="install_fail")
        assert proc.returncode == up.EXIT_FAILED
        assert f"Installing Unity {VERSION} failed" in proc.stderr
        assert "DOWNLOAD_FAILED" in proc.stderr
        assert "Simulated download failure" in proc.stderr
        assert "Exit code:\n  6" in proc.stderr
        assert proc.stdout == ""

    def test_module_install_failure_propagates(self, tmp_path, machine):
        machine.add_editor(VERSION, [])
        proc = machine.run(make_project(tmp_path), "--platform", "WebGL", mode="modules_fail")
        assert proc.returncode == up.EXIT_FAILED
        assert "MODULE_INSTALL_FAILED" in proc.stderr

    def test_false_success_fails_verification(self, tmp_path, machine):
        proc = machine.run(make_project(tmp_path), "--platform", "Android", mode="install_noop")
        assert proc.returncode == up.EXIT_FAILED
        assert "Verification failed" in proc.stderr

    def test_check_mode_never_installs(self, tmp_path, machine):
        machine.add_editor(OTHER_VERSION, ANDROID_FULL)
        proc = machine.run(make_project(tmp_path), "--platform", "Android", "--check")
        assert proc.returncode == up.EXIT_NOT_READY
        assert machine.install_calls() == []
        assert parse_env(proc.stdout)["UNITY_READY"] == "false"
        assert "Preflight would run" in proc.stderr and f"install {VERSION}" in proc.stderr

    def test_editor_only_without_platform(self, tmp_path, machine):
        machine.add_editor(VERSION, [])
        proc = machine.run(make_project(tmp_path))
        assert proc.returncode == 0, proc.stderr
        assert parse_env(proc.stdout)["UNITY_PLATFORMS"] == ""

    def test_unknown_platform_exit_code(self, tmp_path, machine):
        proc = machine.run(make_project(tmp_path), "--platform", "PS5")
        assert proc.returncode == up.EXIT_USAGE
        assert "Valid platforms" in proc.stderr

    def test_builtin_windows_mono_needs_no_module(self, tmp_path, machine):
        machine.add_editor(VERSION, [])
        project = make_project(tmp_path, standalone_backend="0")
        proc = machine.run(project, "--platform", "Windows64", "--host-os", "windows")
        assert proc.returncode == 0, proc.stderr
        assert machine.install_calls() == []

    def test_linux64_il2cpp_project_installs_il2cpp_module(self, tmp_path, machine):
        machine.add_editor(VERSION, [])
        project = make_project(tmp_path, standalone_backend="1")
        proc = machine.run(project, "--platform", "linux64", "--host-os", "windows")
        assert proc.returncode == 0, proc.stderr
        (call,) = machine.install_calls()
        assert call[call.index("-m") + 1:] == ["linux-il2cpp"]
        assert parse_env(proc.stdout)["UNITY_PLATFORMS"] == "Linux64"


# ---------------------------------------------------------------------------
# iOS host requirements
# ---------------------------------------------------------------------------

class TestIOS:
    def test_non_macos_host_never_claims_ios_builds(self, tmp_path, machine):
        machine.add_editor(VERSION, ["ios"])
        proc = machine.run(make_project(tmp_path), "--platform", "iOS", "--host-os", "windows")
        assert proc.returncode == 0, proc.stderr
        assert parse_env(proc.stdout)["UNITY_XCODE"] == "unsupported-host"
        assert "iOS player build:\n  NOT POSSIBLE on this host" in proc.stderr

    def test_macos_without_xcode_fails(self, tmp_path, machine):
        machine.add_editor(VERSION, ["ios"])
        empty_path = tmp_path / "empty-bin"
        empty_path.mkdir()
        proc = subprocess.run(
            machine.argv(make_project(tmp_path), "--platform", "iOS", "--host-os", "darwin"),
            capture_output=True, text=True, timeout=60,
            env=machine.env(extra={"PATH": str(empty_path)}),
        )
        assert proc.returncode == up.EXIT_PREREQ
        assert "Xcode" in proc.stderr and "never installs Xcode" in proc.stderr
        assert machine.install_calls() == []


# ---------------------------------------------------------------------------
# Overrides and missing CLI
# ---------------------------------------------------------------------------

class TestOverride:
    def test_override_at_wrong_version_is_rejected(self, tmp_path, machine):
        other = machine.add_editor(OTHER_VERSION, ANDROID_FULL)
        proc = machine.run(make_project(tmp_path), "--platform", "Android",
                           extra_env={"UNITY_EDITOR": str(other)})
        assert proc.returncode == up.EXIT_PREREQ
        assert OTHER_VERSION in proc.stderr and VERSION in proc.stderr
        assert machine.install_calls() == []

    def test_unregistered_override_is_honoured_without_installing(self, tmp_path, machine):
        custom = tmp_path / "custom" / "Unity"
        custom.parent.mkdir()
        custom.write_text("custom editor\n", encoding="utf-8")
        proc = machine.run(make_project(tmp_path), "--platform", "Android",
                           extra_env={"UNITY_EDITOR": str(custom)})
        assert proc.returncode == 0, proc.stderr
        out = parse_env(proc.stdout)
        assert out["UNITY_EDITOR"] == str(custom)
        assert out["UNITY_EDITOR_SOURCE"] == "override"
        assert out["UNITY_VERSION_VERIFIED"] == "false"
        assert machine.install_calls() == []

    def test_override_at_project_version_is_kept_and_completed(self, tmp_path, machine):
        exe = machine.add_editor(VERSION, [])
        proc = machine.run(make_project(tmp_path), "--platform", "WebGL",
                           extra_env={"UNITY_EDITOR": str(exe)})
        assert proc.returncode == 0, proc.stderr
        out = parse_env(proc.stdout)
        assert out["UNITY_EDITOR"] == str(exe)
        assert out["UNITY_EDITOR_SOURCE"] == "override"
        assert out["UNITY_VERSION_VERIFIED"] == "true"
        assert out["UNITY_INSTALLED"] == "webgl"

    def test_override_to_missing_file_is_rejected(self, tmp_path, machine):
        proc = machine.run(make_project(tmp_path), extra_env={"UNITY_EDITOR": str(tmp_path / "nope")})
        assert proc.returncode == up.EXIT_PREREQ
        assert "does not exist" in proc.stderr

    def test_explicit_cli_path_missing(self, tmp_path, machine):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--project", str(make_project(tmp_path)),
             "--cli", "unity", "--unity-cli", str(tmp_path / "no-such-unity")],
            capture_output=True, text=True, timeout=60, env=machine.env(),
        )
        assert proc.returncode == up.EXIT_PREREQ
        assert "unity.com/install.sh" in proc.stderr and "install.ps1" in proc.stderr

    @pytest.mark.skipif(sys.platform != "linux", reason="default Hub/CLI locations exist on dev hosts")
    def test_no_cli_found(self, tmp_path, machine):
        empty_path = tmp_path / "empty-bin"
        empty_path.mkdir()
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--project", str(make_project(tmp_path)), "--cli", "auto"],
            capture_output=True, text=True, timeout=60,
            env=machine.env(extra={"PATH": str(empty_path)}),
        )
        assert proc.returncode == up.EXIT_PREREQ
        assert "Unity CLI not found" in proc.stderr
        assert "unity.com/install.sh" in proc.stderr


# ---------------------------------------------------------------------------
# Unity Hub fallback backend
# ---------------------------------------------------------------------------

class TestHubBackend:
    def test_detects_editor_and_modules_from_modules_json(self, tmp_path, machine):
        machine.add_editor(VERSION, ANDROID_FULL, hub=True)
        proc = machine.run(make_project(tmp_path), "--platform", "Android", cli="hub")
        assert proc.returncode == 0, proc.stderr
        out = parse_env(proc.stdout)
        assert out["UNITY_CLI_BACKEND"] == "hub"
        assert out["UNITY_INSTALLED"] == ""
        assert machine.install_calls() == []

    def test_installs_editor_with_hub_syntax(self, tmp_path, machine):
        proc = machine.run(make_project(tmp_path), "--platform", "Android", cli="hub")
        assert proc.returncode == 0, proc.stderr
        (call,) = [c for c in machine.calls() if "install" in c]
        assert call[:3] == ["--", "--headless", "install"]
        assert call[call.index("--version") + 1] == VERSION
        assert call[call.index("--changeset") + 1] == CHANGESET
        assert "--childModules" in call
        assert parse_env(proc.stdout)["UNITY_EDITOR_SOURCE"] == "installed"

    def test_installs_missing_module_with_hub_syntax(self, tmp_path, machine):
        machine.add_editor(VERSION, [], hub=True)
        proc = machine.run(make_project(tmp_path), "--platform", "WebGL", cli="hub")
        assert proc.returncode == 0, proc.stderr
        (call,) = machine.install_calls()
        assert call[0] == "install-modules"
        assert call[call.index("--version") + 1] == VERSION
        assert "--childModules" in call and call[call.index("-m") + 1:] == ["webgl"]

    def test_module_not_offered_by_hub_editor_is_prerequisite_error(self, tmp_path, machine):
        machine.add_editor(VERSION, [], hub=True)  # its modules.json offers no windows-il2cpp
        project = make_project(tmp_path, standalone_backend="1")
        proc = machine.run(project, "--platform", "Windows64", "--host-os", "linux", cli="hub")
        assert proc.returncode == up.EXIT_PREREQ
        assert "windows-il2cpp" in proc.stderr
        assert machine.install_calls() == []

    def test_hub_exit_zero_without_install_fails_verification(self, tmp_path, machine):
        proc = machine.run(make_project(tmp_path), "--platform", "Android", cli="hub",
                           mode="install_noop")
        assert proc.returncode == up.EXIT_FAILED
        assert "Verification failed" in proc.stderr

    def test_hub_failure_output_is_reported(self, tmp_path, machine):
        proc = machine.run(make_project(tmp_path), cli="hub", mode="install_fail")
        assert proc.returncode == up.EXIT_FAILED
        assert "network error" in proc.stderr


# ---------------------------------------------------------------------------
# Output formats
# ---------------------------------------------------------------------------

class TestOutput:
    def test_json_format(self, tmp_path, machine):
        machine.add_editor(VERSION, ANDROID_FULL)
        proc = machine.run(make_project(tmp_path), "--platform", "Android", "--format", "json")
        assert proc.returncode == 0, proc.stderr
        data = json.loads(proc.stdout)
        assert data["UNITY_READY"] == "true"
        assert data["UNITY_VERSION"] == VERSION
        assert isinstance(data["warnings"], list)

    def test_github_actions_format_appends_outputs(self, tmp_path, machine):
        machine.add_editor(VERSION, ANDROID_FULL)
        github_output = tmp_path / "github_output"
        proc = machine.run(make_project(tmp_path), "--platform", "Android",
                           "--format", "github-actions",
                           extra_env={"GITHUB_OUTPUT": str(github_output)})
        assert proc.returncode == 0, proc.stderr
        written = github_output.read_text(encoding="utf-8")
        assert "unity_ready=true\n" in written
        assert f"unity_version={VERSION}\n" in written

    @pytest.mark.skipif(shutil.which("bash") is None or os.name == "nt",
                        reason="needs a POSIX bash for eval")
    def test_eval_of_env_output_never_executes_path_content(self, tmp_path, machine):
        hostile = tmp_path / "w$(touch PWNED)`touch PWNED2`;x"
        project = make_project(hostile)
        machine.add_editor(VERSION, [])
        proc = machine.run(project)
        assert proc.returncode == 0, proc.stderr
        check = subprocess.run(
            ["bash", "-c", 'eval "$1"; printf "%s" "$UNITY_PROJECT_PATH"', "_", proc.stdout],
            capture_output=True, text=True, cwd=str(tmp_path), timeout=30,
        )
        assert check.returncode == 0, check.stderr
        assert check.stdout == str(project.resolve())
        assert not (tmp_path / "PWNED").exists() and not (tmp_path / "PWNED2").exists()

    def test_github_output_refuses_line_breaks(self, tmp_path, monkeypatch):
        github_output = tmp_path / "github_output"
        monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))
        result = {"UNITY_READY": "true", "UNITY_PROJECT_PATH": "/w/x\nunity_editor=/evil"}
        with pytest.raises(up.PreflightError):
            up.emit(result, "github-actions")
        assert not github_output.exists()

    def test_env_output_quotes_paths_with_spaces(self, tmp_path):
        machine = Machine(tmp_path / "machine with spaces")
        machine.add_editor(VERSION, ANDROID_FULL)
        proc = machine.run(make_project(tmp_path), "--platform", "Android")
        assert proc.returncode == 0, proc.stderr
        assert "machine with spaces" in parse_env(proc.stdout)["UNITY_EDITOR"]


# ---------------------------------------------------------------------------
# Machine-wide install lock
# ---------------------------------------------------------------------------

class TestConcurrency:
    @pytest.mark.parametrize("editor_present", [False, True], ids=["editor-install", "module-install"])
    def test_parallel_preflights_install_once(self, tmp_path, machine, editor_present):
        if editor_present:
            machine.add_editor(VERSION, [])
        project_a = make_project(tmp_path / "worktree-a")
        project_b = make_project(tmp_path / "worktree-b")
        env = machine.env(delay=2)
        procs = [
            subprocess.Popen(machine.argv(p, "--platform", "Android"), stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, env=env)
            for p in (project_a, project_b)
        ]
        results = [p.communicate(timeout=120) for p in procs]
        for proc, (stdout, stderr) in zip(procs, results):
            assert proc.returncode == 0, stderr
            assert parse_env(stdout)["UNITY_READY"] == "true"
        assert len(machine.install_calls()) == 1
        editors = {parse_env(stdout)["UNITY_EDITOR"] for stdout, _ in results}
        assert len(editors) == 1, "both worktrees must share one machine installation"

    def test_lock_timeout_names_the_holder(self, tmp_path, machine):
        owner = {"pid": 4242, "host": "agent-host", "project": "/w/other",
                 "version": VERSION, "started": "2026-01-01T00:00:00"}
        with up.InstallLock(machine.lock_dir, timeout=5, owner=owner):
            proc = machine.run(make_project(tmp_path), "--platform", "Android", "--lock-timeout", "1")
        assert proc.returncode == up.EXIT_LOCK_TIMEOUT
        assert "pid 4242" in proc.stderr
        assert machine.install_calls() == []

    def test_lock_is_reusable_after_release(self, tmp_path):
        lock_dir = tmp_path / "locks"
        with up.InstallLock(lock_dir, timeout=1, owner={}):
            pass
        with up.InstallLock(lock_dir, timeout=1, owner={}):
            pass
        assert not (lock_dir / up.LOCK_OWNER_FILE_NAME).exists()


# ---------------------------------------------------------------------------
# scripts/unity-preflight.sh -- the agent-facing entry point
# ---------------------------------------------------------------------------

LAUNCHER = REPO_ROOT / "scripts" / "unity-preflight.sh"
BASH = shutil.which("bash")


@pytest.mark.skipif(BASH is None or os.name == "nt", reason="needs a POSIX bash")
class TestLauncher:
    def test_store_placeholder_python_gives_actionable_error(self, tmp_path):
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        for name in ("python3", "python", "py"):
            stub = fake_bin / name
            stub.write_text("#!/bin/sh\necho 'Python was not found; run without arguments "
                            "to install from the Microsoft Store'\nexit 49\n", encoding="utf-8")
            stub.chmod(0o755)
        env = os.environ.copy()
        env.pop("UNITY_PREFLIGHT_PYTHON", None)
        env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
        proc = subprocess.run([BASH, str(LAUNCHER), "--project", str(tmp_path)],
                              capture_output=True, text=True, timeout=30, env=env)
        assert proc.returncode == up.EXIT_PREREQ
        assert "Python 3.8+ was not found" in proc.stderr
        assert "winget install" in proc.stderr and "UNITY_PREFLIGHT_PYTHON" in proc.stderr
        assert proc.stdout == ""

    def test_runs_preflight_with_given_interpreter(self, tmp_path, machine):
        exe = machine.add_editor(VERSION, [])
        env = machine.env(extra={"UNITY_PREFLIGHT_PYTHON": sys.executable})
        argv = machine.argv(make_project(tmp_path))[2:]  # drop python + script path
        proc = subprocess.run([BASH, str(LAUNCHER), *argv], capture_output=True, text=True,
                              timeout=60, env=env)
        assert proc.returncode == 0, proc.stderr
        assert parse_env(proc.stdout)["UNITY_EDITOR"] == str(exe)
