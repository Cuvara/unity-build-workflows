"""
Pipeline stage architecture contract.

These tests pin the orchestration properties the pipeline graph is supposed to
have, independent of what any individual job does:

  * every user-visible node is named `NN / …` so the graph reads as stages
  * the quality gate (02) is on the path from every expensive stage-03 build
  * platform builds fan out — none depends on another platform
  * stage 04 validates each artifact independently of the other platforms
  * publish (05/06) depends on validation, never directly on the build
  * production release sits behind a GitHub Environment
  * a publish can start from a stored artifact without rebuilding (`start-phase`)
  * artifact metadata is produced by 03 and consumed by 04/05, not rediscovered

They read the workflow YAML rather than running Actions, in the same style as
test_platform_selection.py.
"""
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"

PIPELINE = WORKFLOWS / "unity-pipeline.yml"
ORCHESTRATOR = WORKFLOWS / "release-orchestrator.yml"
ANDROID_RELEASE = WORKFLOWS / "pipeline-android-release.yml"
IOS_RELEASE = WORKFLOWS / "pipeline-ios-release.yml"
WEBGL_RELEASE = WORKFLOWS / "pipeline-webgl-release.yml"
BUILD_PLATFORM = WORKFLOWS / "reusable-build-platform.yml"

# Platform build jobs in unity-pipeline.yml (stage 03).
PLATFORM_BUILD_JOBS = [
    "build-android",
    "build-webgl",
    "build-linux64",
    "build-linuxserver",
    "build-windows64",
    "build-ios",
]

# Stage-04 job → the single stage-03 job it validates.
VALIDATION_JOBS = {
    "validate-artifact-android": "build-android",
    "validate-artifact-webgl": "build-webgl",
    "validate-artifact-ios": "build-ios",
}

STAGE_PREFIX = re.compile(r"^\d{2} / ")


def load(path):
    with path.open() as fh:
        return yaml.safe_load(fh)


def triggers(workflow):
    """PyYAML parses a bare `on:` key as the boolean True."""
    return workflow[True] if True in workflow else workflow["on"]


def call_inputs(workflow):
    return (triggers(workflow).get("workflow_call") or {}).get("inputs") or {}


def needs_of(job):
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else list(needs)


def transitive_needs(jobs, job_id, seen=None):
    """Every job reachable through the needs graph from job_id."""
    seen = set() if seen is None else seen
    for dep in needs_of(jobs.get(job_id, {})):
        if dep in seen:
            continue
        seen.add(dep)
        transitive_needs(jobs, dep, seen)
    return seen


@pytest.fixture(scope="module")
def pipeline():
    return load(PIPELINE)


@pytest.fixture(scope="module")
def pipeline_jobs(pipeline):
    return pipeline["jobs"]


# ---------------------------------------------------------------------------
# Node naming — platform, configuration and artifact type must be visible
# ---------------------------------------------------------------------------

def test_every_pipeline_job_is_stage_numbered(pipeline_jobs):
    """Every node in unity-pipeline.yml announces its stage."""
    unnumbered = {
        job_id: job.get("name")
        for job_id, job in pipeline_jobs.items()
        if not STAGE_PREFIX.match(str(job.get("name", "")))
    }
    assert not unnumbered, (
        "these nodes render without a stage number, so the graph cannot be read "
        f"as stages: {unnumbered}"
    )


def test_no_job_name_depends_on_the_needs_context(pipeline_jobs):
    """A `needs.*` expression in a job name is left uninterpolated when the job
    is skipped, so the graph shows raw `${{ … }}` text to the reader."""
    offenders = {
        job_id: job.get("name")
        for job_id, job in pipeline_jobs.items()
        if "needs." in str(job.get("name", ""))
    }
    assert not offenders, (
        "these node names render as literal ${{ … }} when the job is skipped: "
        f"{offenders}"
    )


def test_no_ambiguous_build_node_names(pipeline_jobs):
    """`Build`, `build 2`, `pipeline/.../build` must not reach the UI."""
    for job_id, job in pipeline_jobs.items():
        name = str(job.get("name", ""))
        assert name.lower().strip() not in {"build", "build 2", "build 3"}, (
            f"{job_id} renders as {name!r} — the node name must say which platform"
        )


