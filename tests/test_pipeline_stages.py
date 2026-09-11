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

# Stage 03 and 04 are single MATRIX jobs, not one job per platform. Six
# `if:`-gated build jobs meant GitHub drew a skipped node for every platform
# that was not selected, so an Android-only run still rendered WebGL, iOS,
# Linux64, LinuxServer and Windows64 greyed out beside it.
BUILD_JOB = "build"
VALIDATE_JOB = "validate-artifact"

# The platforms the matrix can carry, and the ones with a stage-04 validator.
BUILD_PLATFORMS = ["Android", "WebGL", "Linux64", "LinuxServer", "Windows64", "iOS"]
VALIDATED_PLATFORMS = ["Android", "WebGL", "iOS"]

# Expensive jobs that must sit behind the quality gate.
GATED_JOBS = [BUILD_JOB, "build-addressables"]

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


def test_build_node_name_carries_the_platform(pipeline_jobs):
    """`03 / ${{ matrix.platform }}` renders one node per selected platform."""
    name = str(pipeline_jobs[BUILD_JOB]["name"])
    assert "matrix.platform" in name, (
        f"the build node must name its platform from the matrix, got {name!r}"
    )
    assert name.startswith("03 / ")


def test_build_node_name_does_not_repeat_the_node_label(pipeline_jobs):
    """GitHub joins the caller's job name with the called workflow's own job
    name, and the callee is already named after `node-label`. Putting
    matrix.node in both halves rendered
    "Development / 03 / Android / APK / APK"."""
    name = str(pipeline_jobs[BUILD_JOB]["name"])
    assert "matrix.node" not in name, (
        f"the artifact type is supplied by node-label; naming it here doubles it: {name!r}"
    )
    assert pipeline_jobs[BUILD_JOB]["with"]["node-label"] == "${{ matrix.node }}"


def test_validate_node_name_carries_the_platform(pipeline_jobs):
    name = str(pipeline_jobs[VALIDATE_JOB]["name"])
    assert "matrix.platform" in name
    assert name.startswith("04 / ")


def test_build_matrix_supplies_configuration_and_artifact_type(pipeline_jobs):
    """The second half of the node name (node-label) and the artifact type come
    from the matrix row, so `03 / Android` + `Production / AAB` renders
    `03 / Android / Production / AAB`."""
    with_block = pipeline_jobs[BUILD_JOB]["with"]
    assert with_block["platform"] == "${{ matrix.platform }}"
    assert with_block["node-label"] == "${{ matrix.node }}"
    assert with_block["artifact-type"] == "${{ matrix.artifact-type }}"
    assert "configuration" in with_block


def test_addressables_node_is_named_and_configured(pipeline_jobs):
    job = pipeline_jobs["build-addressables"]
    assert str(job["name"]).startswith("03 / ")
    assert "node-label" in job["with"]
    assert "configuration" in job["with"]


