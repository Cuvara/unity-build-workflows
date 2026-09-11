"""
Windows and Linux as first-class release platforms, and Steam as a
distribution provider hanging off them.

The distinction those two sentences make is the whole point (I-015): a project
can build and validate a Windows or Linux release artifact with no Steam
account at all, and Steam configuration gates publishing only. Several of the
tests below exist to keep that separation from quietly collapsing, because the
easy implementation — "require the Steam secrets in the build workflow" — makes
desktop a second-class platform in a way nobody notices until a project that
ships on itch.io cannot build.
"""

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
DESKTOP_VALIDATOR = REPO_ROOT / "scripts" / "desktop" / "validate_desktop_artifact.py"
STEAM_CONFIG = REPO_ROOT / "scripts" / "steam" / "resolve_steam_config.py"
STEAM_DEPLOY = REPO_ROOT / "scripts" / "steam" / "deploy_steam.sh"
MANIFEST = REPO_ROOT / "scripts" / "common" / "release_manifest.py"
INVARIANTS = REPO_ROOT / "scripts" / "common" / "validate_pipeline_invariants.py"

DESKTOP_PIPELINES = ["pipeline-windows-release.yml", "pipeline-linux-release.yml"]


def load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())


def triggers(name):
    """The `on:` block. PyYAML parses a bare `on:` key as the boolean True."""
    workflow = load(name)
    return workflow.get("on") or workflow[True]


# ---------------------------------------------------------------------------
# The desktop artifact validator
# ---------------------------------------------------------------------------
# Windows and Linux had no stage-04 validator at all, so a player missing its
# _Data directory — one that cannot start — became an immutable release
# artifact unchallenged.

def make_player(root, platform="Windows64", *, data=True, engine=True,
                code=True, runtime=True, executable_bit=True, size=6_000_000):
    """Write a plausible standalone player tree, with defects on request."""
    root.mkdir(parents=True, exist_ok=True)
    if platform == "Windows64":
        exe = root / "Game.exe"
        exe.write_bytes(b"MZ" + b"\0" * size)
        runtime_name = "UnityPlayer.dll"
    else:
        exe = root / "Game.x86_64"
        exe.write_bytes(b"\x7fELF" + b"\0" * size)
        runtime_name = "UnityPlayer.so"
        if executable_bit:
            exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
        else:
            exe.chmod(0o644)
    if runtime:
        (root / runtime_name).write_bytes(b"\0" * 1024)
    if data:
        data_dir = root / "Game_Data"
        data_dir.mkdir(exist_ok=True)
        if engine:
            (data_dir / "globalgamemanagers").write_bytes(b"\0" * 4096)
        if code:
            managed = data_dir / "Managed"
            managed.mkdir(exist_ok=True)
            (managed / "Assembly-CSharp.dll").write_bytes(b"\0" * 2048)
    return root


def validate_desktop(root, platform):
    return subprocess.run(
        ["python3", str(DESKTOP_VALIDATOR), "--search-root", str(root),
         "--platform", platform],
        capture_output=True, text=True)


@pytest.mark.parametrize("platform", ["Windows64", "Linux64", "LinuxServer"])
def test_a_complete_player_passes(tmp_path, platform):
    make_player(tmp_path / "a", "Windows64" if platform == "Windows64" else "Linux64")
    proc = validate_desktop(tmp_path / "a", platform)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("platform", ["Windows64", "Linux64"])
def test_a_player_without_its_data_directory_fails(tmp_path, platform):
    """The defect that has no other symptom until a player double-clicks it."""
    make_player(tmp_path / "a", platform, data=False)
    proc = validate_desktop(tmp_path / "a", platform)
    assert proc.returncode == 1
    assert "data directory" in proc.stderr.lower()


@pytest.mark.parametrize("platform", ["Windows64", "Linux64"])
def test_a_player_with_no_engine_payload_fails(tmp_path, platform):
    make_player(tmp_path / "a", platform, engine=False)
    proc = validate_desktop(tmp_path / "a", platform)
    assert proc.returncode == 1
    assert "engine payload" in proc.stderr.lower()


