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
    assert 'echo "result-json=$(tr -d' in run


@pytest.mark.parametrize("job,results_dir", [("final-report", "pipeline-results"),
                                             ("notify-discord", "./platform-results")])
def test_report_jobs_fill_missing_results_before_reading(job, results_dir):
    steps = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))["jobs"][job]["steps"]
    names = [s.get("name") for s in steps]
    fill = steps[names.index("Fill missing platform results from build outputs")]
    assert fill["env"]["RESULTS_DIR"] == results_dir
    assert fill["env"]["RJ_Android"] == "${{ needs.build.outputs.result-json-Android }}"
    assert fill["env"]["RJ_Addressables"] == "${{ needs.build-addressables.outputs.result-json-Addressables }}"
    download = next(i for i, s in enumerate(steps) if "download-artifact" in str(s.get("uses", "")) and
                    "pipeline-result" in str(s.get("with", {}).get("pattern", "")))
    assert download < names.index("Fill missing platform results from build outputs")


def test_fill_writes_only_what_is_missing(tmp_path):
    steps = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))["jobs"]["final-report"]["steps"]
    run = next(s for s in steps if s.get("name") == "Fill missing platform results from build outputs")["run"]
    script = run.split("<<'PY'\n", 1)[1].rsplit("PY", 1)[0]
    (tmp_path / "r" / "art").mkdir(parents=True)
    (tmp_path / "r" / "art" / "build-iOS.json").write_text('{"platform":"iOS","result":"failure"}')
    env = dict(os.environ, RESULTS_DIR=str(tmp_path / "r"),
               RJ_Android='{"platform":"Android","result":"success"}',
               RJ_iOS='{"platform":"iOS","result":"success"}', RJ_WebGL="")
    subprocess.run([sys.executable, "-c", script], env=env, check=True)
    files = sorted(p.relative_to(tmp_path / "r").as_posix() for p in (tmp_path / "r").rglob("*.json"))
    assert files == ["art/build-iOS.json", "from-outputs/build-Android.json"]
    assert json.loads((tmp_path / "r" / "art" / "build-iOS.json").read_text())["result"] == "failure"
