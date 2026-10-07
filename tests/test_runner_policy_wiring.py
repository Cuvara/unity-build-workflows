"""The runner scheduler is wired into the workflows the way the design says.

test_runner_scheduler.py tests the decisions; test_runner_selection_golden.py
proves nothing moves without a policy. This file pins the plumbing: where the
selection is made, what reads it, how secrets reach it, and that every
standalone Unity workflow routes through it too.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
FIXTURES = Path(__file__).parent / "fixtures" / "runner_golden"
SCHEMA = REPO_ROOT / "schemas" / "unity-runner-policy.schema.json"
EXAMPLES = REPO_ROOT / "examples" / "runner-policy"

sys.path.insert(0, str(FIXTURES))
import capture  # noqa: E402


def load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def on_block(workflow):
    # PyYAML reads a bare `on:` key as boolean True.
    return workflow.get("on", workflow.get(True))


def steps_of(workflow, job):
    return workflow["jobs"][job]["steps"]


def step_by_id(workflow, job, step_id):
    for step in steps_of(workflow, job):
        if step.get("id") == step_id:
            return step
    raise AssertionError(f"{job} has no step id={step_id}")


PIPELINE = load("unity-pipeline.yml")


# ---------------------------------------------------------------------------
# unity-pipeline.yml
# ---------------------------------------------------------------------------

class TestPipelineWiring:
    def test_selection_is_made_between_the_flow_and_the_matrix(self):
        ids = [s.get("id") for s in steps_of(PIPELINE, "resolve-config")]
        assert ids.index("flow") < ids.index("runners") < ids.index("matrix")

    def test_matrix_reads_the_selection_not_the_legacy_map(self):
        env = step_by_id(PIPELINE, "resolve-config", "matrix")["env"]
        assert env["RUNNER_SELECTION"] == "${{ steps.runners.outputs.runner-selection }}"
        assert "RUNNER_LABELS_BY_PLATFORM" not in env

    def test_rows_carry_engine_activation_and_selected_target(self):
        assert "resolve_build_matrix.sh" in step_by_id(PIPELINE, "resolve-config", "matrix")["run"]
        run = (Path(__file__).resolve().parent.parent / "scripts" / "common"
               / "resolve_build_matrix.sh").read_text(encoding="utf-8")
        for field in ('\\"build-engine\\":', '\\"activation-strategy\\":', '\\"runner-selected\\":'):
            assert field in run

    def test_build_job_routes_on_the_row(self):
        with_ = PIPELINE["jobs"]["build"]["with"]
        assert with_["runner-labels"] == "${{ matrix.runner-labels }}"
        assert with_["build-engine"].startswith("${{ matrix.build-engine ||")
        assert with_["activation-strategy"].startswith("${{ matrix.activation-strategy ||")
        assert "matrix.runner-selected" in with_["runner-selected"]

    @pytest.mark.parametrize("job,output", [("unity-tests", "runs-on-unitytests"),
                                            ("build-addressables", "runs-on-addressables")])
    def test_unity_jobs_route_on_the_selection(self, job, output):
        labels = PIPELINE["jobs"][job]["with"]["runner-labels"]
        assert f"needs.resolve-config.outputs.{output}" in labels

    def test_license_gate_follows_any_docker_job(self):
        cond = PIPELINE["jobs"]["validate-license"]["if"]
        assert "needs.resolve-config.outputs.uses-docker == 'true'" in cond
        assert "outputs.build-engine == 'docker'" not in cond

    def test_selection_is_a_job_output(self):
        outputs = PIPELINE["jobs"]["resolve-config"]["outputs"]
        for key in ("runner-selection", "uses-docker", "runs-on-unitytests", "runs-on-addressables"):
            assert outputs[key] == f"${{{{ steps.runners.outputs.{key} }}}}"

    def test_policy_file_is_checked_out(self):
        checkout = steps_of(PIPELINE, "resolve-config")[0]
        assert "RUNNER_POLICY_FILE" in checkout["with"]["sparse-checkout"]

    def test_runner_policy_input_and_token_secret(self):
        call = on_block(PIPELINE)["workflow_call"]
        assert call["inputs"]["runner-policy"]["default"] == "auto"
        assert call["inputs"]["runner-policy"]["type"] == "string"
        assert call["secrets"]["RUNNER_STATUS_TOKEN"]["required"] is False

    def test_final_report_renders_the_selection(self):
        names = [s.get("name") for s in steps_of(PIPELINE, "final-report")]
        assert "Runner selection report" in names
        step = next(s for s in steps_of(PIPELINE, "final-report") if s.get("name") == "Runner selection report")
        assert step["continue-on-error"] is True and step["if"] == "always()"

    def test_build_platform_prints_the_assigned_runner(self):
        build = load("reusable-build-platform.yml")
        steps = build["jobs"][next(iter(build["jobs"]))]["steps"]
        # The Windows-only Git Bash step has to precede every bash step, the
        # identity print included; it is the only thing allowed before it.
        assert steps[0]["name"] == "Use Git Bash and long paths (Windows)"
        assert steps[1]["name"] == "Runner identity"
        assert "runner-selected" in on_block(build)["workflow_call"]["inputs"]


# ---------------------------------------------------------------------------
# The `runners` step, executed
# ---------------------------------------------------------------------------

def run_runners_step(tmp_path, flow, extra_env=None, files=None):
    """Execute the real `runners` step with `.toolkit` pointing at this repo.

    `files` maps workspace-relative paths to content (e.g. a policy file in the
    consumer checkout).
    """
    work = tmp_path / "ws"
    work.mkdir()
    (work / ".toolkit").symlink_to(REPO_ROOT, target_is_directory=True)
    for rel, content in (files or {}).items():
        (work / rel).parent.mkdir(parents=True, exist_ok=True)
        (work / rel).write_text(content, encoding="utf-8")
    out = work / "out.txt"
    out.write_text("", encoding="utf-8")
    step = step_by_id(PIPELINE, "resolve-config", "runners")
    env = {k: v for k, v in os.environ.items()
           if k not in ("RUNNER_POLICY", "RUNNER_STATUS_TOKEN", "RUNNER_POLICY_FILE")}
    env.update({
        "IN_PLATFORM": flow.get("_in_platform", "All"),
        "SEL_ANDROID": flow["build-android"], "SEL_WEBGL": flow["build-webgl"],
        "SEL_LINUX64": flow["build-linux64"], "SEL_LINUXSERVER": flow["build-linuxserver"],
        "SEL_WINDOWS64": flow["build-windows64"], "SEL_IOS": flow["build-ios"],
        "SEL_TESTS": flow["run-tests"], "SEL_ADDRESSABLES": flow["build-addressables"],
        "RS_LANE": "pipeline",
        "RS_LEGACY_LABELS_BY_PLATFORM": flow["runner-labels-by-platform"],
        "RS_LEGACY_LABELS_LINUX": flow["runner-labels-linux"],
        "RS_LEGACY_LABELS_SOURCE": flow["runner-labels-source"],
        "RS_LEGACY_BUILD_ENGINE": flow["build-engine"],
        "RS_LEGACY_ACTIVATION": flow["activation-strategy"],
        "RS_ACTIVATION_DOCKER": flow["activation-strategy-docker"],
        "RS_ACTIVATION_LOCAL": flow["activation-strategy-local"],
        "RS_UNITY_VERSION": "6000.0.26f1", "RS_POLICY_MODE": "auto",
        "RUNNER_POLICY_FILE": ".github/unity-runner-policy.json",
        "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": str(work / "summary.md"),
    })
    env.update(extra_env or {})
    proc = subprocess.run(["bash", "-c", step["run"]], env=env, cwd=str(work),
                          capture_output=True, text=True)
    outputs = {}
    for line in out.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        outputs[key] = value
    return proc, outputs


IOS_POLICY = {
    "runners": {"mac-01": {"labels": ["self-hosted", "macOS", "mac-01"], "platforms": ["iOS"]}},
    "availability": {"source": "none"},
    "platforms": {"iOS": {"priority": ["mac-01"]}},
}


class TestRunnersStepExecuted:
    def test_without_a_policy_it_passes_the_legacy_answer_through(self, tmp_path):
        flow, _ = capture.run_resolver({})
        proc, outputs = run_runners_step(tmp_path, flow)
        assert proc.returncode == 0, proc.stderr
        selection = json.loads(outputs["runner-selection"])
        assert set(selection["summary"]["jobs"]) == {
            "Android", "iOS", "WebGL", "Linux64", "LinuxServer", "Windows64"}
        assert selection["jobs"]["iOS"]["runsOn"] == ["macos-latest"]
        assert outputs["uses-docker"] == "true"

    def test_the_ci_lane_schedules_no_player_job(self, tmp_path):
        flow, _ = capture.run_resolver(BASELINE_CI_ENV)
        flow["_in_platform"] = "None"
        _, outputs = run_runners_step(tmp_path, flow)
        jobs = json.loads(outputs["runner-selection"])["summary"]["jobs"]
        assert not set(jobs) & {"Android", "iOS", "WebGL", "Linux64", "LinuxServer", "Windows64"}

    def test_a_policy_moves_only_what_it_covers(self, tmp_path):
        flow, platform_input = capture.run_resolver({})
        proc, outputs = run_runners_step(tmp_path, flow, {"RUNNER_POLICY": json.dumps(IOS_POLICY)})
        assert proc.returncode == 0, proc.stderr
        rows = {r["platform"]: r for r in capture.run_matrix(
            flow, platform_input, {"RUNNER_SELECTION": outputs["runner-selection"]})}
        assert json.loads(rows["iOS"]["runner-labels"]) == ["self-hosted", "macOS", "mac-01"]
        assert rows["iOS"]["build-engine"] == "local"
        assert rows["iOS"]["activation-strategy"] == "none"
        assert rows["iOS"]["runner-selected"] == "mac-01"
        assert json.loads(rows["Android"]["runner-labels"]) == ["ubuntu-latest"]
        assert rows["Android"]["build-engine"] == "docker"
        assert "Runner selection — iOS" in proc.stdout

    def test_an_invalid_policy_fails_the_step(self, tmp_path):
        flow, _ = capture.run_resolver({})
        proc, _ = run_runners_step(tmp_path, flow, {"RUNNER_POLICY": '{"mode": "fastest"}'})
        assert proc.returncode != 0
        assert "Runner policy invalid" in proc.stderr

    def test_the_policy_file_is_read_from_the_checkout(self, tmp_path):
        flow, _ = capture.run_resolver({"IN_PLATFORM": "iOS"})
        proc, outputs = run_runners_step(
            tmp_path, flow, files={".github/unity-runner-policy.json": json.dumps(IOS_POLICY)})
        assert proc.returncode == 0, proc.stderr
        selection = json.loads(outputs["runner-selection"])
        assert selection["policySource"] == "file:.github/unity-runner-policy.json"
        assert selection["jobs"]["iOS"]["selectedTarget"] == "mac-01"


BASELINE_CI_ENV = {"IN_PLATFORM": "None", "EVENT_NAME": "push", "REF_NAME": "develop",
                   "DEVELOP_BUILD_PLATFORMS": "Android,WebGL"}


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

def _all_workflow_texts():
    return {p.name: p.read_text(encoding="utf-8") for p in sorted(WORKFLOWS.glob("*.yml"))}


def test_the_status_token_only_ever_reaches_an_env_block():
    """Never inline in `run:`, never in a `with:` -- only `env:` (and the
    `secrets:` pass-through between workflows)."""
    for name, text in _all_workflow_texts().items():
        for line in text.splitlines():
            if "secrets.RUNNER_STATUS_TOKEN" not in line:
                continue
            stripped = line.strip()
            assert re.match(r"^RUNNER_STATUS_TOKEN:\s+\$\{\{ secrets\.RUNNER_STATUS_TOKEN \}\}$", stripped), (
                f"{name}: RUNNER_STATUS_TOKEN used outside an env/secrets mapping: {stripped}")


# ---------------------------------------------------------------------------
# Standalone Unity workflows
# ---------------------------------------------------------------------------

STANDALONE = {
    # workflow: (Unity job id, lane, fallback label, explicit-label input)
    "unity-build-android.yml": ("build", "standalone-docker", "ubuntu-latest", "runner-label"),
    "unity-build-webgl.yml":   ("build", "standalone-docker", "ubuntu-latest", "runner-label"),
    "unity-build-linux.yml":   ("build", "standalone-docker", "ubuntu-latest", "runner-label"),
    "unity-test.yml":          ("test", "standalone-docker", "ubuntu-latest", "runner-label"),
    "unity-build-ios.yml":     ("build", "standalone-native", "macos-unity-xcode", "ios-runner-label"),
    "unity-test-ios.yml":      ("test", "standalone-native", "macos-unity-xcode", "runner-label"),
    "unity-release-ios.yml":   ("release-build", "standalone-native", "macos-unity-xcode", "runner-label"),
}


def select_step(workflow):
    return step_by_id(workflow, "resolve-runner", "select")


@pytest.mark.parametrize("name", sorted(STANDALONE))
class TestStandaloneWiring:
    def test_unity_job_runs_where_the_resolver_says(self, name):
        wf = load(name)
        job_id, lane, fallback, input_name = STANDALONE[name]
        job = wf["jobs"][job_id]
        # Resolver ran: its answer. Resolver skipped: explicit label, else the
        # literal this workflow used before runner scheduling.
        assert job["runs-on"] == (
            "${{ needs.resolve-runner.result == 'success' && fromJSON(needs.resolve-runner.outputs.runs-on)"
            f" || inputs.{input_name} || '{fallback}' }}}}")
        needs = job["needs"] if isinstance(job["needs"], list) else [job["needs"]]
        assert "resolve-runner" in needs
        # A skipped resolver is the normal no-policy case and must not skip the build.
        assert "needs.resolve-runner.result == 'skipped'" in job["if"]
        assert "needs.resolve-runner.result == 'success'" in job["if"]
        assert "!cancelled()" in job["if"]
        env = select_step(wf)["env"]
        assert env["RS_LANE"] == lane
        assert env["RS_FALLBACK_LABELS"] == fallback, "the no-policy label must be today's literal"

    def test_resolver_only_runs_when_a_policy_is_opted_in(self, name):
        """I-1: no GitHub-hosted job unless a policy variable says so."""
        wf = load(name)
        _, _, _, input_name = STANDALONE[name]
        resolver = wf["jobs"]["resolve-runner"]
        assert resolver["runs-on"] == "ubuntu-latest"
        assert resolver["if"] == ("${{ (vars.RUNNER_POLICY != '' || vars.RUNNER_POLICY_FILE != '') && "
                                  f"inputs.{input_name} == '' }}}}")

    def test_release_build_still_requires_its_gates(self, name):
        if name != "unity-release-ios.yml":
            pytest.skip("release workflow only")
        cond = load(name)["jobs"]["release-build"]["if"]
        assert "needs.pre-release-checks.result == 'success'" in cond

    def test_explicit_label_input_defaults_to_empty(self, name):
        wf = load(name)
        _, _, _, input_name = STANDALONE[name]
        trigger = on_block(wf)
        inputs = (trigger.get("workflow_call") or trigger.get("workflow_dispatch"))["inputs"]
        assert inputs[input_name]["default"] == "", (
            "a non-empty default would always win over the policy")
        assert input_name in select_step(wf)["env"]["RS_EXPLICIT_LABELS"]

    def test_status_token_is_an_optional_secret(self, name):
        trigger = on_block(load(name))
        if "workflow_call" in trigger:
            assert trigger["workflow_call"]["secrets"]["RUNNER_STATUS_TOKEN"]["required"] is False


def run_select(tmp_path, name, env, layout="toolkit"):
    """Execute a standalone workflow's real `select` step."""
    work = tmp_path / "ws"
    (work / "project").mkdir(parents=True)
    if layout == "toolkit":
        (work / ".ci").mkdir()
        (work / ".ci" / "unity-build-workflows").symlink_to(REPO_ROOT, target_is_directory=True)
    else:
        (work / "scripts").symlink_to(REPO_ROOT / "scripts", target_is_directory=True)
    out = work / "out.txt"
    out.write_text("", encoding="utf-8")
    step = select_step(load(name))
    full = {k: v for k, v in os.environ.items()
            if k not in ("RUNNER_POLICY", "RUNNER_STATUS_TOKEN", "RUNNER_POLICY_FILE")}
    for key, value in step["env"].items():
        if "${{" not in str(value):
            full[key] = str(value)
    full.update({"RS_EXPLICIT_LABELS": "", "RS_UNITY_VERSION": "6000.0.26f1",
                 "INTEGRATION_MODE": "remote", "RUNNER_POLICY_FILE": ".github/unity-runner-policy.json",
                 "GITHUB_OUTPUT": str(out), "GITHUB_STEP_SUMMARY": ""})
    if "inputs.target-platform" in str(step["env"]["RS_JOBS"]):
        full["RS_JOBS"] = "Linux64"
    full.update(env)
    proc = subprocess.run(["bash", "-c", step["run"]], env=full, cwd=str(work),
                          capture_output=True, text=True)
    outputs = {}
    for line in out.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        outputs[key] = value
    return proc, outputs


