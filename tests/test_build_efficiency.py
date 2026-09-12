"""
Build cost and platform selection.

Measured on real runs before any of this changed: a two-platform release took
27.6 runner-minutes, of which the build jobs were 95%. Inside those jobs the
waste was concrete — 57s (Android) and 276s (WebGL) reclaiming disk that was
already free, 8s caching a Gradle directory the build never writes, and a
dispatch of "All" that ignored the project's configured platform list and built
five players where one was wanted.

These tests pin the fixes. The platform-selection ones matter most: the old
behaviour did not fail, it succeeded quietly and built the wrong number of
things, which costs money and is invisible in a green check.
"""

import os
import subprocess
import tempfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
RESOLVER = REPO_ROOT / "scripts" / "common" / "resolve_build_flow.sh"
BUILD_LANE = REPO_ROOT / ".github" / "workflows" / "reusable-build-platform.yml"
TEMPLATES = REPO_ROOT / "templates"

ALL_FLAGS = ("android", "ios", "webgl", "windows64", "linux64", "linuxserver")


def dispatch(platform, environment="production", **extra):
    """Run the real resolver as a workflow_dispatch and return (rc, platforms)."""
    env = dict(os.environ)
    env.update(
        EVENT_NAME="workflow_dispatch", REF_NAME="develop",
        IN_PLATFORM=platform, IN_ENVIRONMENT=environment,
        IN_RUN_TESTS="false", IN_TEST_MODE="All",
        IN_BUILD_ADDRESSABLES="false", IN_DEFINE_SYMBOLS="",
    )
    # Repository variables leak in from the ambient environment otherwise.
    for key in list(env):
        if key.startswith(("VAR_", "NEW_", "LEG_")) or key == "PLATFORMS":
            del env[key]
    env.update(extra)
    with tempfile.NamedTemporaryFile("w", delete=False) as fh:
        out = fh.name
    env["GITHUB_OUTPUT"] = out
    try:
        proc = subprocess.run(["bash", str(RESOLVER)], env=env,
                              capture_output=True, text=True)
        raw = dict(line.split("=", 1)
                   for line in open(out).read().strip().split("\n") if "=" in line)
    finally:
        os.unlink(out)
    built = {flag for flag in ALL_FLAGS if raw.get(f"build-{flag}") == "true"}
    return proc.returncode, built, proc.stderr


# ---------------------------------------------------------------------------
# "All" means what the form says it means
# ---------------------------------------------------------------------------

def test_all_uses_the_configured_platform_list():
    """It used to be a hardcoded list of five. A project configured for Android
    that picked "All" got five builds — roughly fifty runner-minutes instead of
    nine — while the form said "All uses RELEASE_BUILD_PLATFORMS"."""
    rc, built, _ = dispatch("All", "production", VAR_RELEASE_BUILD_PLATFORMS="Android")
    assert rc == 0
    assert built == {"android"}


def test_all_follows_the_environment():
    rc, built, _ = dispatch("All", "development",
                            VAR_DEVELOP_BUILD_PLATFORMS="Android,WebGL")
    assert rc == 0
    assert built == {"android", "webgl"}


def test_all_never_includes_ios():
    """iOS needs a macOS runner, so including it in a bulk build would make the
    result depend on infrastructure rather than on the request. Ask by name."""
    rc, built, _ = dispatch("All", "production",
                            VAR_RELEASE_BUILD_PLATFORMS="Android,iOS")
    assert rc == 0
    assert built == {"android"}


# ---------------------------------------------------------------------------
# Selecting a subset
# ---------------------------------------------------------------------------

def test_a_comma_separated_list_builds_exactly_those():
    """`Android,WebGL` used to fall through to a warning and build nothing —
    a green run with no artifacts."""
    rc, built, _ = dispatch("Android,WebGL")
    assert rc == 0
    assert built == {"android", "webgl"}


def test_spaces_work_as_well_as_commas():
    rc, built, _ = dispatch("Android WebGL")
    assert rc == 0
    assert built == {"android", "webgl"}