@pytest.mark.parametrize(
    "job_id,platform",
    [
        ("build-android", "Android"),
        ("build-webgl", "WebGL"),
        ("build-linux64", "Linux64"),
        ("build-linuxserver", "LinuxServer"),
        ("build-windows64", "Windows64"),
        ("build-ios", "iOS"),
    ],
)
def test_build_node_names_carry_the_platform(pipeline_jobs, job_id, platform):
    assert platform in str(pipeline_jobs[job_id]["name"]), (
        f"{job_id} must name its platform so a failure is identifiable at a glance"
    )


@pytest.mark.parametrize("job_id", PLATFORM_BUILD_JOBS + ["build-addressables"])
def test_build_node_label_carries_configuration_and_artifact_type(pipeline_jobs, job_id):
    """The second half of the node name (node-label) supplies config + artifact.

    GitHub renders a called workflow as `<caller job name> / <inner job name>`,
    so `03 / Android` + `Production / AAB` reads `03 / Android / Production / AAB`.
    """
    with_block = pipeline_jobs[job_id]["with"]
    assert "node-label" in with_block, f"{job_id} does not set a node-label"
    assert "configuration" in with_block, f"{job_id} does not record its configuration"
    assert "artifact-type" in with_block, f"{job_id} does not declare its artifact type"


def test_resolve_config_exports_node_labels(pipeline_jobs):
    """Labels resolve once in stage 01, not per build job."""
    outputs = pipeline_jobs["resolve-config"]["outputs"]
    for key in (
        "configuration",
        "android-artifact-type",
        "label-android",
        "label-ios",
        "label-webgl",
    ):
        assert key in outputs, f"resolve-config does not export {key}"


# ---------------------------------------------------------------------------
# Quality gate — tests complete before expensive builds start
# ---------------------------------------------------------------------------

def test_quality_gate_job_exists(pipeline_jobs):
    assert "quality-gate" in pipeline_jobs
    assert "passed" in pipeline_jobs["quality-gate"]["outputs"]


def test_quality_gate_depends_on_unity_tests(pipeline_jobs):
    assert "unity-tests" in needs_of(pipeline_jobs["quality-gate"]), (
        "the gate must wait on the test job, otherwise it gates nothing"
    )


@pytest.mark.parametrize("job_id", PLATFORM_BUILD_JOBS + ["build-addressables"])
def test_unity_tests_block_every_expensive_build(pipeline_jobs, job_id):
    """Unity Tests FAIL → no release build starts.

    The dependency must be transitive-through-the-gate, and the build's `if:`
    must actually consult the gate's verdict — a `needs` edge alone still runs
    the job when the gate is skipped.
    """
    reachable = transitive_needs(pipeline_jobs, job_id)
    assert "quality-gate" in reachable, f"{job_id} does not depend on the quality gate"
    assert "unity-tests" in reachable, f"{job_id} can start while Unity Tests are running"

    condition = str(pipeline_jobs[job_id].get("if", ""))
    assert "needs.quality-gate.outputs.passed == 'true'" in condition, (
        f"{job_id} does not check the gate verdict, so a failed gate would not stop it"
    )


def test_quality_gate_treats_skipped_as_pass(pipeline_jobs):
    """run-tests=false skips the test job; that must not close the gate."""
    body = yaml.dump(pipeline_jobs["quality-gate"])
    assert "success|skipped" in body, (
        "the gate must accept `skipped` — validate-license is skipped on the local "
        "build engine and unity-tests is skipped when run-tests resolves false"
    )


# ---------------------------------------------------------------------------
# Fan-out — platform builds are independent after the gate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("job_id", PLATFORM_BUILD_JOBS)
def test_platform_builds_do_not_depend_on_each_other(pipeline_jobs, job_id):
    """Quality gate PASS → Android + iOS + WebGL run independently."""
    others = set(PLATFORM_BUILD_JOBS) - {job_id}
    depends_on_peers = others & transitive_needs(pipeline_jobs, job_id)
    assert not depends_on_peers, (
        f"{job_id} depends on {sorted(depends_on_peers)} — a failure in one platform "
        "would then invalidate the others"
    )


