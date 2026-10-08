"""Fastfile: the Google Play lanes pass supply only options it has.

`upload_internal` passed `release_notes:` to upload_to_play_store, which has
no such option, so every direct-to-store upload (ARTIFACT_STORAGE=firebase,
a Production build) failed with "Could not find option 'release_notes' in
the list of available options" -- hidden by the step's continue-on-error,
the build stayed green and nothing reached the internal track. Supply reads
release notes from <metadata_path>/<locale>/changelogs/default.txt.
"""
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
FASTFILE = REPO_ROOT / "fastlane" / "Fastfile"

# fastlane supply (upload_to_play_store) options this repository may use.
SUPPLY_OPTIONS = {
    "package_name", "version_name", "version_code", "release_status", "track",
    "rollout", "metadata_path", "key", "issuer", "json_key", "json_key_data",
    "apk", "apk_paths", "aab", "aab_paths", "skip_upload_apk", "skip_upload_aab",
    "skip_upload_metadata", "skip_upload_changelogs", "skip_upload_images",
    "skip_upload_screenshots", "track_promote_to", "track_promote_release_status",
    "validate_only", "mapping", "mapping_paths", "timeout", "changes_not_sent_for_review",
    "in_app_update_priority", "version_codes_to_retain",
}


def _supply_calls():
    text = FASTFILE.read_text(encoding="utf-8")
    calls = []
    for m in re.finditer(r"upload_to_play_store\(\s*\n(.*?)\n\s*\)", text, re.S):
        keys = re.findall(r"^\s*([a-z_]+):", m.group(1), re.M)
        calls.append(keys)
    return calls


def test_every_supply_call_uses_real_options():
    calls = _supply_calls()
    assert len(calls) >= 4, "upload_internal, promote_track, update_rollout, upload_metadata"
    for keys in calls:
        unknown = set(keys) - SUPPLY_OPTIONS
        assert not unknown, f"upload_to_play_store has no option(s) {sorted(unknown)}"


def test_upload_internal_writes_notes_where_supply_reads_them():
    text = FASTFILE.read_text(encoding="utf-8")
    lane = text[text.index("lane :upload_internal"):text.index("lane :promote_track")]
    assert 'File.join(metadata_dir, locale, "changelogs")' in lane
    assert '"default.txt"' in lane
    assert "metadata_path:         metadata_dir" in lane
    # No locale, no notes: the upload must not depend on a listing language.
    assert "skip_upload_changelogs: metadata_dir.nil?" in lane
    assert 'ENV["GOOGLE_PLAY_RELEASE_NOTES_LOCALE"]' in lane
    assert "FileUtils.rm_rf(metadata_dir)" in lane


def test_the_build_job_hands_the_locale_to_delivery():
    rbp = yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "reusable-build-platform.yml")
                         .read_text(encoding="utf-8"))
    step = next(s for s in rbp["jobs"]["build"]["steps"] if s.get("id") == "publish")
    assert step["env"]["GOOGLE_PLAY_RELEASE_NOTES_LOCALE"] == "${{ vars.GOOGLE_PLAY_RELEASE_NOTES_LOCALE }}"
