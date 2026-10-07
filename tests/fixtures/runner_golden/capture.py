#!/usr/bin/env python3
"""Capture the pre-scheduler runner routing as golden fixtures.

Run ONLY against the commit before the runner scheduler existed (the baseline
this file records is the behaviour the scheduler must not change):

    python tests/fixtures/runner_golden/capture.py > tests/fixtures/runner_golden/baseline.json

Each case runs the real resolve_build_flow.sh and then the real `matrix` step of
unity-pipeline.yml, and records what every job would have been sent to.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
RESOLVER = REPO_ROOT / "scripts" / "common" / "resolve_build_flow.sh"
PIPELINE = REPO_ROOT / ".github" / "workflows" / "unity-pipeline.yml"

PLATFORM_ENV = {
    "Android": "SEL_ANDROID", "WebGL": "SEL_WEBGL", "Linux64": "SEL_LINUX64",
    "LinuxServer": "SEL_LINUXSERVER", "Windows64": "SEL_WINDOWS64", "iOS": "SEL_IOS",
}

ALL = "Android,WebGL,Linux64,LinuxServer,Windows,iOS"

# name -> environment for resolve_build_flow.sh (on top of a production dispatch)
CASES = {
    "default":                     {},
    "default-ios-only":            {"IN_PLATFORM": "iOS"},
    "default-windows-only":        {"IN_PLATFORM": "Windows"},
    "labels-variable":             {"NEW_RUNNER_LABELS": "self-hosted,build-box"},
    "labels-dispatch":             {"IN_RUNNER_LABELS": "self-hosted,build-box"},
    "labels-dispatch-json":        {"IN_RUNNER_LABELS": '["self-hosted","macOS"]'},
    "labels-none-sentinel":        {"NEW_RUNNER_LABELS": "none", "NEW_RUNNER_TYPE": "self-hosted",
                                    "NEW_BUILD_ENGINE": "local"},
    "selfhosted-local-per-os":     {"NEW_RUNNER_TYPE": "self-hosted", "NEW_BUILD_ENGINE": "local",
                                    "NEW_RUNNER_LINUX_LABEL": "farm,linux",
                                    "NEW_RUNNER_WINDOWS_LABEL": "farm,windows",
                                    "NEW_RUNNER_MACOS_LABEL": "farm,macOS"},
    "selfhosted-docker-per-os":    {"NEW_RUNNER_TYPE": "self-hosted", "NEW_BUILD_ENGINE": "docker",
                                    "NEW_RUNNER_LINUX_LABEL": "self-hosted,linux,docker",
                                    "NEW_RUNNER_MACOS_LABEL": "self-hosted,macOS"},
    "selfhosted-local-named":      {"NEW_RUNNER_TYPE": "self-hosted", "NEW_BUILD_ENGINE": "local",
                                    "NEW_RUNNER_LABELS": "mac-build"},
    "selfhosted-no-labels":        {"NEW_RUNNER_TYPE": "self-hosted", "NEW_BUILD_ENGINE": "local"},
    "legacy-mode-docker":          {"NEW_RUNNER_DEFAULT_MODE": "docker"},
    "legacy-mode-auto":            {"VAR_DEFAULT_RUNNER_MODE": "auto"},
    "legacy-mode-windows":         {"NEW_RUNNER_DEFAULT_MODE": "self-hosted-windows"},
    "legacy-mode-macos":           {"VAR_DEFAULT_RUNNER_MODE": "self-hosted-macos"},
    "ci-lane-none":                {"IN_PLATFORM": "None", "EVENT_NAME": "push", "REF_NAME": "develop",
                                    "DEVELOP_BUILD_PLATFORMS": "Android,WebGL"},
}


def run_resolver(extra):
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.txt"
        out.write_text("", encoding="utf-8")
        env = {**os.environ, "GITHUB_OUTPUT": str(out), "EVENT_NAME": "workflow_dispatch",
               "REF_NAME": "main", "IN_ENVIRONMENT": "production", "PROJECT_PATH": tmp,
               "IN_PLATFORM": ALL}
        env.update(extra)
        proc = subprocess.run(["bash", str(RESOLVER)], capture_output=True, text=True, env=env)
        if proc.returncode != 0:
            raise SystemExit(f"resolver failed: {proc.stderr}")
        outputs = {}
        for line in out.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                outputs[k] = v
        return outputs, env["IN_PLATFORM"]


def matrix_script():
    """The script the matrix step runs (scripts/common/resolve_build_matrix.sh)."""
    workflow = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))
    for step in workflow["jobs"]["resolve-config"]["steps"]:
        if step.get("id") == "matrix":
            if "resolve_build_matrix.sh" not in step["run"]:
                raise SystemExit("the matrix step no longer runs resolve_build_matrix.sh")
            return str(REPO_ROOT / "scripts" / "common" / "resolve_build_matrix.sh")
    raise SystemExit("no matrix step")


def run_matrix(flow, platform_input, extra_env):
    env = dict(os.environ)
    env.update({
        "ENVIRONMENT": flow["environment"], "ANDROID_TYPE": flow.get("android-export-type", "aab"),
        "BUILD_TYPE_IN": flow.get("build-type", "release"), "IN_PLATFORM": platform_input,
        "RUN_NUMBER": "42", "BUILD_NUMBER_OFFSET": "0", "PRODUCT_NAME": "TestGame",
        "APP_VERSION": "1.0.0", "RETENTION_DAYS": "30", "RETENTION_SOURCE": "default",
    })
    for platform, key in PLATFORM_ENV.items():
        env[key] = flow.get("build-" + platform.lower(), "false")
    env.update(extra_env)
    with tempfile.NamedTemporaryFile("w+", delete=False) as fh:
        path = fh.name
    env["GITHUB_OUTPUT"] = path
    try:
        proc = subprocess.run(["bash", matrix_script()], env=env, capture_output=True,
                              text=True, cwd=str(REPO_ROOT))
        if proc.returncode != 0:
            raise SystemExit(f"matrix failed: {proc.stderr}")
        raw = {}
        for line in Path(path).read_text().splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                raw[k] = v
    finally:
        os.unlink(path)
    return json.loads(raw["build-matrix"])


def capture():
    result = {}
    for name, extra in CASES.items():
        flow, platform_input = run_resolver(extra)
        rows = run_matrix(flow, platform_input,
                          {"RUNNER_LABELS_BY_PLATFORM": flow["runner-labels-by-platform"]})
        result[name] = {
            "env": extra,
            "build-engine": flow["build-engine"],
            "activation-strategy": flow["activation-strategy"],
            "runner-labels-linux": flow["runner-labels-linux"],
            "runner-labels-by-platform": flow["runner-labels-by-platform"],
            "rows": {r["platform"]: r["runner-labels"] for r in rows},
        }
    return result


if __name__ == "__main__":
    json.dump(capture(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
