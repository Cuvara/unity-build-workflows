"""
Consumer entry-workflow contract (templates/consumer-build-*.yml).

GitHub's `workflow_dispatch` form has no conditional visibility, so a single
multi-platform form must show Android's APK/AAB choice to somebody building
iOS. The fix is to split the ENTRY POINT — one workflow per platform, each
exposing only what applies — while every one of them calls the same
`unity-pipeline.yml` engine.

These tests pin the two halves of that bargain:

  * the UX half — platform-specific inputs appear only in their own workflow,
    inputs are grouped, and Build All carries no per-platform output format
  * the no-duplication half — every entry point delegates to the shared engine
    and contains no build logic of its own

They read the templates, which are what consumer repositories copy.
"""
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
TEMPLATES = REPO_ROOT / "templates"

ENGINE = "unity-pipeline.yml"

# Three build-layer entry points, one question each:
#   CI          is this code safe to merge?
#   Development give me something to test with
#   Release     give me something we could ship
BUILD_ENTRY_POINTS = {
    "ci": TEMPLATES / "consumer-01-ci.yml",
    "development": TEMPLATES / "consumer-10-build-development.yml",
    "release": TEMPLATES / "consumer-11-build-release.yml",
}

# Manual build entry points (CI is automatic and has no form).
ENTRY_POINTS = {k: v for k, v in BUILD_ENTRY_POINTS.items() if k != "ci"}

# Per-platform release entry points. Separate workflows because the release
# lifecycles differ in shape, not just in credentials.
RELEASE_ENTRY_POINTS = {
    "android": TEMPLATES / "consumer-20-release-android.yml",
    "ios": TEMPLATES / "consumer-21-release-ios.yml",
    "webgl": TEMPLATES / "consumer-22-release-webgl.yml",
    # Windows and Linux ship through Steam. They were absent while no
    # distribution target existed — inventing a store for them would have been
    # fiction — and are here now that SteamCMD publishing exists.
    "windows": TEMPLATES / "consumer-23-release-windows.yml",
    "linux": TEMPLATES / "consumer-24-release-linux.yml",
}

EXPECTED_BUILD_TYPE = {"development": "development", "release": "release"}

# Inputs every entry point must offer — the developer-facing set.
COMMON_INPUTS = {
    "run-tests",
    "test-mode",
    "build-addressables",
    "unity-version",
    "clean-build",
    "define-symbols",
}

# Group prefixes carried in each input's description, since the dispatch form
# has no native grouping.
VALID_GROUPS = {"GENERAL", "QUALITY", "CONTENT", "UNITY", "ADVANCED",
                "ANDROID", "IOS", "WEBGL"}

# Inputs that belong to exactly one platform and must not leak into the others.
PLATFORM_ONLY_INPUTS = {}


def load(path):
    with path.open() as fh:
        return yaml.safe_load(fh)


def triggers(workflow):
    """PyYAML parses a bare `on:` key as the boolean True."""
    return workflow[True] if True in workflow else workflow["on"]


def dispatch_inputs(workflow):
    return (triggers(workflow).get("workflow_dispatch") or {}).get("inputs") or {}


@pytest.fixture(scope="module", params=sorted(ENTRY_POINTS))
def entry(request):
    key = request.param
    return key, load(ENTRY_POINTS[key])


# ---------------------------------------------------------------------------
# The entry points exist and are dispatch-only
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key,path", sorted(ENTRY_POINTS.items()))
def test_entry_workflow_exists(key, path):
    assert path.exists(), f"missing entry-point template: {path.name}"


def test_every_entry_point_is_manual_only(entry):
    """Automatic builds stay in unity-build.yml; these are the manual forms."""
    key, workflow = entry
    assert sorted(triggers(workflow)) == ["workflow_dispatch"], (
        f"{key}: entry points must be dispatch-only — push/PR belongs to unity-build.yml"
    )


def test_unity_build_template_has_no_dispatch_form():
    """The 14-field multi-platform form is what the split replaces."""
    workflow = load(TEMPLATES / "consumer-unity-build.yml")
    assert "workflow_dispatch" not in triggers(workflow), (
        "consumer-unity-build.yml still carries a dispatch form; manual builds "
        "belong to the per-platform entry points"
    )
    assert sorted(triggers(workflow)) == ["pull_request", "push"]


