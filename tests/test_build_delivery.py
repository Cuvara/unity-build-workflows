"""
Getting a finished build to a person.

A GitHub Actions artifact URL returns 404 to anyone who is not signed in with
access to the repository — on a public repo too. Measured:

    GET /actions/runs/<id>/artifacts/<id>   → 307 → 404 (9 bytes)

So the link in a build notification was useless to exactly the people it was
for. These tests cover the two ways out — an object store, or a directory on a
machine you already run — and, more importantly, the rules that keep delivery
from becoming something a build depends on.
"""

import hashlib
import hmac
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "common" / "publish_build.py"
BUILD_LANE = REPO_ROOT / ".github" / "workflows" / "reusable-build-platform.yml"

sys.path.insert(0, str(REPO_ROOT / "scripts" / "common"))
from publish_build import sign_key  # noqa: E402


def run(tmp_path, key="develop/42/abc1234/game.apk", **env):
    source = tmp_path / "game.apk"
    if not source.exists():
        source.write_bytes(b"APK" * 1024)
    environment = {k: v for k, v in os.environ.items()
                   if not k.startswith(("R2_", "BUILD_", "CLOUDFLARE_", "FIREBASE_"))}
    environment.update(env)
    return subprocess.run(
        ["python3", str(SCRIPT), "--file", str(source), "--key", key],
        capture_output=True, text=True, env=environment)


# ---------------------------------------------------------------------------
# Delivery is optional, and never the reason a build fails
# ---------------------------------------------------------------------------

def test_unconfigured_projects_publish_nothing_and_stay_green(tmp_path):
    """The default. A project that has not chosen a host still builds."""
    proc = run(tmp_path)
    assert proc.returncode == 0
    assert "nothing published" in proc.stdout


@pytest.mark.parametrize("value", ["none", "NONE", "off", "false", ""])
def test_every_way_of_saying_off_is_off(tmp_path, value):
    assert run(tmp_path, BUILD_DELIVERY=value).returncode == 0


def test_a_missing_artifact_is_not_a_delivery_failure(tmp_path):
    """Nothing to publish means the build step already decided whether
    producing nothing was a problem. Delivery does not get a second vote."""
    proc = subprocess.run(
        ["python3", str(SCRIPT), "--file", str(tmp_path / "nope.apk"),
         "--key", "a/b.apk"],
        capture_output=True, text=True,
        env={**os.environ, "BUILD_DELIVERY": "r2"})
    assert proc.returncode == 0
    assert "nothing to publish" in proc.stdout


# ---------------------------------------------------------------------------
# Misconfiguration is different — it fails closed
# ---------------------------------------------------------------------------

def test_r2_without_credentials_fails_rather_than_skipping(tmp_path):
    """Asking for a host and not configuring it is a mistake worth stopping
    for. Skipping quietly gives a green pipeline that never delivers."""
    proc = run(tmp_path, BUILD_DELIVERY="r2")
    assert proc.returncode == 1
    assert "R2_ACCESS_KEY_ID" in proc.stderr
    assert "or set BUILD_DELIVERY=none" in proc.stderr


def test_local_without_a_directory_fails(tmp_path):
    proc = run(tmp_path, BUILD_DELIVERY="local")
    assert proc.returncode == 1
    assert "BUILD_PUBLISH_DIR" in proc.stderr


def test_local_without_a_base_url_fails(tmp_path):
    """A copied file nobody has a link to is not a delivery."""
    proc = run(tmp_path, BUILD_DELIVERY="local",
               BUILD_PUBLISH_DIR=str(tmp_path / "www"))
    assert proc.returncode == 1
    assert "BUILD_PUBLISH_BASE_URL" in proc.stderr


def test_an_unknown_provider_fails(tmp_path):
    proc = run(tmp_path, BUILD_DELIVERY="dropbox")
    assert proc.returncode == 1
    assert "Valid: r2, local, firebase, none" in proc.stderr


# ---------------------------------------------------------------------------
# Firebase App Distribution
# ---------------------------------------------------------------------------