def test_the_desktop_alias_expands():
    """GitHub's dispatch form has no multi-select, so the common subset is an
    option rather than a second free-text field for a human to contradict the
    dropdown with."""
    rc, built, _ = dispatch("Desktop")
    assert rc == 0
    assert built == {"windows64", "linux64"}


@pytest.mark.parametrize("platform,expected", [
    ("Android", {"android"}),
    ("iOS", {"ios"}),
    ("WebGL", {"webgl"}),
    ("Windows64", {"windows64"}),
    ("Linux64", {"linux64"}),
    ("LinuxServer", {"linuxserver"}),
])
def test_every_platform_can_be_asked_for_by_name(platform, expected):
    rc, built, _ = dispatch(platform)
    assert rc == 0
    assert built == expected


# ---------------------------------------------------------------------------
# Getting it wrong must be loud
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("typo", ["android", "Win64", "WEBGL", "Android;WebGL", "all"])
def test_an_unknown_platform_fails_the_run(typo):
    """A build that builds nothing and reports success is the worst shape a
    build failure can take: it looks finished."""
    rc, built, stderr = dispatch(typo)
    assert rc != 0, f"{typo!r} was accepted"
    assert built == set()
    assert "Unknown platform" in stderr


def test_an_empty_platform_fails_the_run():
    rc, _, stderr = dispatch("")
    assert rc != 0
    assert "platform is empty" in stderr


def test_a_disabled_platform_is_skipped_not_failed():
    """Different from a typo, and the distinction is the point: the name is
    real, the project has simply not enabled it (I-016)."""
    rc, built, _ = dispatch("WebGL", PLATFORMS="Android")
    assert rc == 0
    assert built == set()


# ---------------------------------------------------------------------------
# Not paying for work nobody needs
# ---------------------------------------------------------------------------

def _step(name):
    workflow = yaml.safe_load(BUILD_LANE.read_text())
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            if str(step.get("name", "")) == name:
                return step
    return None


def test_the_disk_reclaim_is_conditional():
    """Current GitHub-hosted runners ship 145G with ~87G free and the Unity
    image needs ~15G, so the unconditional reclaim cost 57s on Android and 276s
    on WebGL to delete 30G nobody was going to use. The guard keeps the safety
    net for smaller runners."""
    step = _step("Free disk space")
    assert step is not None
    run = str(step["run"])
    assert "REQUIRED_GB" in str(step.get("env", {})) or "REQUIRED_GB" in run
    assert "skipping the reclaim" in run
    # It must still be able to reclaim — a guard that never fires is a deletion.
    assert "docker image prune" in run


def test_the_gradle_cache_is_not_on_the_docker_lane():
    """Unity runs Gradle inside the container with its own JDK, under
    Library/Bee/Android/Prj — the host's ~/.gradle is never written, so this
    cached an empty directory and missed on every run while its name told
    readers Gradle was cached."""
    step = _step("Cache Gradle")
    assert step is not None
    condition = str(step["if"])
    assert "build-engine != 'docker'" in condition, condition


def test_the_library_cache_is_still_on_the_docker_lane():
    """The one cache that does work here — removing it would cost far more
    than the Gradle step ever did."""
    step = _step("Cache Library")
    assert step is not None
    assert "build-engine != 'docker'" not in str(step.get("if", ""))


# ---------------------------------------------------------------------------
# The forms
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["consumer-10-build-development",
                                  "consumer-11-build-release"])
def test_the_form_offers_every_platform_and_the_desktop_group(name):
    template = yaml.safe_load((TEMPLATES / f"{name}.yml").read_text())
    triggers = template.get("on") or template[True]
    options = triggers["workflow_dispatch"]["inputs"]["platform"]["options"]
    # Human names. Windows64/Linux64 are Unity's target identifiers and stay
    # that way in the config and the matrix; a form is read by people.
    for expected in ("All", "Desktop", "Android", "iOS", "WebGL", "Windows",
                     "Linux", "Linux Server"):
        assert expected in options, expected
    for internal in ("Windows64", "Linux64", "LinuxServer"):
        assert internal not in options, f"{internal} is an implementation detail"


