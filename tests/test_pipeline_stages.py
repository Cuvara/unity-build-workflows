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

# `03b` is a sub-stage: iOS production signing sits between the Unity build
# and artifact validation, inside stage 03.
STAGE_PREFIX = re.compile(r"^\d{2}[a-z]? / ")


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
    """`03 / ${{ matrix.label }}` renders one node per selected platform.

    The label, not the identifier: the graph is read by people, so it says
    Windows where the matrix says Windows64. Renaming the identifier itself
    would change the meaning of every `*_BUILD_PLATFORMS` already configured.
    """
    name = str(pipeline_jobs[BUILD_JOB]["name"])
    assert "matrix.label" in name, (
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
    assert "matrix.label" in name
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
    # No stage 03: a release pipeline never builds. iOS keeps an IPA export
    # because signing an archive needs the distribution certificate, which
    # belongs to the release layer, not the build layer.
    for stage in ("04", "05", "06", "07"):
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



# ---------------------------------------------------------------------------
# Promote-only: a release pipeline cannot build
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key,spec", sorted(RELEASE_PIPELINES.items()))
def test_release_pipeline_has_no_unity_build(key, spec):
    """The strongest form of "the binary QA approved is the binary that ships":
    there is no code path here that can produce one."""
    path, _ = spec
    jobs = load(path)["jobs"]
    for job_id, job in jobs.items():
        uses = str(job.get("uses", ""))
        assert "unity-build-" not in uses, (
            f"{path.name}:{job_id} can run a Unity build ({uses}); promotion "
            "must publish the artifact it was given, never make a new one"
        )
        assert "reusable-build-platform" not in uses


@pytest.mark.parametrize("key,spec", sorted(RELEASE_PIPELINES.items()))
def test_release_pipeline_cannot_be_asked_to_build(key, spec):
    path, _ = spec
    workflow = load(path)
    for event in ("workflow_call", "workflow_dispatch"):
        inputs = (triggers(workflow).get(event) or {}).get("inputs") or {}
        phase = inputs.get("start-phase")
        if not phase:
            continue
        assert phase.get("default") != "build"
        assert "build" not in (phase.get("options") or [phase.get("default")]), (
            f"{path.name}: {event} still offers a build phase"
        )


@pytest.mark.parametrize("key,spec", sorted(RELEASE_PIPELINES.items()))
def test_release_pipeline_downloads_across_runs(key, spec):
    """`actions/download-artifact` only sees the current run by default. The
    artifact being promoted was produced by a *different* run — without run-id
    and a token, promotion cannot physically find the binary."""
    path, _ = spec
    workflow = load(path)
    inputs = triggers(workflow)["workflow_call"]["inputs"]
    assert inputs["source-run-id"]["required"] is True, (
        f"{path.name}: source-run-id is optional, so a promotion can silently "
        "look for the artifact in the wrong run"
    )
    for job_id, job in workflow["jobs"].items():
        for step in job.get("steps", []) or []:
            if not str(step.get("uses", "")).startswith("actions/download-artifact"):
                continue
            with_block = step.get("with") or {}
            name = str(with_block.get("name", ""))
            if "release-notes" in name:
                continue  # produced inside this run
            assert "inputs.source-run-id" in str(with_block.get("run-id", "")), (
                f"{path.name}:{job_id} downloads {name} from the current run"
            )
            assert with_block.get("github-token"), (
                f"{path.name}:{job_id} has no token for a cross-run download"
            )


def test_build_release_report_emits_a_runnable_promotion(pipeline_jobs):
    """A promotion needs the artifact name AND the run that produced it. The
    report is where the operator gets both."""
    body = "\n".join(str(s.get("run", "")) for s in pipeline_jobs["final-report"]["steps"])
    assert "source-run-id=" in body, (
        "the hand-off omits the run id, so the command it prints cannot work"
    )
    assert "gh workflow run" in body


def test_no_workflow_both_builds_and_releases(repo_root):
    """release-orchestrator.yml built and published in one run, which
    promote-only makes impossible: it would have to produce the binary it
    publishes. Retired rather than left half-working."""
    assert not (repo_root / ".github" / "workflows" / "release-orchestrator.yml").exists(), (
        "release-orchestrator.yml builds and releases in one run; under "
        "promote-only the release layer must never produce a binary"
    )


# ---------------------------------------------------------------------------
# Store-facing build number
# ---------------------------------------------------------------------------

def test_build_number_is_resolved_once_in_stage_01(pipeline_jobs):
    """Both stores reject a build number they have already seen, so it must be
    decided before the artifact is built, not discovered afterwards."""
    assert "build-number" in pipeline_jobs["resolve-config"]["outputs"]
    for job_id in ("build", "build-addressables"):
        assert pipeline_jobs[job_id]["with"]["build-number"] == \
            "${{ needs.resolve-config.outputs.build-number }}", (
                f"{job_id} does not receive the resolved build number"
            )


def test_builder_applies_the_version_and_build_number(repo_root):
    """Left unset, game-ci falls back to Semantic versioning from git tags and
    generates its own androidVersionCode — two runs of the same commit could
    disagree, and nothing guaranteed the counter increased."""
    engine = (repo_root / ".github" / "workflows" / "reusable-build-platform.yml").read_text()
    assert "androidVersionCode: ${{ inputs.build-number }}" in engine, (
        "the Android version code is not passed to the builder"
    )
    assert "version: ${{ inputs.app-version }}" in engine
    assert "BUILD_NUMBER:   ${{ inputs.build-number }}" in engine, (
        "IOSBuilder reads BUILD_NUMBER for CFBundleVersion; it never arrives"
    )


def test_android_builder_does_not_derive_version_code_from_major(repo_root):
    """`bundleVersionCode = major` meant every 1.x.y release uploaded
    versionCode 1, so Play refused the second one."""
    builder = (repo_root / "unity-package" / "Packages" / "com.company.build-pipeline"
               / "Editor" / "PlatformBuilders" / "AndroidBuilder.cs").read_text()
    assert "cfg.BundleVersion.Split('.')[0]" not in builder, (
        "Android still derives its store counter from the major version"
    )
    assert 'GetEnvironmentVariable("BUILD_NUMBER")' in builder


@pytest.mark.parametrize("offset,run_number,expected", [(0, 42, "42"), (1000, 42, "1042")])
def test_build_number_offset(resolve_matrix, offset, run_number, expected):
    """A project whose store history predates this pipeline clears it once."""
    out = resolve_matrix(["Android"], run_number=run_number, build_number_offset=offset)
    assert out["build-number"] == expected


def test_build_number_rejects_a_nonsense_offset(resolve_matrix):
    with pytest.raises(AssertionError):
        resolve_matrix(["Android"], build_number_offset="not-a-number")


# ---------------------------------------------------------------------------
# The immutable-artifact boundary
# ---------------------------------------------------------------------------
# After Build / Release uploads an artifact, no promotion workflow may rebuild,
# re-export, re-sign, modify or regenerate it. Promotion may only download,
# verify, test, approve and publish. This is the acceptance criterion, so it is
# machine-checked rather than left to review.

# Anything that produces or alters a binary. Matched against each step's `uses`
# and `run`, so a shell call is caught as readily as an action.
MUTATING = [
    ("unity-build-", "runs a Unity build"),
    ("reusable-build-platform", "runs a Unity build"),
    ("game-ci/unity-builder", "runs a Unity build"),
    ("ios-setup-signing", "installs a signing identity"),
    ("ios-archive-export", "archives and exports an IPA"),
    ("xcodebuild", "invokes Xcode"),
    ("xcode_archive.sh", "archives an Xcode project"),
    ("xcode_export.sh", "exports an IPA"),
    ("sign_android_build.sh", "signs an Android artifact"),
    ("compress_webgl.sh", "rewrites the WebGL payload"),
    ("apply_define_symbols.sh", "changes build configuration"),
]


@pytest.mark.parametrize("key,spec", sorted(RELEASE_PIPELINES.items()))
def test_promotion_cannot_modify_the_binary(key, spec):
    """The acceptance criterion, as a test."""
    path, _ = spec
    workflow = load(path)
    violations = []
    for job_id, job in workflow["jobs"].items():
        haystack = str(job.get("uses", ""))
        for step in job.get("steps", []) or []:
            haystack += "\n" + str(step.get("uses", "")) + "\n" + str(step.get("run", ""))
        for token, why in MUTATING:
            if token in haystack:
                violations.append(f"{job_id}: {token} — {why}")
    assert not violations, (
        f"{path.name} can modify the binary it is supposed to be publishing:\n  "
        + "\n  ".join(violations)
    )


def test_ios_signing_happens_before_the_boundary(pipeline_jobs):
    """The correction this test exists for: signing used to run during
    promotion, so the artifact QA validated (an Xcode project) was not the
    artifact that shipped (an IPA built from it afterwards)."""
    assert "sign-ios" in pipeline_jobs, (
        "the build lane does not sign iOS, so the signed IPA can only be "
        "produced during promotion"
    )
    job = pipeline_jobs["sign-ios"]
    uses = "\n".join(str(s.get("uses", "")) for s in job["steps"])
    assert "ios-archive-export" in uses
    assert "ios-setup-signing" in uses
    # Only for release builds — a development iOS build needs no distribution identity.
    assert "sign-ios == 'true'" in str(job.get("if", ""))


def test_ios_release_artifact_is_the_ipa(resolve_matrix):
    """Stage 04 must validate what ships. For a release that is the signed IPA,
    not the Xcode project it was built from."""
    release = {r["platform"]: r for r in resolve_matrix(["iOS"], build_type="release")["validate"]}
    assert release["iOS"]["validator"] == "ipa"
    assert release["iOS"]["artifact-name"] == "release-ios-ipa"

    dev = {r["platform"]: r for r in resolve_matrix(["iOS"], build_type="development")["validate"]}
    assert dev["iOS"]["validator"] == "ios", "a development build has no IPA to validate"


def test_signed_ipa_is_validated_before_it_can_be_promoted(pipeline_jobs):
    body = "\n".join(str(s.get("run", "")) for s in pipeline_jobs["validate-artifact"]["steps"])
    assert "validate_ipa.sh" in body, "the release IPA is never validated"
    assert "REQUIRE_SIGNED=true" in body, (
        "an unsigned IPA would reach App Store Connect and be rejected after it "
        "had already consumed a build number"
    )