def test_firebase_without_credentials_fails_rather_than_skipping(tmp_path):
    """Asking for firebase without a service account is misconfiguration."""
    proc = run(tmp_path, BUILD_DELIVERY="firebase")
    assert proc.returncode == 1
    assert "FIREBASE_SERVICE_ACCOUNT_JSON" in proc.stderr
    assert "BUILD_DELIVERY=none" in proc.stderr


def test_firebase_without_app_id_fails(tmp_path):
    proc = run(tmp_path, BUILD_DELIVERY="firebase",
               FIREBASE_SERVICE_ACCOUNT_JSON='{"type":"service_account"}')
    assert proc.returncode == 1
    assert "FIREBASE_APP_ID" in proc.stderr


def test_firebase_unsupported_file_type_fails(tmp_path):
    """Firebase only accepts APK, AAB and IPA."""
    source = tmp_path / "game.zip"
    source.write_bytes(b"ZIP" * 100)
    environment = {k: v for k, v in os.environ.items()
                   if not k.startswith(("R2_", "BUILD_", "CLOUDFLARE_", "FIREBASE_"))}
    environment.update({
        "BUILD_DELIVERY": "firebase",
        "FIREBASE_SERVICE_ACCOUNT_JSON": '{"type":"service_account"}',
        "FIREBASE_APP_ID": "1:123:android:abc",
    })
    proc = subprocess.run(
        ["python3", str(SCRIPT), "--file", str(source), "--key", "k"],
        capture_output=True, text=True, env=environment)
    assert proc.returncode == 1
    assert "does not support .zip" in proc.stderr


def test_firebase_cli_not_installed(tmp_path, monkeypatch):
    """When firebase CLI is missing, report clearly."""
    import publish_build

    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", '{"type":"service_account"}')
    monkeypatch.setenv("FIREBASE_APP_ID", "1:123:android:abc")
    monkeypatch.setenv("FIREBASE_TESTER_GROUPS", "")
    monkeypatch.setenv("FIREBASE_RELEASE_NOTES", "")

    # Make subprocess.run raise FileNotFoundError (simulating missing CLI)
    monkeypatch.setattr(publish_build.shutil, "which", lambda _: None)
    original_run = subprocess.run
    def fake_run(cmd, **kwargs):
        if cmd[0] == "firebase":
            raise FileNotFoundError("firebase not found")
        return original_run(cmd, **kwargs)
    monkeypatch.setattr(subprocess, "run", fake_run)

    source = tmp_path / "game.apk"
    source.write_bytes(b"APK" * 100)
    url, problem = publish_build.publish_firebase(source, "k")
    assert url is None
    assert "firebase-tools" in problem


def test_firebase_parse_testing_uri():
    """The tester link is extracted from Firebase CLI output."""
    import publish_build

    stdout = (
        "i  uploading binary...\n"
        "✔  uploaded binary successfully\n"
        "i  View this release in the Firebase console: "
        "https://console.firebase.google.com/project/my-project/appdistribution\n"
        "i  Share this release with testers who have access: "
        "https://appdistribution.firebase.google.com/testerapps/1:123:android:abc/releases/abc123\n"
    )
    uri = publish_build._parse_firebase_testing_uri(stdout)
    assert uri == "https://appdistribution.firebase.google.com/testerapps/1:123:android:abc/releases/abc123"


def test_firebase_parse_testing_uri_missing():
    """When no testing URI is found, return None."""
    import publish_build

    uri = publish_build._parse_firebase_testing_uri("some random output\n")
    assert uri is None


# ---------------------------------------------------------------------------
# The local provider
# ---------------------------------------------------------------------------

def test_local_copies_the_build_and_returns_a_link(tmp_path):
    proc = run(tmp_path, BUILD_DELIVERY="local",
               BUILD_PUBLISH_DIR=str(tmp_path / "www"),
               BUILD_PUBLISH_BASE_URL="https://builds.example.com/")
    assert proc.returncode == 0
    assert "https://builds.example.com/develop/42/abc1234/game.apk" in proc.stdout
    published = tmp_path / "www" / "develop" / "42" / "abc1234" / "game.apk"
    assert published.is_file()
    assert published.read_bytes() == (tmp_path / "game.apk").read_bytes()