@pytest.mark.parametrize("platform", ["Windows64", "Linux64"])
def test_a_player_with_no_game_code_fails(tmp_path, platform):
    make_player(tmp_path / "a", platform, code=False)
    proc = validate_desktop(tmp_path / "a", platform)
    assert proc.returncode == 1
    assert "game code" in proc.stderr.lower()


def test_a_downloaded_linux_player_without_the_bit_warns_rather_than_fails(tmp_path):
    """Found on a real run. GitHub stores artifacts in a zip, which carries no
    POSIX modes, so EVERY downloaded Linux binary is 0644. Failing stage 04 on
    that would fail every Linux release forever while blaming the build for
    something the transport did."""
    make_player(tmp_path / "a", "Linux64", executable_bit=False)
    proc = validate_desktop(tmp_path / "a", "Linux64")
    assert proc.returncode == 0, proc.stdout
    report = json.loads(proc.stdout)
    bit = next(c for c in report["checks"] if c["name"] == "executable bit")
    assert bit["status"] == "warn"
    assert "POSIX modes" in bit["detail"]


def test_the_bit_is_still_a_gate_before_upload(tmp_path):
    """Against a build directory on disk, a missing bit really is the build's
    fault — so the check is available as a gate for that caller."""
    make_player(tmp_path / "a", "Linux64", executable_bit=False)
    proc = subprocess.run(
        ["python3", str(DESKTOP_VALIDATOR), "--search-root", str(tmp_path / "a"),
         "--platform", "Linux64", "--require-executable-bit"],
        capture_output=True, text=True)
    assert proc.returncode == 1
    assert "executable bit" in proc.stderr.lower()


def test_staging_restores_the_bit_the_artifact_zip_dropped(tmp_path):
    """A depot built from a 0644 binary ships a game nobody can launch. The
    content is untouched — only the mode the transport lost is put back."""
    artifact = make_player(tmp_path / "promoted", "Linux64", executable_bit=False)
    before = (artifact / "Game.x86_64").read_bytes()
    staging = tmp_path / "staging"
    proc = subprocess.run(
        ["bash", str(STEAM_DEPLOY)], capture_output=True, text=True,
        env={**os.environ, "ARTIFACT_DIR": str(artifact), "STAGING_DIR": str(staging),
             "STEAM_APP_ID": "480", "STEAM_DEPOT_ID": "480012",
             "STEAM_BRANCH": "internal", "DRY_RUN": "true",
             "RUNNER_TEMP": str(tmp_path)})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert os.access(staging / "Game.x86_64", os.X_OK)
    # The artifact itself is untouched, mode included.
    assert not os.access(artifact / "Game.x86_64", os.X_OK)
    assert (artifact / "Game.x86_64").read_bytes() == before
    # And the content fingerprint still matched, so the upload was allowed.
    assert "matches the verified artifact" in proc.stdout


def test_a_windows_player_missing_unityplayer_fails(tmp_path):
    make_player(tmp_path / "a", "Windows64", runtime=False)
    proc = validate_desktop(tmp_path / "a", "Windows64")
    assert proc.returncode == 1
    assert "unity runtime" in proc.stderr.lower()


def test_the_crash_handler_is_not_mistaken_for_the_game(tmp_path):
    root = make_player(tmp_path / "a", "Windows64")
    (root / "UnityCrashHandler64.exe").write_bytes(b"MZ" + b"\0" * 1024)
    proc = validate_desktop(root, "Windows64")
    assert proc.returncode == 0, proc.stdout
    assert "Game.exe" in proc.stdout


def test_an_empty_directory_fails_rather_than_passing_vacuously(tmp_path):
    (tmp_path / "empty").mkdir()
    proc = validate_desktop(tmp_path / "empty", "Windows64")
    assert proc.returncode == 1


def test_a_nested_player_is_found(tmp_path):
    """The uploaded artifact may wrap the player a directory or two deep."""
    make_player(tmp_path / "a" / "build" / "StandaloneWindows64", "Windows64")
    proc = validate_desktop(tmp_path / "a", "Windows64")
    assert proc.returncode == 0, proc.stdout


