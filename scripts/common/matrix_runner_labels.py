#!/usr/bin/env python3
"""One platform's `runs-on`, escaped for a matrix row.

The row travels through `fromJSON` into a workflow input that is itself a JSON
string, so the quotes have to survive one level more than they look like:

    row:   {"platform":"iOS","runner-labels":"[\\"self-hosted\\",\\"macOS\\"]"}
    input: runner-labels: ["self-hosted","macOS"]
    job:   runs-on: ${{ fromJSON(inputs.runner-labels) }}

Get the escaping wrong and the job does not fail — `fromJSON` throws at the
workflow level and the run dies before any step, with no log to read. This is a
script rather than a jq one-liner so the exact code can be run in a test.

Reads RUNNER_LABELS_BY_PLATFORM (JSON object) and RL_PLATFORM from the
environment; prints the escaped string on stdout.
"""
import json
import os
import sys

FALLBACK = ["ubuntu-latest"]


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
    return json.dumps(labels, separators=(",", ":")).replace('"', '\\"')


if __name__ == "__main__":
    sys.stdout.write(escaped_labels(
        os.environ.get("RUNNER_LABELS_BY_PLATFORM", ""),
        os.environ.get("RL_PLATFORM", ""),
    ))