# ---------------------------------------------------------------------------
# Stage 04 — artifact validation is explicit and independent
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("validation_job,build_job", VALIDATION_JOBS.items())
def test_validation_job_exists_and_is_stage_04(pipeline_jobs, validation_job, build_job):
    assert validation_job in pipeline_jobs, f"{validation_job} is missing"
    assert str(pipeline_jobs[validation_job]["name"]).startswith("04 / ")


@pytest.mark.parametrize("validation_job,build_job", VALIDATION_JOBS.items())
def test_validation_depends_only_on_its_own_build(pipeline_jobs, validation_job, build_job):
    """Android's validation must not wait on — or fail with — iOS."""
    reachable = transitive_needs(pipeline_jobs, validation_job)
    assert build_job in reachable
    foreign = (set(PLATFORM_BUILD_JOBS) - {build_job}) & reachable
    assert not foreign, (
        f"{validation_job} also depends on {sorted(foreign)}; a failure there would "
        "leave this artifact unvalidated for no reason"
    )


@pytest.mark.parametrize("validation_job,build_job", VALIDATION_JOBS.items())
def test_validation_downloads_rather_than_rebuilds(pipeline_jobs, validation_job, build_job):
    """Validation consumes the stored artifact — it never re-runs Unity."""
    steps = pipeline_jobs[validation_job]["steps"]
    uses = [str(s.get("uses", "")) for s in steps]
    assert any(u.startswith("actions/download-artifact") for u in uses), (
        f"{validation_job} must download the stored artifact"
    )
    assert not any("reusable-build-platform" in u for u in uses)


def test_validation_reads_the_artifact_type_from_the_build(pipeline_jobs):
    """Downstream stages are told the artifact type, they do not guess it."""
    steps = pipeline_jobs["validate-artifact-android"]["steps"]
    body = yaml.dump(steps)
    assert "needs.build-android.outputs.artifact-type" in body, (
        "stage 04 must take the artifact type from the build that produced it"
    )


# ---------------------------------------------------------------------------
# Artifact metadata — first-class outputs
# ---------------------------------------------------------------------------

def test_build_platform_exports_artifact_metadata():
    workflow = load(BUILD_PLATFORM)
    outputs = (triggers(workflow)["workflow_call"].get("outputs") or {})
    for key in (
        "artifact-name",
        "artifact-type",
        "artifact-path",
        "artifact-size-bytes",
        "artifact-sha256",
        "manifest-artifact-name",
        "configuration",
    ):
        assert key in outputs, f"reusable-build-platform.yml does not export {key}"


def test_build_platform_writes_an_artifact_manifest():
    body = BUILD_PLATFORM.read_text()
    assert "artifact_manifest.py" in body, (
        "stage 03 must write an artifact manifest so stages 04/05 do not have to "
        "rediscover the artifact"
    )
    assert "build-manifest-${{ inputs.platform }}" in body


def test_build_platform_node_label_is_overridable():
    workflow = load(BUILD_PLATFORM)
    inputs = triggers(workflow)["workflow_call"]["inputs"]
    assert "node-label" in inputs
    # Callers that do not set one keep the previous display name.
    assert inputs["node-label"].get("default") == ""
    assert "Build {0}" in str(workflow["jobs"]["build"]["name"])


# ---------------------------------------------------------------------------
# Build vs Publish — separate stages, separate workflows
# ---------------------------------------------------------------------------

def test_ci_pipeline_never_publishes(pipeline_jobs):
    """unity-pipeline.yml builds and validates; it must not touch a store."""
    body = yaml.dump(pipeline_jobs)
    for forbidden in (
        "upload_google_play.py",
        "google-play-promote",
        "ios-testflight",
        "ios-asc-submit-review",
        "deploy_cloudflare_pages.sh",
    ):
        assert forbidden not in body, (
            f"unity-pipeline.yml references {forbidden} — publishing belongs to the "
            "release pipelines, not the build pipeline"
        )