# ---------------------------------------------------------------------------
# Steam configuration — a distribution provider, resolved and fail-closed
# ---------------------------------------------------------------------------

def resolve_steam(platform="Windows64", phase="production", require_credentials=False,
                  **env):
    cmd = ["python3", str(STEAM_CONFIG), "--platform", platform, "--phase", phase]
    if require_credentials:
        cmd.append("--require-credentials")
    environment = {k: v for k, v in os.environ.items()
                   if not k.startswith("STEAM_")}
    environment.update(env)
    return subprocess.run(cmd, capture_output=True, text=True, env=environment)


def test_one_depot_object_covers_every_platform():
    """A Steam app has one identity and several depots. Splitting it into
    unrelated per-platform variables is how Windows and Linux end up pointing
    at different apps."""
    depots = json.dumps({"Windows64": "480011", "Linux64": "480012"})
    for platform, depot in (("Windows64", "480011"), ("Linux64", "480012")):
        proc = resolve_steam(platform, STEAM_APP_ID="480", STEAM_DEPOTS=depots)
        assert proc.returncode == 0, proc.stderr
        assert f'"depot-id": "{depot}"' in proc.stdout


def test_a_per_platform_variable_still_works():
    proc = resolve_steam("Linux64", STEAM_APP_ID="480", STEAM_DEPOT_LINUX64="480012")
    assert proc.returncode == 0, proc.stderr


@pytest.mark.parametrize("phase,branch", [
    ("internal", "internal"),
    ("external", "beta"),
    # Steam's live branch is literally called "default".
    ("production", "default"),
])
def test_each_phase_maps_to_a_branch(phase, branch):
    proc = resolve_steam("Windows64", phase, STEAM_APP_ID="480",
                         STEAM_DEPOTS='{"Windows64": "480011"}')
    assert f'"branch": "{branch}"' in proc.stdout


def test_the_branch_is_configurable():
    proc = resolve_steam("Windows64", "production", STEAM_APP_ID="480",
                         STEAM_DEPOTS='{"Windows64": "480011"}',
                         STEAM_BRANCH_PRODUCTION="live")
    assert '"branch": "live"' in proc.stdout


@pytest.mark.parametrize("env,missing", [
    ({}, "STEAM_APP_ID"),
    ({"STEAM_APP_ID": "480"}, "depot"),
    ({"STEAM_APP_ID": "not-a-number", "STEAM_DEPOTS": '{"Windows64": "1"}'}, "numeric"),
    ({"STEAM_APP_ID": "480", "STEAM_DEPOTS": "{oops"}, "valid JSON"),
    ({"STEAM_APP_ID": "480", "STEAM_DEPOTS": '{"Windows64": "abc"}'}, "numeric"),
])
def test_missing_or_invalid_configuration_fails_loudly(env, missing):
    """Not a silent skip. A deployment job that does nothing and reports
    success is how a release nobody shipped gets believed."""
    proc = resolve_steam("Windows64", **env)
    assert proc.returncode == 1
    assert missing in proc.stderr
    assert "Refusing to deploy" in proc.stderr


def test_credentials_are_required_only_when_publishing():
    base = dict(STEAM_APP_ID="480", STEAM_DEPOTS='{"Windows64": "480011"}')
    assert resolve_steam("Windows64", **base).returncode == 0
    proc = resolve_steam("Windows64", require_credentials=True, **base)
    assert proc.returncode == 1
    assert "STEAM_USERNAME" in proc.stderr


def test_no_credential_value_is_ever_printed():
    proc = resolve_steam("Windows64", require_credentials=True,
                         STEAM_APP_ID="480", STEAM_DEPOTS='{"Windows64": "480011"}',
                         STEAM_USERNAME="publisher", STEAM_CONFIG_VDF="c3VwZXItc2VjcmV0")
    assert proc.returncode == 0, proc.stderr
    assert "c3VwZXItc2VjcmV0" not in proc.stdout + proc.stderr
    assert "publisher" not in proc.stdout + proc.stderr


def test_a_non_steam_platform_is_a_usage_error():
    proc = resolve_steam("Android", STEAM_APP_ID="480")
    assert proc.returncode == 2


