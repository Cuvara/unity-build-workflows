#!/usr/bin/env python3
"""
promote_google_play.py
Promote an existing build from one Google Play track to another without re-uploading.
Uses Google Play Developer API v3 edits.tracks.

Requires: google-api-python-client, google-auth
"""

import argparse
import json
import os
import sys
from pathlib import Path


def get_play_service(service_account_json: str):
    """Build authenticated Google Play API service."""
    try:
        from google.oauth2 import service_account  # type: ignore
        from googleapiclient.discovery import build as google_build  # type: ignore
    except ImportError:
        print(
            "ERROR: Google API client not installed. Run: pip install google-api-python-client google-auth",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        sa_info = json.loads(service_account_json)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid service account JSON: {e}", file=sys.stderr)
        sys.exit(1)

    credentials = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/androidpublisher"],
    )
    return google_build("androidpublisher", "v3", credentials=credentials)


def promote_track(
    service,
    package_name: str,
    from_track: str,
    to_track: str,
    rollout_fraction: float = 1.0,
    release_notes_file: str | None = None,
) -> dict:
    """Read version codes from source track, assign to destination track."""
    edit = service.edits().insert(packageName=package_name).execute()
    edit_id = edit["id"]
    print(f"[promote] Created edit: {edit_id}")

    # Read source track
    source = (
        service.edits()
        .tracks()
        .get(packageName=package_name, editId=edit_id, track=from_track)
        .execute()
    )
    releases = source.get("releases", [])
    if not releases:
        print(f"ERROR: No releases found on track '{from_track}'", file=sys.stderr)
        service.edits().delete(packageName=package_name, editId=edit_id).execute()
        sys.exit(1)

    # Use the latest release's version codes
    latest = releases[0]
    version_codes = latest.get("versionCodes", [])
    if not version_codes:
        print(f"ERROR: No version codes in latest release on '{from_track}'", file=sys.stderr)
        service.edits().delete(packageName=package_name, editId=edit_id).execute()
        sys.exit(1)

    print(f"[promote] Found versionCodes {version_codes} on '{from_track}'")

    # Build destination release
    release = {
        "versionCodes": version_codes,
    }
    if 0.0 < rollout_fraction < 1.0:
        release["status"] = "inProgress"
        release["userFraction"] = rollout_fraction
        print(f"[promote] Staged rollout: {rollout_fraction * 100:.0f}%")
    else:
        release["status"] = "completed"

    # Attach release notes
    if release_notes_file:
        notes_path = Path(release_notes_file)
        if notes_path.is_file():
            with open(notes_path) as nf:
                notes_data = json.load(nf)
            if isinstance(notes_data, dict):
                release["releaseNotes"] = [
                    {"language": lang, "text": text}
                    for lang, text in notes_data.items()
                ]
            elif isinstance(notes_data, list):
                release["releaseNotes"] = notes_data
            print(f"[promote] Release notes loaded from {notes_path.name}")

    track_body = {
        "track": to_track,
        "releases": [release],
    }
    service.edits().tracks().update(
        packageName=package_name,
        editId=edit_id,
        track=to_track,
        body=track_body,
    ).execute()
    print(f"[promote] Assigned versionCodes {version_codes} to track '{to_track}'")

    commit = service.edits().commit(packageName=package_name, editId=edit_id).execute()
    print(f"[promote] Edit committed: {commit}")
    return commit


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Promote a Google Play build from one track to another"
    )
    parser.add_argument("--package-name", required=True, help="Android package name")
    parser.add_argument(
        "--from-track",
        required=True,
        help="Source track (internal/alpha/beta/production)",
    )
    parser.add_argument(
        "--to-track",
        required=True,
        help="Destination track (internal/alpha/beta/production)",
    )
    parser.add_argument(
        "--rollout-fraction",
        type=float,
        default=1.0,
        help="Rollout fraction 0.0-1.0 (default: 1.0 = full rollout)",
    )
    parser.add_argument(
        "--release-notes-file",
        default="",
        help="Path to JSON release notes file",
    )
    args = parser.parse_args()

    service_account_json = os.environ.get("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON", "")
    if not service_account_json:
        print(
            "ERROR: GOOGLE_PLAY_SERVICE_ACCOUNT_JSON environment variable is required",
            file=sys.stderr,
        )
        sys.exit(1)

    service = get_play_service(service_account_json)

    promote_track(
        service=service,
        package_name=args.package_name,
        from_track=args.from_track,
        to_track=args.to_track,
        rollout_fraction=args.rollout_fraction,
        release_notes_file=args.release_notes_file or None,
    )
    print(f"[promote] Successfully promoted '{args.from_track}' → '{args.to_track}'")


if __name__ == "__main__":
    main()
