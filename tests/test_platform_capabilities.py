"""
Platform capability model and the Release Set manifest.

The toolkit supports five platforms; a project enables a subset. Capability
("can this project build iOS at all?") is a different question from branch
selection ("does develop build iOS?"), and capability wins — a project that
cannot build a platform must never see a job for it.

The manifest is what makes a Release Set identifiable and what a promotion
checks an artifact against. Both are verified here by running the real
implementations, not by reading them.
"""
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
RESOLVER = REPO_ROOT / "scripts" / "common" / "resolve_build_flow.sh"
MANIFEST = REPO_ROOT / "scripts" / "common" / "release_manifest.py"
INVARIANTS = REPO_ROOT / "scripts" / "common" / "validate_pipeline_invariants.py"

ALL_PLATFORMS = ["android", "webgl", "linux64", "linuxserver", "windows64", "ios"]


def resolve(platforms="", in_platform="All", event="workflow_dispatch", **extra):
    """Run the real resolver and return its build-* flags."""
    env = dict(os.environ)
    env.update(EVENT_NAME=event, IN_PLATFORM=in_platform, PLATFORMS=platforms,
               IN_ENVIRONMENT="production", IN_RUN_TESTS="false", IN_TEST_MODE="All",
               IN_BUILD_ADDRESSABLES="false", IN_DEFINE_SYMBOLS="", REF_NAME="main")
    env.update(extra)
    with tempfile.NamedTemporaryFile("w", delete=False) as fh:
        out_path = fh.name
    env["GITHUB_OUTPUT"] = out_path
    try:
        proc = subprocess.run(["bash", str(RESOLVER)], env=env,
                              capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr[-800:]
        raw = dict(l.split("=", 1) for l in open(out_path).read().strip().split("\n") if "=" in l)
    finally:
        os.unlink(out_path)
    return {p for p in ALL_PLATFORMS if raw.get(f"build-{p}") == "true"}


# ---------------------------------------------------------------------------
# I-013 / I-014 / I-016 — platform capabilities
# ---------------------------------------------------------------------------

def test_unset_capabilities_keeps_existing_behaviour():
    """Projects that predate the capability model must be unaffected."""
    assert resolve(platforms="") == {"android", "webgl", "linux64", "linuxserver", "windows64"}


@pytest.mark.parametrize("declared,expected", [
    ("Android", {"android"}),
    ("Android,WebGL", {"android", "webgl"}),
    ("Android,Windows64,Linux64", {"android", "windows64", "linux64"}),
    ("Windows64", {"windows64"}),
    ("Linux64,LinuxServer", {"linux64", "linuxserver"}),
])
def test_only_declared_platforms_build(declared, expected):
    """An Android-only project gets Android jobs and nothing else."""
    assert resolve(platforms=declared) == expected


def test_capability_overrides_an_explicit_request():
    """Asking for a platform the project cannot build must produce no job —
    otherwise the capability declaration is advisory, not authoritative."""
    assert resolve(platforms="Android", in_platform="WebGL") == set()


def test_capability_applies_to_branch_flow_too(tmp_path):
    """Not just manual dispatch: a push must be filtered the same way."""
    built = resolve(platforms="Android", in_platform="", event="push",
                    REF_NAME="develop", DEVELOP_BUILD_PLATFORMS="Android,WebGL")
    assert built == {"android"}, "WebGL was built despite not being a capability"


def test_desktop_platforms_are_not_second_class():
    """Windows and Linux go through the same gate as the mobile platforms —
    no separate code path, no extra requirement."""
    for platform, flag in (("Windows64", "windows64"), ("Linux64", "linux64")):
        assert resolve(platforms=platform, in_platform=platform) == {flag}


def test_capability_accepts_spaces_and_commas():
    assert resolve(platforms="Android, WebGL") == {"android", "webgl"}


# ---------------------------------------------------------------------------
# I-006 / I-007 / I-017 — the Release Set manifest
# ---------------------------------------------------------------------------

def _release_set(tmp_path, commit="abc123def456", version="1.4.2", build_number="1042"):
    artifacts = tmp_path / "artifacts"
    (artifacts / "release-android-aab").mkdir(parents=True)
    (artifacts / "release-android-aab" / "Android.aab").write_bytes(b"AAB" * 4096)
    (artifacts / "release-android-aab" / "artifact-manifest.json").write_text(json.dumps({
        "platform": "Android", "artifactName": "release-android-aab",
        "artifactType": "AAB", "version": version, "buildNumber": build_number,
        "gitCommit": commit,
    }))
    manifest = tmp_path / "release-manifest.json"
    proc = subprocess.run([
        "python3", str(MANIFEST), "generate",
        "--search-root", str(artifacts), "--artifacts-root", str(artifacts),
        "--output", str(manifest), "--version", version,
        "--build-number", build_number, "--commit", commit,
        "--run-id", "34579047248", "--unity-version", "6000.0.26f1",
        "--build-type", "release", "--require-artifacts",
    ], capture_output=True, text=True)
    return proc, manifest, artifacts


def test_manifest_records_the_release_set(tmp_path):
    proc, manifest, _ = _release_set(tmp_path)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(manifest.read_text())
    rs = data["releaseSet"]
    assert rs["runId"] == "34579047248"
    assert rs["version"] == "1.4.2"
    assert rs["buildNumber"] == "1042"
    assert rs["commit"] == "abc123def456"
    assert data["buildEnvironment"]["unityVersion"] == "6000.0.26f1"
    entry = data["artifacts"][0]
    assert entry["platform"] == "Android"
    assert len(entry["sha256"]) == 64, "no checksum means nothing can be verified"


def test_manifest_rejects_an_inconsistent_release_set(tmp_path):
    """Every artifact in one set must share the release identity, or it is not
    a set. Catching it here beats discovering at promotion time that Android
    and iOS came from different commits."""
    proc, _, _ = _release_set(tmp_path, commit="deadbeefdead")
    # The per-artifact manifest says deadbeef…, the release says abc123…
    other = subprocess.run([
        "python3", str(MANIFEST), "generate",
        "--search-root", str(tmp_path / "artifacts"),
        "--artifacts-root", str(tmp_path / "artifacts"),
        "--output", str(tmp_path / "m2.json"),
        "--version", "1.4.2", "--build-number", "1042",
        "--commit", "abc123def456", "--require-artifacts",
    ], capture_output=True, text=True)
    assert other.returncode == 1
    assert "inconsistent" in other.stderr.lower()


def test_manifest_refuses_an_empty_release_set(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    proc = subprocess.run([
        "python3", str(MANIFEST), "generate", "--search-root", str(empty),
        "--output", str(tmp_path / "m.json"), "--require-artifacts",
    ], capture_output=True, text=True)
    assert proc.returncode == 1


def _verify(manifest, artifact, **expect):
    cmd = ["python3", str(MANIFEST), "verify", "--manifest", str(manifest),
           "--platform", "Android", "--artifact-path", str(artifact)]
    for key, value in expect.items():
        cmd += [f"--expect-{key.replace('_', '-')}", value]
    return subprocess.run(cmd, capture_output=True, text=True)


def test_verification_accepts_the_exact_artifact(tmp_path):
    _, manifest, artifacts = _release_set(tmp_path)
    proc = _verify(manifest, artifacts / "release-android-aab",
                   run_id="34579047248", version="1.4.2", commit="abc123def456",
                   artifact_name="release-android-aab")
    assert proc.returncode == 0, proc.stderr


def test_verification_fails_closed_on_tampered_bytes(tmp_path):
    """The check a filename cannot give you."""
    _, manifest, artifacts = _release_set(tmp_path)
    tampered = tmp_path / "tampered"
    shutil.copytree(artifacts / "release-android-aab", tampered)
    (tampered / "Android.aab").write_bytes(b"AAB" * 4095 + b"EVIL")
    proc = _verify(manifest, tampered, run_id="34579047248")
    assert proc.returncode == 1
    assert "checksum mismatch" in proc.stderr


@pytest.mark.parametrize("field,value", [
    ("run_id", "99999999"),
    ("version", "9.9.9"),
    ("commit", "0000000000"),
    ("artifact_name", "release-android-apk"),
])
def test_verification_fails_closed_on_identity_mismatch(tmp_path, field, value):
    _, manifest, artifacts = _release_set(tmp_path)
    proc = _verify(manifest, artifacts / "release-android-aab", **{field: value})
    assert proc.returncode == 1, f"{field}={value} was accepted"


def test_verification_fails_closed_without_a_manifest(tmp_path):
    """A promotion that cannot prove what it holds must not publish it."""
    proc = subprocess.run([
        "python3", str(MANIFEST), "verify", "--manifest", str(tmp_path / "nope.json"),
        "--platform", "Android",
    ], capture_output=True, text=True)
    assert proc.returncode == 1
    assert "not found" in proc.stderr


def test_verification_rejects_a_platform_outside_the_set(tmp_path):
    _, manifest, _ = _release_set(tmp_path)
    proc = subprocess.run([
        "python3", str(MANIFEST), "verify", "--manifest", str(manifest),
        "--platform", "iOS",
    ], capture_output=True, text=True)
    assert proc.returncode == 1
    assert "no iOS artifact" in proc.stderr


# ---------------------------------------------------------------------------
# The invariant checker itself
# ---------------------------------------------------------------------------

def test_invariant_checker_passes_on_this_repository():
    proc = subprocess.run(["python3", str(INVARIANTS), "--repo-root", str(REPO_ROOT),
                           "--json"], capture_output=True, text=True)
    report = json.loads(proc.stdout)
    assert report["status"] == "success", json.dumps(report["violations"], indent=2)
    assert proc.returncode == 0


def test_invariant_checker_detects_a_promotion_that_builds(tmp_path):
    """The checker must actually bite, or it is decoration."""
    fake = tmp_path / ".github" / "workflows"
    fake.mkdir(parents=True)
    (tmp_path / "templates").mkdir()
    (tmp_path / "scripts").mkdir()
    (fake / "pipeline-android-release.yml").write_text(
        "name: bad\non:\n  workflow_call:\n    inputs:\n"
        "      source-run-id:\n        required: true\n        type: string\n"
        "jobs:\n  publish:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: game-ci/unity-builder@v5\n"
    )
    proc = subprocess.run(["python3", str(INVARIANTS), "--repo-root", str(tmp_path),
                           "--json"], capture_output=True, text=True)
    report = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert any(v["invariant"] == "I-005" for v in report["violations"]), report
