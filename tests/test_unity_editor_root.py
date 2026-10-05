"""UNITY_EDITOR_ROOT_WINDOWS / UNITY_EDITOR_ROOT_MACOS -> what Unity preflight reads.

scripts/common/unity_editor_root.sh is sourced by the preflight steps of the
build and test workflows. A repository variable reaches every runner, so each
variable must apply only on its own OS, and the folder may be either one editor
or a Hub-style root of editors.
"""
import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "common" / "unity_editor_root.sh"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def run(env):
    """Source the script under the workflow's shell options; return (exports, stderr)."""
    base = {k: v for k, v in os.environ.items()
            if k not in ("UNITY_EDITOR", "UNITY_PREFLIGHT_INSTALL_ROOT",
                         "UNITY_EDITOR_ROOT_WINDOWS", "UNITY_EDITOR_ROOT_MACOS", "RUNNER_OS")}
    base.update(env)
    proc = subprocess.run(
        ["bash", "-c", f'set -Eeuo pipefail; source "{SCRIPT}"; '
                       'printf "UNITY_EDITOR=%s\\nUNITY_PREFLIGHT_INSTALL_ROOT=%s\\n" '
                       '"${UNITY_EDITOR:-}" "${UNITY_PREFLIGHT_INSTALL_ROOT:-}"'],
        capture_output=True, text=True, env=base)
    assert proc.returncode == 0, proc.stderr
    out = dict(line.split("=", 1) for line in proc.stdout.splitlines())
    return out, proc.stderr


def test_no_variable_changes_nothing():
    out, err = run({"RUNNER_OS": "Windows"})
    assert out == {"UNITY_EDITOR": "", "UNITY_PREFLIGHT_INSTALL_ROOT": ""}
    assert err == ""


def test_windows_editors_root_becomes_the_install_root(tmp_path):
    (tmp_path / "6000.0.26f1" / "Editor").mkdir(parents=True)
    out, err = run({"RUNNER_OS": "Windows", "UNITY_EDITOR_ROOT_WINDOWS": str(tmp_path)})
    assert out["UNITY_PREFLIGHT_INSTALL_ROOT"] == str(tmp_path)
    assert out["UNITY_EDITOR"] == ""
    assert "searched first" in err


def test_windows_single_editor_folder_becomes_unity_editor(tmp_path):
    exe = tmp_path / "Editor" / "Unity.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    out, _ = run({"RUNNER_OS": "Windows", "UNITY_EDITOR_ROOT_WINDOWS": str(tmp_path) + "/"})
    assert out["UNITY_EDITOR"] == str(exe)
    assert out["UNITY_PREFLIGHT_INSTALL_ROOT"] == ""


def test_macos_single_editor_app(tmp_path):
    exe = tmp_path / "Unity.app" / "Contents" / "MacOS" / "Unity"
    exe.parent.mkdir(parents=True)
    exe.write_text("", encoding="utf-8")
    out, _ = run({"RUNNER_OS": "macOS", "UNITY_EDITOR_ROOT_MACOS": str(tmp_path)})
    assert out["UNITY_EDITOR"] == str(exe)


@pytest.mark.parametrize("runner_os,var", [("macOS", "UNITY_EDITOR_ROOT_WINDOWS"),
                                           ("Windows", "UNITY_EDITOR_ROOT_MACOS"),
                                           ("Linux", "UNITY_EDITOR_ROOT_WINDOWS")])
def test_a_variable_never_applies_on_another_os(runner_os, var, tmp_path):
    out, err = run({"RUNNER_OS": runner_os, var: "D:\\GameDev\\UnityEditor"})
    assert out == {"UNITY_EDITOR": "", "UNITY_PREFLIGHT_INSTALL_ROOT": ""}
    assert err == ""


def test_runner_unity_editor_wins(tmp_path):
    out, err = run({"RUNNER_OS": "Windows", "UNITY_EDITOR_ROOT_WINDOWS": str(tmp_path),
                    "UNITY_EDITOR": "C:\\machine\\Unity.exe"})
    assert out["UNITY_EDITOR"] == "C:\\machine\\Unity.exe"
    assert out["UNITY_PREFLIGHT_INSTALL_ROOT"] == ""
    assert "wins over" in err


def test_variable_overrides_runner_install_root_and_says_so(tmp_path):
    out, err = run({"RUNNER_OS": "Windows", "UNITY_EDITOR_ROOT_WINDOWS": str(tmp_path),
                    "UNITY_PREFLIGHT_INSTALL_ROOT": "C:\\runner\\editors"})
    assert out["UNITY_PREFLIGHT_INSTALL_ROOT"] == str(tmp_path)
    assert "Overriding the runner's UNITY_PREFLIGHT_INSTALL_ROOT" in err


def test_missing_folder_is_warned_not_fatal(tmp_path):
    missing = tmp_path / "nope"
    out, err = run({"RUNNER_OS": "Windows", "UNITY_EDITOR_ROOT_WINDOWS": str(missing)})
    assert out["UNITY_PREFLIGHT_INSTALL_ROOT"] == str(missing)
    assert "does not exist" in err


@pytest.mark.parametrize("workflow,job", [("reusable-build-platform.yml", "build"),
                                          ("reusable-unity-tests.yml", "test")])
def test_preflight_steps_pass_the_variables_and_source_the_helper(workflow, job):
    wf = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
    steps = next(iter(wf["jobs"].values()))["steps"]
    step = next(s for s in steps if s.get("id") == "unity-preflight")
    assert step["env"]["UNITY_EDITOR_ROOT_WINDOWS"] == "${{ vars.UNITY_EDITOR_ROOT_WINDOWS || '' }}"
    assert step["env"]["UNITY_EDITOR_ROOT_MACOS"] == "${{ vars.UNITY_EDITOR_ROOT_MACOS || '' }}"
    run_text = step["run"]
    assert "source .toolkit/scripts/common/unity_editor_root.sh" in run_text
    # Sourced before preflight runs, guarded for older toolkit checkouts.
    assert run_text.index("unity_editor_root.sh") < run_text.index("unity-preflight.sh")
    assert "if [[ -f .toolkit/scripts/common/unity_editor_root.sh ]]" in run_text
