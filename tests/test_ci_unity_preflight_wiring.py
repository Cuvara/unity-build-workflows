"""
Unity preflight wiring in the native (build-engine=local) lanes of
reusable-build-platform.yml.

Contract:
  - Self-hosted native lanes (Windows / macOS) run scripts/unity-preflight.sh
    before any Unity invocation; Docker lanes never do (their editor is in the
    image).
  - Every native Unity step runs the executable preflight resolved
    (steps.unity-preflight.outputs.unity_editor) -- never a path rebuilt from
    inputs.unity-version, which would let a stale pin pick a different editor
    than ProjectVersion.txt.
  - A preflight failure stops the job before Unity starts.

The behavioural tests execute the real `run:` scripts of the preflight step and
the macOS build step under bash, with the fake Unity CLI from
tests/fixtures/fake_unity_cli.py, and check which executable Unity was actually
started as.
"""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

import unity_preflight as up

REPO_ROOT = Path(__file__).parent.parent
REUSABLE = REPO_ROOT / ".github" / "workflows" / "reusable-build-platform.yml"
FAKE_CLI = Path(__file__).parent / "fixtures" / "fake_unity_cli.py"
WORKFLOW = yaml.safe_load(REUSABLE.read_text(encoding="utf-8"))
STEPS = WORKFLOW["jobs"]["build"]["steps"]

VERSION = "6000.0.26f1"
PREFLIGHT_OUTPUT = "${{ steps.unity-preflight.outputs.unity_editor }}"
NATIVE_UNITY_STEP_IDS = ("addressables-pre-selfhosted", "build-windows", "build-macos")
OLD_PATH_MARKERS = ("Hub/Editor", "Hub\\Editor", "Program Files", "/Applications/Unity", "Unity.app")
BASH = shutil.which("bash")


# The "Install toolkit build package" step's outputs (test_toolkit_build_package.py).
BUILD_PACKAGE_METHODS = {
    "steps.build-package.outputs.player-method || 'PlayerBuilder.Build'":
        "Company.BuildPipeline.Editor.PlayerBuilder.Build",
    "steps.build-package.outputs.addressables-method || 'AddressableBuilder.Build'":
        "Company.BuildPipeline.Editor.AddressableBuilder.Build",
}


def step(step_id):
    matches = [s for s in STEPS if s.get("id") == step_id]
    assert len(matches) == 1, f"expected exactly one step with id {step_id}"
    return matches[0]


def index_of(step_id):
    return next(i for i, s in enumerate(STEPS) if s.get("id") == step_id)


def valid_platforms():
    text = (REPO_ROOT / "scripts" / "common" / "resolve_build_flow.sh").read_text(encoding="utf-8")
    match = re.search(r'VALID_PLATFORMS="([^"]+)"', text)
    assert match, "VALID_PLATFORMS not found in resolve_build_flow.sh"
    return match.group(1).split()


# ---------------------------------------------------------------------------
# Structure
# ---------------------------------------------------------------------------

class TestWiring:
    def test_preflight_runs_the_toolkit_entry_point(self):
        run = step("unity-preflight")["run"]
        assert "bash .toolkit/scripts/unity-preflight.sh" in run
        assert "--format github-actions" in run and "--cli unity" in run
        assert "--install-cli-to" in run
        assert "--fallback-install-root" in run

    def test_toolkit_is_checked_out_before_preflight(self):
        checkout = step("preflight-toolkit")
        assert checkout["uses"].startswith("actions/checkout@")
        assert checkout["with"]["path"] == ".toolkit"
        assert index_of("preflight-toolkit") < index_of("unity-preflight")

    def test_preflight_precedes_every_native_unity_step(self):
        for step_id in NATIVE_UNITY_STEP_IDS:
            assert index_of("unity-preflight") < index_of(step_id), step_id

    @pytest.mark.parametrize("step_id", ["unity-preflight"])
    def test_preflight_only_on_native_windows_and_macos_lanes(self, step_id):
        condition = step(step_id)["if"]
        assert "inputs.build-engine == 'local'" in condition
        assert "runner.os == 'Windows'" in condition and "runner.os == 'macOS'" in condition
        assert "docker" not in condition

    def test_docker_steps_never_touch_preflight(self):
        docker_steps = [s for s in STEPS if "inputs.build-engine == 'docker'" in str(s.get("if", ""))]
        assert docker_steps, "expected docker-lane steps"
        for s in docker_steps:
            assert "unity-preflight" not in json.dumps(s), s.get("name")
            assert "UNITY_EDITOR" not in json.dumps(s.get("env", {})), s.get("name")

    def test_ci_never_waits_on_an_elevation_prompt(self):
        assert step("unity-preflight")["env"]["UNITY_NO_ELEVATE"] == "1"

    def test_runs_on_still_comes_from_the_scheduler_labels(self):
        runs_on = WORKFLOW["jobs"]["build"]["runs-on"]
        assert runs_on == ("${{ fromJSON(inputs.runner-labels != '' && inputs.runner-labels "
                           "|| '[\"ubuntu-latest\"]') }}")


