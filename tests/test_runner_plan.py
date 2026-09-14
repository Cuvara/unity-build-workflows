"""The runner plan: say where each platform lands, and which tier decided it.

Eight settings feed one decision — `RUNNER_TYPE`, `BUILD_ENGINE`,
`RUNNER_LABELS`, the three per-OS label variables, the legacy
`RUNNER_DEFAULT_MODE` family, and the derived `runner-mode` — and until now
nothing printed the answer. Somebody asking "why did this job land on that
machine?" had to read the resolver.

These tests pin the report, not the routing. The routing is deliberately
unchanged: every platform still gets one shared `runs-on`, which is the finding
rather than a rendering bug. See docs/adr/004-runner-selection.md.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
RESOLVER = REPO_ROOT / "scripts" / "common" / "resolve_build_flow.sh"


def run_resolver(tmp_path, **env):
    out = tmp_path / "github_output.txt"
    out.write_text("", encoding="utf-8")
    environment = {
        **os.environ,
        "GITHUB_OUTPUT": str(out),
        "EVENT_NAME": "workflow_dispatch",
        "REF_NAME": "main",
        "IN_ENVIRONMENT": "production",
        "PROJECT_PATH": str(tmp_path),
        **{k: str(v) for k, v in env.items()},
    }
    result = subprocess.run(["bash", str(RESOLVER)], capture_output=True, text=True,
                            env=environment)
    assert result.returncode == 0, result.stderr
    outputs = {}
    for line in out.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            outputs[key] = value
    return outputs, result.stderr


def plan_of(tmp_path, **env):
    outputs, stderr = run_resolver(tmp_path, **env)
    assert "runner-plan" in outputs, "the resolver must emit a runner-plan"
    return json.loads(outputs["runner-plan"]), stderr


def test_every_selected_platform_gets_a_row(tmp_path):
    plan, _ = plan_of(tmp_path, IN_PLATFORM="All",
                      DEVELOP_BUILD_PLATFORMS="Android,WebGL")
    platforms = {row["platform"] for row in plan}
    assert platforms, "a run that builds something must plan something"
    for row in plan:
        # A row that cannot say where it runs is worse than no row: it reads
        # like an answer.
        assert row["runsOn"], f"{row['platform']} has no runs-on"
        assert row["engine"], f"{row['platform']} has no engine"
        assert row["labelsSource"], f"{row['platform']} does not say which tier decided"


def test_the_ci_lane_plans_nothing(tmp_path):
    """`platform: None` is the CI lane, and it builds no player.

    The resolver's own platform flags still read true for it: on a push it takes
    the platform set from the branch's `*_BUILD_PLATFORMS` variable and never
    looks at the input, and the honouring happens later, where the matrix is
    built. A plan listing Android and WebGL for a run that builds neither would
    be a confident answer to the wrong question.
    """
    plan, stderr = plan_of(tmp_path, EVENT_NAME="push", REF_NAME="develop",
                           IN_PLATFORM="None",
                           DEVELOP_BUILD_PLATFORMS="Android,WebGL")
    assert plan == [], f"the CI lane planned builds it will not run: {plan}"
    assert "no player build" in stderr


def test_the_plan_names_the_tier_that_won(tmp_path):
    """`default` and `dispatch` must be distinguishable — 'why' is the whole
    point, and a plan that always says the same thing answers nothing."""
    plan, _ = plan_of(tmp_path, IN_PLATFORM="Android")
    assert plan[0]["labelsSource"] == "default"

    plan, _ = plan_of(tmp_path, IN_PLATFORM="Android",
                      IN_RUNNER_LABELS="self-hosted,linux",
                      IN_RUNNER_TYPE="self-hosted", IN_BUILD_ENGINE="docker")
    assert plan[0]["labelsSource"] == "dispatch"
    assert plan[0]["runsOn"] == ["self-hosted", "linux"]


def test_ios_on_non_macos_labels_is_reported(tmp_path):
    """The self-hosted default is `self-hosted,windows` regardless of OS, so an
    iOS build lands on a Windows box and simply queues — GitHub does not fail a
    job whose labels match no runner, it waits forever.
    """
    plan, stderr = plan_of(tmp_path, IN_PLATFORM="iOS",
                           IN_RUNNER_TYPE="self-hosted", IN_BUILD_ENGINE="local")
    row = next(r for r in plan if r["platform"] == "iOS")
    assert row["note"], "iOS on Windows labels must carry a note"
    assert "macOS" in row["note"]
    assert "Xcode only exists on macOS" in stderr


def test_ios_on_macos_labels_is_not_flagged(tmp_path):
    plan, _ = plan_of(tmp_path, IN_PLATFORM="iOS",
                      IN_RUNNER_TYPE="self-hosted", IN_BUILD_ENGINE="local",
                      IN_RUNNER_LABELS="self-hosted,macOS")
    row = next(r for r in plan if r["platform"] == "iOS")
    assert row["note"] == "", f"unexpected note: {row['note']}"


def test_the_plan_reports_the_shared_runs_on_it_actually_has(tmp_path):
    """Today every build platform shares ONE runs-on, because stage 03 passes a
    single runner-labels list to every leg of the matrix. The plan must show
    that rather than imply per-platform routing that does not exist yet.
    """
    plan, _ = plan_of(tmp_path, IN_PLATFORM="All",
                      DEVELOP_BUILD_PLATFORMS="Android,WebGL",
                      RELEASE_BUILD_PLATFORMS="Android,WebGL")
    runs_on = {tuple(row["runsOn"]) for row in plan}
    assert len(runs_on) == 1, (
        "the plan claims per-platform runners the pipeline does not implement; "
        "if routing became per-platform, this test is the one to update"
    )
