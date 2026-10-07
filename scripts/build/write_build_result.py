#!/usr/bin/env python3
"""write_build_result.py -- the build leg's verdict, as a file and as outputs.

The last word of reusable-build-platform.yml's build job ("Set job outputs").
It decides the platform's result from whichever build step ran, writes
pipeline-results/build-<Platform>.json for the report stage, and sets the
job's outputs, including that JSON hex-encoded as result-json.

It was ~100 lines of bash that built the JSON with printf: values were not
escaped, and the Unity log's first error was spliced into the script by
`${{ }}` interpolation, so an error message holding `$(...)` was run by the
shell. Every value now arrives through the step's env and is serialized by
json.

Environment (all optional; empty means "not produced"):
  PLATFORM CONFIGURATION ENVIRONMENT BLOCKED
  OUTCOME_DOCKER OUTCOME_ADDRESSABLES_DOCKER OUTCOME_DOCKER_WINDOWS
  OUTCOME_WINDOWS OUTCOME_MACOS        step outcomes, in this priority order
  ARTIFACT_TYPE ARTIFACT_NAME ARTIFACT_SIZE_BYTES ARTIFACT_ID
  BUILD_DURATION UNITY_VERSION_USED
  ERROR_COUNT WARNING_COUNT FIRST_ERROR LOG_FOUND
  DOWNLOAD_URL DIRECT_URL
  GITHUB_SERVER_URL GITHUB_REPOSITORY GITHUB_RUN_ID GITHUB_OUTPUT

Usage: write_build_result.py [--results-dir pipeline-results]
"""
import argparse
import json
import os
import pathlib
import sys

# Whichever build step actually ran decides the result; at most one does.
OUTCOME_KEYS = ("OUTCOME_DOCKER", "OUTCOME_ADDRESSABLES_DOCKER", "OUTCOME_DOCKER_WINDOWS",
                "OUTCOME_WINDOWS", "OUTCOME_MACOS")


def env(name, default=""):
    return (os.environ.get(name) or default).strip()


def decide():
    """(result, artifact name) for the leg."""
    if env("BLOCKED") == "true":
        return "blocked", ""
    outcome = next((env(k) for k in OUTCOME_KEYS if env(k) and env(k) != "skipped"), "")
    if outcome == "success":
        return "success", env("ARTIFACT_NAME")
    if not outcome:
        return "skipped", ""          # no build step ran
    return outcome, ""


def build_result():
    result, artifact_name = decide()
    artifact_id = env("ARTIFACT_ID")
    artifact_url = ""
    if artifact_id:
        # /artifacts/<id> is the endpoint the browser's own download button
        # uses: a link that starts the download, not a page to search.
        artifact_url = "%s/%s/actions/runs/%s/artifacts/%s" % (
            env("GITHUB_SERVER_URL", "https://github.com"), env("GITHUB_REPOSITORY"),
            env("GITHUB_RUN_ID"), artifact_id)
    first_error = env("FIRST_ERROR").replace("\n", " ")[:160]
    # Field order and the all-strings form are what the report stage and
    # Discord read; keep them.
    return {
        "platform": env("PLATFORM"),
        "stage": "03",
        "result": result,
        "artifactType": env("ARTIFACT_TYPE"),
        "artifactName": artifact_name,
        "artifactSizeBytes": env("ARTIFACT_SIZE_BYTES"),
        "durationSeconds": env("BUILD_DURATION", "0"),
        "configuration": env("CONFIGURATION") or env("ENVIRONMENT"),
        "errorCount": env("ERROR_COUNT", "0"),
        "warningCount": env("WARNING_COUNT", "0"),
        "firstError": first_error,
        "artifactUrl": artifact_url,
        "downloadUrl": env("DOWNLOAD_URL"),
        "directDownloadUrl": env("DIRECT_URL"),
        "logMeasured": "true" if env("LOG_FOUND") == "true" else "false",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Write the build leg's result file and outputs.")
    parser.add_argument("--results-dir", default="pipeline-results")
    args = parser.parse_args(argv)

    row = build_result()
    blocked = env("BLOCKED") == "true"
    text = json.dumps(row, separators=(",", ":")) + "\n"
    results = pathlib.Path(args.results_dir)
    results.mkdir(parents=True, exist_ok=True)
    # When this workflow is called from a MATRIX job, `needs.<job>.outputs`
    # collapses to one arbitrary leg; the report stage collects this file.
    (results / ("build-%s.json" % row["platform"])).write_text(text, encoding="utf-8")

    outputs = {
        "result": row["result"],
        "artifact-name": row["artifactName"],
        "unity-version-used": env("UNITY_VERSION_USED"),
        "build-duration-seconds": row["durationSeconds"],
        # The manifest exists whenever the build was not blocked, failures
        # included: stage 07 uses it to say what was being built when it broke.
        "manifest-artifact-name": "" if blocked else "%s-manifest" % env("ARTIFACT_NAME"),
        "configuration": row["configuration"],
        # The same result for the report jobs when the artifact upload of the
        # file fails (a full storage quota). Hex, not raw JSON: GitHub masks
        # every line of a multi-line secret, so a service-account JSON
        # registers "{" and "}" and an output holding a brace is dropped
        # ("Skip output ... may contain secret"). Base64 forms of secrets are
        # hidden too; hex digits are not.
        "result-json": text.encode("utf-8").hex(),
    }
    if row["artifactUrl"]:
        outputs["artifact-url"] = row["artifactUrl"]
        print("[artifact] %s" % row["artifactUrl"])
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            for key, value in outputs.items():
                fh.write("%s=%s\n" % (key, value))
    print("[result] %s: %s" % (row["platform"], row["result"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