class TestEditorPropagation:
    @pytest.mark.parametrize("step_id", NATIVE_UNITY_STEP_IDS)
    def test_native_step_runs_the_preflight_editor(self, step_id):
        s = step(step_id)
        assert s["env"]["UNITY_EDITOR"] == PREFLIGHT_OUTPUT
        run = s["run"]
        for marker in OLD_PATH_MARKERS:
            assert marker not in run, f"{step_id} still rebuilds an editor path ({marker})"

    @pytest.mark.parametrize("step_id", NATIVE_UNITY_STEP_IDS)
    def test_native_step_does_not_derive_the_editor_from_the_version_pin(self, step_id):
        assert "inputs.unity-version" not in step(step_id)["run"]

    @pytest.mark.parametrize("step_id", NATIVE_UNITY_STEP_IDS)
    def test_preflight_failure_stops_the_build(self, step_id):
        condition = step(step_id)["if"]
        for escape in ("always()", "failure()", "cancelled()"):
            assert escape not in condition, f"{step_id} would run after a failed preflight"
        assert "continue-on-error" not in step("unity-preflight")

    def test_reported_version_prefers_the_preflight_version(self):
        outputs = step("set-outputs")["run"]
        assert "steps.unity-preflight.outputs.unity_version || inputs.unity-version" in outputs


class TestPlatformForwarding:
    @pytest.mark.parametrize("platform", valid_platforms())
    def test_every_pipeline_platform_is_a_preflight_platform(self, platform):
        assert up.resolve_platforms([platform]) == [platform]

    def test_platform_input_reaches_preflight(self):
        s = step("unity-preflight")
        assert s["env"]["PLATFORM"] == "${{ inputs.platform }}"
        assert 'args+=(--platform "${PLATFORM}")' in s["run"]


# ---------------------------------------------------------------------------
# Behaviour: run the real step scripts
# ---------------------------------------------------------------------------

def render(run, values):
    """Substitute the ${{ }} expressions a step script uses with test values."""
    def sub(match):
        key = match.group(1).strip()
        assert key in values, f"test does not provide a value for ${{{{ {key} }}}}"
        return values[key]
    return re.sub(r"\$\{\{\s*([^}]+?)\s*\}\}", sub, run)


@pytest.fixture
def ci(tmp_path):
    """A fake self-hosted job workspace with the toolkit and a pre-bootstrapped fake CLI."""
    workspace = tmp_path / "workspace"
    project = workspace / "project"
    (project / "ProjectSettings").mkdir(parents=True)
    (project / "ProjectSettings" / "ProjectVersion.txt").write_text(
        f"m_EditorVersion: {VERSION}\nm_EditorVersionWithRevision: {VERSION} (ccb7c73d2c02)\n",
        encoding="utf-8")
    (workspace / ".toolkit").symlink_to(REPO_ROOT, target_is_directory=True)

    cli_home = tmp_path / "tool" / "unity-cli"
    (cli_home / "bin").mkdir(parents=True)
    shim = cli_home / "bin" / "unity"
    shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_CLI}" "$@"\n', encoding="utf-8")
    shim.chmod(0o755)

    state = tmp_path / "state.json"
    state.write_text(json.dumps({"editors": {}}), encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("UNITY_", "GITHUB_", "RUNNER_"))}
    env.update({
        "GITHUB_OUTPUT": str(tmp_path / "github_output"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "step_summary"),
        "RUNNER_TOOL_CACHE": str(tmp_path / "tool"),
        "UNITY_PREFLIGHT_LOCK_DIR": str(tmp_path / "locks"),
        "FAKE_UNITY_STATE": str(state),
        "FAKE_UNITY_ROOT": str(tmp_path / "editors"),
        "FAKE_UNITY_LOG": str(tmp_path / "cli.log"),
    })
    return {"workspace": workspace, "env": env, "tmp": tmp_path}


