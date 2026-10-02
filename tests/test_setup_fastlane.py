"""
.github/actions/setup-fastlane on self-hosted runners.

ruby/setup-ruby installs into the hosted tool cache (/Users/runner/... on
macOS), which a self-hosted runner account cannot create; on a consumer's Mac
the step failed with "EACCES: permission denied, mkdir '/Users/runner'" and
took a successful Android build down with it.
"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent
ACTION = REPO_ROOT / ".github" / "actions" / "setup-fastlane" / "action.yml"
BUILD_PLATFORM = REPO_ROOT / ".github" / "workflows" / "reusable-build-platform.yml"


def _steps():
    return yaml.safe_load(ACTION.read_text(encoding="utf-8"))["runs"]["steps"]


def test_setup_ruby_only_on_github_hosted_runners():
    ruby = next(s for s in _steps() if str(s.get("uses", "")).startswith("ruby/setup-ruby"))
    assert ruby["if"] == "${{ runner.environment != 'self-hosted' }}"


def test_self_hosted_runners_use_their_own_ruby():
    own = next(s for s in _steps() if s.get("name") == "Install gems (self-hosted)")
    assert "bundle install" in own["run"]
    assert "RUNNER_TOOL_CACHE" in own["run"], "gems go to a per-runner cache, no sudo"
    find = next(s for s in _steps() if s.get("id") == "ruby")
    assert "ensure_ruby.sh" in find["run"]
    assert find["if"] == "${{ runner.environment == 'self-hosted' }}"


def test_delivery_setup_never_fails_the_build():
    steps = yaml.safe_load(BUILD_PLATFORM.read_text(encoding="utf-8"))["jobs"]["build"]["steps"]
    step = next(s for s in steps if s.get("name") == "Setup Fastlane (delivery)")
    assert step.get("continue-on-error") is True
