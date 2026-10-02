"""
scripts/common/redact_log_secrets.py and its place in the build job.

Unity dumps the process environment into Editor.log when Gradle fails, so the
keystore password exported to the native build step reached the `-logs`
artifact. GitHub masks secrets on the console only.
"""

import os
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "common" / "redact_log_secrets.py"
BUILD_PLATFORM = REPO_ROOT / ".github" / "workflows" / "reusable-build-platform.yml"

PASSWORD = "s3cret-Keystore-Pass"


def redact(tmp_path, env, *paths):
    full_env = {"PATH": os.environ.get("PATH", "")}
    full_env.update(env)
    args = [sys.executable, str(SCRIPT)]
    for name in env:
        args += ["--env", name]
    return subprocess.run(args + [str(p) for p in paths], env=full_env,
                          capture_output=True, text=True, check=True)


def test_value_is_replaced_in_files_and_directories(tmp_path):
    log = tmp_path / "Editor.log"
    log.write_text(f"ANDROID_KEYSTORE_PASS = {PASSWORD}\nGradle failed\n", encoding="utf-8")
    nested = tmp_path / "Logs" / "sub" / "gradle.log"
    nested.parent.mkdir(parents=True)
    nested.write_text(f"-Pandroid.injected.signing.store.password={PASSWORD}", encoding="utf-8")

    result = redact(tmp_path, {"ANDROID_KEYSTORE_PASS": PASSWORD}, log, tmp_path / "Logs")

    assert PASSWORD not in log.read_text(encoding="utf-8")
    assert "ANDROID_KEYSTORE_PASS = ***" in log.read_text(encoding="utf-8")
    assert PASSWORD not in nested.read_text(encoding="utf-8")
    assert PASSWORD not in result.stdout, "the script must never print a secret"


def test_multiline_secret_is_redacted_line_by_line(tmp_path):
    key = "-----BEGIN KEY-----\nAAAABBBBCCCCDDDD\nEEEEFFFFGGGGHHHH\n-----END KEY-----"
    log = tmp_path / "Editor.log"
    log.write_text("partial dump: EEEEFFFFGGGGHHHH\n", encoding="utf-8")
    redact(tmp_path, {"SUBMODULE_SSH_KEY": key}, log)
    assert "EEEEFFFFGGGGHHHH" not in log.read_text(encoding="utf-8")


def test_short_and_unset_values_leave_the_log_alone(tmp_path):
    log = tmp_path / "Editor.log"
    text = "build 1 of 1 finished\n"
    log.write_text(text, encoding="utf-8")
    redact(tmp_path, {"ANDROID_KEY_PASS": "1", "UNITY_PASSWORD": ""}, log)
    assert log.read_text(encoding="utf-8") == text


def test_missing_paths_do_not_fail(tmp_path):
    redact(tmp_path, {"ANDROID_KEYSTORE_PASS": PASSWORD}, tmp_path / "nope.log")


def test_redaction_runs_before_logs_are_read_or_uploaded():
    steps = yaml.safe_load(BUILD_PLATFORM.read_text(encoding="utf-8"))["jobs"]["build"]["steps"]
    names = [s.get("name") for s in steps]
    redact_at = names.index("Redact secrets from logs")
    assert redact_at < names.index("Summarise the Unity log")
    assert redact_at < names.index("Upload logs artifact")
    step = steps[redact_at]
    assert step.get("if") == "${{ always() }}"
    for name in ("ANDROID_KEYSTORE_PASS", "ANDROID_KEY_PASS", "UNITY_PASSWORD"):
        assert step["env"][name] == "${{ secrets.%s }}" % name
        assert f"--env {name}" in step["run"]