def run_preflight_step(ci, platform="Android", expected=VERSION):
    s = step("unity-preflight")
    env = dict(ci["env"])
    env.update({"PROJECT_PATH": "project", "PLATFORM": platform,
                "EXPECTED_VERSION": expected, "UNITY_NO_ELEVATE": "1"})
    return subprocess.run([BASH, "-c", s["run"]], cwd=str(ci["workspace"]), env=env,
                          capture_output=True, text=True, timeout=120)


def step_outputs(ci):
    path = Path(ci["env"]["GITHUB_OUTPUT"])
    if not path.exists():
        return {}
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line)


@pytest.mark.skipif(BASH is None or os.name == "nt", reason="runs the step scripts under POSIX bash")
class TestStepBehaviour:
    def test_build_invokes_exactly_the_editor_preflight_installed(self, ci):
        proc = run_preflight_step(ci)
        assert proc.returncode == 0, proc.stderr
        editor = step_outputs(ci)["unity_editor"]
        assert VERSION in editor

        # Make the provisioned editor record how it was started.
        invoked = ci["tmp"] / "invoked"
        Path(editor).write_text(f'#!/bin/sh\nprintf "%s\\n" "$0" "$@" > "{invoked}"\n',
                                encoding="utf-8")
        Path(editor).chmod(Path(editor).stat().st_mode | stat.S_IXUSR)

        build = step("build-macos")
        script = render(build["run"], {
            "inputs.project-path": "project", "inputs.platform": "Android",
            "inputs.build-method": "", "inputs.android-export-type": "apk",
            **BUILD_PACKAGE_METHODS,
        })
        env = dict(ci["env"], UNITY_EDITOR=editor)
        proc = subprocess.run([BASH, "-c", script], cwd=str(ci["workspace"]), env=env,
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode == 0, proc.stdout + proc.stderr
        argv = invoked.read_text(encoding="utf-8").splitlines()
        assert argv[0] == editor, "the build ran a different Unity than preflight provisioned"
        assert "-buildTarget" in argv and "Android" in argv

    def test_second_job_reuses_the_installation(self, ci):
        assert run_preflight_step(ci).returncode == 0
        Path(ci["env"]["GITHUB_OUTPUT"]).unlink()
        proc = run_preflight_step(ci)
        assert proc.returncode == 0, proc.stderr
        installs = [l for l in Path(ci["env"]["FAKE_UNITY_LOG"]).read_text().splitlines()
                    if '"install"' in l]
        assert len(installs) == 1, "the editor must be installed once, then reused"

    def test_version_pin_disagreeing_with_project_fails_the_step(self, ci):
        proc = run_preflight_step(ci, expected="6000.3.9f1")
        assert proc.returncode != 0
        assert "Unity version mismatch" in proc.stdout

    def test_failed_preflight_leaves_the_build_without_an_editor(self, ci):
        (ci["workspace"] / "project" / "ProjectSettings" / "ProjectVersion.txt").unlink()
        proc = run_preflight_step(ci)
        assert proc.returncode != 0
        assert "unity_editor" not in step_outputs(ci)

        script = render(step("build-macos")["run"], {
            "inputs.project-path": "project", "inputs.platform": "Android",
            "inputs.build-method": "", "inputs.android-export-type": "apk",
            **BUILD_PACKAGE_METHODS,
        })
        env = dict(ci["env"], UNITY_EDITOR="")
        proc = subprocess.run([BASH, "-c", script], cwd=str(ci["workspace"]), env=env,
                              capture_output=True, text=True, timeout=60)
        assert proc.returncode != 0
        assert "Unity preflight did not provide an editor executable" in proc.stdout

    def test_addressables_only_build_needs_no_platform_module(self, ci):
        proc = run_preflight_step(ci, platform="Addressables")
        assert proc.returncode == 0, proc.stderr
        assert step_outputs(ci)["unity_platforms"] == ""
