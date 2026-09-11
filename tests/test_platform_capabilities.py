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

# What a Docker lane records when game-ci's image could be inspected. Used as
# the default so the manifest tests exercise a realistic artifact; the
# provenance-specific tests below vary it deliberately.
DOCKER_PROVENANCE = {
    "builder": "game-ci/unity-builder@v5",
    "builderKind": "docker",
    "imageReference": "unityci/editor:6000.0.26f1-android-3",
    "imageDigest": "sha256:" + "ab" * 32,
    "unityVersion": "6000.0.26f1",
    "runner": "Linux/X64",
    "provenanceStrength": "immutable",
}


def _release_set(tmp_path, commit="abc123def456", version="1.4.2", build_number="1042",
                 provenance=DOCKER_PROVENANCE, extra_args=()):
    artifacts = tmp_path / "artifacts"
    (artifacts / "release-android-aab").mkdir(parents=True)
    (artifacts / "release-android-aab" / "Android.aab").write_bytes(b"AAB" * 4096)
    entry = {
        "platform": "Android", "artifactName": "release-android-aab",
        "artifactType": "AAB", "version": version, "buildNumber": build_number,
        "gitCommit": commit,
    }
    if provenance is not None:
        entry["builderProvenance"] = provenance
    (artifacts / "release-android-aab" / "artifact-manifest.json").write_text(
        json.dumps(entry))
    manifest = tmp_path / "release-manifest.json"
    proc = subprocess.run([
        "python3", str(MANIFEST), "generate",
        "--search-root", str(artifacts), "--artifacts-root", str(artifacts),
        "--output", str(manifest), "--version", version,
        "--build-number", build_number, "--commit", commit,
        "--run-id", "34579047248", "--unity-version", "6000.0.26f1",
        "--build-type", "release", "--require-artifacts", *extra_args,
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


# ---------------------------------------------------------------------------
# I-008 — builder provenance: immutable OR auditable, honest about which
# ---------------------------------------------------------------------------
# The rule is not "every release must use a digest". It is "a release must be
# able to say what produced it, and must not overstate how firmly". These
# tests exist mostly to pin the second half: an overstated provenance is worse
# than an absent one, because it invites trust that is not there.

import sys  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "scripts" / "common"))
from artifact_manifest import build_provenance, classify_provenance  # noqa: E402


@pytest.mark.parametrize("kwargs,expected", [
    # A digest pins content — re-running reproduces the environment.
    ({"image_digest": "sha256:" + "ab" * 32}, "immutable"),
    # A tag can move under you; you can still say which tag it was.
    ({"image_reference": "unityci/editor:6000.0.26f1-android-3"}, "auditable"),
    # A native lane has no image at all, but the Unity version is real
    # provenance — this is the case the old digest-only rule could never pass.
    ({"unity_version": "6000.0.26f1"}, "auditable"),
    # Nothing recorded is not "fine", and must not read as fine.
    ({}, "unknown"),
])
def test_provenance_strength_matches_what_was_actually_established(kwargs, expected):
    assert classify_provenance(**kwargs) == expected


def test_immutable_cannot_be_claimed_without_a_digest():
    """A caller asserting `immutable` over a tag gets downgraded, not believed."""
    prov = build_provenance(
        builder_kind="docker",
        image_reference="unityci/editor:6000.0.26f1-android-3",
        provenance_strength="immutable",
    )
    assert prov["provenanceStrength"] == "auditable"


def test_a_native_build_records_auditable_provenance():
    """No Docker anywhere, and the invariant is still satisfiable."""
    prov = build_provenance(
        builder="local-unity-editor", builder_kind="native",
        unity_version="6000.0.26f1", runner="macOS/ARM64",
    )
    assert prov["provenanceStrength"] == "auditable"
    assert prov["imageDigest"] == ""
    assert prov["unityVersion"] == "6000.0.26f1"


def test_a_repo_qualified_digest_is_normalised():
    prov = build_provenance(image_digest="unityci/editor@sha256:" + "cd" * 32)
    assert prov["imageDigest"] == "sha256:" + "cd" * 32
    assert prov["provenanceStrength"] == "immutable"


def test_release_set_carries_per_artifact_provenance(tmp_path):
    proc, manifest, _ = _release_set(tmp_path)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(manifest.read_text())
    prov = data["artifacts"][0]["builderProvenance"]
    assert prov["builder"] == "game-ci/unity-builder@v5"
    assert prov["provenanceStrength"] == "immutable"
    assert data["buildEnvironment"]["provenanceStrength"] == "immutable"


