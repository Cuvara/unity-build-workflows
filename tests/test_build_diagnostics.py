"""
Reporting what Unity said, after the run that said it.

A build cannot summarise its own Unity output on the game-ci lane. Unity
streams to the job console and writes no `Editor.log`, and `GITHUB_TOKEN`
cannot download a job's log while the run is in progress — the endpoint 404s
until the whole run completes. Both halves were proven the hard way: the
file-based summariser found only a shader log, and an in-run API fetch warned
three times per build and produced nothing.

So the report runs afterwards, on `workflow_run`, and attaches a check run to
the commit — which is the difference between a diagnostic on the line that
caused it and a diagnostic in a console nobody opens.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "common" / "unity_log_report.py"
TEMPLATE = REPO_ROOT / "templates" / "consumer-09-build-diagnostics.yml"

sys.path.insert(0, str(REPO_ROOT / "scripts" / "common"))
import unity_log_report as report  # noqa: E402
from summarise_unity_log import parse_lines  # noqa: E402


JOB_LOG = """2026-01-01T00:00:00Z Initialize engine version: 6000.0.26f1
2026-01-01T00:00:01Z Assets/Scripts/Player.cs(42,17): error CS0103: The name 'foo' does not exist
2026-01-01T00:00:02Z Assets/Scripts/Enemy.cs(10,5): warning CS0168: The variable 'e' is unused
2026-01-01T00:00:03Z [Error] Failed to load asset bundle
"""


# ---------------------------------------------------------------------------
# Parsing a log held as text
# ---------------------------------------------------------------------------

def test_a_job_log_parses_without_touching_disk():
    """The log arrives from the API as text. Writing it to a file first only
    to read it back would be ceremony."""
    errors, warnings = parse_lines(JOB_LOG.splitlines())
    assert len(errors) == 2
    assert len(warnings) == 1
    assert errors[0]["file"] == "Assets/Scripts/Player.cs"
    assert errors[0]["line"] == 42


def test_timestamps_do_not_hide_the_diagnostics():
    """GitHub prefixes every job-log line with an ISO timestamp; a parser
    anchored to the start of the line would match nothing."""
    errors, _ = parse_lines(JOB_LOG.splitlines())
    assert any(e.get("code") == "CS0103" for e in errors)


# ---------------------------------------------------------------------------
# What lands on the commit
# ---------------------------------------------------------------------------

def test_only_located_diagnostics_become_annotations():
    """An annotation needs a file and a line. A message without one still
    counts and still appears in the summary — it just has nowhere to point."""
    errors, warnings = parse_lines(JOB_LOG.splitlines())
    annotations = report.annotations_for(errors, warnings)
    assert len(annotations) == 2  # the CS0103 and the CS0168
    paths = {a["path"] for a in annotations}
    assert paths == {"Assets/Scripts/Player.cs", "Assets/Scripts/Enemy.cs"}


def test_errors_and_warnings_keep_their_severity():
    errors, warnings = parse_lines(JOB_LOG.splitlines())
    levels = {a["path"]: a["annotation_level"]
              for a in report.annotations_for(errors, warnings)}
    assert levels["Assets/Scripts/Player.cs"] == "failure"
    assert levels["Assets/Scripts/Enemy.cs"] == "warning"


def test_annotations_are_capped():
    """GitHub takes 50 per request, and more than a handful on one commit is
    already unreadable."""
    errors = [{"file": f"A/F{i}.cs", "line": 1, "col": 1,
               "code": "CS0103", "message": "x"} for i in range(80)]
    assert len(report.annotations_for(errors, [])) == report.MAX_ANNOTATIONS


# ---------------------------------------------------------------------------
# It must never be the reason anything is red
# ---------------------------------------------------------------------------

def test_without_a_token_it_says_so_and_exits_zero(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    proc = subprocess.run(
        ["python3", str(SCRIPT), "--repo", "o/r", "--run-id", "1"],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin"})
    assert proc.returncode == 0
    assert "::warning::" in proc.stdout


def test_the_check_is_neutral_not_a_failure():
    """The build already reported its own outcome. A second red mark on the
    same commit for the same reason helps nobody."""
    body = SCRIPT.read_text()
    assert '"conclusion": "neutral"' in body


def test_only_build_jobs_are_read():
    """Reading every job's log would drown the report in the report's own
    output, and stages 01, 02 and 07 run no Unity."""
    body = SCRIPT.read_text()
    assert '"/ 03 /" in j.get("name", "")' in body
    assert '"/ 03b /" in j.get("name", "")' in body


# ---------------------------------------------------------------------------
# The workflow that runs it
# ---------------------------------------------------------------------------

def workflow():
    template = yaml.safe_load(TEMPLATE.read_text())
    return template, (template.get("on") or template[True])


def test_it_runs_after_the_build_not_inside_it():
    """The constraint that shaped this: a job log is unreadable until the run
    it belongs to has finished."""
    _, triggers = workflow()
    assert "workflow_run" in triggers
    assert triggers["workflow_run"]["types"] == ["completed"]


def test_it_watches_the_build_workflows_by_name():
    _, triggers = workflow()
    watched = triggers["workflow_run"]["workflows"]
    for name in ("Build / Development", "Build / Release"):
        assert name in watched, watched


@pytest.mark.parametrize("permission,level", [
    ("actions", "read"),   # read another run's job logs
    ("checks", "write"),   # attach the diagnostics to the commit
])
def test_it_asks_for_exactly_what_it_needs(permission, level):
    template, _ = workflow()
    assert template["permissions"][permission] == level


def test_it_does_not_ask_for_write_access_to_the_repository():
    """It reads logs and posts a check. Nothing else."""
    template, _ = workflow()
    assert template["permissions"].get("contents") == "read"
    assert "packages" not in template["permissions"]


def test_the_watched_names_match_the_shipped_entry_points():
    """A `workflow_run` trigger matches on the workflow's `name:`. Rename an
    entry point without updating this list and the diagnostics stop running,
    silently — there is no error for a trigger that never fires."""
    _, triggers = workflow()
    watched = set(triggers["workflow_run"]["workflows"])
    for name in ("consumer-10-build-development", "consumer-11-build-release",
                 "consumer-01-ci"):
        entry = yaml.safe_load((REPO_ROOT / "templates" / f"{name}.yml").read_text())
        assert entry["name"] in watched, f"{name} is named {entry['name']!r}"