def test_local_creates_the_directories_it_needs(tmp_path):
    proc = run(tmp_path, key="a/deep/nested/path/game.apk",
               BUILD_DELIVERY="local",
               BUILD_PUBLISH_DIR=str(tmp_path / "www"),
               BUILD_PUBLISH_BASE_URL="https://x.test")
    assert proc.returncode == 0
    assert (tmp_path / "www" / "a" / "deep" / "nested" / "path" / "game.apk").is_file()


def test_an_unwritable_destination_is_reported(tmp_path):
    blocker = tmp_path / "www"
    blocker.write_text("not a directory")
    proc = run(tmp_path, BUILD_DELIVERY="local",
               BUILD_PUBLISH_DIR=str(blocker),
               BUILD_PUBLISH_BASE_URL="https://x.test")
    assert proc.returncode == 1
    assert "could not copy" in proc.stderr


# ---------------------------------------------------------------------------
# The signing, which is the part that is silently wrong or silently right
# ---------------------------------------------------------------------------

def test_sigv4_matches_the_published_aws_test_vector():
    """Signed here with hmac rather than shelling out to aws-cli, which is
    absent on most self-hosted runners. A signing bug produces a 403 that
    looks like a credentials problem, so this is checked against AWS's own
    documented vector."""
    key = sign_key("wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
                   "20150830", "us-east-1", "iam")
    assert key.hex() == (
        "c4afb1cc5771d871763a393e44b703571b55cc28424d1a5e86da6ed3c154a4b9")


def test_r2_warns_when_the_public_url_is_missing(tmp_path, monkeypatch):
    """Uploading to the S3 endpoint and calling that a download link would be
    handing someone a URL that does not open in a browser."""
    import publish_build

    monkeypatch.setattr(publish_build, "put_object",
                        lambda *a, **k: "https://acct.r2.cloudflarestorage.com/b/k")
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    monkeypatch.setenv("R2_BUCKET", "b")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "id")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.delenv("R2_PUBLIC_BASE_URL", raising=False)
    source = tmp_path / "game.apk"
    source.write_bytes(b"x")
    url, problem = publish_build.publish_r2(source, "k")
    assert url
    assert "R2_PUBLIC_BASE_URL is not set" in problem


def test_r2_uses_the_public_base_when_given(tmp_path, monkeypatch):
    import publish_build

    monkeypatch.setattr(publish_build, "put_object", lambda *a, **k: "private")
    for name, value in (("R2_ACCOUNT_ID", "acct"), ("R2_BUCKET", "b"),
                        ("R2_ACCESS_KEY_ID", "id"),
                        ("R2_SECRET_ACCESS_KEY", "secret"),
                        ("R2_PUBLIC_BASE_URL", "https://cdn.example.com/")):
        monkeypatch.setenv(name, value)
    source = tmp_path / "game.apk"
    source.write_bytes(b"x")
    url, problem = publish_build.publish_r2(source, "develop/1/game.apk")
    assert url == "https://cdn.example.com/develop/1/game.apk"
    assert problem is None


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def test_the_build_lane_publishes_and_the_choice_is_an_input():
    workflow = yaml.safe_load(BUILD_LANE.read_text())
    inputs = (workflow.get("on") or workflow[True])["workflow_call"]["inputs"]
    assert inputs["build-delivery"]["default"] == "none", "must be opt-in"
    assert "publish_build.py" in BUILD_LANE.read_text()


def test_the_credentials_are_optional_on_the_interface():
    """A required secret is checked when the call is resolved, so requiring
    these would stop a project with no object store from building at all."""
    for path in (BUILD_LANE,
                 REPO_ROOT / ".github" / "workflows" / "unity-pipeline.yml"):
        workflow = yaml.safe_load(path.read_text())
        secrets = ((workflow.get("on") or workflow[True])["workflow_call"]
                   .get("secrets") or {})
        for name in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY",
                     "FIREBASE_SERVICE_ACCOUNT_JSON"):
            assert secrets[name].get("required") is False, f"{path.name}:{name}"


