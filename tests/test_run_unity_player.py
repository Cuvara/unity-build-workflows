"""scripts/build/run_unity_player.sh -- the one Unity command line of the
native lanes (self-hosted macOS / Windows, the Addressables pre-step, and the
Windows docker lane's container)."""
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "build" / "run_unity_player.sh"

pytestmark = pytest.mark.skipif(os.name == "nt", reason="runs a POSIX stand-in for the editor")


@pytest.fixture
def editor(tmp_path):
    """A stand-in Unity that records its argv and the PlayerBuilder env."""
    record = tmp_path / "record"
    fake = tmp_path / "Unity"
    fake.write_text(
        "#!/bin/sh\n"
        f'printf "%s\n" "$@" > "{record}.argv"\n'
        f'printf "BUILD_OUTPUT_DIR=%s\nANDROID_APP_BUNDLE=%s\nBUILD_NUMBER=%s\n" '
        f'"$BUILD_OUTPUT_DIR" "${{ANDROID_APP_BUNDLE:-}}" "$BUILD_NUMBER" > "{record}.env"\n'
        'exit "${FAKE_EXIT:-0}"\n')
    fake.chmod(0o755)
    return fake, record


def run(editor, *args, **env):
    fake, record = editor
    full = dict(os.environ, UNITY_EDITOR=str(fake), **env)
    r = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=full)
    argv = Path(f"{record}.argv").read_text().splitlines() if Path(f"{record}.argv").exists() else []
    envs = dict(l.split("=", 1) for l in Path(f"{record}.env").read_text().splitlines()) \
        if Path(f"{record}.env").exists() else {}
    return r, argv, envs


@pytest.mark.parametrize("platform,target", [
    ("Android", "Android"), ("WebGL", "WebGL"), ("Windows64", "StandaloneWindows64"),
    ("Linux64", "StandaloneLinux64"), ("iOS", "iOS"),
])
def test_maps_the_platform_to_its_build_target(editor, platform, target):
    r, argv, _ = run(editor, "--platform", platform, "--project", "proj", "--host-os", "darwin")
    assert r.returncode == 0, r.stdout + r.stderr
    assert argv[argv.index("-buildTarget") + 1] == target
    assert argv[argv.index("-projectPath") + 1] == "proj"


def test_linux_server_adds_the_subtarget(editor):
    _, argv, _ = run(editor, "--platform", "LinuxServer", "--project", "p")
    assert argv[-2:] == ["-standaloneBuildSubtarget", "Server"]


def test_build_method_precedence(editor):
    _, argv, _ = run(editor, "--platform", "Android", "--project", "p",
                     "--build-method", "", "--player-method", "Toolkit.PlayerBuilder.Build")
    assert argv[argv.index("-executeMethod") + 1] == "Toolkit.PlayerBuilder.Build"
    _, argv, _ = run(editor, "--platform", "Android", "--project", "p",
                     "--build-method", "Mine.Build", "--player-method", "Toolkit.PlayerBuilder.Build")
    assert argv[argv.index("-executeMethod") + 1] == "Mine.Build"
    _, argv, _ = run(editor, "--platform", "Android", "--project", "p")
    assert argv[argv.index("-executeMethod") + 1] == "PlayerBuilder.Build"


def test_player_builder_contract_env(editor):
    _, _, envs = run(editor, "--platform", "Android", "--project", "p",
                     "--android-export-type", "aab", BUILD_NUMBER="467")
    assert envs == {"BUILD_OUTPUT_DIR": "build", "ANDROID_APP_BUNDLE": "1", "BUILD_NUMBER": "467"}
    _, _, envs = run(editor, "--platform", "Android", "--project", "p",
                     "--android-export-type", "apk", ANDROID_APP_BUNDLE="1")
    assert envs["ANDROID_APP_BUNDLE"] == "", "an inherited flag must not turn an APK build into an AAB"


