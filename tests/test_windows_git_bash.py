"""
Windows jobs put Git Bash first on PATH before any `shell: bash` step.

On a self-hosted Windows runner `bash` resolved to C:\WINDOWS\system32\bash.exe
(the WSL launcher), and every bash step failed with "Windows Subsystem for
Linux has no installed distributions".
"""

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).parent.parent / ".github" / "workflows"


@pytest.mark.parametrize("workflow,job", [
    ("reusable-build-platform.yml", "build"),
    ("reusable-unity-tests.yml", "test"),
])
def test_git_bash_step_runs_before_any_bash_step(workflow, job):
    steps = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))["jobs"][job]["steps"]
    first = steps[0]
    assert first["name"] == "Use Git Bash and long paths (Windows)"
    assert first["if"] == "${{ runner.os == 'Windows' }}"
    assert first["shell"] == "powershell", "it cannot itself depend on bash"
    assert "--exec-path" in first["run"] and "GITHUB_PATH" in first["run"]
    assert "--version" in first["run"], "Git Bash is run before it is trusted"
    assert "core.longpaths true" in first["run"], "checkout must not hit MAX_PATH"
