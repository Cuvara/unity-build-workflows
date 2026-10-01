"""Backward compatibility: with no runner policy, nothing moves.

`fixtures/runner_golden/baseline.json` was captured from the toolkit as it was
before the runner scheduler existed (see `fixtures/runner_golden/capture.py`):
for each legacy configuration, the exact `runner-labels` string every matrix row
carried, plus the engine, activation and the Linux label list the Unity-tests
and Addressables jobs used.

These tests run the same configurations through the new chain --

    resolve_build_flow.sh → runner_scheduler.py (no policy) → matrix_runner_labels.py

-- and require every value to come out byte-for-byte identical. If one of these
fails, the scheduler changed where an existing consumer's job runs.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "runner_golden"
SCHEDULER = REPO_ROOT / "scripts" / "common" / "runner_scheduler.py"
MATRIX_LABELS = REPO_ROOT / "scripts" / "common" / "matrix_runner_labels.py"

sys.path.insert(0, str(FIXTURES))
import capture  # noqa: E402  (the baseline's own resolver runner)

BASELINE = json.loads((FIXTURES / "baseline.json").read_text(encoding="utf-8"))

PLAYER_FLAGS = {"Android": "build-android", "WebGL": "build-webgl", "Linux64": "build-linux64",
                "LinuxServer": "build-linuxserver", "Windows64": "build-windows64", "iOS": "build-ios"}


def run_scheduler(flow, jobs, workdir, extra_env=None):
    """Run the scheduler CLI the way the pipeline's `runners` step does."""
    out = Path(workdir) / "scheduler_out.txt"
    out.write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k not in ("RUNNER_POLICY", "RUNNER_STATUS_TOKEN",
                                                            "RUNNER_POLICY_FILE")}
    env.update({
        "RS_JOBS": ",".join(jobs),
        "RS_INACTIVE_JOBS": ",".join(j for j in ("UnityTests", "Addressables") if j not in jobs),
        "RS_LANE": "pipeline",
        "RS_LEGACY_LABELS_BY_PLATFORM": flow["runner-labels-by-platform"],
        "RS_LEGACY_LABELS_LINUX": flow["runner-labels-linux"],
        "RS_LEGACY_LABELS_SOURCE": flow["runner-labels-source"],
        "RS_LEGACY_BUILD_ENGINE": flow["build-engine"],
        "RS_LEGACY_ACTIVATION": flow["activation-strategy"],
        "RS_ACTIVATION_DOCKER": flow["activation-strategy-docker"],
        "RS_ACTIVATION_LOCAL": flow["activation-strategy-local"],
        "GITHUB_OUTPUT": str(out),
        "GITHUB_STEP_SUMMARY": "",
    })
    env.update(extra_env or {})
    proc = subprocess.run([sys.executable, str(SCHEDULER)], capture_output=True, text=True,
                          env=env, cwd=str(workdir))
    assert proc.returncode == 0, proc.stderr
    line = out.read_text(encoding="utf-8").strip().splitlines()[0]
    key, _, value = line.partition("=")
    assert key == "runner-selection"
    return value


def matrix_value(selection, platform, field):
    escaped = subprocess.run(
        [sys.executable, str(MATRIX_LABELS)], capture_output=True, text=True,
        env={**os.environ, "RUNNER_SELECTION": selection, "RL_PLATFORM": platform, "RL_FIELD": field},
    ).stdout
    # Undo exactly one level of escaping, as the row's JSON parse does.
    return json.loads('"%s"' % escaped)


def test_the_baseline_covers_the_legacy_surface():
    names = set(BASELINE)
    for required in ("default", "labels-variable", "labels-dispatch", "labels-none-sentinel",
                     "selfhosted-local-per-os", "selfhosted-docker-per-os", "legacy-mode-windows",
                     "legacy-mode-macos", "legacy-mode-docker", "ci-lane-none", "default-ios-only"):
        assert required in names, f"baseline lost case {required}"


