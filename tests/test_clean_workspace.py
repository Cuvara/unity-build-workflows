"""
test_clean_workspace.py
=======================
Validates scripts/common/clean_workspace.sh — the workspace reset that replaces
actions/checkout's own `git clean -ffdx` in the build and tests jobs, so a
self-hosted runner keeps the project's Library/ between incremental builds.

Contract
--------
C1  incremental (CLEAN_BUILD unset/false): <project>/Library/ survives
C2  clean build (CLEAN_BUILD=true): <project>/Library/ is removed
C3  other untracked and ignored files are removed either way
C4  tracked files are reset to HEAD either way
C5  the toolkit checkout (.toolkit) survives either way
C6  only the project's Library/ is spared, not a Library/ folder elsewhere
C7  PROJECT_PATH '.' (project at the repository root) works
C8  both jobs check out with clean: false and run the script after the
    toolkit checkout, passing CLEAN_BUILD and PROJECT_PATH
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "common" / "clean_workspace.sh"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
JOBS = {
    "reusable-build-platform.yml": "build",
    "reusable-unity-tests.yml": "test",
}


@pytest.fixture(autouse=True)
def _require_git():
    if shutil.which("git") is None:
        pytest.skip("git not available")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo(tmp_path: Path, project: str = "Game") -> Path:
    repo = tmp_path / "repo"
    proj = repo / project if project != "." else repo
    (proj / "Assets").mkdir(parents=True)
    (proj / "Assets" / "a.cs").write_text("tracked\n")
    (repo / ".gitignore").write_text("Library/\n")
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    # What a previous run leaves behind.
    (proj / "Library").mkdir()
    (proj / "Library" / "ArtifactDB").write_text("db\n")
    (repo / "build").mkdir()
    (repo / "build" / "game.apk").write_text("apk\n")
    (repo / "junk.txt").write_text("junk\n")
    (repo / ".toolkit" / "scripts").mkdir(parents=True)
    (repo / ".toolkit" / "scripts" / "x.sh").write_text("toolkit\n")
    (repo / "Other" / "Library").mkdir(parents=True)
    (repo / "Other" / "Library" / "f").write_text("other\n")
    (proj / "Assets" / "a.cs").write_text("modified by the last build\n")
    return repo


def run_clean(repo: Path, project: str = "Game", clean_build=None):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(repo)),
        "PROJECT_PATH": project,
    }
    if clean_build is not None:
        env["CLEAN_BUILD"] = clean_build
    return subprocess.run(
        ["bash", str(SCRIPT)], cwd=repo, capture_output=True, text=True, timeout=30, env=env
    )


@pytest.mark.parametrize("clean_build", [None, "false"])
def test_incremental_keeps_library(tmp_path, clean_build):
    repo = _repo(tmp_path)
    r = run_clean(repo, clean_build=clean_build)
    assert r.returncode == 0, r.stderr
    assert (repo / "Game" / "Library" / "ArtifactDB").exists()  # C1
    assert not (repo / "build").exists()  # C3
    assert not (repo / "junk.txt").exists()  # C3
    assert (repo / "Game" / "Assets" / "a.cs").read_text() == "tracked\n"  # C4
    assert (repo / ".toolkit" / "scripts" / "x.sh").exists()  # C5
    assert not (repo / "Other" / "Library").exists()  # C6


def test_clean_build_removes_library(tmp_path):
    repo = _repo(tmp_path)
    r = run_clean(repo, clean_build="true")
    assert r.returncode == 0, r.stderr
    assert not (repo / "Game" / "Library").exists()  # C2
    assert not (repo / "build").exists()  # C3
    assert (repo / "Game" / "Assets" / "a.cs").read_text() == "tracked\n"  # C4
    assert (repo / ".toolkit" / "scripts" / "x.sh").exists()  # C5


@pytest.mark.parametrize("project", ["./Game", "Game/"])
def test_project_path_forms(tmp_path, project):
    repo = _repo(tmp_path)
    r = run_clean(repo, project=project)
    assert r.returncode == 0, r.stderr
    assert (repo / "Game" / "Library" / "ArtifactDB").exists()


def test_project_at_repository_root(tmp_path):
    repo = _repo(tmp_path, project=".")
    r = run_clean(repo, project=".")
    assert r.returncode == 0, r.stderr
    assert (repo / "Library" / "ArtifactDB").exists()  # C7
    assert not (repo / "Other" / "Library").exists()  # C6


def _steps(workflow: str):
    doc = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))
    return doc["jobs"][JOBS[workflow]]["steps"]


@pytest.mark.parametrize("workflow", sorted(JOBS))
def test_jobs_checkout_without_clean_and_run_the_script(workflow):
    steps = _steps(workflow)
    names = [s.get("name") for s in steps]
    checkout = steps[names.index("Checkout")]
    assert checkout["with"]["clean"] is False  # C8
    clean = steps[names.index("Clean workspace")]
    assert names.index("Checkout toolkit") < names.index("Clean workspace")  # C8
    assert "scripts/common/clean_workspace.sh" in clean["run"]
    assert clean["env"]["CLEAN_BUILD"] == "${{ inputs.clean-build }}"
    assert clean["env"]["PROJECT_PATH"] == "${{ inputs.project-path }}"
