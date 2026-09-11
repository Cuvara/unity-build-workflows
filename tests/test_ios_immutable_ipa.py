"""
The iOS immutable artifact contract.

iOS is the one platform whose shippable artifact is not what Unity emits.
Unity produces an Xcode project; what ships is a signed IPA exported from it.
Both carry a manifest saying `"platform": "iOS"`, and for a while the Release
Set took whichever it happened to read last — so a promotion could be pointed
at an Xcode project, which nobody can install and which a promotion is
forbidden from turning into anything else (I-005).

The contract these tests pin:

    Build / Release:  xcodeproj -> archive -> sign -> export IPA -> validate
                      -> release-ios-ipa in the Release Set
    Promotion:        download the IPA -> verify -> upload

and never:

    release-ios-xcodeproj -> promotion builds/exports/signs -> IPA
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
TEMPLATES = REPO_ROOT / "templates"
MANIFEST = REPO_ROOT / "scripts" / "common" / "release_manifest.py"

sys.path.insert(0, str(REPO_ROOT / "scripts" / "common"))
from artifact_manifest import build_manifest  # noqa: E402


def pipeline():
    return yaml.safe_load((WORKFLOWS / "unity-pipeline.yml").read_text())


# ---------------------------------------------------------------------------
# What counts as shippable
# ---------------------------------------------------------------------------

def test_an_xcode_project_is_marked_intermediate():
    """It is an input to stage 03b, not something anyone can install."""
    assert build_manifest("iOS", "Production", "XCODEPROJ")["intermediate"] is True


def test_the_signed_ipa_is_not_intermediate():
    assert build_manifest("iOS", "Production", "IPA")["intermediate"] is False


@pytest.mark.parametrize("platform,artifact_type", [
    ("Android", "AAB"), ("WebGL", "WEBGL"),
    ("Windows64", "EXE"), ("Linux64", "LINUX"),
])
def test_no_other_platform_is_accidentally_intermediate(platform, artifact_type):
    assert build_manifest(platform, "Production", artifact_type)["intermediate"] is False


# ---------------------------------------------------------------------------
# The Release Set takes the IPA, not the project
# ---------------------------------------------------------------------------

PROVENANCE_NATIVE = {
    "builder": "local-xcode",
    "builderKind": "native",
    "imageReference": "",
    "imageDigest": "",
    "unityVersion": "6000.0.26f1",
    "runner": "macOS/ARM64",
    "provenanceStrength": "auditable",
}


def _ios_release_set(tmp_path, *, with_ipa=True, with_project=True,
                     project_intermediate=True):
    artifacts = tmp_path / "artifacts"

    def write(name, artifact_type, intermediate, payload):
        directory = artifacts / name
        directory.mkdir(parents=True)
        (directory / f"App.{artifact_type.lower()}").write_bytes(payload)
        entry = {
            "platform": "iOS", "artifactName": name, "artifactType": artifact_type,
            "version": "1.4.2", "buildNumber": "1042",
            "gitCommit": "abc123def456", "builderProvenance": PROVENANCE_NATIVE,
        }
        if intermediate:
            entry["intermediate"] = True
        (directory / "artifact-manifest.json").write_text(json.dumps(entry))

    if with_project:
        write("release-ios-xcodeproj", "XCODEPROJ", project_intermediate, b"PROJ" * 512)
    if with_ipa:
        write("release-ios-ipa", "IPA", False, b"IPA!" * 4096)

    manifest = tmp_path / "release-manifest.json"
    proc = subprocess.run([
        "python3", str(MANIFEST), "generate",
        "--search-root", str(artifacts), "--artifacts-root", str(artifacts),
        "--output", str(manifest), "--version", "1.4.2",
        "--build-number", "1042", "--commit", "abc123def456",
        "--run-id", "34579047248", "--unity-version", "6000.0.26f1",
        "--build-type", "release", "--require-artifacts",
    ], capture_output=True, text=True)
    return proc, manifest, artifacts


def test_the_release_set_contains_the_ipa_and_not_the_project(tmp_path):
    proc, manifest, _ = _ios_release_set(tmp_path)
    assert proc.returncode == 0, proc.stderr
    entries = [a for a in json.loads(manifest.read_text())["artifacts"]
               if a["platform"] == "iOS"]
    assert len(entries) == 1, entries
    assert entries[0]["artifactName"] == "release-ios-ipa"
    assert entries[0]["artifactType"] == "IPA"
    assert len(entries[0]["sha256"]) == 64


def test_two_shippable_ios_artifacts_are_refused_rather_than_guessed(tmp_path):
    """If the project ever stopped being marked intermediate, the Release Set
    would have to choose between two artifacts. Choosing silently is how it
    picked the wrong one before; this fails instead."""
    proc, _, _ = _ios_release_set(tmp_path, project_intermediate=False)
    assert proc.returncode == 1
    assert "ambiguous" in proc.stderr.lower()


def test_an_unsigned_ios_build_contributes_no_promotable_artifact(tmp_path):
    """Stage 03b did not run, so there is no IPA. The Xcode project must not
    quietly stand in for one."""
    proc, manifest, _ = _ios_release_set(tmp_path, with_ipa=False)
    # Only intermediates were found, so the set has no artifacts at all.
    assert proc.returncode == 1
    assert "no artifacts" in proc.stderr.lower()


def test_promoting_an_intermediate_is_refused(tmp_path):
    """Belt and braces: even if an intermediate reached a manifest, verifying
    it must fail rather than publish something nobody can install."""
    manifest = tmp_path / "m.json"
    manifest.write_text(json.dumps({
        "schemaVersion": 2,
        "releaseSet": {"runId": "1", "version": "1.4.2", "buildNumber": "1042",
                       "commit": "abc123def456"},
        "artifacts": [{
            "platform": "iOS", "artifactName": "release-ios-xcodeproj",
            "artifactType": "XCODEPROJ", "sha256": "0" * 64, "intermediate": True,
            "version": "1.4.2", "buildNumber": "1042", "commit": "abc123def456",
        }],
    }))
    proc = subprocess.run([
        "python3", str(MANIFEST), "verify", "--manifest", str(manifest),
        "--platform", "iOS",
    ], capture_output=True, text=True)
    assert proc.returncode == 1
    assert "intermediate" in proc.stderr


# ---------------------------------------------------------------------------
# Build / Release signs before the boundary and manifests the IPA
# ---------------------------------------------------------------------------

def test_signing_happens_in_the_build_lane():
    """I-004. If the IPA were exported during promotion, the binary QA
    validated (a project) would not be the binary that ships."""
    jobs = pipeline()["jobs"]
    assert "sign-ios" in jobs
    steps = json.dumps(jobs["sign-ios"]["steps"])
    assert "ios-archive-export" in steps
    assert "ios-setup-signing" in steps


def test_the_ipa_gets_its_own_artifact_manifest():
    """Without one, the only iOS manifest in the run is the Xcode project's and
    the Release Set lists a project where the shippable binary should be."""
    steps = pipeline()["jobs"]["sign-ios"]["steps"]
    manifest_steps = [s for s in steps
                      if "artifact_manifest.py" in str(s.get("run", ""))]
    assert manifest_steps, "stage 03b writes no artifact manifest for the IPA"
    run = str(manifest_steps[0]["run"])
    assert "--artifact-type   IPA" in run or "--artifact-type IPA" in run
    assert "-ios-ipa" in run

    uploads = [s for s in steps
               if "upload-artifact" in str(s.get("uses", ""))
               and "ipa-manifest" in json.dumps(s.get("with", {}))]
    assert uploads, "the IPA manifest is never uploaded, so stage 05 cannot see it"


def test_the_ipa_is_validated_before_it_becomes_immutable(resolve_matrix):
    """Stage 04 validates the IPA, not the Xcode project, for a release.

    Validating the project would check that the build produced something
    compilable and say nothing about the thing that actually ships.
    """
    release = {r["platform"]: r for r in
               resolve_matrix(["iOS"], build_type="release")["validate"]}
    assert release["iOS"]["artifact-type"] == "IPA"
    assert release["iOS"]["artifact-name"] == "release-ios-ipa"
    assert release["iOS"]["validator"] == "ipa"

    # A development build has no IPA — nothing signs it — so the project is
    # the only thing there is to check.
    development = {r["platform"]: r for r in
                   resolve_matrix(["iOS"], build_type="development")["validate"]}
    assert development["iOS"]["artifact-type"] == "XCODEPROJ"

    assert "validate_ipa.sh" in (WORKFLOWS / "unity-pipeline.yml").read_text()


def test_stage_04_waits_for_signing():
    validate = pipeline()["jobs"]["validate-artifact"]
    assert "sign-ios" in validate["needs"]


# ---------------------------------------------------------------------------
# Promotion consumes the IPA and cannot produce one
# ---------------------------------------------------------------------------

def test_the_promotion_default_is_the_ipa():
    """The defect this file exists for: the consumer template defaulted to
    `release-ios-xcodeproj`, which a promote-only pipeline cannot turn into
    anything installable."""
    template = yaml.safe_load(
        (TEMPLATES / "consumer-21-release-ios.yml").read_text())
    inputs = (template.get("on") or template[True])["workflow_dispatch"]["inputs"]
    assert inputs["artifact-name"]["default"] == "release-ios-ipa"


def test_nothing_anywhere_still_points_at_the_xcode_project():
    for path in list(WORKFLOWS.glob("*.yml")) + list(TEMPLATES.glob("*.yml")):
        body = path.read_text()
        for line_number, line in enumerate(body.splitlines(), 1):
            if "release-ios-xcodeproj" not in line:
                continue
            # The build lane legitimately produces it as an intermediate; only
            # a promotion consuming it is wrong.
            assert "promote" not in line.lower(), f"{path.name}:{line_number}"
            assert "artifact-name" not in line.lower(), f"{path.name}:{line_number}"


def test_ios_promotion_contains_no_xcode_operation():
    """I-005, spelled out for the platform most likely to erode it: the whole
    archive/export/sign chain belongs to Build / Release."""
    body = (WORKFLOWS / "pipeline-ios-release.yml").read_text().lower()
    for operation in ("xcodebuild", "-exportarchive", "-archivepath", "codesign",
                      "ios-archive-export", "ios-setup-signing", "unity-builder",
                      "-executemethod", "xcrun"):
        assert operation not in body, (
            f"pipeline-ios-release.yml performs {operation}; exporting or signing "
            f"during promotion means the bytes QA approved are not the bytes that ship"
        )


def test_ios_promotion_verifies_before_every_publish():
    workflow = yaml.safe_load((WORKFLOWS / "pipeline-ios-release.yml").read_text())
    for job_id, job in workflow["jobs"].items():
        needs = job.get("needs") or []
        if "verify-artifact" not in needs or job_id == "report":
            continue
        assert "verify-artifact.result == 'success'" in str(job.get("if", "")), job_id