@pytest.mark.parametrize("name", sorted(STANDALONE))
def test_no_policy_keeps_the_old_label(tmp_path, name):
    proc, outputs = run_select(tmp_path, name, {"SCHEDULER": "false"})
    assert proc.returncode == 0, proc.stderr
    assert json.loads(outputs["runs-on"]) == [STANDALONE[name][2]]


@pytest.mark.parametrize("label,expected", [
    ("my-mac", ["my-mac"]),
    ("self-hosted,macOS", ["self-hosted", "macOS"]),
    ('["self-hosted","macOS"]', ["self-hosted", "macOS"]),
])
def test_explicit_label_wins(tmp_path, label, expected):
    proc, outputs = run_select(tmp_path, "unity-build-ios.yml",
                               {"SCHEDULER": "false", "RS_EXPLICIT_LABELS": label})
    assert proc.returncode == 0, proc.stderr
    assert json.loads(outputs["runs-on"]) == expected


MAC_POLICY = json.dumps({
    "runners": {"mac-01": {"labels": ["self-hosted", "macOS", "mac-01"]}},
    "availability": {"source": "none"},
    "platforms": {"iOS": {"priority": ["mac-01"]}},
})
LINUX_POLICY = json.dumps({
    "pools": {"docker-pool": {"labels": ["self-hosted", "linux", "docker"]}},
    "availability": {"source": "none"},
    "default": {"priority": ["docker-pool"], "fallback": ["github-hosted"]},
})


