"""
Per-platform Discord thread routing in .github/actions/discord-upload-build.

Runs the action's shell body with a fake `curl` on PATH that records every
request (URL + JSON body), so the tests see exactly which messages would be
posted to which thread. No network access.

Also covers scripts/common/resolve_discord_threads.py, which turns repository
variables into the action's `platform-thread-ids` / `thread-id` inputs.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
ACTION_FILE = REPO_ROOT / ".github" / "actions" / "discord-upload-build" / "action.yml"
RESOLVER = REPO_ROOT / "scripts" / "common" / "resolve_discord_threads.py"
PIPELINE = REPO_ROOT / ".github" / "workflows" / "unity-pipeline.yml"

ANDROID_THREAD = "111111111111111111"
IOS_THREAD = "222222222222222222"
DEFAULT_THREAD = "333333333333333333"

FAKE_CURL = """#!/usr/bin/env bash
n=$(find "${CURL_LOG_DIR}" -name "*.url" | wc -l | tr -d " ")
url="${@: -1}"
printf '%s' "${url}" > "${CURL_LOG_DIR}/${n}.url"
cat > "${CURL_LOG_DIR}/${n}.body"
printf '204'
"""


@pytest.fixture(scope="module")
def run_body():
    action = yaml.safe_load(ACTION_FILE.read_text(encoding="utf-8"))
    return action["runs"]["steps"][0]["run"]


def post(run_body, tmp_path, **inputs):
    """Run the action body; return [(thread_id or '', payload dict), ...]."""
    bindir = tmp_path / "bin"
    logdir = tmp_path / "calls"
    bindir.mkdir()
    logdir.mkdir()
    curl = bindir / "curl"
    curl.write_text(FAKE_CURL, encoding="utf-8")
    curl.chmod(0o755)
    script = tmp_path / "body.sh"
    script.write_text(run_body, encoding="utf-8")

    env = {
        "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path),
        "CURL_LOG_DIR": str(logdir),
        "DISCORD_WEBHOOK_URL": "https://discord.invalid/api/webhooks/1/token",
        "INPUT_STATUS": "success",
        "INPUT_ENVIRONMENT": "development",
        "INPUT_ARTIFACT_DIR": str(tmp_path / "artifacts"),
    }
    env.update({k: v for k, v in inputs.items()})
    subprocess.run(["bash", str(script)], env=env, stdin=subprocess.DEVNULL,
                   capture_output=True, text=True, timeout=60, check=True)

    calls = []
    for i in range(len(list(logdir.glob("*.url")))):
        url = (logdir / f"{i}.url").read_text(encoding="utf-8")
        thread = url.split("thread_id=")[1] if "thread_id=" in url else ""
        calls.append((thread, json.loads((logdir / f"{i}.body").read_text(encoding="utf-8"))))
    return calls


def text_of(payload):
    return json.dumps(payload, ensure_ascii=False)


class TestRouting:
    def test_no_routes_posts_one_message_to_thread_id(self, run_body, tmp_path):
        calls = post(run_body, tmp_path, INPUT_RESULT_ANDROID="success",
                     INPUT_RESULT_IOS="success", INPUT_THREAD_ID=DEFAULT_THREAD)
        assert [t for t, _ in calls] == [DEFAULT_THREAD]
        body = text_of(calls[0][1])
        assert "**Android**" in body and "**iOS**" in body

    def test_each_platform_posts_to_its_thread(self, run_body, tmp_path):
        calls = post(run_body, tmp_path, INPUT_RESULT_ANDROID="success",
                     INPUT_RESULT_IOS="success",
                     INPUT_PLATFORM_THREAD_IDS=f"Android={ANDROID_THREAD}\niOS={IOS_THREAD}")
        by_thread = {t: text_of(p) for t, p in calls}
        assert set(by_thread) == {ANDROID_THREAD, IOS_THREAD}
        assert "**Android**" in by_thread[ANDROID_THREAD]
        assert "**iOS**" not in by_thread[ANDROID_THREAD]
        assert "**iOS**" in by_thread[IOS_THREAD]
        assert "**Android**" not in by_thread[IOS_THREAD]

    def test_message_status_is_its_platforms_status(self, run_body, tmp_path):
        calls = post(run_body, tmp_path, INPUT_STATUS="failure",
                     INPUT_RESULT_ANDROID="success", INPUT_RESULT_IOS="failure",
                     INPUT_PLATFORM_THREAD_IDS=f"Android={ANDROID_THREAD}\niOS={IOS_THREAD}")
        titles = {t: p["embeds"][0]["title"] for t, p in calls}
        assert "Success" in titles[ANDROID_THREAD]
        assert "Failure" in titles[IOS_THREAD]

    def test_unrouted_platform_uses_default_thread(self, run_body, tmp_path):
        calls = post(run_body, tmp_path, INPUT_RESULT_ANDROID="success",
                     INPUT_RESULT_WEBGL="success", INPUT_THREAD_ID=DEFAULT_THREAD,
                     INPUT_PLATFORM_THREAD_IDS=f"Android={ANDROID_THREAD}")
        by_thread = {t: text_of(p) for t, p in calls}
        assert set(by_thread) == {ANDROID_THREAD, DEFAULT_THREAD}
        assert "**WebGL**" in by_thread[DEFAULT_THREAD]
        assert "**Android**" not in by_thread[DEFAULT_THREAD]

    def test_single_platform_goes_to_its_thread_with_one_message(self, run_body, tmp_path):
        calls = post(run_body, tmp_path, INPUT_RESULT_IOS="success",
                     INPUT_THREAD_ID=DEFAULT_THREAD,
                     INPUT_PLATFORM_THREAD_IDS=f"Android={ANDROID_THREAD}\niOS={IOS_THREAD}")
        assert [t for t, _ in calls] == [IOS_THREAD]

    def test_nothing_ran_posts_once_to_default(self, run_body, tmp_path):
        calls = post(run_body, tmp_path, INPUT_STATUS="failure",
                     INPUT_THREAD_ID=DEFAULT_THREAD,
                     INPUT_PLATFORM_THREAD_IDS=f"Android={ANDROID_THREAD}")
        assert [t for t, _ in calls] == [DEFAULT_THREAD]

    def test_invalid_snowflake_falls_back(self, run_body, tmp_path):
        calls = post(run_body, tmp_path, INPUT_RESULT_ANDROID="success",
                     INPUT_THREAD_ID=DEFAULT_THREAD,
                     INPUT_PLATFORM_THREAD_IDS="Android=not-a-thread")
        assert [t for t, _ in calls] == [DEFAULT_THREAD]

    def test_planned_platforms_reach_their_threads_when_nothing_built(self, run_body, tmp_path):
        # The quality gate stopped the run: no platform ran, but each thread
        # still has to hear why there is no build.
        calls = post(run_body, tmp_path, INPUT_STATUS="failure",
                     INPUT_FAILED_STAGE="Unity Tests",
                     INPUT_PLANNED_PLATFORMS="Android iOS",
                     INPUT_PLATFORM_THREAD_IDS=f"Android={ANDROID_THREAD}\niOS={IOS_THREAD}")
        by_thread = {t: p for t, p in calls}
        assert set(by_thread) == {ANDROID_THREAD, IOS_THREAD}
        assert all("Failure" in p["embeds"][0]["title"] for p in by_thread.values())
        assert "**iOS**" not in text_of(by_thread[ANDROID_THREAD])

    def test_unplanned_skipped_platform_is_not_routed(self, run_body, tmp_path):
        calls = post(run_body, tmp_path, INPUT_RESULT_ANDROID="success",
                     INPUT_PLANNED_PLATFORMS="Android", INPUT_THREAD_ID=DEFAULT_THREAD,
                     INPUT_PLATFORM_THREAD_IDS=f"Android={ANDROID_THREAD}\niOS={IOS_THREAD}")
        assert [t for t, _ in calls] == [ANDROID_THREAD]

    def test_addressables_listed_in_every_message(self, run_body, tmp_path):
        calls = post(run_body, tmp_path, INPUT_RESULT_ANDROID="success",
                     INPUT_RESULT_IOS="success", INPUT_RESULT_ADDRESSABLES="success",
                     INPUT_PLATFORM_THREAD_IDS=f"Android={ANDROID_THREAD}\niOS={IOS_THREAD}")
        assert len(calls) == 2
        assert all("**Addressables**" in text_of(p) for _, p in calls)


# ---------------------------------------------------------------------------
# Repository variables → action inputs
# ---------------------------------------------------------------------------

def _write_config(tmp_path, config):
    path = tmp_path / "discord.json"
    path.write_text(config if isinstance(config, str) else json.dumps(config), encoding="utf-8")
    return path


def resolve(environment, variables, config=None, tmp_path=None, with_stderr=False):
    proc = subprocess.run(
        [sys.executable, str(RESOLVER), "--environment", environment]
        + (["--config", str(_write_config(tmp_path, config))] if config is not None else []),
        input=json.dumps(variables), capture_output=True, text=True, check=True,
    )
    out = proc.stdout
    result = {}
    lines = out.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if "<<" in line and ("=" not in line or line.index("<<") < line.index("=")):
            key, _, delim = line.partition("<<")
            body = []
            i += 1
            while lines[i] != delim:
                body.append(lines[i])
                i += 1
            result[key] = "\n".join(body)
        else:
            key, _, value = line.partition("=")
            result[key] = value
        i += 1
    return (result, proc.stderr) if with_stderr else result


class TestResolver:
    def test_no_variables(self):
        out = resolve("development", {})
        assert out == {"thread-id": "", "platform-thread-ids": ""}

    def test_legacy_single_thread(self):
        out = resolve("development", {"DISCORD_THREAD_ID": DEFAULT_THREAD})
        assert out["thread-id"] == DEFAULT_THREAD
        assert out["platform-thread-ids"] == ""

    def test_platform_threads(self):
        out = resolve("staging", {"DISCORD_THREAD_ID_ANDROID": ANDROID_THREAD,
                                  "DISCORD_THREAD_ID_IOS": IOS_THREAD})
        assert out["platform-thread-ids"].splitlines() == [
            f"Android={ANDROID_THREAD}", f"iOS={IOS_THREAD}"]

    def test_environment_platform_wins(self):
        out = resolve("development", {
            "DISCORD_THREAD_ID_ANDROID": "999999999999999999",
            "DISCORD_THREAD_ID_DEVELOPMENT_ANDROID": ANDROID_THREAD})
        assert out["platform-thread-ids"] == f"Android={ANDROID_THREAD}"

    def test_environment_platform_ignored_for_other_environment(self):
        out = resolve("production", {"DISCORD_THREAD_ID_DEVELOPMENT_ANDROID": ANDROID_THREAD})
        assert out["platform-thread-ids"] == ""

    def test_environment_default_wins_over_global(self):
        out = resolve("production", {"DISCORD_THREAD_ID": DEFAULT_THREAD,
                                     "DISCORD_THREAD_ID_PRODUCTION": IOS_THREAD})
        assert out["thread-id"] == IOS_THREAD

    def test_all_platform_keys(self):
        names = {"ANDROID": "Android", "IOS": "iOS", "WEBGL": "WebGL",
                 "LINUX64": "Linux64", "LINUXSERVER": "LinuxServer",
                 "WINDOWS64": "Windows64"}
        variables = {f"DISCORD_THREAD_ID_{k}": str(10 ** 17 + i)
                     for i, k in enumerate(names)}
        routed = dict(line.split("=") for line in
                      resolve("development", variables)["platform-thread-ids"].splitlines())
        assert set(routed) == set(names.values())


class TestConfigFile:
    CONFIG = {"threads": {
        "development": {"Android": ANDROID_THREAD, "iOS": IOS_THREAD},
        "production": {"default": DEFAULT_THREAD},
        "*": {"default": "444444444444444444"},
    }}

    def test_file_routes_platforms(self, tmp_path):
        out = resolve("development", {}, self.CONFIG, tmp_path)
        assert out["platform-thread-ids"].splitlines() == [
            f"Android={ANDROID_THREAD}", f"iOS={IOS_THREAD}"]
        assert out["thread-id"] == "444444444444444444", "'*' default applies"

    def test_environment_default(self, tmp_path):
        out = resolve("production", {}, self.CONFIG, tmp_path)
        assert out["thread-id"] == DEFAULT_THREAD
        assert out["platform-thread-ids"] == ""

    def test_keys_are_case_insensitive(self, tmp_path):
        config = {"threads": {"Development": {"android": ANDROID_THREAD, "IOS": IOS_THREAD}}}
        out = resolve("development", {}, config, tmp_path)
        assert out["platform-thread-ids"].splitlines() == [
            f"Android={ANDROID_THREAD}", f"iOS={IOS_THREAD}"]

    def test_star_platform_beats_environment_default(self, tmp_path):
        config = {"threads": {"*": {"iOS": IOS_THREAD}, "staging": {"default": DEFAULT_THREAD}}}
        out = resolve("staging", {}, config, tmp_path)
        assert out["platform-thread-ids"] == f"iOS={IOS_THREAD}"
        assert out["thread-id"] == DEFAULT_THREAD

    def test_variables_override_the_file(self, tmp_path):
        out = resolve("development",
                      {"DISCORD_THREAD_ID_IOS": "555555555555555555",
                       "DISCORD_THREAD_ID": DEFAULT_THREAD},
                      self.CONFIG, tmp_path)
        assert "iOS=555555555555555555" in out["platform-thread-ids"].splitlines()
        assert out["thread-id"] == DEFAULT_THREAD

    def test_missing_file_is_no_config(self, tmp_path):
        out = subprocess.run(
            [sys.executable, str(RESOLVER), "--environment", "development",
             "--config", str(tmp_path / "absent.json")],
            input="{}", capture_output=True, text=True, check=True).stdout
        assert "thread-id=\n" in out + "\n"

    @pytest.mark.parametrize("config,needle", [
        ("{not json", "not valid JSON"),
        ({"threads": {"dev": {"Android": ANDROID_THREAD}}}, "unknown environment 'dev'"),
        ({"threads": {"development": {"Switch": ANDROID_THREAD}}}, "unknown platform 'Switch'"),
        ({"threads": {"development": {"Android": "123"}}}, "is not a Discord thread ID"),
    ])
    def test_problems_are_annotated_not_fatal(self, tmp_path, config, needle):
        out, err = resolve("development", {}, config, tmp_path, with_stderr=True)
        assert "::error::Discord config:" in err and needle in err
        assert out["platform-thread-ids"] == ""

    def test_bad_entry_does_not_drop_good_ones(self, tmp_path):
        config = {"threads": {"development": {"Android": ANDROID_THREAD, "iOS": "oops"}}}
        out = resolve("development", {}, config, tmp_path)
        assert out["platform-thread-ids"] == f"Android={ANDROID_THREAD}"


def test_pipeline_resolves_threads_in_resolve_config():
    wf = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))
    resolve_job = wf["jobs"]["resolve-config"]
    step = next(s for s in resolve_job["steps"] if s.get("id") == "discord")
    assert step["env"]["VARS_JSON"] == "${{ toJSON(vars) }}"
    assert "--config" in step["run"]
    assert step.get("continue-on-error") is True, "a notification setting never fails a build"
    checkout = resolve_job["steps"][0]["with"]["sparse-checkout"]
    assert "${{ vars.DISCORD_CONFIG_FILE || '.github/discord.json' }}" in checkout
    assert resolve_job["outputs"]["discord-platform-thread-ids"] == (
        "${{ steps.discord.outputs.platform-thread-ids }}")

    post = next(s for s in wf["jobs"]["notify-discord"]["steps"]
                if s.get("name") == "Post build to Discord thread")
    assert post["with"]["thread-id"] == (
        "${{ needs.resolve-config.outputs.discord-thread-id || vars.DISCORD_THREAD_ID }}")
    assert post["with"]["platform-thread-ids"] == (
        "${{ needs.resolve-config.outputs.discord-platform-thread-ids }}")


def test_pipeline_passes_planned_platforms():
    wf = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))
    assert wf["jobs"]["resolve-config"]["outputs"]["planned-platforms"] == (
        "${{ steps.matrix.outputs.planned-platforms }}")
    post = next(s for s in wf["jobs"]["notify-discord"]["steps"]
                if s.get("name") == "Post build to Discord thread")
    assert post["with"]["planned-platforms"] == "${{ needs.resolve-config.outputs.planned-platforms }}"
