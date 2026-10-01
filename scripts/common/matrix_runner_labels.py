#!/usr/bin/env python3
"""One platform's matrix-row value, escaped for a matrix row.

The row travels through `fromJSON` into a workflow input that is itself a JSON
string, so the quotes have to survive one level more than they look like:

    row:   {"platform":"iOS","runner-labels":"[\\"self-hosted\\",\\"macOS\\"]"}
    input: runner-labels: ["self-hosted","macOS"]
    job:   runs-on: ${{ fromJSON(inputs.runner-labels) }}

Get the escaping wrong and the job does not fail — `fromJSON` throws at the
workflow level and the run dies before any step, with no log to read. This is a
script rather than a jq one-liner so the exact code can be run in a test.

Serialization only: this script never decides where a job runs. It reads the
decision from one of two places, in this order:

  RUNNER_SELECTION     the runner-selection document from runner_scheduler.py;
                       RL_FIELD picks the field (default `runsOn`; also
                       `buildEngine`, `activationStrategy`, `selectedTarget`).
  RUNNER_LABELS_BY_PLATFORM
                       the legacy per-platform label map (JSON object), read
                       when no selection document is given.

RL_PLATFORM names the platform. Prints the escaped value on stdout.
"""
import json
import os
import sys

FALLBACK = ["ubuntu-latest"]


def _escape(value) -> str:
    if isinstance(value, (list, dict)):
        text = json.dumps(value, separators=(",", ":"))
    else:
        text = str(value)
    return text.replace("\\", "\\\\").replace('"', '\\"')


def escaped_labels(mapping_json: str, platform: str) -> str:
    try:
        mapping = json.loads(mapping_json) if mapping_json.strip() else {}
    except json.JSONDecodeError:
        mapping = {}
    labels = mapping.get(platform) if isinstance(mapping, dict) else None
    if not isinstance(labels, list) or not labels:
        # A platform the resolver did not plan still has to land somewhere; a
        # row with no runs-on kills the whole run at expression-evaluation time.
        labels = FALLBACK
    return _escape(labels)


def escaped_field(selection_json: str, platform: str, field: str = "runsOn") -> str:
    try:
        selection = json.loads(selection_json) if selection_json.strip() else {}
    except json.JSONDecodeError:
        selection = {}
    jobs = selection.get("jobs") if isinstance(selection, dict) else None
    entry = jobs.get(platform) if isinstance(jobs, dict) else None
    value = entry.get(field) if isinstance(entry, dict) else None
    if field == "runsOn":
        valid_list = isinstance(value, list) and value
        valid_group = isinstance(value, dict) and (value.get("group") or value.get("labels"))
        if not (valid_list or valid_group):
            value = FALLBACK
    elif value is None:
        value = ""
    return _escape(value)


if __name__ == "__main__":
    _selection = os.environ.get("RUNNER_SELECTION", "")
    _platform = os.environ.get("RL_PLATFORM", "")
    _field = os.environ.get("RL_FIELD", "") or "runsOn"
    if _selection.strip():
        sys.stdout.write(escaped_field(_selection, _platform, _field))
    elif _field != "runsOn":
        # The legacy map only knows labels; any other field is "not decided
        # here", and the workflow falls back to the run-wide value.
        sys.stdout.write("")
    else:
        sys.stdout.write(escaped_labels(os.environ.get("RUNNER_LABELS_BY_PLATFORM", ""), _platform))