def test_addressables_only(editor):
    _, argv, _ = run(editor, "--addressables-only", "--platform", "Android", "--project", "p",
                     "--addressables-method", "Toolkit.AddressableBuilder.Build", "--log-file", "pre.log")
    assert "-buildTarget" not in argv
    assert argv[argv.index("-executeMethod") + 1] == "Toolkit.AddressableBuilder.Build"
    assert argv[argv.index("-logFile") + 1] == "pre.log"
    _, argv, _ = run(editor, "--platform", "Addressables", "--project", "p")
    assert argv[argv.index("-executeMethod") + 1] == "AddressableBuilder.Build"


def test_unity_exit_code_is_the_scripts(editor):
    r, _, _ = run(editor, "--platform", "Android", "--project", "p", FAKE_EXIT="1")
    assert r.returncode == 1


@pytest.mark.parametrize("args,message", [
    (["--platform", "iOS", "--project", "p", "--host-os", "windows"], "macOS runner"),
    (["--platform", "Switch", "--project", "p"], "Unsupported platform"),
])
def test_lane_errors_are_annotated(editor, args, message):
    r, argv, _ = run(editor, *args)
    assert r.returncode == 2 and argv == []
    assert "::error::" in r.stdout and message in r.stdout


def test_missing_editor_is_reported(tmp_path):
    r = subprocess.run(["bash", str(SCRIPT), "--platform", "Android", "--project", "p"],
                       capture_output=True, text=True, env={**os.environ, "UNITY_EDITOR": ""})
    assert r.returncode == 2
    assert "Unity preflight did not provide an editor executable" in r.stdout


# ── --tests: the native test lanes run the same script ─────────────────────

@pytest.fixture
def test_editor(tmp_path):
    """A stand-in Unity that appends one argv line per run, and fails on PlayMode."""
    calls = tmp_path / "calls"
    fake = tmp_path / "UnityTests"
    fake.write_text(
        "#!/bin/sh\n"
        f'echo "$*" >> "{calls}"\n'
        'case "$*" in *PlayMode*) exit 2 ;; esac\n'
        "exit 0\n")
    fake.chmod(0o755)
    return fake, calls


def test_all_runs_editmode_then_playmode_and_never_fails_the_step(test_editor, tmp_path):
    fake, calls = test_editor
    r = subprocess.run(["bash", str(SCRIPT), "--tests", "All", "--project", "proj",
                        "--editor", str(fake), "--results-dir", str(tmp_path / "res")],
                       capture_output=True, text=True, cwd=tmp_path)
    # The verdict is the parsed results.xml; Unity's exit code is a warning.
    assert r.returncode == 0, r.stdout + r.stderr
    assert "::warning::PlayMode tests exited with code 2" in r.stdout
    lines = calls.read_text().splitlines()
    assert [l.split("-testPlatform ")[1].split()[0] for l in lines] == ["EditMode", "PlayMode"]
    assert f"-testResults {tmp_path / 'res'}/EditMode/results.xml" in lines[0]
    assert "-runTests" in lines[0] and "-quit" not in lines[0]
    assert (tmp_path / "res" / "PlayMode").is_dir()


def test_a_relative_results_dir_is_made_absolute(test_editor, tmp_path):
    fake, calls = test_editor
    subprocess.run(["bash", str(SCRIPT), "--tests", "EditMode", "--project", "p",
                    "--editor", str(fake)], capture_output=True, text=True, cwd=tmp_path)
    assert f"-testResults {tmp_path}/test-results/EditMode/results.xml" in calls.read_text()


def test_an_unknown_test_mode_is_a_usage_error(test_editor, tmp_path):
    fake, _ = test_editor
    r = subprocess.run(["bash", str(SCRIPT), "--tests", "Everything", "--project", "p",
                        "--editor", str(fake)], capture_output=True, text=True, cwd=tmp_path)
    assert r.returncode == 2 and "--tests takes EditMode, PlayMode or All" in r.stdout
