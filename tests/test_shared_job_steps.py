"""Steps the build job and the tests job share.

reusable-build-platform.yml and reusable-unity-tests.yml both prepare a
Windows runner and fetch SSH submodules. The submodule fetch is one script
(scripts/common/fetch_submodules_ssh.sh); the Git Bash step cannot be one,
because it runs before any checkout, so the two copies are held identical
here instead -- a fix made to one and not the other is the drift this
catches.
"""
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
JOBS = (("reusable-build-platform.yml", "build"), ("reusable-unity-tests.yml", "test"))
SCRIPT = REPO_ROOT / "scripts" / "common" / "fetch_submodules_ssh.sh"


def _steps(name, job):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))["jobs"][job]["steps"]


def _named(steps, name):
    return next(s for s in steps if s.get("name") == name)


def test_the_git_bash_step_is_the_same_in_both_jobs():
    a, b = (_named(_steps(*j), "Use Git Bash and long paths (Windows)") for j in JOBS)
    assert a == b


@pytest.mark.parametrize("name,job", JOBS)
def test_the_git_bash_step_comes_first(name, job):
    assert _steps(name, job)[0]["name"] == "Use Git Bash and long paths (Windows)"


@pytest.mark.parametrize("name,job", JOBS)
def test_both_jobs_fetch_submodules_with_the_shared_script(name, job):
    steps = _steps(name, job)
    names = [s.get("name") for s in steps]
    fetch = _named(steps, "Fetch submodules over SSH")
    assert fetch["run"].strip() == "bash .toolkit/scripts/common/fetch_submodules_ssh.sh"
    assert fetch["env"]["SUBMODULE_SSH_KEY"] == "${{ secrets.SUBMODULE_SSH_KEY }}"
    # The toolkit comes after the project checkout (which cleans the
    # workspace) and before the fetch that runs its script.
    toolkit = names.index("Checkout toolkit")
    assert names.index("Checkout") < toolkit < names.index("Fetch submodules over SSH")
    assert steps[toolkit]["with"]["path"] == ".toolkit"
    assert steps[toolkit]["with"]["clean"] is False
    if job == "test":
        assert "inputs.submodule-auth == 'ssh'" in steps[toolkit]["if"], \
            "the tests job checks the toolkit out for the fetch too"


def test_the_fetch_script_keeps_its_safeguards():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "git -c core.longpaths=true submodule update --init --recursive --force" in text
    assert "trap cleanup EXIT" in text and 'chmod 600 "${_key_file}"' in text
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