@pytest.mark.parametrize(
    "path,publish_jobs,validation_job",
    [
        (ANDROID_RELEASE, ["internal-testing"], "validate-aab"),
        (WEBGL_RELEASE, ["deploy-staging"], "validate-webgl"),
    ],
)
def test_publish_depends_on_artifact_validation(path, publish_jobs, validation_job):
    jobs = load(path)["jobs"]
    for publish_job in publish_jobs:
        reachable = transitive_needs(jobs, publish_job)
        assert validation_job in reachable, (
            f"{path.name}:{publish_job} can publish an artifact that stage 04 never checked"
        )


@pytest.mark.parametrize(
    "path,publish_job",
    [
        (ANDROID_RELEASE, "internal-testing"),
        (IOS_RELEASE, "internal-testing"),
        (WEBGL_RELEASE, "deploy-staging"),
        (WEBGL_RELEASE, "deploy-production"),
    ],
)
def test_publish_downloads_instead_of_rebuilding(path, publish_job):
    """Publishing an already-built artifact must not re-run Unity."""
    job = load(path)["jobs"][publish_job]
    uses = [str(s.get("uses", "")) for s in job["steps"]]
    assert any(u.startswith("actions/download-artifact") for u in uses), (
        f"{path.name}:{publish_job} does not download the stored artifact"
    )
    assert not any("unity-build-" in u for u in uses)


@pytest.mark.parametrize("path", [ANDROID_RELEASE, IOS_RELEASE, WEBGL_RELEASE])
def test_dry_run_supports_build_only(path):
    """Build Only: dry-run must exist and must gate every publish node."""
    workflow = load(path)
    assert "dry-run" in call_inputs(workflow), f"{path.name} has no dry-run input"
    published = [
        job_id
        for job_id, job in workflow["jobs"].items()
        if job.get("environment") in {"internal-testing", "external-testing", "staging", "production"}
    ]
    assert published, f"{path.name} declares no publish nodes"
    for job_id in published:
        assert "!inputs.dry-run" in str(workflow["jobs"][job_id].get("if", "")), (
            f"{path.name}:{job_id} publishes even on a dry run"
        )


# ---------------------------------------------------------------------------
# Retry — publish a stored artifact without rebuilding
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [ANDROID_RELEASE, IOS_RELEASE, WEBGL_RELEASE])
def test_start_phase_allows_skipping_the_build(path):
    workflow = load(path)
    inputs = call_inputs(workflow)
    assert "start-phase" in inputs, f"{path.name} cannot resume from a later phase"

    jobs = workflow["jobs"]
    build_jobs = [j for j in jobs if j.startswith("build-")]
    assert build_jobs, f"{path.name} has no build job"
    for job_id in build_jobs:
        assert "inputs.start-phase == 'build'" in str(jobs[job_id].get("if", "")), (
            f"{path.name}:{job_id} rebuilds even when resuming from a later phase"
        )


@pytest.mark.parametrize(
    "path,publish_job,phase",
    [
        (ANDROID_RELEASE, "internal-testing", "internal"),
        (ANDROID_RELEASE, "external-testing", "external"),
        (ANDROID_RELEASE, "production-release", "production"),
        (IOS_RELEASE, "internal-testing", "internal"),
        (IOS_RELEASE, "external-testing", "external"),
        (IOS_RELEASE, "production-release", "production"),
        (WEBGL_RELEASE, "deploy-staging", "staging"),
        (WEBGL_RELEASE, "deploy-production", "production"),
    ],
)
def test_each_publish_phase_is_independently_startable(path, publish_job, phase):
    condition = str(load(path)["jobs"][publish_job].get("if", ""))
    assert f"inputs.start-phase == '{phase}'" in condition, (
        f"{path.name}:{publish_job} cannot be retried on its own"
    )