# Every dropdown option and the exact platform set it must produce. Asserting
# only "it does not fail" is not enough: "Linux Server" tokenised on its space
# and selected the desktop build alongside the dedicated server, which exits 0
# and builds the wrong thing.
PLATFORM_OPTIONS = {
    "Android": {"android"},
    "iOS": {"ios"},
    "WebGL": {"webgl"},
    "Windows": {"windows64"},
    "Linux": {"linux64"},
    "Linux Server": {"linuxserver"},
    "Desktop": {"windows64", "linux64"},
}


@pytest.mark.parametrize("name", ["consumer-10-build-development",
                                  "consumer-11-build-release"])
def test_every_option_the_form_offers_resolves_to_exactly_what_it_says(name):
    """The form and the resolver disagreeing is how "All" came to mean
    something its own description denied."""
    template = yaml.safe_load((TEMPLATES / f"{name}.yml").read_text())
    triggers = template.get("on") or template[True]
    options = triggers["workflow_dispatch"]["inputs"]["platform"]["options"]
    for option in options:
        rc, built, stderr = dispatch(option)
        assert rc == 0, f"{name}: the form offers {option!r} but it fails: {stderr[-200:]}"
        if option == "All":
            continue  # covered by its own tests; depends on project config
        expected = PLATFORM_OPTIONS.get(option)
        assert expected is not None, (
            f"{name} offers {option!r}, which no test knows the meaning of — "
            f"add it to PLATFORM_OPTIONS"
        )
        assert built == expected, f"{option!r} selected {sorted(built)}"


@pytest.mark.parametrize("label", [l for l in PLATFORM_OPTIONS if " " in l])
def test_a_multi_word_label_survives_tokenising(label):
    """`IN_PLATFORM` is split on whitespace as well as commas, so a label with
    a space in it has to be folded first. The fold and the alias table are one
    table for exactly this reason."""
    rc, built, _ = dispatch(label)
    assert rc == 0
    assert built == PLATFORM_OPTIONS[label]
    # And it still works inside a list, which is where the split happens.
    rc, built, _ = dispatch(f"{label},Android")
    assert rc == 0
    assert built == PLATFORM_OPTIONS[label] | {"android"}


def test_the_superseded_per_platform_templates_are_gone():
    """consumer-build-{android,ios,webgl,all}.yml were the draft that the
    numbered set replaced. Nothing referenced them; shipping two generations
    side by side only made it unclear which one was current."""
    for name in ("android", "ios", "webgl", "all"):
        assert not (TEMPLATES / f"consumer-build-{name}.yml").exists(), name


def test_the_previous_caller_says_it_is_superseded():
    """It stays — removing it breaks existing projects — but a reader must not
    mistake it for the current path."""
    body = (TEMPLATES / "consumer-unity-build.yml").read_text()
    assert "DEPRECATED" in body
    assert "10-build-development" in body


@pytest.mark.parametrize("doc", ["README.md", "docs/CONSUMER_SETUP.md",
                                 "docs/NEW_PROJECT_END_TO_END.md"])
def test_onboarding_docs_install_the_numbered_set(doc):
    """Following the docs used to leave a project with the pre-refactor
    architecture: no release layer, no immutable boundary, no capability
    model."""
    body = (REPO_ROOT / doc).read_text()
    assert "11-build-release" in body, f"{doc} never mentions the release layer"
    assert "-o .github/workflows/unity-build.yml" not in body, (
        f"{doc} still installs the superseded single caller"
    )


# ---------------------------------------------------------------------------
# Retention: how long an artifact has to stay promotable
# ---------------------------------------------------------------------------
# Thirty days for everything turned a storage default into a promotion
# deadline. Once a Release Set's artifacts expire, download-artifact cannot
# fetch them and that release can never be promoted again — the immutable
# boundary holds and there is simply nothing left on the other side of it.

@pytest.mark.parametrize("build_type,environment,expected", [
    ("release", "production", 90),
    ("development", "development", 7),
    ("development", "staging", 14),
])
def test_retention_follows_what_the_artifact_is_for(resolve_matrix, build_type,
                                                    environment, expected):
    out = resolve_matrix(["Android"], build_type=build_type, environment=environment)
    assert int(out["retention-days"]) == expected


