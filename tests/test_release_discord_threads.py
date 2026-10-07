"""Store-release pipelines post their report into the project's production
Discord thread for their platform (.github/discord.json), like Build / Release."""
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
ACTION = REPO_ROOT / ".github" / "actions" / "release-report" / "action.yml"


PROMOTIONS = sorted(p.name for p in WORKFLOWS.glob("pipeline-*-release.yml"))


@pytest.mark.parametrize("name", PROMOTIONS)
def test_every_release_report_resolves_its_production_thread(name):
    """The resolution lives in release-report, so every store pipeline --
    WebGL and desktop too, not only the two that had copied it -- posts to
    its platform's thread."""
    steps = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))["jobs"]["report"]["steps"]
    report = next(s for s in steps if s.get("name") == "Report")
    assert report["with"]["discord-vars-json"] == "${{ toJSON(vars) }}"
    assert "vars.DISCORD_CONFIG_FILE" in report["with"]["discord-config-file"]
    assert "discord-thread-id" not in report["with"], "resolved inside the action"
    assert not any(s.get("id") == "discord" for s in steps), "no copy of the resolver left"


def test_the_action_resolves_the_production_thread_for_its_platform():
    steps = yaml.safe_load(ACTION.read_text(encoding="utf-8"))["runs"]["steps"]
    by_id = {s.get("id"): s for s in steps}
    resolve = by_id["discord"]
    assert resolve["env"]["PLATFORM"] == "${{ inputs.platform }}"
    assert "resolve_discord_threads.py" in resolve["run"]
    assert "--environment production" in resolve["run"]
    assert resolve.get("continue-on-error") is True, "a notification setting never fails a release"
    checkout = next(s for s in steps if s.get("name") == "Checkout Discord config")
    assert checkout.get("continue-on-error") is True
    # An explicit thread wins; otherwise the resolved one.
    notify = next(s for s in steps if s.get("name") == "Notify Discord")
    assert notify["env"]["THREAD_ID"] == "${{ inputs.discord-thread-id || steps.discord.outputs.thread-id }}"
    names = [s.get("name") for s in steps]
    assert names.index("Checkout Discord config") < names.index("Resolve Discord thread") < names.index("Notify Discord")


def test_release_report_posts_into_the_thread():
    action = yaml.safe_load(ACTION.read_text(encoding="utf-8"))
    assert action["inputs"]["discord-thread-id"]["default"] == ""
    text = ACTION.read_text(encoding="utf-8")
    assert '"thread_id=" + thread' in text


def test_release_report_sends_a_user_agent_discord_accepts():
    """urllib's default User-Agent gets 403 Forbidden from Discord (seen on the
    first Release / Android run); the build pipeline uses curl and never hit it."""
    text = ACTION.read_text(encoding="utf-8")
    assert '"User-Agent": "DiscordBot (' in text