# ---------------------------------------------------------------------------
# UX: only the applicable options are on the form
# ---------------------------------------------------------------------------

def test_common_inputs_are_present(entry):
    key, workflow = entry
    missing = COMMON_INPUTS - set(dispatch_inputs(workflow))
    assert not missing, f"{key}: entry point is missing common inputs {sorted(missing)}"


def test_every_input_declares_a_group(entry):
    """The dispatch form cannot group inputs, so the description carries it."""
    key, workflow = entry
    for name, spec in dispatch_inputs(workflow).items():
        description = str(spec.get("description", ""))
        group = description.split("·")[0].strip()
        assert group in VALID_GROUPS, (
            f"{key}: input {name!r} has no recognised group prefix "
            f"(got {group!r}, expected one of {sorted(VALID_GROUPS)})"
        )


def test_infrastructure_inputs_are_marked_advanced(entry):
    """A normal developer should not have to reason about runners or licences."""
    key, workflow = entry
    inputs = dispatch_inputs(workflow)
    for name in ("runner-type", "build-engine", "activation-strategy", "runner-labels"):
        if name not in inputs:
            continue
        description = str(inputs[name].get("description", ""))
        assert description.startswith("ADVANCED"), (
            f"{key}: {name} is infrastructure and must be grouped ADVANCED, got {description!r}"
        )


def test_choice_inputs_have_no_empty_option(entry):
    """GitHub rejects an empty string in a `choice` option list."""
    key, workflow = entry
    for name, spec in dispatch_inputs(workflow).items():
        if spec.get("type") != "choice":
            continue
        options = spec.get("options") or []
        assert "" not in options, (
            f"{key}: choice input {name!r} contains an empty option, which makes "
            "the workflow file invalid — use an explicit sentinel such as 'auto'"
        )


# ---------------------------------------------------------------------------
# No duplication: every entry point delegates to the shared engine
# ---------------------------------------------------------------------------

def test_entry_point_delegates_to_the_shared_engine(entry):
    key, workflow = entry
    jobs = workflow["jobs"]
    assert len(jobs) == 1, f"{key}: an entry point should be a single delegating job"
    job = next(iter(jobs.values()))
    assert ENGINE in str(job.get("uses", "")), (
        f"{key}: entry point does not call {ENGINE}"
    )


def test_entry_point_contains_no_build_logic(entry):
    """Entry points are UX. Logic lives in the engine and the scripts."""
    key, workflow = entry
    job = next(iter(workflow["jobs"].values()))
    assert "steps" not in job, (
        f"{key}: entry point declares steps — build logic must stay in the engine"
    )
    assert "runs-on" not in job


def _eval_ternary(expression, value):
    """Evaluate GitHub's `A && B || C` for one input value.

    GitHub's `&&`/`||` return operands, not booleans, and an empty string is
    FALSY — so `X == 'auto' && '' || X` can never produce ''. It falls through
    to X. That is not visible by reading the expression, which is why the
    previous version of this test (a substring check) passed while every
    dispatch failed with `Invalid runner-type='auto'`.
    """
    body = expression.strip()
    assert body.startswith("${{") and body.endswith("}}"), body
    body = body[3:-2].strip()
    left, _, right = body.partition("||")
    cond, _, then = left.partition("&&")

    def resolve(token):
        token = token.strip()
        if token.startswith("'") and token.endswith("'"):
            return token[1:-1]
        assert token.startswith("inputs."), token
        return value

    # cond is `inputs.X <op> 'literal'`
    cond = cond.strip()
    if "!=" in cond:
        a, _, b = cond.partition("!=")
        truth = resolve(a) != resolve(b)
    else:
        a, _, b = cond.partition("==")
        truth = resolve(a) == resolve(b)

    result = resolve(then) if truth else False
    # GitHub's || returns the first truthy operand; '' is falsy.
    return result if result else resolve(right)