def test_resolve_config_exports_the_build_matrix(pipeline_jobs):
    """The platform set resolves once, in stage 01, and travels as data."""
    outputs = pipeline_jobs["resolve-config"]["outputs"]
    for key in (
        "configuration",
        "android-artifact-type",
        "build-matrix",
        "validate-matrix",
        "has-builds",
        "has-validations",
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


@pytest.mark.parametrize("job_id", GATED_JOBS)
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

def test_platform_builds_fan_out_independently(pipeline_jobs):
    """Quality gate PASS → Android + iOS + WebGL run independently.

    With a matrix that is `fail-fast: false`: a failed iOS leg must not cancel a
    running Android leg, and no leg waits on another.
    """
    strategy = pipeline_jobs[BUILD_JOB]["strategy"]
    assert strategy.get("fail-fast") is False, (
        "fail-fast must be off, or one platform's failure cancels the others"
    )
    assert "matrix" in strategy


def test_build_matrix_comes_from_resolved_config(pipeline_jobs):
    """The platform set is data from stage 01, not hardcoded jobs — which is
    what stops unselected platforms rendering as skipped nodes."""
    include = str(pipeline_jobs[BUILD_JOB]["strategy"]["matrix"]["include"])
    assert "needs.resolve-config.outputs.build-matrix" in include


def test_build_matrix_is_guarded_against_being_empty(pipeline_jobs):
    """A matrix with no vectors is a workflow error, so the job is gated."""
    condition = str(pipeline_jobs[BUILD_JOB].get("if", ""))
    assert "has-builds" in condition


# ---------------------------------------------------------------------------
# Stage 04 — artifact validation is explicit and independent
# ---------------------------------------------------------------------------

def test_validation_job_exists_and_is_stage_04(pipeline_jobs):
    assert VALIDATE_JOB in pipeline_jobs
    assert str(pipeline_jobs[VALIDATE_JOB]["name"]).startswith("04 / ")


def test_validation_matrix_comes_from_resolved_config(pipeline_jobs):
    """Only platforms that HAVE a validator get a node — Linux and Windows
    contribute none rather than a permanently skipped one."""
    include = str(pipeline_jobs[VALIDATE_JOB]["strategy"]["matrix"]["include"])
    assert "needs.resolve-config.outputs.validate-matrix" in include
    assert "has-validations" in str(pipeline_jobs[VALIDATE_JOB].get("if", ""))


def test_validation_legs_are_independent(pipeline_jobs):
    """A failed Android validation must not cancel the WebGL one."""
    assert pipeline_jobs[VALIDATE_JOB]["strategy"].get("fail-fast") is False


def test_validation_downloads_rather_than_rebuilds(pipeline_jobs):
    """Validation consumes the stored artifact — it never re-runs Unity."""
    steps = pipeline_jobs[VALIDATE_JOB]["steps"]
    uses = [str(s.get("uses", "")) for s in steps]
    assert any(u.startswith("actions/download-artifact") for u in uses)
    assert not any("reusable-build-platform" in u for u in uses)


def test_validation_reads_the_artifact_type_from_the_matrix(pipeline_jobs):
    """Downstream stages are told the artifact type, they do not guess it."""
    body = yaml.dump(pipeline_jobs[VALIDATE_JOB]["steps"])
    assert "matrix.artifact-type" in body


def test_every_validated_platform_has_a_validator_branch(pipeline_jobs):
    """A matrix row whose validator has no branch would fail at runtime only."""
    body = yaml.dump(pipeline_jobs[VALIDATE_JOB]["steps"])
    for validator in ("android)", "webgl)", "ios)"):
        assert validator in body, f"no dispatch branch for validator {validator}"


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
    assert "-manifest" in body, "the manifest is not uploaded as its own artifact"
    assert "artifact-name" in body, (
        "the manifest artifact must follow the build-type-scoped artifact name, "
        "so a development manifest cannot be mistaken for a release one"
    )


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
    """`Pipeline failed` sent people to read seven job logs. The report names
    the stage and the platform."""
    # Read the raw step scripts: yaml.dump re-wraps long lines, which breaks a
    # literal substring search.
    body = "\n".join(
        str(step.get("run", "")) for step in pipeline_jobs["final-report"]["steps"]
    )
    for token in (
        "01 PREPARE / Validate Unity Project",
        "02 QUALITY GATE / Unity Tests",
        "03 BUILD ARTIFACTS /",
        "04 ARTIFACT VALIDATION /",
        "failed_stage",
    ):
        assert token in body, f"the final report never mentions {token!r}"


def test_final_report_collects_matrix_leg_results(pipeline_jobs):
    """A matrix job's legs are not addressable through `needs`, so each leg
    uploads its own result and this stage aggregates them. Without that, the
    report can only say the whole matrix failed."""
    job = pipeline_jobs["final-report"]
    steps = yaml.dump(job["steps"])
    assert "pipeline-result-*" in steps, (
        "final-report does not download the per-leg result artifacts"
    )
    assert "download-artifact" in steps


def test_build_result_is_uploaded_after_it_is_written(repo_root):
    """Ordering, not presence.

    The upload step originally sat before `Set job outputs`, which is the step
    that writes the file — so it uploaded an empty directory on every build and
    `if-no-files-found: ignore` kept that quiet. Stage 07 then saw no result for
    a platform that had built fine and failed the run
    (NDCUnityTemplate run 34567145749).
    """
    import yaml as _yaml
    with (repo_root / ".github" / "workflows" / "reusable-build-platform.yml").open() as fh:
        engine = _yaml.safe_load(fh)
    steps = engine["jobs"]["build"]["steps"]
    names = [str(step.get("name", "")) for step in steps]
    writer = names.index("Set job outputs")
    uploader = names.index("Upload platform result")
    assert uploader > writer, (
        "the platform result is uploaded before the step that writes it, so the "
        "artifact is always empty"
    )
    assert steps[uploader]["with"].get("if-no-files-found") != "ignore", (
        "a missing result file must not be silent — it makes stage 07 report a "
        "successful platform as unreported"
    )


def test_report_tolerates_a_missing_leg_result(pipeline_jobs):
    """A reporting gap must not fail a green build, but a dead leg still must."""
    body = "\n".join(
        str(step.get("run", "")) for step in pipeline_jobs["final-report"]["steps"]
    )
    assert "unreported" in body, (
        "the report has no state for 'the matrix succeeded but this leg did not "
        "report', so a reporting gap fails an otherwise green run"
    )
    assert "R_BUILD" in body, (
        "the report must consult the matrix's own verdict to decide whether a "
        "missing result file is a gap or a dead leg"
    )


def test_build_and_validate_legs_publish_their_results(pipeline_jobs, repo_root):
    """The other half of that contract."""
    validate_steps = yaml.dump(pipeline_jobs[VALIDATE_JOB]["steps"])
    assert "pipeline-result-validate-" in validate_steps

    engine = (repo_root / ".github" / "workflows" / "reusable-build-platform.yml").read_text()
    assert "pipeline-result-build-" in engine, (
        "the build engine does not publish a per-platform result, so the report "
        "cannot tell which platform failed"
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
    assert "uses" not in pipeline_jobs[VALIDATE_JOB], (
        f"{VALIDATE_JOB} is a workflow_call; that would put a consumer at 4 levels "
        "and leave no room for the reusable build workflow underneath"
    )
    assert "runs-on" in pipeline_jobs[VALIDATE_JOB]


# ---------------------------------------------------------------------------
# Report readability
# ---------------------------------------------------------------------------

def _report_script(pipeline_jobs):
    return "\n".join(str(s.get("run", "")) for s in pipeline_jobs["final-report"]["steps"])


def test_report_draws_stage_progress(pipeline_jobs):
    """GitHub renders no progress indicator on a workflow node, so the report
    draws the pipeline's shape instead: which stages ran and where it stopped."""
    body = _report_script(pipeline_jobs)
    assert "def bar(" in body, "the report has no progress bar helper"
    for stage in ("01 Prepare", "02 Quality Gate", "03 Build", "04 Validate"):
        assert stage in body, f"the stage strip omits {stage!r}"


def test_report_draws_per_platform_progress(pipeline_jobs):
    body = _report_script(pipeline_jobs)
    assert "platforms" in body and "████████ done" in body


def test_release_report_hands_off_to_the_release_workflow(pipeline_jobs):
    """A Build / Release run produced immutable artifacts and stopped, and
    nothing told the reader what to do with them — which is why the release
    layer looked absent."""
    body = _report_script(pipeline_jobs)
    assert "RELEASE_LANES" in body
    for workflow in ("20-release-android.yml", "21-release-ios.yml", "22-release-webgl.yml"):
        assert workflow in body, f"the hand-off never names {workflow}"


def test_development_report_says_it_cannot_be_published(pipeline_jobs):
    body = _report_script(pipeline_jobs)
    assert "cannot be published" in body


def test_stage_04_node_names_the_artifact_it_validates(pipeline_jobs):
    name = str(pipeline_jobs[VALIDATE_JOB]["name"])
    assert "matrix.artifact-type" in name, (
        f"'{name}' does not say what it validates — '04 / Android / Validate AAB' "
        "tells the reader more than '04 / Android / Validate'"
    )


# ---------------------------------------------------------------------------
# Stage 07 in the release pipelines
# ---------------------------------------------------------------------------

RELEASE_PIPELINES = {
    "android": (ANDROID_RELEASE, "Google Play"),
    "ios": (IOS_RELEASE, "App Store Connect"),
    "webgl": (WEBGL_RELEASE, "hosting / CDN"),
}


@pytest.mark.parametrize("key,spec", sorted(RELEASE_PIPELINES.items()))
def test_release_pipeline_reaches_stage_07(key, spec):
    """The pipelines ended at stage 06, so a release run produced no report at
    all — the only way to see how far a promotion got was to read the graph."""
    path, _ = spec
    jobs = load(path)["jobs"]
    stages = {str(j.get("name", ""))[:2] for j in jobs.values()}
    for stage in ("03", "04", "05", "06", "07"):
        assert stage in stages, f"{path.name} has no stage {stage}: {sorted(stages)}"


@pytest.mark.parametrize("key,spec", sorted(RELEASE_PIPELINES.items()))
def test_release_report_runs_even_when_a_phase_failed(key, spec):
    path, _ = spec
    job = load(path)["jobs"]["report"]
    assert str(job.get("if", "")).strip() == "always()", (
        "a report that only runs on success cannot tell you where a release stopped"
    )


@pytest.mark.parametrize("key,spec", sorted(RELEASE_PIPELINES.items()))
def test_release_report_covers_every_phase(key, spec):
    """Every publish/release job must appear in the phase list, or the report
    silently omits the one that failed."""
    path, _ = spec
    jobs = load(path)["jobs"]
    report = jobs["report"]
    phases = str(report["steps"][-1]["with"]["phases"])
    for job_id, job in jobs.items():
        if job_id == "report":
            continue
        assert f"needs.{job_id}.result" in phases, (
            f"{path.name}: phase list omits {job_id} ({job.get('name')})"
        )
        assert job_id in needs_of(report), f"{path.name}: report does not depend on {job_id}"


@pytest.mark.parametrize("key,spec", sorted(RELEASE_PIPELINES.items()))
def test_release_report_names_its_destination(key, spec):
    path, destination = spec
    step = load(path)["jobs"]["report"]["steps"][-1]
    assert step["with"]["destination"] == destination


@pytest.mark.parametrize("key,spec", sorted(RELEASE_PIPELINES.items()))
def test_release_report_declares_the_discord_secret(key, spec):
    """Referencing a secret a reusable workflow never declared makes it always
    empty — the notification would silently never fire."""
    path, _ = spec
    workflow = load(path)
    secrets = triggers(workflow)["workflow_call"].get("secrets") or {}
    step = workflow["jobs"]["report"]["steps"][-1]
    if "DISCORD_WEBHOOK_URL" in str(step["with"].get("discord-webhook", "")):
        assert "DISCORD_WEBHOOK_URL" in secrets, (
            f"{path.name} uses DISCORD_WEBHOOK_URL without declaring it"
        )


def test_release_report_is_one_shared_implementation(repo_root):
    """Three pipelines, one report. The phase names differ per platform; the
    rendering does not."""
    action = repo_root / ".github" / "actions" / "release-report" / "action.yml"
    assert action.exists(), "the shared release report action is missing"
    for path, _ in RELEASE_PIPELINES.values():
        body = yaml.dump(load(path)["jobs"]["report"])
        assert "release-report" in body, f"{path.name} does not use the shared action"