def test_release_set_reports_the_weakest_artifact(tmp_path):
    """A digest-pinned Android build does not make an unpinned set immutable."""
    auditable = dict(DOCKER_PROVENANCE, imageDigest="", provenanceStrength="auditable")
    _, manifest, _ = _release_set(tmp_path, provenance=auditable)
    data = json.loads(manifest.read_text())
    assert data["buildEnvironment"]["provenanceStrength"] == "auditable"


def test_release_set_fails_closed_on_an_untraceable_artifact(tmp_path):
    proc, _, _ = _release_set(tmp_path, provenance=None)
    assert proc.returncode == 1
    assert "no builder provenance" in proc.stderr


def test_untraceable_artifact_can_be_allowed_explicitly(tmp_path):
    """The escape hatch exists for a lane being brought up — and is loud."""
    proc, manifest, _ = _release_set(
        tmp_path, provenance=None, extra_args=("--allow-unknown-provenance",))
    assert proc.returncode == 0, proc.stderr
    data = json.loads(manifest.read_text())
    assert data["buildEnvironment"]["provenanceStrength"] == "unknown"


def test_promotion_refuses_an_artifact_with_unknown_provenance(tmp_path):
    _, manifest, artifacts = _release_set(
        tmp_path, provenance=None, extra_args=("--allow-unknown-provenance",))
    proc = _verify(manifest, artifacts / "release-android-aab", run_id="34579047248")
    assert proc.returncode == 1
    assert "no builder provenance" in proc.stderr


def test_invariant_checker_detects_a_lane_that_records_no_provenance(tmp_path):
    """I-008 must bite on a build lane that writes a manifest and nothing else."""
    fake = tmp_path / ".github" / "workflows"
    fake.mkdir(parents=True)
    (tmp_path / "templates").mkdir()
    scripts = tmp_path / "scripts" / "common"
    scripts.mkdir(parents=True)
    for name in ("artifact_manifest.py", "release_manifest.py"):
        shutil.copy(REPO_ROOT / "scripts" / "common" / name, scripts / name)
    (fake / "build.yml").write_text(
        "name: build\non:\n  workflow_call:\njobs:\n  build:\n"
        "    runs-on: ubuntu-latest\n    steps:\n"
        "      - run: python3 scripts/common/artifact_manifest.py --platform Android\n"
    )
    proc = subprocess.run(["python3", str(INVARIANTS), "--repo-root", str(tmp_path),
                           "--json"], capture_output=True, text=True)
    report = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert any(v["invariant"] == "I-008" for v in report["violations"]), report


def test_invariant_checker_detects_provenance_that_overstates_itself(tmp_path):
    """Rewrite the classifier to always say `immutable`; the gate must notice."""
    fake = tmp_path / ".github" / "workflows"
    fake.mkdir(parents=True)
    (tmp_path / "templates").mkdir()
    scripts = tmp_path / "scripts" / "common"
    scripts.mkdir(parents=True)
    shutil.copy(REPO_ROOT / "scripts" / "common" / "release_manifest.py",
                scripts / "release_manifest.py")
    (scripts / "artifact_manifest.py").write_text(
        "def classify_provenance(image_digest='', image_reference='', unity_version=''):\n"
        "    return \"immutable\"\n\n\n"
        "def build_provenance(**kwargs):\n"
        "    return {}\n"
    )
    proc = subprocess.run(["python3", str(INVARIANTS), "--repo-root", str(tmp_path),
                           "--json"], capture_output=True, text=True)
    report = json.loads(proc.stdout)
    assert any(v["invariant"] == "I-008" for v in report["violations"]), report


# ---------------------------------------------------------------------------
# Full capability matrix
# ---------------------------------------------------------------------------
# Every supported project shape, plus the combinations that should produce
# nothing. A capability declaration that can be talked around is not a
# capability declaration.

CAPABILITY_MATRIX = [
    # declared,                       requested,     expected built
    ("Android",                       "Android",     {"android"}),
    ("Android",                       "All",         {"android"}),
    ("WebGL",                         "WebGL",       {"webgl"}),
    ("WebGL",                         "All",         {"webgl"}),
    ("Windows64",                     "Windows64",   {"windows64"}),
    ("Windows64",                     "All",         {"windows64"}),
    ("Linux64",                       "Linux64",     {"linux64"}),
    ("Linux64",                       "All",         {"linux64"}),
    ("Android,WebGL",                 "All",         {"android", "webgl"}),
    ("Android,Windows64",             "All",         {"android", "windows64"}),
    ("Windows64,Linux64",             "All",         {"windows64", "linux64"}),
    ("Android,WebGL,Windows64,Linux64", "All",
     {"android", "webgl", "windows64", "linux64"}),
    ("Linux64,LinuxServer",           "All",         {"linux64", "linuxserver"}),
]