@pytest.mark.parametrize("name,layout,policy,expected", [
    ("unity-build-ios.yml", "toolkit", MAC_POLICY, ["self-hosted", "macOS", "mac-01"]),
    ("unity-test-ios.yml", "root", MAC_POLICY, ["self-hosted", "macOS", "mac-01"]),
    ("unity-release-ios.yml", "root", MAC_POLICY, ["self-hosted", "macOS", "mac-01"]),
    ("unity-build-android.yml", "toolkit", LINUX_POLICY, ["self-hosted", "linux", "docker"]),
    ("unity-build-linux.yml", "toolkit", LINUX_POLICY, ["self-hosted", "linux", "docker"]),
    ("unity-test.yml", "toolkit", LINUX_POLICY, ["self-hosted", "linux", "docker"]),
])
def test_policy_path_runs_the_scheduler(tmp_path, name, layout, policy, expected):
    proc, outputs = run_select(tmp_path, name, {"SCHEDULER": "true", "RUNNER_POLICY": policy},
                               layout=layout)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(outputs["runs-on"]) == expected
    assert outputs["selected-target"]


def test_standalone_lane_rejects_a_policy_engine_it_cannot_run(tmp_path):
    policy = json.dumps({"runners": {"w": {"labels": ["self-hosted", "windows", "w"]}},
                         "platforms": {"Android": {"build-engine": "local", "priority": ["w"]}}})
    proc, _ = run_select(tmp_path, "unity-build-android.yml",
                         {"SCHEDULER": "true", "RUNNER_POLICY": policy})
    assert proc.returncode != 0
    assert "standalone-docker lane" in proc.stderr


