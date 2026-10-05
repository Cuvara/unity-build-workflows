"""
scripts/common/sync_runner_choices.py — the Runner dropdown follows the real runners.
"""

import json
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "common" / "sync_runner_choices.py"
sys.path.insert(0, str(SCRIPT.parent))
import sync_runner_choices as src  # noqa: E402

CALLER = """\
on:
  workflow_dispatch:
    inputs:
      runner:
        description: "RUNNER"
        required: false
        default: 'auto'
        type: choice
        options: ['auto'] # sync-runner-choices
jobs:
  build:
    uses: owner/toolkit/.github/workflows/unity-pipeline.yml@v6
    with:
      runner-labels: ${{ inputs.runner != 'auto' && '' || '' }} # sync-runner-choices
"""

MAC = {"id": 1, "name": "mac-mini", "os": "macOS", "status": "online", "busy": False,
       "labels": [{"name": "self-hosted"}, {"name": "macOS"}, {"name": "ARM64"}]}
WIN = {"id": 2, "name": "win-build", "os": "Windows", "status": "offline", "busy": False,
       "labels": [{"name": "self-hosted"}, {"name": "Windows"}, {"name": "X64"}]}


def sync(tmp_path, runners):
    wf = tmp_path / "10-build.yml"
    wf.write_text(CALLER, encoding="utf-8")
    rf = tmp_path / "runners.json"
    rf.write_text(json.dumps(runners), encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPT), "--runners", str(rf), "--workflow", str(wf)],
                       capture_output=True, text=True)
    return r, wf.read_text(encoding="utf-8")


def test_dropdown_lists_real_runner_names(tmp_path):
    r, text = sync(tmp_path, [WIN, MAC])
    assert r.returncode == 0, r.stderr
    doc = yaml.safe_load(text)
    on = doc.get(True, doc.get("on"))
    assert on["workflow_dispatch"]["inputs"]["runner"]["options"] == ["auto", "mac-mini", "win-build"]


def test_mapping_sends_each_name_to_its_labels(tmp_path):
    _, text = sync(tmp_path, [MAC, WIN])
    expr = yaml.safe_load(text)["jobs"]["build"]["with"]["runner-labels"]
    blob = expr.split("fromJSON('")[1].split("')")[0]
    mapping = json.loads(blob)
    assert mapping["mac-mini"] == ["self-hosted", "macOS", "ARM64"]
    assert mapping["win-build"] == ["self-hosted", "Windows", "X64"]
    assert "inputs.runner != 'auto'" in expr


def test_ambiguous_runner_gets_its_name_as_label_and_a_warning(tmp_path):
    mac2 = dict(MAC, id=3, name="mac-studio")
    r, text = sync(tmp_path, [MAC, mac2])
    assert "Runner needs its own label" in r.stderr
    blob = yaml.safe_load(text)["jobs"]["build"]["with"]["runner-labels"].split("fromJSON('")[1].split("')")[0]
    mapping = json.loads(blob)
    assert mapping["mac-mini"][-1] == "mac-mini" and mapping["mac-studio"][-1] == "mac-studio"


def test_no_change_reports_unchanged(tmp_path):
    wf = tmp_path / "10-build.yml"
    rf = tmp_path / "runners.json"
    rf.write_text(json.dumps([MAC]), encoding="utf-8")
    wf.write_text(CALLER, encoding="utf-8")
    subprocess.run([sys.executable, str(SCRIPT), "--runners", str(rf), "--workflow", str(wf)], check=True)
    out = tmp_path / "out"
    out.write_text("")
    subprocess.run([sys.executable, str(SCRIPT), "--runners", str(rf), "--workflow", str(wf)],
                   check=True, env={"GITHUB_OUTPUT": str(out), "PATH": ""})
    assert "changed=false" in out.read_text()


def test_workflow_without_markers_fails(tmp_path):
    wf = tmp_path / "x.yml"
    wf.write_text("on: push\n", encoding="utf-8")
    rf = tmp_path / "runners.json"
    rf.write_text("[]", encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPT), "--runners", str(rf), "--workflow", str(wf)],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "sync-runner-choices" in r.stderr


def test_no_runners_leaves_only_auto(tmp_path):
    _, text = sync(tmp_path, [])
    doc = yaml.safe_load(text)
    assert doc.get(True, doc.get("on"))["workflow_dispatch"]["inputs"]["runner"]["options"] == ["auto"]


def test_reusable_workflow_wiring():
    wf = yaml.safe_load((REPO_ROOT / ".github/workflows/sync-runner-choices.yml").read_text(encoding="utf-8"))
    on = wf.get(True, wf.get("on"))
    assert on["workflow_call"]["secrets"]["RUNNER_SYNC_TOKEN"]["required"] is True
    steps = wf["jobs"]["sync"]["steps"]
    run = "\n".join(str(s.get("run", "")) for s in steps)
    assert "orgs/${ORG}/actions/runners" in run and "repos/${REPO}/actions/runners" in run
    assert "sync_runner_choices.py" in run
    commit = next(s for s in steps if s.get("name") == "Commit")
    assert commit["if"] == "${{ steps.sync.outputs.changed == 'true' }}"
