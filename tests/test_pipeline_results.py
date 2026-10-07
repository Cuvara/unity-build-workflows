"""scripts/common/pipeline_results.py -- the report jobs' shared logic.

Final Report and Notify Discord each held inline Python over the same build
results, with one block copied verbatim into both. These run the script the
way the workflow does: a results directory, the step's env, $GITHUB_OUTPUT.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "common" / "pipeline_results.py"
PIPELINE = REPO_ROOT / ".github" / "workflows" / "unity-pipeline.yml"


def run(tmp_path, *args, **env):
    out = tmp_path / "github_output"
    summary = tmp_path / "summary.md"
    proc = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True,
                          env=dict(os.environ, GITHUB_OUTPUT=str(out),
                                   GITHUB_STEP_SUMMARY=str(summary), **env))
    outputs, block = {}, None
    for line in (out.read_text().splitlines() if out.exists() else []):
        if block is not None:            # inside a `key<<EOF` value
            if line == "EOF":
                block = None
            else:
                outputs[block] = (outputs[block] + "\n" + line).lstrip("\n")
        elif line.endswith("<<EOF"):
            block = line[:-len("<<EOF")]
            outputs[block] = ""
        elif "=" in line:
            key, value = line.split("=", 1)
            outputs[key] = value
    return proc, outputs, summary


def results(tmp_path, *rows):
    root = tmp_path / "results"
    root.mkdir(exist_ok=True)
    for row in rows:
        (root / f"build-{row['platform']}.json").write_text(json.dumps(row))
    return str(root)


def build(platform, result="success", **extra):
    row = {"platform": platform, "stage": "03", "result": result, "artifactType": "APK",
           "artifactName": f"Game_1.0_1_development_{platform.lower()}_apk",
           "artifactSizeBytes": "1000", "durationSeconds": "60"}
    row.update(extra)
    return row


GREEN = dict(R_RESOLVE="success", R_VALIDATE="success", R_LICENSE="skipped", R_TESTS="skipped",
             R_GATE="success", R_ADDR="skipped", R_BUILD="success", BUILD_TYPE="development")


# ── the workflow calls the script, not a copy of it ─────────────────────────

def test_no_report_job_carries_inline_python_any_more():
    jobs = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))["jobs"]
    for job in ("final-report", "notify-discord"):
        for step in jobs[job]["steps"]:
            assert "<<'PY'" not in str(step.get("run", "")), (job, step.get("name"))
    runs = "\n".join(str(s.get("run", "")) for j in ("final-report", "notify-discord")
                     for s in jobs[j]["steps"])
    for command in ("fill-missing", "final-report", "discord-status", "attachable", "diagnostics"):
        assert f"pipeline_results.py {command}" in runs, command


# ── final-report ─────────────────────────────────────────────────────────────

def test_a_green_run_reports_success(tmp_path):
    d = results(tmp_path, build("Android"))
    proc, out, summary = run(tmp_path, "final-report", "--results-dir", d,
                             BUILD_MATRIX='[{"platform":"Android"}]', **GREEN)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out["overall"] == "success" and out["failed-stage"] == ""
    assert json.loads(out["platform-summary"])[0]["build"] == "success"
    assert "Final Report" in summary.read_text(encoding="utf-8")


def test_a_failed_leg_names_its_stage_and_fails_the_step(tmp_path):
    d = results(tmp_path, build("Android", "failure"))
    env = dict(GREEN, R_BUILD="failure")
    proc, out, _ = run(tmp_path, "final-report", "--results-dir", d, BUILD_MATRIX="[]", **env)
    assert proc.returncode == 1
    assert out["failed-stage"] == "03 BUILD ARTIFACTS / Android (failure)"


def test_a_missing_result_on_a_green_matrix_is_unreported_not_a_failure(tmp_path):
    d = results(tmp_path)
    proc, out, _ = run(tmp_path, "final-report", "--results-dir", d,
                       BUILD_MATRIX='[{"platform":"Android"}]', **GREEN)
    assert proc.returncode == 0
    assert json.loads(out["platform-summary"])[0]["build"] == "unreported"


# ── discord-status ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("summary,expected", [
    ([{"platform": "Android", "build": "success"}], "success"),
    ([{"platform": "Android", "build": "success"}, {"platform": "iOS", "build": "failure"}], "partial"),
    ([{"platform": "iOS", "build": "failure"}], "failure"),
    ([{"platform": "iOS", "build": "cancelled"}], "cancelled"),
])
def test_discord_status_unpacks_the_summary(tmp_path, summary, expected):
    proc, out, _ = run(tmp_path, "discord-status", PLATFORM_SUMMARY=json.dumps(summary))
    assert proc.returncode == 0, proc.stderr
    assert out["overall"] == expected
    assert out["result-" + summary[0]["platform"].lower()] == summary[0]["build"]


# ── diagnostics ──────────────────────────────────────────────────────────────

def test_counts_are_reported_only_when_a_log_was_read(tmp_path):
    d = results(tmp_path,
                build("Android", errorCount="2", warningCount="7", logMeasured="true",
                      downloadUrl="https://testers", directDownloadUrl="https://direct"),
                build("WebGL", errorCount="0", warningCount="0", logMeasured="false"))
    proc, out, _ = run(tmp_path, "diagnostics", "--results-dir", d, "--build-type", "development")
    assert proc.returncode == 0, proc.stderr
    lines = dict(line.split("=", 1) for line in out["platform-diagnostics"].splitlines())
    assert lines["Android"] == "2,7,,,Game_1.0_1_development_android_apk,https://testers|https://direct"
    # The Docker lane writes no Editor.log: no count, rather than a 0 nobody measured.
    assert lines["WebGL"].startswith(",,")
    assert out["total-errors"] == "2" and out["total-warnings"] == "7"


def test_no_log_anywhere_leaves_the_totals_empty(tmp_path):
    d = results(tmp_path, build("Android", errorCount="0", logMeasured="false"))
    _, out, _ = run(tmp_path, "diagnostics", "--results-dir", d, "--build-type", "development")
    assert out["total-errors"] == "" and out["total-warnings"] == ""


def test_artifact_ids_come_from_the_run_artifact_map(tmp_path):
    d = results(tmp_path, build("Android", logMeasured="true", errorCount="0", warningCount="1"))
    amap = tmp_path / "map.txt"
    amap.write_text("Game_1.0_1_development_android_apk 111\n"
                    "Game_1.0_1_development_android_apk-logs 222\n"
                    "Game_1.0_1_development_android_apk-manifest 333\n")
    _, out, _ = run(tmp_path, "diagnostics", "--results-dir", d, "--build-type", "development",
                    "--artifact-map", str(amap))
    line = out["platform-diagnostics"].strip()
    assert line == "Android=0,1,222,111,Game_1.0_1_development_android_apk,"


def test_the_build_leg_records_whether_it_read_a_log():
    rbp = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "reusable-build-platform.yml")
                         .read_text(encoding="utf-8"))
    writer = next(s for s in rbp["jobs"]["build"]["steps"] if s.get("id") == "set-outputs")
    assert writer["env"]["LOG_FOUND"] == "${{ steps.unity-log.outputs.log-found }}"
    script = (REPO_ROOT / "scripts" / "build" / "write_build_result.py").read_text(encoding="utf-8")
    assert '"logMeasured": "true" if env("LOG_FOUND") == "true" else "false"' in script
    summariser = (REPO_ROOT / "scripts" / "common" / "summarise_unity_log.py").read_text(encoding="utf-8")
    assert "log-found=" in summariser
