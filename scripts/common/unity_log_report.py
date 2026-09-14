#!/usr/bin/env python3
"""
unity_log_report.py
Read a finished build run's logs and report what Unity said, as a check run on
the commit.

On the game-ci lane Unity streams its output to the job console and writes no
`Editor.log`, so there is nothing on disk for the build job to parse. The
output does exist — in the job's own log — but `GITHUB_TOKEN` cannot download
a job log while the run is still in progress: the endpoint 404s until the whole
run completes. A build cannot summarise itself.

So this runs afterwards, from a `workflow_run` trigger, when the logs are
readable. It creates a **check run** against the build's commit rather than
printing annotations into its own run, because a check run attaches the
diagnostics to the commit and the pull request — where someone reviewing the
change will see `Player.cs:42  CS0103` on the line itself, instead of finding a
second workflow run and reading its console.

Stdlib only. Never fails: a diagnostics job that goes red teaches people to
ignore it, and it has no opinion about whether the build was any good — the
build already decided that.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from summarise_unity_log import parse_lines, render  # noqa: E402

API = "https://api.github.com"

# The check run's annotations are what appear on the diff. GitHub accepts 50
# per request; more than a handful on one commit is already unreadable.
MAX_ANNOTATIONS = 50


def api(path, token, method="GET", payload=None, raw=False):
    request = urllib.request.Request(
        f"{API}{path}" if path.startswith("/") else path,
        method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Content-Type": "application/json"} if payload is not None else {}),
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        body = response.read()
    return body if raw else json.loads(body or b"{}")


def build_jobs(repo, run_id, token):
    """Jobs that ran a Unity build. Stage 03 and 03b, by name."""
    jobs, page = [], 1
    while True:
        data = api(f"/repos/{repo}/actions/runs/{run_id}/jobs"
                   f"?per_page=100&page={page}", token)
        batch = data.get("jobs") or []
        jobs += batch
        if len(batch) < 100:
            break
        page += 1
    return [j for j in jobs
            if "/ 03 /" in j.get("name", "") or "/ 03b /" in j.get("name", "")]


def annotations_for(errors, warnings):
    """Only diagnostics with a file and line can be attached to the diff.

    A message without a location still counts and still appears in the check's
    summary text; it simply has nowhere to point.
    """
    items = []
    for severity, source in (("failure", errors), ("warning", warnings)):
        for item in source:
            if not item.get("file"):
                continue
            items.append({
                "path": item["file"],
                "start_line": item["line"],
                "end_line": item["line"],
                "annotation_level": severity,
                "message": item["message"][:400],
                "title": item.get("code") or "Unity",
            })
    return items[:MAX_ANNOTATIONS]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Report a build run's Unity log")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--run-id", required=True, help="The completed build run")
    parser.add_argument("--head-sha", default="", help="Commit to attach the check to")
    parser.add_argument("--check-name", default="Unity build diagnostics")
    args = parser.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token or not args.repo:
        print("::warning::GITHUB_TOKEN or repository missing — no diagnostics")
        return 0

    try:
        jobs = build_jobs(args.repo, args.run_id, token)
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        print(f"::warning::Could not list the run's jobs: {exc}")
        return 0

    if not jobs:
        print("No Unity build jobs in this run — nothing to report.")
        return 0

    sections, all_errors, all_warnings = [], [], []
    for job in jobs:
        name = job.get("name", "?")
        try:
            log = api(f"/repos/{args.repo}/actions/jobs/{job['id']}/logs",
                      token, raw=True).decode("utf-8", "replace")
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            # A skipped job has no log, which is not a problem worth reporting.
            print(f"[log-report] no log for {name}: {exc}")
            continue
        errors, warnings = parse_lines(log.splitlines())
        all_errors += errors
        all_warnings += warnings
        sections.append(render(errors, warnings, name, show=15,
                               saw_editor_log=True))
        print(f"[log-report] {name}: {len(errors)} error(s), "
              f"{len(warnings)} warning(s)")

    if not sections:
        print("No readable logs — nothing to report.")
        return 0

    head_sha = args.head_sha or os.environ.get("GITHUB_SHA", "")
    if not head_sha:
        print("::warning::No head SHA — printing the summary instead of a check")
        print("\n\n".join(sections))
        return 0

    total = f"{len(all_errors)} error(s), {len(all_warnings)} warning(s)"
    body = "\n\n".join(sections)
    # Neutral, never failure: the build already reported its own outcome, and a
    # second red mark on the same commit for the same reason helps nobody.
    payload = {
        "name": args.check_name,
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": "neutral",
        "output": {
            "title": total,
            "summary": body[:65000],
            "annotations": annotations_for(all_errors, all_warnings),
        },
    }
    try:
        result = api(f"/repos/{args.repo}/check-runs", token,
                     method="POST", payload=payload)
        print(f"[log-report] check run created: {result.get('html_url', '')}")
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        detail = getattr(exc, "code", exc)
        print(f"::warning::Could not create the check run ({detail}). "
              "This needs `checks: write`.")
        print(body)

    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write(body + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