@pytest.mark.parametrize("field", ["runner-type", "build-engine"])
def test_auto_sentinel_actually_resolves_to_empty(entry, field):
    """'auto' on the form means "use the repository variable", which the engine
    spells as an empty string. It must actually evaluate to '' — the resolver
    rejects the literal 'auto' outright."""
    key, workflow = entry
    job = next(iter(workflow["jobs"].values()))
    expression = str(job["with"].get(field, ""))
    if not expression:
        return
    assert _eval_ternary(expression, "auto") == "", (
        f"{key}: {field} passes the literal 'auto' to the engine, which rejects it "
        f"(`Invalid {field}='auto'`). Expression: {expression}"
    )


@pytest.mark.parametrize("field,concrete", [
    ("runner-type", "self-hosted"),
    ("build-engine", "docker"),
])
def test_explicit_lane_choice_is_passed_through(entry, field, concrete):
    """The sentinel fix must not swallow a real selection."""
    key, workflow = entry
    job = next(iter(workflow["jobs"].values()))
    expression = str(job["with"].get(field, ""))
    if not expression:
        return
    assert _eval_ternary(expression, concrete) == concrete, (
        f"{key}: {field}={concrete} does not reach the engine"
    )


def test_entry_points_share_one_engine_reference(entry):
    """A drifted `uses:` ref is how one platform silently builds on old code."""
    key, workflow = entry
    job = next(iter(workflow["jobs"].values()))
    uses = str(job["uses"])
    ref = uses.rsplit("@", 1)[1]
    toolkit_ref = str(job["with"].get("toolkit-ref", "")).strip("'\"")
    assert ref == toolkit_ref, (
        f"{key}: uses@{ref} but toolkit-ref={toolkit_ref!r} — the engine and the "
        "scripts it checks out would come from different revisions"
    )


def test_all_entry_points_agree_on_the_engine_ref():
    refs = {
        key: str(next(iter(load(path)["jobs"].values()))["uses"]).rsplit("@", 1)[1]
        for key, path in ENTRY_POINTS.items()
    }
    assert len(set(refs.values())) == 1, f"entry points disagree on the engine ref: {refs}"


def test_entry_points_have_distinct_concurrency_groups():
    """Build Android must not cancel a Build iOS run of the same ref."""
    groups = {}
    for key, path in ENTRY_POINTS.items():
        workflow = load(path)
        assert "concurrency" in workflow, f"{key}: no concurrency group"
        groups[key] = workflow["concurrency"]["group"]
    assert len(set(groups.values())) == len(groups), (
        f"entry points share a concurrency group and would cancel each other: {groups}"
    )


# ---------------------------------------------------------------------------
# Build layer: CI / Development / Release are three different questions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("input_name,owner", sorted(PLATFORM_ONLY_INPUTS.items()))
def test_platform_specific_input_appears_only_where_it_applies(input_name, owner):
    for key, path in ENTRY_POINTS.items():
        present = input_name in dispatch_inputs(load(path))
        assert present == (key == owner), (
            f"{key}: {input_name} should be offered only by the {owner} entry point"
        )


def test_no_entry_point_asks_for_an_android_output_format():
    """It is not a decision. Development builds an APK, release builds the App
    Bundle Google Play requires — the lifecycle already says which, so offering
    the choice only creates `release + Android + apk`: an artifact that passes
    every gate and cannot be published."""
    for key, path in ENTRY_POINTS.items():
        assert "android-export" not in dispatch_inputs(load(path)), key
        job = next(iter(load(path)["jobs"].values()))
        assert "android-export" not in (job.get("with") or {}), (
            f"{key} still states a format; the resolver derives it"
        )


def test_development_always_produces_an_apk():
    """Not configurable: an AAB is a store artifact with no place in a dev
    build. The entry point states the lifecycle; the format follows from it."""
    job = next(iter(load(ENTRY_POINTS["development"])["jobs"].values()))
    assert job["with"]["build-type"] == "development"
    assert "android-export" not in dispatch_inputs(load(ENTRY_POINTS["development"]))


def test_release_states_the_release_lifecycle():
    """Google Play requires an App Bundle for new apps, and the release
    lifecycle is what produces one."""
    job = next(iter(load(ENTRY_POINTS["release"])["jobs"].values()))
    assert job["with"]["build-type"] == "release"