@pytest.mark.parametrize("declared,requested,expected", CAPABILITY_MATRIX)
def test_capability_matrix(declared, requested, expected):
    assert resolve(platforms=declared, in_platform=requested) == expected


# Requesting a platform the project has not declared. Each of these must build
# nothing at all — not fail, not fall back, not build something else.
INVALID_COMBINATIONS = [
    ("Android", "WebGL"),
    ("Android", "Windows64"),
    ("Android", "Linux64"),
    ("Android", "iOS"),
    ("WebGL", "Android"),
    ("WebGL", "Windows64"),
    ("Windows64", "Android"),
    ("Windows64", "WebGL"),
    ("Linux64", "Windows64"),
    ("Android,WebGL", "Windows64"),
    ("Android,WebGL", "Linux64"),
    ("Windows64,Linux64", "Android"),
]


@pytest.mark.parametrize("declared,requested", INVALID_COMBINATIONS)
def test_undeclared_platform_creates_no_job(declared, requested):
    """The capability must win over the request, or it is only advice."""
    built = resolve(platforms=declared, in_platform=requested)
    assert built == set(), (
        f"a project declaring {declared} built {sorted(built)} when asked for "
        f"{requested}; a disabled platform must produce no job"
    )


@pytest.mark.parametrize("declared", ["Android", "WebGL", "Windows64", "Linux64"])
def test_disabled_platforms_produce_no_matrix_row(resolve_matrix, declared):
    """No job means no artifact: a platform absent from the build matrix has
    nothing to upload, validate or promote."""
    enabled = {
        "Android": "Android", "WebGL": "WebGL",
        "Windows64": "Windows64", "Linux64": "Linux64",
    }[declared]
    out = resolve_matrix([enabled], build_type="release")
    assert out["build_platforms"] == [enabled]
    for name in out["artifact_names"]:
        assert enabled.lower().rstrip("64") in name.replace("-", ""), name
    # Nothing belonging to another platform slipped into the set.
    for other in ("android", "webgl", "windows", "linux"):
        if other in enabled.lower():
            continue
        assert not any(n.startswith(f"release-{other}") for n in out["artifact_names"]), (
            f"{declared}-only project produced a {other} artifact"
        )


def test_every_toolkit_platform_is_declarable():
    """I-013: all five are first-class. A platform the toolkit supports but
    that cannot be declared would be second-class by omission."""
    for platform, flag in [("Android", "android"), ("WebGL", "webgl"),
                           ("Windows64", "windows64"), ("Linux64", "linux64"),
                           ("LinuxServer", "linuxserver")]:
        assert resolve(platforms=platform, in_platform=platform) == {flag}
    # iOS is declarable too, but only reachable by explicit dispatch.
    assert resolve(platforms="iOS", in_platform="iOS") == {"ios"}


def test_capability_does_not_require_a_distribution_provider():
    """I-015: Windows and Linux produce artifacts with no distribution
    configured. Requiring one would make desktop second-class."""
    for platform in ("Windows64", "Linux64"):
        assert resolve(platforms=platform, in_platform=platform)

def _resolver_to_matrix(resolve_matrix, declared, requested, build_type="release"):
    """Run the real chain: capability resolver → the pipeline's matrix step.

    Testing the resolver alone proves a flag is `false`. It does not prove no
    job is created — that depends on what the matrix step does with the flag,
    and a matrix that emitted a row anyway would still fan out a job which
    fails, or worse, builds. This runs both halves the way the pipeline does.
    """
    built = resolve(platforms=declared, in_platform=requested)
    enabled = [p for p in ("Android", "iOS", "WebGL", "Windows64", "Linux64",
                           "LinuxServer") if p.lower() in built]
    return resolve_matrix(enabled, build_type=build_type,
                          platform_input=requested if requested != "All" else "All")


@pytest.mark.parametrize("declared,requested", INVALID_COMBINATIONS)
def test_undeclared_platform_reaches_the_matrix_as_nothing(resolve_matrix, declared,
                                                           requested):
    """End to end: a disabled platform creates no job and so no artifact."""
    out = _resolver_to_matrix(resolve_matrix, declared, requested)
    assert out["build"] == [], out["build"]
    assert out["validate"] == [], out["validate"]
    assert out["artifact_names"] == []
    assert out["has-builds"] == "false"
    assert out["has-validations"] == "false"


