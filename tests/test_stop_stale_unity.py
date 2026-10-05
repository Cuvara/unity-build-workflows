"""stop_stale_unity.py: a Unity left running by a cancelled job must not block
the next job on a self-hosted runner, and nothing else may be touched."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "common" / "stop_stale_unity.py"
REUSABLE = REPO_ROOT / ".github" / "workflows" / "reusable-build-platform.yml"

sys.path.insert(0, str(SCRIPT.parent))
import stop_stale_unity as ssu  # noqa: E402


@pytest.mark.parametrize("command,expected", [
    ('/Apps/Unity.app/Contents/MacOS/Unity -batchmode -projectPath Proj -quit', "Proj"),
    ('"C:\\U\\Unity.exe" -projectPath "C:\\w\\My Proj" -quit', "C:\\w\\My Proj"),
    ("Unity -projectpath '/w/p' -quit", "/w/p"),
    ("Unity -batchmode -quit", ""),
])
def test_project_path_of(command, expected):
    assert ssu.project_path_of(command) == expected


@pytest.mark.skipif(os.name == "nt", reason="spawns a POSIX stand-in for the editor")
def test_stops_only_the_unity_holding_this_project(tmp_path):
    fake = tmp_path / "bin" / "Unity"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\nsleep 120\n")
    fake.chmod(0o755)
    workspace = tmp_path / "ws"
    (workspace / "Proj" / "Temp").mkdir(parents=True)
    (workspace / "Other").mkdir()
    lock = workspace / "Proj" / "Temp" / "UnityLockfile"
    lock.write_text("")

    ours = subprocess.Popen([str(fake), "-batchmode", "-projectPath", "Proj"], cwd=workspace)
    other = subprocess.Popen([str(fake), "-batchmode", "-projectPath", str(workspace / "Other")], cwd=workspace)
    try:
        time.sleep(0.5)
        r = subprocess.run([sys.executable, str(SCRIPT), "--project", "Proj"],
                           cwd=workspace, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        assert ours.wait(timeout=20) is not None, r.stderr
        assert other.poll() is None, "an editor holding another project must be left alone"
        assert not lock.exists()
    finally:
        for p in (ours, other):
            if p.poll() is None:
                p.kill()
                p.wait()


@pytest.mark.skipif(os.name == "nt", reason="spawns a POSIX stand-in for the editor")
def test_nothing_to_stop_is_a_no_op(tmp_path):
    (tmp_path / "Proj").mkdir()
    r = subprocess.run([sys.executable, str(SCRIPT), "--project", "Proj"],
                       cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    assert "No Unity process has" in r.stderr


def test_wired_before_the_first_native_unity_step_and_after_the_build():
    steps = yaml.safe_load(REUSABLE.read_text(encoding="utf-8"))["jobs"]["build"]["steps"]
    index = {s.get("id") or s.get("name"): i for i, s in enumerate(steps)}
    stop = steps[index["stop-stale-unity"]]
    assert "stop_stale_unity.py" in stop["run"]
    assert "inputs.build-engine == 'local'" in stop["if"]
    assert index["unity-preflight"] < index["stop-stale-unity"] < min(
        index["addressables-pre-selfhosted"], index["build-windows"], index["build-macos"])
    after = steps[index["Stop Unity left by this job"]]
    assert after["if"].startswith("${{ always() && ")
    assert index["Stop Unity left by this job"] > index["build-macos"]
