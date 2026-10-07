"""Release pipeline wiring that used to make promotions impossible.

- iOS production read a `build-id` output no step ever set, and called
  ios-asc-submit-review with inputs it does not declare while omitting the
  required bundle-id / build-number.
- Android and iOS production verified `--artifact-path .`: sha256 of the whole
  workspace, which can never match the Release Set, so production always failed.
"""
import io
import plistlib
import subprocess
import sys
import zipfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
ACTIONS = REPO_ROOT / ".github" / "actions"
IPA_IDENTITY = REPO_ROOT / "scripts" / "ios" / "ipa_identity.py"


def _jobs(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))["jobs"]


def _uses_inputs(step):
    return set((step.get("with") or {}).keys())


def test_ios_submit_passes_exactly_the_inputs_the_action_declares():
    action = yaml.safe_load((ACTIONS / "ios-asc-submit-review" / "action.yml").read_text(encoding="utf-8"))
    declared = set(action["inputs"])
    required = {k for k, v in action["inputs"].items() if v.get("required")}
    submit = next(s for s in _jobs("pipeline-ios-release.yml")["production-release"]["steps"]
                  if "ios-asc-submit-review" in str(s.get("uses", "")))
    passed = _uses_inputs(submit)
    assert passed <= declared, f"undeclared inputs: {passed - declared}"
    assert required <= passed, f"missing required inputs: {required - passed}"
    assert submit["with"]["bundle-id"] == "${{ steps.ipa.outputs.bundle-id }}"
    assert submit["with"]["build-number"] == "${{ steps.ipa.outputs.build-number }}"


def test_no_job_reads_an_output_the_testflight_action_does_not_set():
    text = (WORKFLOWS / "pipeline-ios-release.yml").read_text(encoding="utf-8")
    assert "build-id" not in text


def test_no_release_pipeline_hashes_the_whole_workspace():
    for path in sorted(WORKFLOWS.glob("pipeline-*-release.yml")):
        assert "--artifact-path        . " not in path.read_text(encoding="utf-8"), path.name


def test_ios_production_verifies_the_downloaded_ipa():
    steps = _jobs("pipeline-ios-release.yml")["production-release"]["steps"]
    names = [s.get("name") for s in steps]
    download = names.index("Download IPA artifact")
    verify = next(i for i, s in enumerate(steps) if "release_manifest.py verify" in str(s.get("run", "")))
    assert download < verify
    assert "--artifact-path        ipa-artifact" in steps[verify]["run"]


def _ipa(tmp_path, plist):
    path = tmp_path / "Game.ipa"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Payload/Game.app/Info.plist", plistlib.dumps(plist, fmt=plistlib.FMT_BINARY))
        z.writestr("Payload/Game.app/Game", b"\0")
    return path


def test_ipa_identity_reads_bundle_id_and_build_number(tmp_path):
    _ipa(tmp_path, {"CFBundleIdentifier": "com.example.game",
                    "CFBundleShortVersionString": "1.4.2", "CFBundleVersion": "463"})
    r = subprocess.run([sys.executable, str(IPA_IDENTITY), "--ipa", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    out = dict(line.split("=", 1) for line in r.stdout.splitlines())
    assert out["bundle-id"] == "com.example.game"
    assert out["version"] == "1.4.2"
    assert out["build-number"] == "463"


def test_ipa_identity_refuses_an_ipa_without_a_build_number(tmp_path):
    _ipa(tmp_path, {"CFBundleIdentifier": "com.example.game", "CFBundleShortVersionString": "1.4.2"})
    r = subprocess.run([sys.executable, str(IPA_IDENTITY), "--ipa", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode != 0
    assert "CFBundleVersion" not in r.stdout and "build-number" in (r.stdout + r.stderr)
