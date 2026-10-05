"""Unity Build Profiles per platform.

`build-profiles: 'Android=Android-Staging,iOS=iOS-dev'` on unity-pipeline.yml
gives each build leg its platform's profile; the toolkit's PlayerBuilder
builds with it. Unlisted platforms and `ProjectSettings` build with the
project's Player Settings, as before. docs/TOOLKIT_BUILD_PACKAGE.md
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
HELPER = REPO_ROOT / "scripts" / "common" / "build_profile_for_platform.py"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
PIPELINE = WORKFLOWS / "unity-pipeline.yml"
REUSABLE = WORKFLOWS / "reusable-build-platform.yml"
PLAYER_BUILDER = (REPO_ROOT / "unity-package" / "Packages" / "com.company.build-pipeline"
                  / "Editor" / "Builders" / "PlayerBuilder.cs")
GUARD = "Guard — build profile needs PlayerBuilder"
TOOLKIT_METHOD = "Company.BuildPipeline.Editor.PlayerBuilder.Build"


def _helper(mapping, *args, platform=None):
    env = {"PATH": os.environ.get("PATH", ""), "BUILD_PROFILES": mapping}
    if platform is not None:
        env["BP_PLATFORM"] = platform
    return subprocess.run([sys.executable, str(HELPER), *args],
                          capture_output=True, text=True, env=env, timeout=30)


# ── build_profile_for_platform.py ───────────────────────────────────────────

@pytest.mark.parametrize("platform, expected", [
    ("Android", "Android-Staging"),
    ("iOS", "iOS-dev"),
    ("WebGL", ""),          # not listed → Player Settings
])
def test_each_platform_gets_its_own_profile(platform, expected):
    r = _helper("Android=Android-Staging,iOS=iOS-dev", platform=platform)
    assert r.returncode == 0, r.stderr
    assert r.stdout == expected


@pytest.mark.parametrize("value", ["ProjectSettings", "projectsettings", "none", "default", "", "  "])
def test_project_settings_means_no_profile(value):
    r = _helper(f"Android={value},iOS=iOS-dev", platform="Android")
    assert r.returncode == 0, r.stderr
    assert r.stdout == ""


def test_an_empty_mapping_changes_nothing():
    r = _helper("", platform="Android")
    assert (r.returncode, r.stdout) == (0, "")


def test_spaces_and_platform_aliases_are_accepted():
    mapping = " android = Android-Staging , ios=iOS-dev, windows=Win-Release "
    assert _helper(mapping, platform="Android").stdout == "Android-Staging"
    assert _helper(mapping, platform="iOS").stdout == "iOS-dev"
    assert _helper(mapping, platform="Windows64").stdout == "Win-Release"


def test_a_profile_path_is_passed_through():
    r = _helper("Android=Assets/Build/My Profile.asset", platform="Android")
    assert r.stdout == "Assets/Build/My Profile.asset"


@pytest.mark.parametrize("mapping, needle", [
    ("Android-Staging", "is not Platform=Profile"),
    ("Androidd=Android-Staging", "unknown platform 'Androidd'"),
    ("Android=A,Android=B", "lists Android twice"),
    ('Android=A"B', "must not contain quotes"),
    ("Android=A\\B", "must not contain quotes"),
])
def test_a_bad_mapping_fails_with_the_reason(mapping, needle):
    for args in ((), ("--validate",)):
        r = _helper(mapping, *args, platform="Android")
        assert r.returncode == 1
        assert "::error title=Invalid build-profiles::" in r.stderr
        assert needle in r.stderr


def test_validate_logs_the_mapping_and_prints_nothing():
    r = _helper("Android=Android-Staging,iOS=ProjectSettings", "--validate")
    assert r.returncode == 0
    assert r.stdout == ""
    assert "[build-profiles] Android: Android-Staging" in r.stderr
    assert "iOS" not in r.stderr


# ── Stage 01: the matrix rows ───────────────────────────────────────────────

def test_matrix_rows_carry_their_platforms_profile(resolve_matrix, monkeypatch):
    monkeypatch.setenv("BUILD_PROFILES", "Android=Android-Staging,iOS=iOS-dev")
    out = resolve_matrix(["Android", "iOS", "WebGL"])
    rows = {r["platform"]: r for r in out["build"]}
    assert rows["Android"]["build-profile"] == "Android-Staging"
    assert rows["iOS"]["build-profile"] == "iOS-dev"
    assert rows["WebGL"]["build-profile"] == ""


def test_without_build_profiles_every_row_uses_player_settings(resolve_matrix, monkeypatch):
    monkeypatch.delenv("BUILD_PROFILES", raising=False)
    out = resolve_matrix(["Android", "iOS"])
    assert {r.get("build-profile") for r in out["build"]} == {""}


def test_a_bad_mapping_fails_stage_01(resolve_matrix, monkeypatch):
    monkeypatch.setenv("BUILD_PROFILES", "Andriod=Android-Staging")
    with pytest.raises(AssertionError, match="unknown platform"):
        resolve_matrix(["Android"])


# ── Workflow wiring ─────────────────────────────────────────────────────────

def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _inputs(workflow):
    on = workflow.get("on", workflow.get(True))
    return on["workflow_call"]["inputs"]


def _reusable_steps():
    wf = _load(REUSABLE)
    return [s for job in wf["jobs"].values() for s in job.get("steps", [])]


def test_pipeline_input_defaults_to_player_settings():
    spec = _inputs(_load(PIPELINE))["build-profiles"]
    assert spec["type"] == "string"
    assert spec["default"] == ""
    assert spec["required"] is False


def test_pipeline_hands_each_leg_its_row_profile():
    wf = _load(PIPELINE)
    matrix = next(s for s in wf["jobs"]["resolve-config"]["steps"] if s.get("id") == "matrix")
    assert matrix["env"]["BUILD_PROFILES"] == "${{ inputs.build-profiles || '' }}"
    text = PIPELINE.read_text(encoding="utf-8")
    assert "build-profile:       ${{ matrix.build-profile || '' }}" in text


def test_reusable_input_defaults_to_player_settings():
    spec = _inputs(_load(REUSABLE))["build-profile"]
    assert (spec["type"], spec["default"]) == ("string", "")


def test_every_player_builder_lane_receives_build_profile():
    """Native Windows, native macOS and Docker-on-Windows run PlayerBuilder."""
    steps = [s for s in _reusable_steps()
             if "BUILD_PROFILE" in (s.get("env") or {})]
    assert len(steps) >= 3
    for step in steps:
        assert step["env"]["BUILD_PROFILE"] == "${{ inputs.build-profile }}"
    docker = next(s for s in _reusable_steps() if s.get("id") == "build-docker-windows")
    assert "-e BUILD_PROFILE" in docker["run"]


def test_the_guard_runs_before_any_player_build():
    steps = _reusable_steps()
    names = [s.get("name") for s in steps]
    guard = names.index(GUARD)
    assert guard > next(i for i, s in enumerate(steps) if s.get("id") == "build-package")
    for build_id in ("build-docker", "build-docker-windows"):
        assert guard < next(i for i, s in enumerate(steps) if s.get("id") == build_id)


def _run_guard(tmp_path, profile, engine="local", runner_os="Windows",
               custom="", package=TOOLKIT_METHOD):
    step = next(s for s in _reusable_steps() if s.get("name") == GUARD)
    env = {"PATH": os.environ.get("PATH", ""), "PROFILE": profile, "ENGINE": engine,
           "RUNNER_OS": runner_os, "CUSTOM_METHOD": custom, "PACKAGE_METHOD": package}
    script = tmp_path / "guard.sh"
    script.write_text(step["run"], encoding="utf-8")
    return subprocess.run(["bash", str(script)], capture_output=True, text=True, env=env, timeout=30)


def test_guard_passes_the_toolkit_builder(tmp_path):
    for engine, runner_os in (("local", "Windows"), ("local", "macOS"), ("docker", "Windows")):
        r = _run_guard(tmp_path, "Android-Staging", engine, runner_os)
        assert r.returncode == 0, (engine, runner_os, r.stdout)


def test_guard_rejects_the_gameci_lane(tmp_path):
    r = _run_guard(tmp_path, "Android-Staging", engine="docker", runner_os="Linux")
    assert r.returncode == 1
    assert "Build profile not supported on the GameCI lane" in r.stdout


def test_guard_rejects_a_project_player_builder(tmp_path):
    r = _run_guard(tmp_path, "Android-Staging", package="PlayerBuilder.Build")
    assert r.returncode == 1
    assert "own global PlayerBuilder" in r.stdout


def test_guard_rejects_a_custom_build_method(tmp_path):
    r = _run_guard(tmp_path, "Android-Staging", custom="MyBuild.Run")
    assert r.returncode == 1
    assert "UNITY_BUILD_METHOD" in r.stdout


def test_guard_lets_project_settings_through_anywhere(tmp_path):
    r = _run_guard(tmp_path, "ProjectSettings", engine="docker", runner_os="Linux",
                   package="PlayerBuilder.Build")
    assert r.returncode == 0


# ── PlayerBuilder contract ──────────────────────────────────────────────────

def _src():
    return PLAYER_BUILDER.read_text(encoding="utf-8")


def test_player_builder_reads_the_profile_contract():
    src = _src()
    assert '"BUILD_PROFILE"' in src and '"BUILD_PROFILE_DIR"' in src
    assert 'DefaultProfileDirectory = "Assets/Settings/Build Profiles"' in src
    assert "new BuildPlayerWithProfileOptions" in src


def test_profile_code_compiles_only_on_unity_6():
    src = _src()
    using = src.index("using UnityEditor.Build.Profile;")
    assert src.rfind("#if UNITY_6000_0_OR_NEWER", 0, using) > src.rfind("#endif", 0, using)


def test_the_profile_is_active_before_run_values_are_applied():
    """A profile that overrides Player Settings owns version and keystore, so
    they must be applied after it is activated."""
    src = _src()
    run = src[src.index("public static int Run()"):]
    assert run.index("ActivateProfile(") < run.index("BuildActiveTarget(target")
    body = src[src.index("private static int BuildActiveTarget"):]
    assert body.index("ApplyVersion(target)") < body.index("BuildPipeline.BuildPlayer(")


def test_project_settings_deactivates_a_leftover_profile():
    src = _src()
    activate = src[src.index("private static string ActivateProfile"):]
    activate = activate[:activate.index("\n        }\n")]
    # Not guarded by `profile != null`: null selects the platform settings.
    assert "BuildProfile.SetActiveBuildProfile(profile);" in activate
    assert "if (profile == null)" in activate
    assert activate.index("SetActiveBuildProfile(profile)") < activate.index("if (profile == null)")


def test_everything_is_restored_after_the_build():
    src = _src()
    run = src[src.index("public static int Run()"):]
    finally_block = run[run.index("finally"):]
    assert "RestoreProfile(previousProfile);" in finally_block
    assert "RestoreProfileFile(profilePath, profileBytes);" in finally_block
    # Bytes are captured before the profile is activated.
    assert run.index("File.ReadAllBytes(profilePath)") < run.index("ActivateProfile(")
