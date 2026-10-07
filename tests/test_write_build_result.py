"""scripts/build/write_build_result.py -- the build leg's verdict.

It replaced ~100 lines of bash in reusable-build-platform.yml's "Set job
outputs" that built JSON with printf (values unescaped) and spliced the Unity
log's first error into the script through `${{ }}`, where an error message
holding `$(...)` was run by the shell.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "build" / "write_build_result.py"
REUSABLE = REPO_ROOT / ".github" / "workflows" / "reusable-build-platform.yml"


def write(tmp_path, **env):
    out = tmp_path / "github_output"
    base = {k: v for k, v in os.environ.items() if not k.startswith(("OUTCOME_", "ARTIFACT_"))}
    base.update(GITHUB_OUTPUT=str(out), GITHUB_SERVER_URL="https://github.com",
                GITHUB_REPOSITORY="org/game", GITHUB_RUN_ID="42", PLATFORM="Android")
    base.update(env)
    proc = subprocess.run([sys.executable, str(SCRIPT), "--results-dir", str(tmp_path / "r")],
                          capture_output=True, text=True, env=base)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines())
    row = json.loads((tmp_path / "r" / f"build-{base['PLATFORM']}.json").read_text())
    return row, outputs


def test_a_successful_build_names_its_artifact(tmp_path):
    row, out = write(tmp_path, OUTCOME_MACOS="success", ARTIFACT_NAME="Game_development_android_apk",
                     ARTIFACT_TYPE="APK", ARTIFACT_SIZE_BYTES="1000", ARTIFACT_ID="7",
                     DOWNLOAD_URL="https://testers", DIRECT_URL="https://direct", LOG_FOUND="true",
                     ERROR_COUNT="0", WARNING_COUNT="3", CONFIGURATION="Development")
    assert row["result"] == "success" and row["artifactName"] == "Game_development_android_apk"
    assert row["artifactUrl"] == "https://github.com/org/game/actions/runs/42/artifacts/7"
    assert row["logMeasured"] == "true" and row["warningCount"] == "3"
    assert out["result"] == "success"
    assert out["manifest-artifact-name"] == "Game_development_android_apk-manifest"
    assert out["artifact-url"] == row["artifactUrl"]
    assert json.loads(bytes.fromhex(out["result-json"]).decode()) == row


def test_the_field_order_is_the_one_the_report_reads(tmp_path):
    row, _ = write(tmp_path, OUTCOME_DOCKER="success")
    assert list(row) == ["platform", "stage", "result", "artifactType", "artifactName",
                         "artifactSizeBytes", "durationSeconds", "configuration", "errorCount",
                         "warningCount", "firstError", "artifactUrl", "downloadUrl",
                         "directDownloadUrl", "logMeasured"]


@pytest.mark.parametrize("outcomes,expected", [
    ({"OUTCOME_WINDOWS": "failure"}, "failure"),
    ({"OUTCOME_DOCKER": "skipped", "OUTCOME_MACOS": "cancelled"}, "cancelled"),
    ({}, "skipped"),
])
def test_the_step_that_ran_decides(tmp_path, outcomes, expected):
    row, out = write(tmp_path, ARTIFACT_NAME="x", **outcomes)
    assert row["result"] == expected and row["artifactName"] == ""
    assert out["manifest-artifact-name"] == "x-manifest", "stage 07 names what was being built"


def test_a_blocked_leg_has_no_manifest(tmp_path):
    row, out = write(tmp_path, BLOCKED="true", OUTCOME_MACOS="success", ARTIFACT_NAME="x")
    assert row["result"] == "blocked" and out["manifest-artifact-name"] == ""


def test_an_error_message_is_data_not_shell(tmp_path):
    message = 'Assets/A.cs(1,2): error CS0103: "$(touch pwned)" `id` \\ done'
    row, _ = write(tmp_path, OUTCOME_MACOS="failure", FIRST_ERROR=message)
    assert row["firstError"] == message
    assert not (tmp_path / "pwned").exists()
    step = next(s for s in yaml.safe_load(REUSABLE.read_text(encoding="utf-8"))["jobs"]["build"]["steps"]
                if s.get("id") == "set-outputs")
    assert "${{" not in step["run"], "every value reaches the script through env"
    assert step["env"]["FIRST_ERROR"] == "${{ steps.unity-log.outputs.first-error }}"


def test_no_artifact_id_means_no_artifact_url(tmp_path):
    row, out = write(tmp_path, OUTCOME_MACOS="success", ARTIFACT_NAME="x")
    assert row["artifactUrl"] == "" and "artifact-url" not in out


def test_without_the_toolkit_a_blocked_leg_still_reports(tmp_path):
    """The iOS guard skips the toolkit checkout too; stage 07 must still see
    `blocked`, not a dead leg."""
    step = next(s for s in yaml.safe_load(REUSABLE.read_text(encoding="utf-8"))["jobs"]["build"]["steps"]
                if s.get("id") == "set-outputs")
    out = tmp_path / "github_output"
    env = dict(os.environ, GITHUB_OUTPUT=str(out), PLATFORM="iOS", BLOCKED="true")
    proc = subprocess.run(["bash", "-c", step["run"]], cwd=tmp_path, capture_output=True,
                          text=True, env=env)
    assert proc.returncode == 0, proc.stderr
    outputs = dict(line.split("=", 1) for line in out.read_text().splitlines())
    assert outputs["result"] == "blocked"
    row = json.loads(bytes.fromhex(outputs["result-json"]).decode())
    assert row == {"platform": "iOS", "stage": "03", "result": "blocked"}