def test_missing_scheduler_is_an_actionable_error(tmp_path):
    work = tmp_path / "ws"
    (work / "project").mkdir(parents=True)
    out = work / "out.txt"
    step = select_step(load("unity-build-android.yml"))
    env = {**os.environ, "SCHEDULER": "true", "INTEGRATION_MODE": "remote", "RS_JOBS": "Android",
           "GITHUB_OUTPUT": str(out)}
    proc = subprocess.run(["bash", "-c", step["run"]], env=env, cwd=str(work),
                          capture_output=True, text=True)
    assert proc.returncode != 0
    assert "Runner scheduler missing" in proc.stdout + proc.stderr


def test_unity_build_passes_the_token_to_the_test_workflow():
    wf = load("unity-build.yml")
    assert wf["jobs"]["test"]["secrets"]["RUNNER_STATUS_TOKEN"] == "${{ secrets.RUNNER_STATUS_TOKEN }}"
    assert on_block(wf)["workflow_call"]["inputs"]["ios-runner-label"]["default"] == ""


# ---------------------------------------------------------------------------
# Schema and examples
# ---------------------------------------------------------------------------

sys.path.insert(0, str(REPO_ROOT / "scripts" / "common"))
import runner_scheduler as rs  # noqa: E402


@pytest.fixture(scope="module")
def policy_validator():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(schema)


