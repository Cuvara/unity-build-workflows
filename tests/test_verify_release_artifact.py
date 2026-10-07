"""The promotion pipelines' shared download + verify step.

Twenty-two jobs across five pipeline-*-release.yml files each spelled the same
three steps (download the artifact from the Release Set run, download its
manifest, run release_manifest.py verify), and a fix to one -- the PR that
found production verifying `--artifact-path .` against the whole workspace --
had to be found and made in each. They now call
.github/actions/verify-release-artifact, and the invariant checker accepts
that step as a verification only while the action still does both halves.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
ACTION = REPO_ROOT / ".github" / "actions" / "verify-release-artifact" / "action.yml"
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
CHECKER = REPO_ROOT / "scripts" / "common" / "validate_pipeline_invariants.py"
# Thin wrappers (Windows, Linux) run pipeline-desktop-release.yml and hold
# no steps of their own.
PROMOTIONS = sorted(p for p in WORKFLOWS.glob("pipeline-*-release.yml")
                    if not all("uses" in j for j in yaml.safe_load(p.read_text(encoding="utf-8"))["jobs"].values()))
VERIFIERS = ("/verify-release-artifact", "/steam-publish")


def _action():
    return yaml.safe_load(ACTION.read_text(encoding="utf-8"))


def test_the_action_downloads_from_the_source_run_and_verifies():
    steps = _action()["runs"]["steps"]
    downloads = [s for s in steps if str(s.get("uses", "")).startswith("actions/download-artifact@")]
    assert [s["with"]["name"] for s in downloads] == ["${{ inputs.artifact-name }}", "release-manifest"]
    for s in downloads:
        assert s["with"]["run-id"] == "${{ inputs.source-run-id }}"
        assert s["with"]["github-token"] == "${{ inputs.github-token }}"
    assert downloads[0]["if"] == "${{ inputs.identity-only != 'true' }}"
    run = next(s for s in steps if "release_manifest.py verify" in str(s.get("run", "")))["run"]
    for flag in ("--expect-run-id", "--expect-artifact-name", "--expect-version"):
        assert flag in run
    # Inputs reach the script through env, never interpolated into it.
    assert "${{" not in run


@pytest.mark.parametrize("path", PROMOTIONS, ids=lambda p: p.name)
def test_every_promotion_job_uses_the_action(path):
    jobs = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]
    users = 0
    for job_id, job in jobs.items():
        for s in job.get("steps") or []:
            if str(s.get("uses", "")).endswith(VERIFIERS):
                users += 1
                w = s["with"]
                assert w["source-run-id"] == "${{ inputs.source-run-id }}", job_id
                assert w["artifact-name"] == "${{ inputs.artifact-name }}", job_id
                assert w["build-version"] == "${{ inputs.build-version }}", job_id
                assert w["github-token"] == "${{ github.token }}", job_id
            # Nothing downloads the platform artifact around the action.
            if str(s.get("uses", "")).startswith("actions/download-artifact"):
                assert s["with"].get("name") != "${{ inputs.artifact-name }}", (path.name, job_id)
        assert "release_manifest.py verify" not in json.dumps(job.get("steps") or []), (path.name, job_id)
    assert users >= 4, path.name


def test_steam_publish_verifies_through_the_shared_action():
    steps = yaml.safe_load((REPO_ROOT / ".github" / "actions" / "steam-publish" / "action.yml")
                           .read_text(encoding="utf-8"))["runs"]["steps"]
    verify = next(s for s in steps if str(s.get("uses", "")).endswith("/verify-release-artifact"))
    assert verify["with"]["source-run-id"] == "${{ inputs.source-run-id }}"
    assert verify["with"]["path"] == "promoted", "deploy_steam.sh stages ARTIFACT_DIR=promoted"


def test_the_checker_fails_when_steam_publish_stops_verifying(tmp_path):
    """steam-publish counts as a verification only through the action it
    calls; swap that call for a bare download and the Steam phases hold
    unverified bytes."""
    def drop(root):
        p = root / ".github" / "actions" / "steam-publish" / "action.yml"
        p.write_text(p.read_text(encoding="utf-8").replace(
            "uses: ./.toolkit/.github/actions/verify-release-artifact",
            "uses: actions/download-artifact@fa0a91b85d4f404e444e00e005971372dc801d16"), encoding="utf-8")
    r = _check(tmp_path, drop)
    assert r.returncode != 0
    assert "I-017" in r.stdout and "actions/steam-publish downloads the artifact" in r.stdout


def test_only_android_production_skips_the_download():
    seen = []
    for path in PROMOTIONS:
        for job_id, job in yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"].items():
            for s in job.get("steps") or []:
                if str(s.get("uses", "")).endswith("/verify-release-artifact") \
                        and s["with"].get("identity-only") == "true":
                    seen.append((path.name, job_id))
    # Play promotes the internal-track release; that job holds no binary.
    assert seen == [("pipeline-android-release.yml", "production-release")]


def _check(tmp_path, mutate=None):
    root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / ".github", root / ".github")
    shutil.copytree(REPO_ROOT / "scripts", root / "scripts")
    shutil.copytree(REPO_ROOT / "templates", root / "templates")
    if mutate:
        mutate(root)
    return subprocess.run([sys.executable, str(root / "scripts" / "common" / CHECKER.name)],
                          cwd=root, capture_output=True, text=True)


def test_the_checker_accepts_the_action(tmp_path):
    r = _check(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_checker_fails_when_the_action_stops_verifying(tmp_path):
    def gut(root):
        p = root / ".github" / "actions" / "verify-release-artifact" / "action.yml"
        p.write_text(p.read_text(encoding="utf-8").replace("release_manifest.py verify", "true"),
                     encoding="utf-8")
    r = _check(tmp_path, gut)
    assert r.returncode != 0
    assert "I-007" in r.stdout and "verify-release-artifact" in r.stdout