@pytest.mark.parametrize("declared,requested,expected", CAPABILITY_MATRIX)
def test_declared_platforms_reach_the_matrix_intact(resolve_matrix, declared,
                                                    requested, expected):
    """The other half of the same proof: what IS declared does fan out, once."""
    out = _resolver_to_matrix(resolve_matrix, declared, requested)
    got = {r["platform"].lower() for r in out["build"]}
    assert got == expected, f"{declared} asked for {requested}: {sorted(got)}"
    assert out["has-builds"] == "true"
    # One row per platform — a duplicate row means two jobs racing to upload
    # the same artifact name, which fails late and confusingly.
    assert len(out["build"]) == len(got)
    assert len(out["artifact_names"]) == len(set(out["artifact_names"]))


@pytest.mark.parametrize("platform,artifact", [
    ("Android", "release-android-aab"),
    ("WebGL", "release-webgl"),
    ("Windows64", "release-windows"),
    ("Linux64", "release-linux"),
    # The pair most likely to collide: two Linux targets, one artifact type.
    ("LinuxServer", "release-linux-server"),
])
def test_release_artifact_names_are_platform_unique(resolve_matrix, platform, artifact):
    """Artifact naming is what promotion addresses; a collision between two
    platforms or two build types would promote the wrong binary."""
    out = resolve_matrix([platform], build_type="release")
    assert out["artifact_names"] == [artifact], out["artifact_names"]
    dev = resolve_matrix([platform], build_type="development")
    assert dev["artifact_names"] != out["artifact_names"], (
        "a development and a release build share an artifact name"
    )


def test_linux_desktop_and_server_do_not_share_an_artifact_name(resolve_matrix):
    """Both are LINUX artifacts from one project; promoting the wrong one would
    ship a headless server build to desktop players."""
    out = resolve_matrix(["Linux64", "LinuxServer"], build_type="release")
    assert len(set(out["artifact_names"])) == 2, out["artifact_names"]


def test_the_pipeline_actually_passes_the_capability_variable():
    """The gate is only real if the workflow hands it to the resolver.

    Found at runtime, not in tests: every capability test set PLATFORMS in the
    subprocess environment itself, so they all passed while `unity-pipeline.yml`
    never forwarded `vars.PLATFORMS`. A project declaring Android,WebGL built
    Windows64 on request. The unit tests were testing a resolver nobody was
    calling that way.
    """
    import yaml

    pipeline = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "unity-pipeline.yml").read_text())
    steps = [step
             for job in pipeline["jobs"].values()
             for step in job.get("steps", [])
             if "resolve_build_flow.sh" in str(step.get("run", ""))]
    assert steps, "no step runs the resolver"
    for step in steps:
        env = step.get("env", {})
        assert "PLATFORMS" in env, (
            "the resolver step does not receive PLATFORMS, so the platform "
            "capability gate cannot see the project's declaration"
        )
        assert "vars.PLATFORMS" in str(env["PLATFORMS"]), env["PLATFORMS"]


def test_release_set_refuses_a_missing_version(tmp_path):
    """Found on a real run: the pipeline's shallow checkout omitted
    ProjectSettings.asset, so app-version resolved empty and the Release Set
    was written with `"version": ""`. `verify --expect-version ''` then
    compared nothing and passed — the identity check silently lost a field.
    """
    proc, _, _ = _release_set(tmp_path, version="")
    assert proc.returncode == 1
    assert "no version" in proc.stderr


def test_development_sets_may_have_no_version(tmp_path):
    """The rule is a release rule. A development set is disposable (I-002)."""
    artifacts = tmp_path / "artifacts"
    (artifacts / "development-android-apk").mkdir(parents=True)
    (artifacts / "development-android-apk" / "app.apk").write_bytes(b"APK")
    (artifacts / "development-android-apk" / "artifact-manifest.json").write_text(
        json.dumps({"platform": "Android", "artifactName": "development-android-apk",
                    "artifactType": "APK", "gitCommit": "abc123def456",
                    "builderProvenance": DOCKER_PROVENANCE}))
    proc = subprocess.run([
        "python3", str(MANIFEST), "generate", "--search-root", str(artifacts),
        "--artifacts-root", str(artifacts), "--output", str(tmp_path / "m.json"),
        "--commit", "abc123def456", "--build-type", "development",
        "--require-artifacts",
    ], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_the_pipeline_checks_out_the_file_version_comes_from():
    """The other half: the manifest can only carry a version the job can read."""
    import yaml

    pipeline = yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "unity-pipeline.yml").read_text())
    sparse = [step.get("with", {}).get("sparse-checkout", "")
              for job in pipeline["jobs"].values()
              for step in job.get("steps", [])
              if "checkout" in str(step.get("uses", ""))]
    assert any("ProjectSettings.asset" in str(p) for p in sparse), (
        "no checkout brings in ProjectSettings.asset, so bundleVersion cannot "
        "be read and the Release Set will have an empty version"
    )