@pytest.mark.parametrize("name", sorted(BASELINE))
def test_no_policy_reproduces_legacy_routing(name, tmp_path):
    case = BASELINE[name]
    flow, _ = capture.run_resolver(case["env"])
    jobs = list(case["rows"])  # the platforms the matrix actually built
    if flow.get("run-tests") == "true":
        jobs.append("UnityTests")
    selection = run_scheduler(flow, jobs, tmp_path)

    for platform, expected in case["rows"].items():
        assert matrix_value(selection, platform, "runsOn") == expected, (
            f"{name}: {platform} moved from {expected}")
        assert matrix_value(selection, platform, "buildEngine") == case["build-engine"]
        assert matrix_value(selection, platform, "activationStrategy") == case["activation-strategy"]

    doc = json.loads(selection)
    expected_linux = json.loads(case["runner-labels-linux"])
    for unity_job in ("UnityTests", "Addressables"):
        assert doc["jobs"][unity_job]["runsOn"] == expected_linux, f"{name}: {unity_job} moved"
        assert doc["jobs"][unity_job]["buildEngine"] == case["build-engine"]
    assert doc["summary"]["usesDocker"] == (case["build-engine"] == "docker")
    assert doc["policySource"] == "none"
    assert doc["inventory"]["provider"] == "none", "no policy must mean no runner API call"


@pytest.mark.parametrize("name", sorted(BASELINE))
def test_full_chain_through_the_real_matrix_step(name, tmp_path):
    """resolver → scheduler → the pipeline's own `matrix` step, as stage 01 runs them.

    The matrix step is given only the runner selection (not the legacy label
    map it used to read), so this proves the rows it now builds are the rows it
    built before.
    """
    case = BASELINE[name]
    flow, platform_input = capture.run_resolver(case["env"])
    selection = run_scheduler(flow, list(case["rows"]), tmp_path)
    rows = capture.run_matrix(flow, platform_input, {"RUNNER_SELECTION": selection})
    assert {r["platform"]: r["runner-labels"] for r in rows} == case["rows"]
    for row in rows:
        assert row["build-engine"] == case["build-engine"]
        assert row["activation-strategy"] == case["activation-strategy"]


@pytest.mark.parametrize("name", sorted(BASELINE))
def test_runner_policy_legacy_switch_is_also_identical(name, tmp_path):
    """`runner-policy: legacy` with a policy present behaves exactly like no policy."""
    case = BASELINE[name]
    flow, _ = capture.run_resolver(case["env"])
    policy = json.dumps({"default": {"priority": ["github-hosted-only-pool"]},
                         "pools": {"github-hosted-only-pool": {"labels": ["self-hosted", "linux", "x"]}}})
    selection = run_scheduler(flow, list(case["rows"]), tmp_path,
                              {"RUNNER_POLICY": policy, "RS_POLICY_MODE": "legacy"})
    for platform, expected in case["rows"].items():
        assert matrix_value(selection, platform, "runsOn") == expected


def test_a_missing_selection_entry_still_lands_somewhere():
    """Same safety net as the legacy map: a row with no runs-on kills the run."""
    assert matrix_value('{"jobs":{}}', "Android", "runsOn") == '["ubuntu-latest"]'
    assert matrix_value("not json", "Android", "runsOn") == '["ubuntu-latest"]'
    assert matrix_value('{"jobs":{}}', "Android", "buildEngine") == ""


def test_a_group_target_survives_the_row_escaping():
    selection = json.dumps({"jobs": {"iOS": {"runsOn": {"group": "mac-pool", "labels": ["macOS", "xcode-16"]}}}})
    row = json.loads('{"runner-labels":"%s"}' % subprocess.run(
        [sys.executable, str(MATRIX_LABELS)], capture_output=True, text=True,
        env={**os.environ, "RUNNER_SELECTION": selection, "RL_PLATFORM": "iOS"}).stdout)
    assert json.loads(row["runner-labels"]) == {"group": "mac-pool", "labels": ["macOS", "xcode-16"]}