# ---------------------------------------------------------------------------
# Release approval — production is never automatic
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "path,production_job",
    [
        (ANDROID_RELEASE, "production-release"),
        (IOS_RELEASE, "production-release"),
        (WEBGL_RELEASE, "deploy-production"),
    ],
)
def test_production_release_sits_behind_an_environment(path, production_job):
    """The approval boundary is a GitHub Environment — the only mechanism
    Actions offers for a human gate."""
    job = load(path)["jobs"][production_job]
    assert job.get("environment") == "production", (
        f"{path.name}:{production_job} has no production environment, so a green build "
        "would publish to production unattended"
    )


@pytest.mark.parametrize("path", [ANDROID_RELEASE, IOS_RELEASE, WEBGL_RELEASE])
def test_store_credentials_are_not_required_at_the_call_boundary(path):
    """A `required: true` secret is validated when the call is RESOLVED, which
    makes Build Only impossible in a repo without store credentials."""
    secrets = (triggers(load(path))["workflow_call"].get("secrets") or {})
    required = [name for name, spec in secrets.items() if (spec or {}).get("required")]
    assert not required, (
        f"{path.name} requires {required} to resolve the call, blocking a build-only run"
    )


# ---------------------------------------------------------------------------
# Release orchestrator — gate, fan-out, independence
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def orchestrator_jobs():
    return load(ORCHESTRATOR)["jobs"]


def test_orchestrator_has_a_quality_gate(orchestrator_jobs):
    assert "quality-gate" in orchestrator_jobs
    assert "unity-tests" in needs_of(orchestrator_jobs["quality-gate"])


@pytest.mark.parametrize("job_id", ["android-release", "ios-release", "webgl-release"])
def test_orchestrator_platforms_wait_for_the_gate(orchestrator_jobs, job_id):
    assert job_id in orchestrator_jobs, f"{job_id} is missing from the orchestrator"
    condition = str(orchestrator_jobs[job_id].get("if", ""))
    assert "needs.quality-gate.outputs.passed == 'true'" in condition


@pytest.mark.parametrize("job_id", ["android-release", "ios-release", "webgl-release"])
def test_orchestrator_platforms_are_mutually_independent(orchestrator_jobs, job_id):
    peers = {"android-release", "ios-release", "webgl-release"} - {job_id}
    assert not peers & transitive_needs(orchestrator_jobs, job_id)


def test_orchestrator_covers_all_three_platforms_in_the_report(orchestrator_jobs):
    report_needs = needs_of(orchestrator_jobs["report"])
    for job_id in ("android-release", "ios-release", "webgl-release", "quality-gate"):
        assert job_id in report_needs, f"the report cannot see {job_id}"


# ---------------------------------------------------------------------------
# Failure reporting
# ---------------------------------------------------------------------------

def test_final_report_names_the_failing_stage(pipeline_jobs):
    body = yaml.dump(pipeline_jobs["final-report"])
    for token in ("01/validate-project", "02/unity-tests", "03/android", "04/android-validate"):
        assert token in body, (
            f"the final report does not gate on {token}, so that stage could fail "
            "while the pipeline reports green"
        )


def test_discord_receives_stage_and_validation_context(pipeline_jobs):
    body = yaml.dump(pipeline_jobs["notify-discord"])
    for token in ("failed-stage", "result-validation-android", "configuration"):
        assert token in body, f"Discord is not told {token}"


def test_discord_action_accepts_the_new_context():
    action = load(REPO_ROOT / ".github" / "actions" / "discord-upload-build" / "action.yml")
    for name in (
        "failed-stage",
        "configuration",
        "result-validation-android",
        "result-validation-webgl",
        "result-validation-ios",
    ):
        assert name in action["inputs"], f"discord-upload-build has no {name} input"


# ---------------------------------------------------------------------------
# Nesting budget — GitHub allows 4 levels of workflow_call
# ---------------------------------------------------------------------------

def test_stage_04_jobs_do_not_add_a_nesting_level(pipeline_jobs):
    """caller → unity-pipeline → reusable-build-platform already spends 3."""
    for job_id in VALIDATION_JOBS:
        assert "uses" not in pipeline_jobs[job_id], (
            f"{job_id} is a workflow_call; that would put a consumer at 4 levels and "
            "leave no room for the reusable build workflow underneath"
        )
        assert "runs-on" in pipeline_jobs[job_id]
