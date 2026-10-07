"""Platform results reach the report even when the artifact upload fails.

A full artifact storage quota made the pipeline-result-build-<Platform>
artifact fail to upload; the report then called a green Android build
"unreported" and Discord lost its download links. Each leg now also returns
the JSON as its own result-json-<Platform> output, and the report jobs fill
in what the artifacts did not bring."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
REUSABLE = REPO_ROOT / ".github" / "workflows" / "reusable-build-platform.yml"
PIPELINE = REPO_ROOT / ".github" / "workflows" / "unity-pipeline.yml"
RESULTS = REPO_ROOT / "scripts" / "common" / "pipeline_results.py"
PLATFORMS = ["Android", "WebGL", "Linux64", "LinuxServer", "Windows64", "iOS", "Addressables"]


def _on(doc):
    return doc.get("on", doc.get(True))


def test_each_platform_has_its_own_output_key():
    doc = yaml.safe_load(REUSABLE.read_text(encoding="utf-8"))
    job_outputs = doc["jobs"]["build"]["outputs"]
    wf_outputs = _on(doc)["workflow_call"]["outputs"]
    for p in PLATFORMS:
        assert job_outputs[f"result-json-{p}"] == (
            "${{ inputs.platform == '%s'%s && steps.set-outputs.outputs.result-json || '' }}"
            % (p, " " * (12 - len(p))))
        assert wf_outputs[f"result-json-{p}"]["value"] == "${{ jobs.build.outputs.result-json-%s }}" % p
    run = next(s for s in doc["jobs"]["build"]["steps"] if s.get("id") == "set-outputs")["run"]
    # Hex: a multi-line secret masks "{" and "}", and an output with a brace
    # is dropped as "may contain secret".
    assert 'echo "result-json=$(od -An -v -tx1' in run


@pytest.mark.parametrize("job,results_dir", [("final-report", "pipeline-results"),
                                             ("notify-discord", "./platform-results")])
def test_report_jobs_fill_missing_results_before_reading(job, results_dir):
    steps = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))["jobs"][job]["steps"]
    names = [s.get("name") for s in steps]
    fill = steps[names.index("Fill missing platform results from build outputs")]
    assert fill["run"] == ("python3 .toolkit/scripts/common/pipeline_results.py fill-missing "
                           "--results-dir %s" % results_dir)
    # The script comes from the toolkit checkout, which must come first.
    checkout = next(i for i, s in enumerate(steps) if str(s.get("name", "")).startswith("Checkout toolkit"))
    assert checkout < names.index("Fill missing platform results from build outputs")
    assert fill["env"]["RJ_Android"] == "${{ needs.build.outputs.result-json-Android }}"
    assert fill["env"]["RJ_Addressables"] == "${{ needs.build-addressables.outputs.result-json-Addressables }}"
    download = next(i for i, s in enumerate(steps) if "download-artifact" in str(s.get("uses", "")) and
                    "pipeline-result" in str(s.get("with", {}).get("pattern", "")))
    assert download < names.index("Fill missing platform results from build outputs")


def test_fill_writes_only_what_is_missing(tmp_path):
    (tmp_path / "r" / "art").mkdir(parents=True)
    (tmp_path / "r" / "art" / "build-iOS.json").write_text('{"platform":"iOS","result":"failure"}')
    env = dict(os.environ,
               RJ_Android=b'{"platform":"Android","result":"success"}'.hex(),
               RJ_iOS='{"platform":"iOS","result":"success"}', RJ_WebGL="")
    subprocess.run([sys.executable, str(RESULTS), "fill-missing", "--results-dir", str(tmp_path / "r")],
                   env=env, check=True)
    files = sorted(p.relative_to(tmp_path / "r").as_posix() for p in (tmp_path / "r").rglob("*.json"))
    assert files == ["art/build-iOS.json", "from-outputs/build-Android.json"]
    assert json.loads((tmp_path / "r" / "art" / "build-iOS.json").read_text())["result"] == "failure"


# ── Notify Discord downloads only what it can attach ──────────────────────

def _discord_steps():
    return yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))["jobs"]["notify-discord"]["steps"]


def _pick(tmp_path, rows):
    (tmp_path / "r").mkdir()
    for i, row in enumerate(rows):
        (tmp_path / "r" / f"build-{i}.json").write_text(json.dumps(row))
    out = tmp_path / "out"
    env = dict(os.environ, GITHUB_OUTPUT=str(out))
    subprocess.run([sys.executable, str(RESULTS), "attachable", "--results-dir", str(tmp_path / "r"),
                    "--max-bytes", "8388608"], env=env, check=True)
    return out.read_text().strip().split("=", 1)[1]


def test_discord_never_downloads_every_artifact_of_the_run():
    steps = _discord_steps()
    downloads = [s for s in steps if "download-artifact" in str(s.get("uses", ""))]
    patterns = [s["with"]["pattern"] for s in downloads]
    assert patterns == ["pipeline-result-build-*", "${{ steps.attachable.outputs.pattern }}"]
    attach = downloads[1]
    assert "steps.attachable.outputs.pattern != ''" in attach["if"], \
        "an empty pattern downloads everything"
    names = [s.get("name") for s in steps]
    assert names.index("Fill missing platform results from build outputs") \
        < names.index("Pick the builds small enough to attach")
    call = next(s for s in steps if "discord-upload-build" in str(s.get("uses", "")))
    threshold = int(call["with"]["attach-size-threshold-mb"]) * 1024 * 1024
    pick = next(s for s in steps if s.get("id") == "attachable")
    assert pick["run"].endswith("--max-bytes %d" % threshold)


def test_only_small_successful_builds_are_picked(tmp_path):
    rows = [
        {"platform": "Android", "result": "success", "artifactName": "development-android-apk",
         "artifactSizeBytes": "5000000"},
        {"platform": "WebGL", "result": "success", "artifactName": "development-webgl",
         "artifactSizeBytes": "90000000"},
        {"platform": "iOS", "result": "failure", "artifactName": "", "artifactSizeBytes": "100"},
    ]
    assert _pick(tmp_path, rows) == "development-android-apk"


def test_several_small_builds_become_one_brace_pattern(tmp_path):
    rows = [{"result": "success", "artifactName": n, "artifactSizeBytes": "10"}
            for n in ("b-linux", "a-android")]
    assert _pick(tmp_path, rows) == "{a-android,b-linux}"


def test_nothing_small_means_no_pattern(tmp_path):
    rows = [{"result": "success", "artifactName": "big", "artifactSizeBytes": "999999999"},
            {"result": "success", "artifactName": "unmeasured", "artifactSizeBytes": ""}]
    assert _pick(tmp_path, rows) == ""
