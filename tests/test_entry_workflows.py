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

ENTRY_POINTS = {
    "android": TEMPLATES / "consumer-build-android.yml",
    "ios": TEMPLATES / "consumer-build-ios.yml",
    "webgl": TEMPLATES / "consumer-build-webgl.yml",
    "all": TEMPLATES / "consumer-build-all.yml",
}

EXPECTED_PLATFORM = {
    "android": "Android",
    "ios": "iOS",
    "webgl": "WebGL",
    "all": "All",
}

# Inputs every entry point must offer — the developer-facing set.
COMMON_INPUTS = {
    "environment",
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
PLATFORM_ONLY_INPUTS = {"android-export": "android"}


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


@pytest.mark.parametrize("input_name,owner", sorted(PLATFORM_ONLY_INPUTS.items()))
def test_platform_specific_input_appears_only_in_its_own_entry_point(input_name, owner):
    """`android-export` on a multi-platform form is the original defect."""
    for key, path in ENTRY_POINTS.items():
        present = input_name in dispatch_inputs(load(path))
        if key == owner:
            assert present, f"{key}: {input_name} must be offered here"
        else:
            assert not present, (
                f"{key}: {input_name} is {owner}-only and must not appear on this form"
            )


def test_build_all_has_no_per_platform_output_format():
    """An output format for one platform does not belong on a multi-platform form."""
    inputs = dispatch_inputs(load(ENTRY_POINTS["all"]))
    leaked = [n for n in inputs if n.startswith(("android-", "ios-", "webgl-"))]
    assert not leaked, f"Build All exposes platform-specific inputs: {leaked}"


def test_ios_entry_point_exposes_no_android_or_webgl_options():
    inputs = dispatch_inputs(load(ENTRY_POINTS["ios"]))
    leaked = [n for n in inputs if n.startswith(("android-", "webgl-"))]
    assert not leaked, f"Build iOS exposes foreign platform inputs: {leaked}"


def test_webgl_entry_point_exposes_no_android_or_ios_options():
    inputs = dispatch_inputs(load(ENTRY_POINTS["webgl"]))
    leaked = [n for n in inputs if n.startswith(("android-", "ios-"))]
    assert not leaked, f"Build WebGL exposes foreign platform inputs: {leaked}"


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


def test_entry_point_pins_its_platform(entry):
    key, workflow = entry
    job = next(iter(workflow["jobs"].values()))
    assert job["with"]["platform"] == EXPECTED_PLATFORM[key]


def test_only_android_passes_an_android_export(entry):
    key, workflow = entry
    job = next(iter(workflow["jobs"].values()))
    has_export = "android-export" in job["with"]
    assert has_export == (key == "android"), (
        f"{key}: android-export should be passed only by the Android entry point"
    )


def test_auto_sentinel_is_translated_to_the_engine_default(entry):
    """'auto' on the form means "use the repository variable", which the engine
    spells as an empty string."""
    key, workflow = entry
    job = next(iter(workflow["jobs"].values()))
    for name in ("runner-type", "build-engine"):
        value = str(job["with"].get(name, ""))
        if not value:
            continue
        assert f"inputs.{name} == 'auto'" in value, (
            f"{key}: {name} passes the raw 'auto' sentinel to the engine instead of ''"
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
