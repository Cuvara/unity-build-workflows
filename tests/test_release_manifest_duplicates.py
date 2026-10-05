"""One artifact, two copies of its manifest.

The build uploads artifact-manifest.json as "<name>-manifest" and, where build/
is writable (the native self-hosted lanes), also inside the build artifact. The
release-manifest job downloads both. That is one artifact, not an ambiguous
Release Set -- seen on a self-hosted Mac: "Android has two shippable artifacts
-- X (AAB) and X (AAB)".
"""
import json
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "scripts" / "common" / "release_manifest.py"
REUSABLE = REPO_ROOT / ".github" / "workflows" / "reusable-build-platform.yml"

NAME = "Game_1.1.1_467_release_android_aab"

PROVENANCE = {
    "builder": "local-unity", "builderKind": "native", "imageReference": "",
    "imageDigest": "", "unityVersion": "6000.3.9f1", "runner": "macOS/ARM64",
    "provenanceStrength": "auditable",
}


def _write(root, folder, sha, name=NAME):
    d = root / folder
    d.mkdir(parents=True)
    (d / "Game.aab").write_bytes(b"AAB!" * 1024)
    (d / "artifact-manifest.json").write_text(json.dumps({
        "platform": "Android", "artifactType": "AAB", "artifactName": name,
        "artifactSha256": sha, "intermediate": False, "version": "1.1.1",
        "buildNumber": "467", "gitCommit": "abc123def456",
        "builderProvenance": PROVENANCE,
    }))


def _generate(tmp_path, artifacts):
    out = tmp_path / "release-manifest.json"
    proc = subprocess.run([
        sys.executable, str(MANIFEST), "generate",
        "--search-root", str(artifacts), "--artifacts-root", str(artifacts),
        "--output", str(out), "--version", "1.1.1", "--build-number", "467",
        "--commit", "abc123def456", "--run-id", "1", "--unity-version", "6000.3.9f1",
        "--build-type", "release",
    ], capture_output=True, text=True)
    return proc, out


def test_two_copies_of_one_manifest_are_one_artifact(tmp_path):
    artifacts = tmp_path / "artifacts"
    _write(artifacts, f"{NAME}-manifest", "a" * 64)
    _write(artifacts, NAME, "a" * 64)  # the copy inside the build artifact
    proc, out = _generate(tmp_path, artifacts)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    entries = [a for a in json.loads(out.read_text())["artifacts"] if a["platform"] == "Android"]
    assert len(entries) == 1
    assert entries[0]["artifactName"] == NAME


def test_same_name_different_content_is_still_refused(tmp_path):
    artifacts = tmp_path / "artifacts"
    _write(artifacts, f"{NAME}-manifest", "a" * 64)
    _write(artifacts, NAME, "b" * 64)
    proc, _ = _generate(tmp_path, artifacts)
    assert proc.returncode == 1
    assert "ambiguous" in (proc.stdout + proc.stderr).lower()


def test_two_different_artifacts_are_still_refused(tmp_path):
    artifacts = tmp_path / "artifacts"
    _write(artifacts, "one", "a" * 64, name=NAME)
    _write(artifacts, "two", "a" * 64, name=NAME.replace("aab", "apk"))
    proc, _ = _generate(tmp_path, artifacts)
    assert proc.returncode == 1


def test_the_artifact_manifest_records_the_runs_build_number():
    """It recorded github.run_number (5) while the AAB carried versionCode 467,
    so validation reported the wrong versionCode."""
    steps = yaml.safe_load(REUSABLE.read_text(encoding="utf-8"))["jobs"]["build"]["steps"]
    run = next(s for s in steps if s.get("id") == "manifest")["run"]
    assert '--build-number   "${{ inputs.build-number || github.run_number }}"' in run