EXAMPLE_FILES = sorted(EXAMPLES.glob("*.json"))


def test_examples_exist():
    assert len(EXAMPLE_FILES) >= 3


@pytest.mark.parametrize("path", EXAMPLE_FILES, ids=lambda p: p.name)
def test_example_validates_against_the_schema_and_the_scheduler(path, policy_validator):
    doc = json.loads(path.read_text(encoding="utf-8"))
    errors = sorted(policy_validator.iter_errors(doc), key=str)
    assert not errors, f"{path.name}: {errors[0].message}"
    rs.parse_policy(doc, f"file:{path.name}")  # raises on anything the scheduler rejects


@pytest.mark.parametrize("doc", [
    {"version": 2},
    {"mode": "fastest"},
    {"surprise": True},
    {"platforms": {"Atari": {}}},
    {"runners": {"r": {"labels": []}}},
    {"availability": {"timeout-seconds": 600}},
    {"runners": {"github-hosted": {"labels": ["self-hosted", "linux"]}}},
])
def test_schema_and_scheduler_agree_on_invalid_policies(doc, policy_validator):
    assert list(policy_validator.iter_errors(doc)), f"schema accepted {doc}"
    with pytest.raises(rs.PolicyError):
        rs.parse_policy(doc, "t")


def test_examples_schedule_as_documented():
    """The Android/iOS example does what docs/MULTI_RUNNER_SCHEDULING.md says it does."""
    doc = json.loads((EXAMPLES / "ios-priority-android-pool.json").read_text(encoding="utf-8"))
    pol = rs.parse_policy(doc, "example")
    inv = rs.Inventory(provider="snapshot", status="ok", runners={
        "ios-build-01": rs.InventoryRunner("ios-build-01", "macos", False, False,
                                           ("self-hosted", "macOS", "ios-build-01")),
        "ios-build-02": rs.InventoryRunner("ios-build-02", "macos", True, False,
                                           ("self-hosted", "macOS", "ios-build-02")),
        "android-build-01": rs.InventoryRunner("android-build-01", "linux", True, True,
                                               ("self-hosted", "linux", "android-build-01")),
    })

    class P(rs.InventoryProvider):
        def discover(self, request):
            return inv

    legacy = rs.LegacyConfig({"Android": ("ubuntu-latest",), "iOS": ("macos-latest",)},
                             "docker", "auto", {"docker": "auto", "local": "none"})
    sel = rs.build_selection(["Android", "iOS"], [], "pipeline", legacy, pol, P(),
                             unity_version="6000.0.26f1")
    assert sel["jobs"]["iOS"]["selectedTarget"] == "ios-build-02"      # 01 offline
    assert sel["jobs"]["Android"]["selectedTarget"] == "android-build-01"  # busy, on-busy=wait
    assert sel["jobs"]["Android"]["availability"] == "busy"
