"""
The native test lanes find the editor the same way the build lanes do.

reusable-unity-tests.yml assumed Unity Hub's default install paths and failed
with "Unity executable not found" on a self-hosted Mac whose editors live in
~/Unity/Editors — while the build job, which runs Unity preflight, used the
same editor without trouble.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
TESTS_WF = REPO_ROOT / ".github" / "workflows" / "reusable-unity-tests.yml"
PIPELINE = REPO_ROOT / ".github" / "workflows" / "unity-pipeline.yml"


def _steps():
    return yaml.safe_load(TESTS_WF.read_text(encoding="utf-8"))["jobs"]["test"]["steps"]


def test_preflight_runs_before_the_native_test_steps():
    names = [s.get("name") for s in _steps()]
    pre = names.index("Unity preflight (provision editor)")
    assert pre < names.index("Run tests (self-hosted)")
    assert "unity-preflight.sh" in _steps()[pre]["run"]


def test_native_test_steps_use_the_preflight_editor():
    step = next(s for s in _steps() if s.get("name") == "Run tests (self-hosted)")
    assert step["env"]["UNITY_EDITOR"] == "${{ steps.unity-preflight.outputs.unity_editor }}"
    assert '--editor "${UNITY_EDITOR}"' in step["run"]
    # One step and one script for both native lanes, as for the build.
    assert "run_unity_player.sh --tests" in step["run"]
    assert step["shell"] == "bash"
    assert "runner.os == 'Windows' || runner.os == 'macOS'" in step["if"]


def test_pipeline_passes_the_toolkit_repo_to_the_tests():
    job = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))["jobs"]["unity-tests"]
    assert job["with"]["toolkit-repo"] == "${{ inputs.toolkit-repo }}"
