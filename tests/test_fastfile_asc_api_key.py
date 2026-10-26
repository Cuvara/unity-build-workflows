"""Fastfile: App Store Connect API key -- team or individual.

A team key (Admin-created, every app of the team) has an issuer ID. An
individual key (a user's own key, scoped to that user's apps and role, which a
user added to one app can create without an Admin) has none, and fastlane
expects `issuer_id: nil` for it. An unset GitHub secret arrives as "", which
Ruby treats as present, so `configure_asc_api_key` turns a blank issuer into
nil. docs/IOS_SIGNING.md § Step 3.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
FASTFILE = REPO_ROOT / "fastlane" / "Fastfile"
ACTIONS = REPO_ROOT / ".github" / "actions"


def _key_lane():
    src = FASTFILE.read_text(encoding="utf-8")
    start = src.index("private_lane :configure_asc_api_key do")
    return src[start:src.index("\n  end", start)]


def test_the_key_lane_passes_a_normalized_issuer():
    lane = _key_lane()
    assert 'issuer = ENV["APP_STORE_CONNECT_ISSUER_ID"].to_s.strip' in lane
    assert "issuer = nil if issuer.empty?" in lane
    assert re.search(r"issuer_id:\s+issuer,", lane)
    assert 'ENV["APP_STORE_CONNECT_ISSUER_ID"],' not in lane, "the raw env value reaches fastlane"
    assert lane.index("issuer = nil if issuer.empty?") < lane.index("app_store_connect_api_key(")


def test_every_store_lane_goes_through_the_key_lane():
    src = FASTFILE.read_text(encoding="utf-8")
    assert src.count("app_store_connect_api_key(") == 1, "configure the key in one place"


@pytest.mark.skipif(shutil.which("ruby") is None, reason="ruby not installed")
@pytest.mark.parametrize("value, expected", [
    ("", "nil"), ("  ", "nil"), (None, "nil"),
    ("69a6de8f-1234-47e3-e053-5b8c7c11a4d1", '"69a6de8f-1234-47e3-e053-5b8c7c11a4d1"'),
])
def test_the_normalization_in_ruby(value, expected):
    lines = [l.strip() for l in _key_lane().splitlines() if l.strip().startswith("issuer =")]
    script = "\n".join(lines + ["puts issuer.inspect"])
    env = {"PATH": "/usr/bin:/bin"}
    if value is not None:
        env["APP_STORE_CONNECT_ISSUER_ID"] = value
    r = subprocess.run(["ruby", "-e", script], capture_output=True, text=True, env=env, timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == expected


@pytest.mark.parametrize("action, name", [
    ("ios-testflight", "app-store-connect-issuer-id"),
    ("ios-asc-submit-review", "asc-issuer-id"),
])
def test_the_issuer_input_is_optional(action, name):
    spec = yaml.safe_load((ACTIONS / action / "action.yml").read_text(encoding="utf-8"))["inputs"][name]
    assert spec["required"] is False
    assert spec["default"] == ""
    assert "individual" in spec["description"]
