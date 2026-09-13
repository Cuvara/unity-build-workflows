"""The Unity log summariser."""
import json, os, subprocess, sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "common" / "summarise_unity_log.py"

LOG = """Initialize engine version: 6000.0.26f1
Assets/Scripts/Player.cs(42,17): error CS0103: The name 'foo' does not exist
Assets/Scripts/Player.cs(42,17): error CS0103: The name 'foo' does not exist
Assets/Scripts/Enemy.cs(10,5): warning CS0168: The variable 'e' is unused
[Error] Failed to load asset bundle 'characters'
NullReferenceException: Object reference not set to an instance of an object
Trying to reload asset from disk that is not stored on disk
[Warning] Shader Unsupported: 'Hidden/Foo'
Build completed with a result of 'Failed' in 12 seconds
Some/Path.cs(7,1): error CS0618: 'Old.Api' is obsolete
"""


def run(tmp_path, text=LOG, *args):
    log = tmp_path / "Editor.log"
    log.write_text(text)
    report = tmp_path / "r.json"
    proc = subprocess.run(
        ["python3", str(SCRIPT), str(log), "--platform", "Android",
         "--report", str(report), *args],
        capture_output=True, text=True)
    data = json.loads(report.read_text()) if report.exists() else {}
    return proc, data


def test_compiler_errors_become_inline_annotations(tmp_path):
    """`::error file=…,line=…` is what puts a diagnostic on the changed file
    rather than only in a log nobody opens."""
    proc, _ = run(tmp_path)
    assert "::error file=Assets/Scripts/Player.cs,line=42,col=17::CS0103:" in proc.stdout


def test_a_repeated_error_is_reported_once(tmp_path):
    """Unity repeats each compiler error once per assembly. Twelve copies of
    one typo is not twelve problems."""
    _, data = run(tmp_path)
    assert sum(1 for e in data["errors"] if e.get("code") == "CS0103") == 1


def test_unity_errors_and_exceptions_are_caught(tmp_path):
    _, data = run(tmp_path)
    messages = " ".join(e["message"] for e in data["errors"])
    assert "Failed to load asset bundle" in messages
    assert "NullReferenceException" in messages


def test_a_build_level_failure_is_caught(tmp_path):
    """The line that explains an exit code when the compiler was happy."""
    _, data = run(tmp_path)
    assert any("result of 'Failed'" in e["message"] for e in data["errors"])


def test_known_noise_is_not_reported(tmp_path):
    """Matching the word "error" alone trains people to ignore annotations."""
    _, data = run(tmp_path)
    blob = json.dumps(data)
    assert "Trying to reload asset" not in blob
    assert "Shader Unsupported" not in blob
    assert "CS0618" not in blob


def test_warnings_are_counted_separately(tmp_path):
    _, data = run(tmp_path)
    assert data["warningCount"] == 1
    assert data["errorCount"] == 4  # CS0103, asset bundle, NRE, build failed


def test_a_clean_log_says_so(tmp_path):
    proc, data = run(tmp_path, "Initialize engine version: 6000.0.26f1\nBuild succeeded\n")
    assert data["errorCount"] == 0 and data["warningCount"] == 0
    assert "No errors or warnings" in proc.stdout


def test_a_missing_log_never_fails_the_build(tmp_path):
    """A diagnostic tool that breaks the pipeline is worse than one that says
    nothing."""
    proc = subprocess.run(
        ["python3", str(SCRIPT), str(tmp_path / "nope.log")],
        capture_output=True, text=True)
    assert proc.returncode == 0
    assert "::warning::" in proc.stdout


def test_errors_present_still_exits_zero(tmp_path):
    """The build step already decided whether the run fails."""
    proc, _ = run(tmp_path)
    assert proc.returncode == 0


def test_annotation_volume_is_capped(tmp_path):
    """A wall of annotations is a wall people learn to scroll past."""
    text = "\n".join(
        f"A/F{i}.cs(1,1): warning CS0168: unused {i}" for i in range(50))
    proc, data = run(tmp_path, text)
    assert data["warningCount"] == 50
    assert proc.stdout.count("::warning file=") <= 10
    assert "and 40 more" in proc.stdout


def test_the_build_lane_runs_it():
    lane = (REPO_ROOT / ".github" / "workflows"
            / "reusable-build-platform.yml").read_text()
    assert "summarise_unity_log.py" in lane
    assert "always()" in lane.split("Summarise the Unity log")[1][:200]