def test_discord_prefers_a_link_a_person_can_open():
    """The published URL wins over the artifact URL, which 404s for anyone not
    signed in with repository access."""
    action = (REPO_ROOT / ".github" / "actions" / "discord-upload-build"
              / "action.yml").read_text()
    assert "_PLAT_DLURL" in action
    assert 'if [ -n "${_P_DLURL}" ]; then\n            BIN_URL="${_P_DLURL}"' in action


def test_the_key_includes_the_commit_so_a_public_bucket_is_not_a_listing():
    """Branch, run number and short SHA. A public bucket with predictable
    paths is a directory listing for anyone who guesses the scheme."""
    body = BUILD_LANE.read_text()
    assert 'KEY="${GITHUB_REF_NAME//\\//-}/${GITHUB_RUN_NUMBER}/${GITHUB_SHA:0:7}/' in body


def test_no_credential_is_passed_on_a_command_line():
    """`ps` is readable by other processes on a self-hosted runner."""
    body = BUILD_LANE.read_text()
    publish = body[body.index("Publish the build for download"):]
    publish = publish[:publish.index("Summarise the Unity log")]
    assert "--file" in publish and "--key" in publish
    for secret in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        # Present as an env var, never as an argument.
        assert f"--{secret.lower()}" not in publish
        assert f"{secret}:" in publish


@pytest.mark.parametrize("suffix", [".apk", ".aab", ".ipa"])
@pytest.mark.parametrize("outcome", ["success", "error", "timeout", "missing_link"])
def test_firebase_upload_contract_and_credential_cleanup(tmp_path, monkeypatch, suffix, outcome):
    import publish_build
    source = tmp_path / ("game" + suffix)
    source.write_bytes(b"build")
    creds = '{"type":"service_account"}'
    uri = "https://appdistribution.firebase.google.com/testerapps/app/releases/release"
    monkeypatch.setenv("FIREBASE_SERVICE_ACCOUNT_JSON", creds)
    monkeypatch.setenv("FIREBASE_APP_ID", "app")
    monkeypatch.setenv("FIREBASE_TESTER_GROUPS", "qa")
    monkeypatch.setenv("FIREBASE_RELEASE_NOTES", "QA build")
    credential_paths = []

    def fake_run(cmd, **kwargs):
        assert "--json" in cmd and "--non-interactive" in cmd
        assert cmd[cmd.index("--groups") + 1] == "qa"
        assert cmd[cmd.index("--release-notes") + 1] == "QA build"
        assert creds not in cmd
        path = Path(kwargs["env"]["GOOGLE_APPLICATION_CREDENTIALS"])
        assert path.read_text() == creds
        credential_paths.append(path)
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(cmd, 600)
        return subprocess.CompletedProcess(cmd, 1 if outcome == "error" else 0,
            json.dumps({"status":"success", "result": {"testingUri": uri} if outcome == "success" else {}}),
            str(path) + " upload failed" if outcome == "error" else "")

    monkeypatch.setattr(publish_build.subprocess, "run", fake_run)
    url, problem = publish_build.publish_firebase(source, "key")
    assert credential_paths and all(not p.exists() for p in credential_paths)
    if outcome == "success":
        assert (url, problem) == (uri, None)
    else:
        assert url is None and problem
        assert all(str(p) not in problem for p in credential_paths)


def test_firebase_output_and_summary_are_utf8(tmp_path, monkeypatch):
    import publish_build
    source = tmp_path / "game.apk"
    source.write_bytes(b"build")
    output, summary = tmp_path / "output", tmp_path / "summary"
    uri = "https://appdistribution.firebase.google.com/testerapps/app/releases/release"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(publish_build, "publish_firebase", lambda *args: (uri, None))
    assert publish_build.main(["--file", str(source), "--key", "key", "--provider", "firebase", "--github-output"]) == 0
    assert output.read_text(encoding="utf-8") == f"download-url={uri}\n"
    assert uri in summary.read_text(encoding="utf-8")
