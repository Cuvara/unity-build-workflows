"""Wiring for environment-scoped variables (docs/ENVIRONMENT_VARIABLES.md).

A job sees a GitHub Environment's variables only when it declares that
environment. resolve-config reads every vars.* the resolver consumes, so it has
to declare the build's environment -- and the name has to be known before the
job starts, which is why select-environment exists.
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PIPELINE = REPO_ROOT / ".github" / "workflows" / "unity-pipeline.yml"
DOC = REPO_ROOT / "docs" / "ENVIRONMENT_VARIABLES.md"

GENERIC = {
    "NEW_ENV_BUILD_PLATFORMS": "BUILD_PLATFORMS",
    "NEW_ENV_TEST_ENABLED": "TEST_ENABLED",
    "NEW_ENV_ADDRESSABLES_ENABLED": "ADDRESSABLES_ENABLED",
    "NEW_ENV_UNITY_DEFINE_SYMBOLS": "UNITY_DEFINE_SYMBOLS",
}


def _jobs():
    return yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))["jobs"]


def _step(job, step_id):
    return next(s for s in job["steps"] if s.get("id") == step_id)


def test_select_environment_runs_first_and_exposes_name():
    job = _jobs()["select-environment"]
    assert "needs" not in job
    assert job["runs-on"] == "ubuntu-latest"
    assert "environment" not in job, "the selector must not need the answer it computes"
    assert job["outputs"]["name"] == "${{ steps.select.outputs.secrets-environment }}"


def test_selector_runs_the_shared_resolver():
    step = _step(_jobs()["select-environment"], "select")
    assert "resolve_build_flow.sh" in step["run"]
    assert step["env"]["NEW_BUILD_ENVIRONMENT_SECRETS"] == "${{ vars.BUILD_ENVIRONMENT_SECRETS }}"
    for key in ("EVENT_NAME", "REF_NAME", "BASE_REF", "IN_ENVIRONMENT"):
        assert key in step["env"], key


def test_resolve_config_declares_the_selected_environment():
    job = _jobs()["resolve-config"]
    needs = job["needs"] if isinstance(job["needs"], list) else [job["needs"]]
    assert "select-environment" in needs
    assert job["environment"] == "${{ needs.select-environment.outputs.name }}"


def test_build_job_uses_the_same_environment_as_resolve_config():
    """Both come from the resolver's secrets-environment, so the values the
    config was resolved with and the secrets the build signs with belong to
    the same environment."""
    jobs = _jobs()
    assert (jobs["resolve-config"]["outputs"]["secrets-environment"]
            == "${{ steps.flow.outputs.secrets-environment }}")
    assert (jobs["build"]["with"]["secrets-environment"]
            == "${{ needs.resolve-config.outputs.secrets-environment }}")


def test_generic_names_are_wired_into_the_flow_step():
    env = _step(_jobs()["resolve-config"], "flow")["env"]
    for key, var in GENERIC.items():
        assert env.get(key) == "${{ vars.%s }}" % var, key


def test_generic_names_are_documented():
    text = DOC.read_text(encoding="utf-8")
    for var in GENERIC.values():
        assert "`%s`" % var in text, var
    assert "`BUILD_PLATFORMS_ENABLED`" in text, "capability vs. selection must be explained"