def test_a_project_that_chose_a_number_keeps_it(resolve_matrix):
    """`ARTIFACT_RETENTION_DAYS` is a decision, not a suggestion. Lengthening
    the default is help; overruling an explicit value is not."""
    out = resolve_matrix(["Android"], build_type="release",
                         retention_days=45, retention_source="variable-new")
    assert int(out["retention-days"]) == 45


def test_diagnostics_expire_sooner_than_the_artifact(resolve_matrix):
    """Logs, reports and result files are for the run that produced them.
    Keeping them as long as a release artifact is 83 days of storage for
    something nothing reads."""
    out = resolve_matrix(["Android"], build_type="release")
    assert int(out["log-retention-days"]) == 7
    assert int(out["log-retention-days"]) < int(out["retention-days"])


def test_diagnostics_never_outlive_the_artifact(resolve_matrix):
    """A project that sets a very short retention gets short logs too, rather
    than logs that outlast the thing they describe."""
    out = resolve_matrix(["Android"], build_type="release",
                         retention_days=3, retention_source="variable-new")
    assert int(out["log-retention-days"]) == 3


def test_the_build_lane_separates_the_two_lifetimes():
    workflow = yaml.safe_load(BUILD_LANE.read_text())
    inputs = (workflow.get("on") or workflow[True])["workflow_call"]["inputs"]
    assert "log-retention-days" in inputs
    body = BUILD_LANE.read_text()
    # The artifact and its manifest keep the long tier; logs and result files
    # take the short one.
    for name, expected in (
        ("${{ steps.artifact-type.outputs.artifact-name }}-logs", "log-retention-days"),
        ("pipeline-result-build-${{ inputs.platform }}", "log-retention-days"),
        ("${{ steps.artifact-type.outputs.artifact-name }}", "artifact-retention-days"),
    ):
        at = body.index(f"name: {name}\n")
        window = body[at:at + 500]
        assert expected in window, f"{name} does not use {expected}"


def test_the_release_manifest_does_not_outlive_its_artifacts():
    """90 days was hardcoded for the manifest while the artifacts followed the
    project's setting. A project retaining artifacts for 30 days kept a
    manifest for 60 days after the bytes it points at had gone — a Release Set
    that reads as promotable and is not."""
    import yaml as _yaml

    pipeline = _yaml.safe_load(
        (REPO_ROOT / ".github" / "workflows" / "unity-pipeline.yml").read_text())
    for step in pipeline["jobs"]["release-manifest"]["steps"]:
        if "upload-artifact" not in str(step.get("uses", "")):
            continue
        retention = str(step["with"]["retention-days"])
        assert "artifact-retention-days" in retention, retention


# ---------------------------------------------------------------------------
# The artifact contract: lifecycle + platform, not a form field
# ---------------------------------------------------------------------------

def resolve(platform="Android", build_type="", environment="production", **extra):
    """The resolver's view of a dispatch, including the artifact contract."""
    env = dict(os.environ)
    env.update(
        EVENT_NAME="workflow_dispatch", REF_NAME="develop",
        IN_PLATFORM=platform, IN_ENVIRONMENT=environment, IN_BUILD_TYPE=build_type,
        IN_RUN_TESTS="false", IN_TEST_MODE="All",
        IN_BUILD_ADDRESSABLES="false", IN_DEFINE_SYMBOLS="",
    )
    for key in list(env):
        if key.startswith(("VAR_", "NEW_", "LEG_")) or key == "PLATFORMS":
            del env[key]
    env.update(extra)
    with tempfile.NamedTemporaryFile("w", delete=False) as fh:
        out = fh.name
    env["GITHUB_OUTPUT"] = out
    try:
        proc = subprocess.run(["bash", str(RESOLVER)], env=env,
                              capture_output=True, text=True)
        raw = dict(line.split("=", 1)
                   for line in open(out).read().strip().split("\n") if "=" in line)
    finally:
        os.unlink(out)
    return proc.returncode, raw, proc.stderr