# ---------------------------------------------------------------------------
# The Steam deploy script — staging must not mutate the artifact
# ---------------------------------------------------------------------------

def test_staging_copies_the_artifact_without_changing_it(tmp_path):
    """SteamCMD wants a content directory of its own, which is the one place a
    promotion could rewrite the bytes it is supposed to be publishing."""
    artifact = make_player(tmp_path / "promoted", "Linux64")
    (artifact / "artifact-manifest.json").write_text('{"platform": "Linux64"}')
    before = {p.relative_to(artifact): p.read_bytes()
              for p in artifact.rglob("*") if p.is_file()}

    staging = tmp_path / "staging"
    proc = subprocess.run(
        ["bash", str(STEAM_DEPLOY)],
        capture_output=True, text=True,
        env={**os.environ, "ARTIFACT_DIR": str(artifact), "STAGING_DIR": str(staging),
             "STEAM_APP_ID": "480", "STEAM_DEPOT_ID": "480012",
             "STEAM_BRANCH": "internal", "DRY_RUN": "true",
             "RUNNER_TEMP": str(tmp_path)})
    assert proc.returncode == 0, proc.stdout + proc.stderr

    after = {p.relative_to(artifact): p.read_bytes()
             for p in artifact.rglob("*") if p.is_file()}
    assert before == after, "the promotion modified the artifact it downloaded"
    assert (staging / "Game.x86_64").is_file()
    # The manifest is not game content and does not belong in a depot.
    assert not (staging / "artifact-manifest.json").exists()


def test_staging_preserves_the_linux_executable_bit(tmp_path):
    """A depot uploaded from a `cp` that dropped the bit ships a game nobody
    can launch."""
    artifact = make_player(tmp_path / "promoted", "Linux64")
    staging = tmp_path / "staging"
    subprocess.run(
        ["bash", str(STEAM_DEPLOY)], capture_output=True, text=True, check=True,
        env={**os.environ, "ARTIFACT_DIR": str(artifact), "STAGING_DIR": str(staging),
             "STEAM_APP_ID": "480", "STEAM_DEPOT_ID": "480012",
             "STEAM_BRANCH": "internal", "DRY_RUN": "true",
             "RUNNER_TEMP": str(tmp_path)})
    assert os.access(staging / "Game.x86_64", os.X_OK)


def test_a_dry_run_needs_no_steam_credentials(tmp_path):
    """Verification and staging must be reachable in a repository that has no
    Steam account, or nobody can test the promotion path."""
    artifact = make_player(tmp_path / "promoted", "Windows64")
    proc = subprocess.run(
        ["bash", str(STEAM_DEPLOY)], capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if k not in
             ("STEAM_USERNAME", "STEAM_CONFIG_VDF")}
        | {"ARTIFACT_DIR": str(artifact), "STAGING_DIR": str(tmp_path / "s"),
           "STEAM_APP_ID": "480", "STEAM_DEPOT_ID": "480011",
           "STEAM_BRANCH": "internal", "DRY_RUN": "true",
           "RUNNER_TEMP": str(tmp_path)})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "nothing uploaded" in proc.stdout.lower()


def test_a_missing_artifact_directory_fails(tmp_path):
    proc = subprocess.run(
        ["bash", str(STEAM_DEPLOY)], capture_output=True, text=True,
        env={**os.environ, "ARTIFACT_DIR": str(tmp_path / "nope"),
             "STEAM_APP_ID": "480", "STEAM_DEPOT_ID": "480011",
             "STEAM_BRANCH": "internal", "DRY_RUN": "true",
             "RUNNER_TEMP": str(tmp_path)})
    assert proc.returncode == 1
    assert "does not exist" in proc.stderr


# ---------------------------------------------------------------------------
# The promotion workflows themselves
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", DESKTOP_PIPELINES)
def test_desktop_promotion_cannot_build(name):
    """I-005. The invariant checker enforces this across every promotion
    workflow; this pins it for the two new ones specifically."""
    body = (WORKFLOWS / name).read_text().lower()
    for operation in ("unity-builder", "unity-test-runner", "-executemethod",
                      "xcodebuild", "gradlew", "codesign", "buildplayer"):
        assert operation not in body, f"{name} contains a build operation: {operation}"


