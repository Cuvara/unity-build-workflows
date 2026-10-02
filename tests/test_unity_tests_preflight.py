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
    assert pre < names.index("Run tests (Windows)")
    assert pre < names.index("Run tests (macOS)")
    assert "unity-preflight.sh" in _steps()[pre]["run"]


def test_native_test_steps_use_the_preflight_editor():
    for name in ("Run tests (Windows)", "Run tests (macOS)"):
        step = next(s for s in _steps() if s.get("name") == name)
        assert step["env"]["UNITY_EDITOR"] == "${{ steps.unity-preflight.outputs.unity_editor }}", name
        assert "UNITY_EDITOR" in step["run"], name


def test_pipeline_passes_the_toolkit_repo_to_the_tests():
    job = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))["jobs"]["unity-tests"]
    assert job["with"]["toolkit-repo"] == "${{ inputs.toolkit-repo }}"
