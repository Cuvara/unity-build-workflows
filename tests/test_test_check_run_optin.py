"""game-ci's check run is opt-in, because the token is read-only by default.

`game-ci/unity-test-runner` posts its results as a GitHub check run when it is
given a token. The default `GITHUB_TOKEN` is read-only unless a repository says
otherwise, so passing one unconditionally produced

    ##[error]Resource not accessible by integration - .../checks/runs#create-a-check-run

on every run, twice — once per test mode. Granting the permission is not free
either: an explicit `permissions:` block REPLACES the default set, so a caller
that lists only `checks: write` also revokes `packages: read`, and the docker
lane can no longer pull its image from GHCR.
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
TESTS_WF = REPO_ROOT / ".github" / "workflows" / "reusable-unity-tests.yml"
PIPELINE = REPO_ROOT / ".github" / "workflows" / "unity-pipeline.yml"


def _inputs(path):
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    on = doc[True] if True in doc else doc["on"]
    return on["workflow_call"]["inputs"]


def test_the_toggle_exists_and_defaults_off():
    spec = _inputs(TESTS_WF)["post-test-check-run"]
    assert spec["type"] == "boolean"
    assert spec["default"] is False, "a default of on fails every run on a read-only token"


def test_no_token_is_handed_over_unless_asked():
    text = TESTS_WF.read_text(encoding="utf-8")
    assert "githubToken:   ${{ github.token }}" not in text, (
        "an unconditional token is what makes the action attempt the check run"
    )
    assert text.count("inputs.post-test-check-run && github.token || ''") == 2, (
        "both docker-lane invocations must be gated, not just EditMode"
    )


def test_the_permission_cost_is_written_down_where_it_is_switched_on():
    """Anyone enabling this has to grant the whole set, not just checks:write."""
    spec = _inputs(TESTS_WF)["post-test-check-run"]
    for needed in ("packages: read", "checks:   write", "contents: read"):
        assert needed in spec["description"], f"the description must list {needed!r}"


def test_the_pipeline_drives_it_from_a_repository_variable():
    text = PIPELINE.read_text(encoding="utf-8")
    assert "post-test-check-run: ${{ vars.POST_TEST_CHECK_RUN == 'true' }}" in text