@pytest.mark.parametrize("name", DESKTOP_PIPELINES)
def test_desktop_promotion_pins_an_exact_run(name):
    inputs = triggers(name)["workflow_call"]["inputs"]
    assert inputs["source-run-id"]["required"] is True
    body = (WORKFLOWS / name).read_text()
    # Every download must name the run, or it silently takes this run's
    # artifacts — of which there are none.
    assert body.count("run-id: ${{ inputs.source-run-id }}") >= 4


@pytest.mark.parametrize("name", DESKTOP_PIPELINES)
def test_every_steam_phase_reverifies_the_artifact(name):
    """Each phase downloads the artifact again, so each phase must prove for
    itself what it is holding. An approval on the internal branch is not
    evidence about the bytes a later job fetched."""
    workflow = load(name)
    for job_id, job in workflow["jobs"].items():
        if not job_id.startswith("steam-"):
            continue
        steps = json.dumps(job["steps"])
        assert "release_manifest.py verify" in steps, f"{name}:{job_id}"


@pytest.mark.parametrize("name", DESKTOP_PIPELINES)
def test_steam_phases_are_environment_gated(name):
    workflow = load(name)
    environments = {job_id: job.get("environment")
                    for job_id, job in workflow["jobs"].items()
                    if job_id.startswith("steam-")}
    assert environments == {
        "steam-internal": "steam-internal",
        "steam-external": "steam-external",
        "steam-production": "steam-production",
    }, environments


@pytest.mark.parametrize("name", DESKTOP_PIPELINES)
def test_steam_secrets_are_optional_at_the_workflow_level(name):
    """Required secrets are checked when the call is RESOLVED, so declaring
    them required would make a dry run unresolvable in a repository with no
    Steam account — a platform capability blocked by a distribution provider,
    which is exactly what I-015 forbids."""
    secrets = triggers(name)["workflow_call"]["secrets"]
    for key in ("STEAM_USERNAME", "STEAM_CONFIG_VDF"):
        assert secrets[key].get("required") is False, key


@pytest.mark.parametrize("name", DESKTOP_PIPELINES)
def test_no_steam_id_is_hardcoded_in_the_toolkit(name):
    """App and depot ids are project configuration, never toolkit constants."""
    workflow = (WORKFLOWS / name).read_text()
    assert "vars.STEAM_APP_ID" in workflow
    assert "vars.STEAM_DEPOTS" in workflow


def test_the_toolkit_hardcodes_no_steam_app_id():
    """A real id anywhere in scripts/ or the workflows would publish one
    project's build into another's depot."""
    import re

    suspicious = re.compile(r"(app-?id|depot-?id)\s*[:=]\s*[\"']?\d{4,}", re.I)
    for path in list((REPO_ROOT / "scripts").rglob("*.py")) + \
            list((REPO_ROOT / "scripts").rglob("*.sh")) + \
            list(WORKFLOWS.glob("*.yml")):
        for line_number, line in enumerate(path.read_text().splitlines(), 1):
            assert not suspicious.search(line), f"{path}:{line_number}: {line.strip()}"


# ---------------------------------------------------------------------------
# Build / Release must not require Steam
# ---------------------------------------------------------------------------

def test_build_release_never_mentions_steam():
    """I-015 in its most direct form: the build pipeline does not know Steam
    exists, so no Steam configuration can block a Windows or Linux build."""
    pipeline = (WORKFLOWS / "unity-pipeline.yml").read_text()
    build_lane = (WORKFLOWS / "reusable-build-platform.yml").read_text()
    for body, name in ((build_lane, "reusable-build-platform.yml"),):
        assert "steam" not in body.lower(), f"{name} references Steam"
    # unity-pipeline may only mention Steam as a promotion hint in the report.
    for line in pipeline.splitlines():
        if "steam" in line.lower():
            assert "release-windows.yml" in line or "release-linux.yml" in line, line


# ---------------------------------------------------------------------------
# Release Set membership for desktop
# ---------------------------------------------------------------------------

