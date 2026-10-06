"""Firebase delivery on a Windows runner: fastlane runs under a native
Windows Ruby, which cannot open the Git Bash path mktemp returns (/tmp/...).
Seen on a self-hosted Windows runner: "Service credentials file does not exist"."""
from pathlib import Path

import yaml

REUSABLE = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "reusable-build-platform.yml"


def test_service_account_path_is_converted_for_windows_ruby():
    steps = yaml.safe_load(REUSABLE.read_text(encoding="utf-8"))["jobs"]["build"]["steps"]
    run = next(s for s in steps if s.get("name") == "Publish the build for download")["run"]
    assert 'SA_PATH="$(cygpath -w "${SA_FILE}")"' in run
    assert 'service_account_path:"${SA_PATH}"' in run
    assert 'service_account_path:"${SA_FILE}"' not in run


# ── Two links for a Firebase build: tester page + 1-hour direct download ────

FASTFILE = Path(__file__).resolve().parent.parent / "fastlane" / "Fastfile"
PIPELINE = REUSABLE.parent / "unity-pipeline.yml"
DISCORD = REUSABLE.parent.parent / "actions" / "discord-upload-build" / "action.yml"


def test_both_firebase_lanes_record_the_release_links():
    text = FASTFILE.read_text(encoding="utf-8")
    assert text.count("release = firebase_app_distribution(") == 2, "Android and iOS lanes"
    assert text.count("write_firebase_release_info(release, options[:release_info])") == 2
    for key in ('"testingUri"', '"binaryDownloadUri"'):
        assert key in text


def test_publish_step_outputs_tester_and_direct_links():
    steps = yaml.safe_load(REUSABLE.read_text(encoding="utf-8"))["jobs"]["build"]["steps"]
    run = next(s for s in steps if s.get("name") == "Publish the build for download")["run"]
    assert 'release_info:"${RI_PATH}"' in run
    assert "read_release testingUri" in run and "read_release binaryDownloadUri" in run
    assert 'echo "download-url=${TESTER_URL}"' in run
    assert 'echo "direct-url=${DIRECT_URL}"' in run


def test_result_file_and_discord_carry_the_direct_link():
    reusable = REUSABLE.read_text(encoding="utf-8")
    assert '"directDownloadUrl":"%s"' in reusable
    assert "${{ steps.publish.outputs.direct-url }}" in reusable
    assert 'd.get("directDownloadUrl"' in PIPELINE.read_text(encoding="utf-8")
    discord = DISCORD.read_text(encoding="utf-8")
    assert "[⬇️ testers](${BIN_URL}) · [⬇️ direct, 1 h](${DIRECT_URL})" in discord


def test_an_unreported_result_is_explained_not_a_question_mark():
    discord = DISCORD.read_text(encoding="utf-8")
    assert 'unreported) RE="➖"' in discord
    assert "result not reported" in discord
