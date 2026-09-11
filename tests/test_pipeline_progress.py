"""
The progress ladder.

GitHub renders no progress indicator on a workflow node: the graph shows shape
and colour but not distance. With eleven nodes, three of them matrix legs and
four legitimately skipped, "how far did this get?" was a question a human had
to reconstruct by eye. The ladder answers it, and these tests pin the parts
that are easy to get subtly and misleadingly wrong — mainly the arithmetic
around skipped phases, which decides whether a failed run reads as "nearly
done" or "stopped early".
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
SCRIPT = REPO_ROOT / "scripts" / "common" / "pipeline_progress.py"

sys.path.insert(0, str(REPO_ROOT / "scripts" / "common"))
from pipeline_progress import (  # noqa: E402
    apply_current, parse_stages, render, resolve_blocked,
)

PROMOTION_PIPELINES = [
    "pipeline-android-release.yml",
    "pipeline-ios-release.yml",
    "pipeline-webgl-release.yml",
    "pipeline-windows-release.yml",
    "pipeline-linux-release.yml",
]


def run(*args, **env):
    return subprocess.run(["python3", str(SCRIPT), *args],
                          capture_output=True, text=True,
                          env={**os.environ, **env})


# ---------------------------------------------------------------------------
# Counting distance
# ---------------------------------------------------------------------------

def test_a_finished_run_is_full():
    block = render("t", [("a", "success"), ("b", "success")])
    assert "2/2 stages" in block


def test_a_deliberately_skipped_phase_still_counts_as_covered():
    """A phase skipped on purpose — a dry run, a later start-phase, a platform
    this project does not ship — is not a stall. Drawing it as one would make
    every normal run look stuck."""
    block = render("t", [("a", "success"), ("b", "skipped"), ("c", "success")])
    assert "3/3 stages" in block


def test_a_phase_skipped_after_a_failure_does_not_count():
    """The arithmetic that matters. Without this a failed run counts its own
    wreckage as distance: "4/5" beside a red cross reads as nearly finished,
    when nothing after the failure was even attempted."""
    block = render("t", [("a", "success"), ("b", "skipped"), ("c", "failure"),
                         ("d", "skipped"), ("e", "skipped")])
    assert "2/5 stages" in block
    assert "not reached" in block


def test_blocked_and_skipped_are_told_apart():
    resolved = dict(resolve_blocked([
        ("a", "skipped"), ("b", "failure"), ("c", "skipped")]))
    assert resolved["a"] == "skipped"
    assert resolved["c"] == "blocked"


def test_a_cancelled_run_blocks_what_follows():
    block = render("t", [("a", "success"), ("b", "cancelled"), ("c", "skipped")])
    assert "1/3 stages" in block


def test_an_empty_result_is_pending_not_broken():
    """`needs.x.result` renders as the empty string for a job that has not
    reported."""
    assert parse_stages("a=\nb=success") == [("a", "pending"), ("b", "success")]


def test_no_stages_does_not_crash():
    assert "no stages" in render("t", [])


# ---------------------------------------------------------------------------
# The live marker
# ---------------------------------------------------------------------------

def test_the_current_stage_is_running_and_earlier_ones_are_done():
    """A job calling this knows its own position and nothing else. Everything
    earlier is done because the sequence already guarantees it — a later phase
    cannot start until the earlier ones let it."""
    stages = apply_current(
        [("a", "pending"), ("b", "pending"), ("c", "pending")], "b")
    assert stages == [("a", "success"), ("b", "running"), ("c", "pending")]


def test_a_known_result_is_not_overwritten_by_the_marker():
    stages = apply_current([("a", "skipped"), ("b", "pending")], "b")
    assert stages[0] == ("a", "skipped")


def test_an_unknown_current_stage_is_a_usage_error():
    """A typo in a job's `current:` would otherwise draw a silently wrong
    ladder — every stage done, none of them running."""
    proc = run("--title", "t", "--stages", "a\nb", "--current", "typo")
    assert proc.returncode == 2
    assert "not one of the declared stages" in proc.stderr


def test_the_ladder_reaches_the_job_summary(tmp_path):
    summary = tmp_path / "summary.md"
    proc = run("--title", "Release / Android", "--stages", "a\nb", "--current", "a",
               GITHUB_STEP_SUMMARY=str(summary))
    assert proc.returncode == 0, proc.stderr
    text = summary.read_text()
    assert "```text" in text and "Release / Android" in text


def test_a_note_is_rendered_under_the_ladder():
    block = render("t", [("a", "failure")], note="Steam configuration missing.")
    assert block.rstrip().endswith("Steam configuration missing.")


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def load(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())


@pytest.mark.parametrize("name", PROMOTION_PIPELINES)
def test_the_stage_list_is_declared_once(name):
    """Per-job stage lists would drift, and a ladder that disagrees with itself
    between two jobs of the same run is worse than no ladder."""
    workflow = load(name)
    assert "PIPELINE_STAGES" in (workflow.get("env") or {}), name


@pytest.mark.parametrize("name", PROMOTION_PIPELINES)
def test_every_stage_job_draws_the_ladder(name):
    """Otherwise the run summary has holes exactly where the reader is
    waiting."""
    workflow = load(name)
    declared = [line.strip() for line in
                workflow["env"]["PIPELINE_STAGES"].strip().splitlines()]
    marked = []
    for job_id, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            if "pipeline-progress" in str(step.get("uses", "")):
                marked.append(step["with"]["current"])
    # `report` is not a stage of its own here; every other declared stage is.
    missing = [s for s in declared if s not in marked]
    assert not missing, f"{name}: no ladder in {missing}"


@pytest.mark.parametrize("name", PROMOTION_PIPELINES + ["unity-pipeline.yml"])
def test_every_marked_stage_is_a_declared_one(name):
    workflow = load(name)
    declared = [line.strip() for line in
                (workflow.get("env") or {}).get("PIPELINE_STAGES", "").strip().splitlines()]
    for job_id, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            if "pipeline-progress" not in str(step.get("uses", "")):
                continue
            current = step["with"]["current"]
            assert current in declared, f"{name}:{job_id} marks '{current}'"


@pytest.mark.parametrize("name", PROMOTION_PIPELINES + ["unity-pipeline.yml"])
def test_the_ladder_never_fails_a_release(name):
    """A progress indicator is not worth failing a promotion over."""
    workflow = load(name)
    for job_id, job in workflow["jobs"].items():
        for step in job.get("steps", []):
            if "pipeline-progress" in str(step.get("uses", "")):
                assert step.get("continue-on-error") is True, f"{name}:{job_id}"
                assert "always()" in str(step.get("if", "")), f"{name}:{job_id}"


@pytest.mark.parametrize("name", PROMOTION_PIPELINES + ["unity-pipeline.yml"])
def test_the_action_is_taken_from_the_toolkit_checkout(name):
    """A step-level `uses: ./…` inside a reusable workflow resolves against the
    CALLER's workspace, where the toolkit's actions do not exist — the trap the
    release pipelines already documented once."""
    workflow = load(name)
    for job_id, job in workflow["jobs"].items():
        steps = job.get("steps", [])
        for step in steps:
            uses = str(step.get("uses", ""))
            if "pipeline-progress" not in uses:
                continue
            assert uses.startswith("./.toolkit/"), f"{name}:{job_id}: {uses}"
            # And the job must actually have that checkout, or the step errors.
            assert any("checkout" in str(s.get("uses", ""))
                       and ".toolkit" in json.dumps(s.get("with", {}))
                       for s in steps), f"{name}:{job_id} has no toolkit checkout"


def test_the_shared_report_draws_the_same_ladder():
    """What you watched during the run and what you read afterwards should be
    the same picture."""
    action = (REPO_ROOT / ".github" / "actions" / "release-report"
              / "action.yml").read_text()
    assert "from pipeline_progress import render" in action