PROVENANCE = {
    "builder": "game-ci/unity-builder@v5",
    "builderKind": "docker",
    "imageReference": "unityci/editor:6000.0.26f1-linux-il2cpp-3",
    "imageDigest": "sha256:" + "ab" * 32,
    "unityVersion": "6000.0.26f1",
    "runner": "Linux/X64",
    "provenanceStrength": "immutable",
}


def _desktop_release_set(tmp_path, platforms=("Windows64", "Linux64")):
    artifacts = tmp_path / "artifacts"
    names = {"Windows64": "release-windows", "Linux64": "release-linux"}
    for platform in platforms:
        directory = artifacts / names[platform]
        directory.mkdir(parents=True)
        (directory / "Game.bin").write_bytes(b"GAME" * 4096)
        (directory / "artifact-manifest.json").write_text(json.dumps({
            "platform": platform, "artifactName": names[platform],
            "artifactType": "EXE" if platform == "Windows64" else "LINUX",
            "version": "1.4.2", "buildNumber": "1042",
            "gitCommit": "abc123def456", "builderProvenance": PROVENANCE,
        }))
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


def test_desktop_artifacts_join_the_release_set(tmp_path):
    proc, manifest, _ = _desktop_release_set(tmp_path)
    assert proc.returncode == 0, proc.stderr
    data = json.loads(manifest.read_text())
    entries = {a["platform"]: a for a in data["artifacts"]}
    assert set(entries) == {"Windows64", "Linux64"}
    for platform, entry in entries.items():
        assert len(entry["sha256"]) == 64, platform
        assert entry["version"] == "1.4.2"
        assert entry["buildNumber"] == "1042"
        assert entry["commit"] == "abc123def456"
        assert entry["builderProvenance"]["provenanceStrength"] == "immutable"


def _verify(manifest, platform, artifact, **expect):
    cmd = ["python3", str(MANIFEST), "verify", "--manifest", str(manifest),
           "--platform", platform, "--artifact-path", str(artifact)]
    for key, value in expect.items():
        cmd += [f"--expect-{key.replace('_', '-')}", value]
    return subprocess.run(cmd, capture_output=True, text=True)


@pytest.mark.parametrize("platform,name", [
    ("Windows64", "release-windows"),
    ("Linux64", "release-linux"),
])
def test_desktop_promotion_accepts_the_exact_artifact(tmp_path, platform, name):
    _, manifest, artifacts = _desktop_release_set(tmp_path)
    proc = _verify(manifest, platform, artifacts / name,
                   run_id="34579047248", version="1.4.2", artifact_name=name)
    assert proc.returncode == 0, proc.stderr


def test_a_tampered_desktop_artifact_fails_promotion(tmp_path):
    _, manifest, artifacts = _desktop_release_set(tmp_path)
    tampered = tmp_path / "tampered"
    shutil.copytree(artifacts / "release-windows", tampered)
    (tampered / "Game.bin").write_bytes(b"EVIL" * 4096)
    proc = _verify(manifest, "Windows64", tampered, run_id="34579047248")
    assert proc.returncode == 1
    assert "checksum mismatch" in proc.stderr


@pytest.mark.parametrize("field,value", [
    ("run_id", "99999999"),
    ("version", "9.9.9"),
    ("artifact_name", "release-linux"),
])
def test_desktop_identity_mismatches_fail_promotion(tmp_path, field, value):
    _, manifest, artifacts = _desktop_release_set(tmp_path)
    proc = _verify(manifest, "Windows64", artifacts / "release-windows",
                   **{field: value})
    assert proc.returncode == 1, f"{field}={value} was accepted"


def test_a_platform_absent_from_the_set_cannot_be_promoted(tmp_path):
    """A disabled platform creates no artifact, so its promotion has nothing
    to verify and must refuse rather than invent one."""
    _, manifest, _ = _desktop_release_set(tmp_path, platforms=("Windows64",))
    proc = subprocess.run([
        "python3", str(MANIFEST), "verify", "--manifest", str(manifest),
        "--platform", "Linux64",
    ], capture_output=True, text=True)
    assert proc.returncode == 1
    assert "no Linux64 artifact" in proc.stderr
