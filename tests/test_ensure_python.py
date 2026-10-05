"""
scripts/common/ensure_python.sh — find or install Python >= 3.8 on a
self-hosted runner. Every candidate is run; a placeholder that exists on PATH
but does not run Python (the Windows Store stub) is rejected like any broken
interpreter. Fake executables stand in for the real ones.
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "common" / "ensure_python.sh"
BASE = "/usr/bin:/bin"

pytestmark = pytest.mark.skipif(os.name == "nt", reason="bash fakes need a POSIX shell")


def exe(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def fake_python(path, version):
    exe(path, f'echo "{version} {path}"\n')


def run(path, tmp_path, **env):
    full = {"PATH": path, "HOME": str(tmp_path), "GITHUB_PATH": str(tmp_path / "gp"),
            "GITHUB_OUTPUT": str(tmp_path / "go"), "ENSURE_PYTHON_NO_INSTALL": "1"}
    full.update(env)
    (tmp_path / "gp").write_text("")
    r = subprocess.run([shutil.which("bash"), str(SCRIPT)], env=full, capture_output=True, text=True)
    out = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
    return r, out


def isolated(tmp_path, *dirs):
    # Only our fakes plus coreutils; hide any real python on the CI host.
    core = tmp_path / "core"
    core.mkdir(exist_ok=True)
    for tool in ("bash", "sort", "head", "dirname", "uname", "mkdir", "chmod", "printf", "tr", "env", "cat"):
        src = next((Path(d) / tool for d in BASE.split(":") if (Path(d) / tool).exists()), None)
        if src and not (core / tool).exists():
            (core / tool).symlink_to(src)
    return ":".join([str(d) for d in dirs] + [str(core)])


def test_python3_on_path_is_used(tmp_path):
    fake_python(tmp_path / "a" / "python3", "3.12.4")
    r, out = run(isolated(tmp_path, tmp_path / "a"), tmp_path)
    assert r.returncode == 0, r.stderr
    assert out["python-version"] == "3.12.4" and out["python-source"] == "path"
    assert str(tmp_path / "a") in (tmp_path / "gp").read_text()


def test_store_placeholder_is_rejected_and_python_is_used(tmp_path):
    exe(tmp_path / "stub" / "python3", "exit 9009\n")
    fake_python(tmp_path / "real" / "python", "3.11.9")
    r, out = run(isolated(tmp_path, tmp_path / "stub", tmp_path / "real"), tmp_path)
    assert r.returncode == 0, r.stderr
    assert out["python-version"] == "3.11.9"


def test_too_old_is_rejected(tmp_path):
    fake_python(tmp_path / "old" / "python3", "3.6.15")
    r, _ = run(isolated(tmp_path, tmp_path / "old"), tmp_path)
    assert r.returncode == 3
    assert "rejected" in r.stderr and "winget install --id Python.Python.3.12" in r.stderr


def test_py_launcher_is_asked(tmp_path):
    target = tmp_path / "Py312" / "python"
    fake_python(target, "3.12.1")
    exe(tmp_path / "launcher" / "py", f'echo "{target}"\n')
    r, out = run(isolated(tmp_path, tmp_path / "launcher"), tmp_path)
    assert r.returncode == 0, r.stderr
    assert out["python-source"] == "py-launcher"


@pytest.mark.parametrize("workflow,anchor", [
    ("reusable-build-platform.yml", "Unity preflight (provision editor + modules)"),
    ("reusable-unity-tests.yml", "Unity preflight (provision editor)"),
])
def test_runs_before_the_preflight(workflow, anchor):
    jobs = yaml.safe_load((REPO_ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8"))["jobs"]
    steps = next(iter(jobs.values()))["steps"]
    names = [s.get("name") for s in steps]
    i = names.index("Ensure Python (self-hosted)")
    assert i < names.index(anchor)
    assert "ensure_python.sh" in steps[i]["run"]
