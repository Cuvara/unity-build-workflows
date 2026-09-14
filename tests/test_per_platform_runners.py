"""Each platform routes to the runner its OS actually has.

Stage 03 used to hand one `runner-labels` list to every leg of the build matrix,
and stage 03b fell back to the same one. A project on a self-hosted Windows
runner therefore sent its iOS signing job to the Windows box — and GitHub does
not fail a job whose labels match no runner, it queues it forever.

The three per-OS label variables that fix this were already resolved, emitted
and re-exported as pipeline outputs, and read by nothing. Stage 2 of ADR 004
plugs them in.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
RESOLVER = REPO_ROOT / "scripts" / "common" / "resolve_build_flow.sh"
MATRIX_LABELS = REPO_ROOT / "scripts" / "common" / "matrix_runner_labels.py"
PIPELINE = REPO_ROOT / ".github" / "workflows" / "unity-pipeline.yml"

MAPPING = json.dumps({
    "Android": ["ubuntu-latest"],
    "Windows64": ["self-hosted", "windows"],
    "iOS": ["self-hosted", "macOS"],
})


def resolve(tmp_path, **env):
    out = tmp_path / "out.txt"
    out.write_text("", encoding="utf-8")
    result = subprocess.run(["bash", str(RESOLVER)], capture_output=True, text=True, env={
        **os.environ, "GITHUB_OUTPUT": str(out), "EVENT_NAME": "workflow_dispatch",
        "REF_NAME": "main", "IN_ENVIRONMENT": "production", "PROJECT_PATH": str(tmp_path),
        **{k: str(v) for k, v in env.items()},
    })
    assert result.returncode == 0, result.stderr
    outputs = {}
    for line in out.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            outputs[key] = value
    return outputs, result.stderr


# ---------------------------------------------------------------------------
# The escaping, run as the workflow runs it
# ---------------------------------------------------------------------------

def matrix_labels(platform, mapping=MAPPING):
    return subprocess.run(
        [sys.executable, str(MATRIX_LABELS)], capture_output=True, text=True,
        env={**os.environ, "RUNNER_LABELS_BY_PLATFORM": mapping, "RL_PLATFORM": platform},
    ).stdout


@pytest.mark.parametrize("platform,expected", [
    ("Android", ["ubuntu-latest"]),
    ("Windows64", ["self-hosted", "windows"]),
    ("iOS", ["self-hosted", "macOS"]),
])
def test_the_row_survives_both_json_levels(platform, expected):
    """A matrix row goes through fromJSON into an input that is itself a JSON
    string. Get the escaping wrong and the run dies at expression-evaluation
    time, before any step, with no log to read."""
    row = json.loads('{"runner-labels":"%s"}' % matrix_labels(platform))
    assert json.loads(row["runner-labels"]) == expected


def test_an_unplanned_platform_still_lands_somewhere():
    """A row with no runs-on kills the whole run, not just that platform."""
    assert json.loads(json.loads('{"x":"%s"}' % matrix_labels("Atari"))["x"]) == ["ubuntu-latest"]


def test_a_broken_mapping_does_not_take_the_run_down():
    assert json.loads(json.loads('{"x":"%s"}' % matrix_labels("iOS", "not json"))["x"]) == ["ubuntu-latest"]


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

def test_each_os_gets_its_own_runner(tmp_path):
    outputs, _ = resolve(tmp_path, IN_PLATFORM="Android,iOS,Windows")
    mapping = json.loads(outputs["runner-labels-by-platform"])
    assert mapping["Android"] == ["ubuntu-latest"]
    assert mapping["Windows64"] == ["windows-latest"]
    assert mapping["iOS"] == ["macos-latest"]
    # The whole point: they must not all be the same list any more.
    assert len({tuple(v) for v in mapping.values()}) == 3


def test_self_hosted_defaults_name_the_os_they_mean(tmp_path):
    """The old default was `self-hosted,windows` for every platform, so a
    self-hosted Linux build asked for a Windows machine and waited."""
    outputs, _ = resolve(tmp_path, IN_PLATFORM="Android,iOS,Windows",
                         IN_RUNNER_TYPE="self-hosted", IN_BUILD_ENGINE="docker")
    mapping = json.loads(outputs["runner-labels-by-platform"])
    assert mapping["Android"] == ["self-hosted", "linux"]
    assert mapping["Windows64"] == ["self-hosted", "windows"]
    assert mapping["iOS"] == ["self-hosted", "macOS"]


def test_an_explicit_global_label_set_still_wins(tmp_path):
    """One runner that does everything is a real setup, and removing its escape
    hatch would break exactly those projects."""
    outputs, _ = resolve(tmp_path, IN_PLATFORM="Android,iOS,Windows",
                         IN_RUNNER_TYPE="self-hosted", IN_BUILD_ENGINE="docker",
                         IN_RUNNER_LABELS="self-hosted,everything")
    mapping = json.loads(outputs["runner-labels-by-platform"])
    assert all(v == ["self-hosted", "everything"] for v in mapping.values()), mapping


def test_ios_is_no_longer_flagged_when_it_gets_its_own_macos_runner(tmp_path):
    """Stage 1 warned because iOS shared the Windows label set. Stage 2 removes
    the cause, so the warning must stop — a warning that never clears is noise."""
    _, stderr = resolve(tmp_path, IN_PLATFORM="iOS",
                        IN_RUNNER_TYPE="self-hosted", IN_BUILD_ENGINE="local")
    assert "Xcode only exists on macOS" not in stderr


def test_the_unity_jobs_do_not_ride_the_build_matrix_labels(tmp_path):
    """Tests and Addressables always run in the Linux container, whatever the
    matrix is doing."""
    outputs, _ = resolve(tmp_path, IN_PLATFORM="Windows",
                         IN_RUNNER_TYPE="self-hosted", IN_BUILD_ENGINE="docker")
    assert json.loads(outputs["runner-labels-linux"]) == ["self-hosted", "linux"]


def test_the_build_job_routes_on_the_matrix_row():
    """Guard against a revert to one shared list."""
    text = PIPELINE.read_text(encoding="utf-8")
    assert "runner-labels:       ${{ matrix.runner-labels }}" in text
    assert text.count("runner-labels:       ${{ needs.resolve-config.outputs.runner-labels }}") == 0