def test_release_is_not_development_with_another_environment():
    """The two must differ in build-type, not only in environment."""
    dev = next(iter(load(ENTRY_POINTS["development"])["jobs"].values()))["with"]
    rel = next(iter(load(ENTRY_POINTS["release"])["jobs"].values()))["with"]
    assert dev["build-type"] == "development" and rel["build-type"] == "release"


def test_development_cannot_target_production():
    """A production build comes from Build / Release, or the artifact naming
    stops meaning anything."""
    envs = dispatch_inputs(load(ENTRY_POINTS["development"]))["environment"]["options"]
    assert "production" not in envs, envs


def test_every_build_entry_point_declares_its_build_type():
    for key in ("development", "release"):
        job = next(iter(load(ENTRY_POINTS[key])["jobs"].values()))
        assert job["with"]["build-type"] == EXPECTED_BUILD_TYPE[key]


def test_ci_builds_nothing():
    """CI answers "is this safe to merge?" — it must not pay for player builds."""
    workflow = load(BUILD_ENTRY_POINTS["ci"])
    job = next(iter(workflow["jobs"].values()))
    assert job["with"]["platform"] == "None", "CI would build players on every push"
    assert sorted(triggers(workflow)) == ["pull_request", "push"]
    assert "workflow_dispatch" not in triggers(workflow)


def test_ci_skips_docs_only_changes():
    on = triggers(load(BUILD_ENTRY_POINTS["ci"]))
    for event in ("push", "pull_request"):
        assert "**.md" in on[event]["paths-ignore"]


# ---------------------------------------------------------------------------
# Release layer: one workflow per platform, promoting immutable artifacts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key,path", sorted(RELEASE_ENTRY_POINTS.items()))
def test_release_entry_point_promotes_a_stored_artifact(key, path):
    """Release must never rebuild: the binary QA approved is the binary that ships."""
    inputs = dispatch_inputs(load(path))
    assert "artifact-name" in inputs, f"{key}: cannot name the artifact to promote"
    assert inputs["artifact-name"]["default"].startswith("release-"), (
        f"{key}: defaults to something other than a Build / Release artifact"
    )
    assert "start-phase" in inputs
    assert inputs["start-phase"]["default"] != "build", (
        f"{key}: defaults to rebuilding rather than promoting"
    )


@pytest.mark.parametrize("key,path", sorted(RELEASE_ENTRY_POINTS.items()))
def test_release_entry_point_delegates_to_its_own_pipeline(key, path):
    """One workflow per platform, because the lifecycles differ in shape."""
    job = next(iter(load(path)["jobs"].values()))
    assert f"pipeline-{key}-release.yml" in str(job["uses"])
    assert "steps" not in job


@pytest.mark.parametrize("key,path", sorted(RELEASE_ENTRY_POINTS.items()))
def test_release_entry_point_can_dry_run(key, path):
    assert "dry-run" in dispatch_inputs(load(path))


@pytest.mark.parametrize("key,artifact", [
    ("windows", "release-windows"),
    ("linux", "release-linux"),
])
def test_desktop_release_entry_points_promote_to_steam(key, artifact):
    """Windows and Linux now have a real distribution target, so they get the
    same promote-only entry point as everyone else."""
    body = RELEASE_ENTRY_POINTS[key].read_text()
    assert f"pipeline-{key}-release.yml" in body
    inputs = dispatch_inputs(load(RELEASE_ENTRY_POINTS[key]))
    assert inputs["artifact-name"]["default"] == artifact
    assert inputs["source-run-id"]["required"] is True


@pytest.mark.parametrize("key", ["windows", "linux"])
def test_a_desktop_entry_point_never_asks_for_steam_credentials(key):
    """Credentials are repository secrets, not something a human types into a
    dispatch form where they would land in the run's inputs — and therefore in
    the run log."""
    inputs = dispatch_inputs(load(RELEASE_ENTRY_POINTS[key]))
    for name in inputs:
        assert "username" not in name.lower()
        assert "vdf" not in name.lower()
        assert "password" not in name.lower()
        assert "token" not in name.lower()