def test_development_android_is_an_apk():
    rc, out, _ = resolve("Android", build_type="development", environment="development")
    assert rc == 0
    assert out["android-export-type"] == "apk"
    assert out["build-type"] == "development"


def test_release_android_is_an_app_bundle():
    rc, out, _ = resolve("Android", build_type="release")
    assert rc == 0
    assert out["android-export-type"] == "aab"


def test_release_android_cannot_be_an_apk():
    """The configuration that used to be reachable from the form: an artifact
    that passes every gate and cannot be published."""
    rc, _, stderr = resolve("Android", build_type="release", IN_ANDROID_EXPORT="apk")
    assert rc != 0
    assert "contradicts the release lifecycle" in stderr


def test_development_android_cannot_be_an_app_bundle():
    rc, _, stderr = resolve("Android", build_type="development",
                            environment="development", IN_ANDROID_EXPORT="aab")
    assert rc != 0
    assert "contradicts the development lifecycle" in stderr


@pytest.mark.parametrize("platform", ["Linux", "WebGL", "Windows", "iOS"])
def test_an_android_format_on_a_non_android_build_fails(platform):
    """Linux + AAB, WebGL + APK: not silently ignored, because ignoring it
    leaves the caller believing something untrue about what it will get."""
    rc, _, stderr = resolve(platform, build_type="release", IN_ANDROID_EXPORT="aab")
    assert rc != 0
    assert "no Android build was selected" in stderr


def test_a_matching_format_is_accepted():
    """The workflow_call interface stays technical: a caller may state the
    format, and gets it checked rather than ignored."""
    rc, out, _ = resolve("Android", build_type="release", IN_ANDROID_EXPORT="aab")
    assert rc == 0
    assert out["android-export-type"] == "aab"


def test_the_lifecycle_is_resolved_once():
    """The matrix step used to derive build-type a second time from the same
    inputs — one divergence away from a release build naming its artifacts
    `development-*`."""
    pipeline = (REPO_ROOT / ".github" / "workflows" / "unity-pipeline.yml").read_text()
    assert "steps.flow.outputs.build-type" in pipeline
    # The old local derivation must not come back.
    assert 'production) BUILD_TYPE="release"' not in pipeline


@pytest.mark.parametrize("ui,internal", [
    ("Windows", "windows64"),
    ("Linux", "linux64"),
    ("Linux Server", "linuxserver"),
])
def test_human_platform_names_map_to_the_identifiers(ui, internal):
    """The form says Windows; the capability config and artifact names keep
    Unity's Windows64. Renaming the identifier would change the meaning of
    every *_BUILD_PLATFORMS already set."""
    rc, out, _ = resolve(ui, build_type="release")
    assert rc == 0
    assert out[f"build-{internal}"] == "true"


def test_the_graph_shows_the_human_name(resolve_matrix):
    out = resolve_matrix(["Windows64", "Linux64", "LinuxServer"], build_type="release")
    labels = {row["platform"]: row["label"] for row in out["build"]}
    assert labels == {"Windows64": "Windows", "Linux64": "Linux",
                      "LinuxServer": "Linux Server"}


@pytest.mark.parametrize("name", ["consumer-10-build-development",
                                  "consumer-11-build-release",
                                  "consumer-20-release-android",
                                  "consumer-21-release-ios",
                                  "consumer-22-release-webgl",
                                  "consumer-23-release-windows",
                                  "consumer-24-release-linux"])
def test_a_form_stays_within_reach_of_the_dispatch_input_limit(name):
    """GitHub documents a maximum of 10 `workflow_dispatch` inputs. The two
    build forms sit at 11 and have dispatched fine all along, so the limit is
    not enforced as documented — but it is not a number to drift past casually.
    This is a tripwire, not a rule: if a form needs a twelfth input, check the
    limit is still unenforced before adding it, and move something into a
    repository variable if it is not.
    """
    template = yaml.safe_load((TEMPLATES / f"{name}.yml").read_text())
    triggers = template.get("on") or template[True]
    count = len(triggers["workflow_dispatch"]["inputs"])
    assert count <= 11, f"{name} has {count} inputs; GitHub documents 10"
