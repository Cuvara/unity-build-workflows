#!/usr/bin/env python3
"""
upload_google_play.py
Upload Android AAB/APK to Google Play using the Google Play Developer API.
Requires: google-api-python-client, google-auth
"""

import argparse
import json
import os
import sys
from pathlib import Path


def find_build_artifact(build_path: Path) -> Path | None:
    """Find the AAB or APK to upload (prefer AAB)."""
    for pattern in ("*.aab", "*-signed.apk", "*.apk"):
        matches = list(build_path.glob(pattern))
        if matches:
            # Take the largest file (most likely the release artifact)
            return max(matches, key=lambda f: f.stat().st_size)
    return None


def upload_to_play(
    artifact_path: Path,
    service_account_json: str,
    package_name: str,
    track: str,
    version_code: int | None,
    rollout_fraction: float = 1.0,
    release_notes_file: str | None = None,
) -> dict:
    """Upload artifact to Google Play. Returns upload response."""
    try:
        from google.oauth2 import service_account  # type: ignore
        from googleapiclient.discovery import build as google_build  # type: ignore
        from googleapiclient.http import MediaFileUpload  # type: ignore
    except ImportError:
        print(
            "ERROR: Google API client not installed. Run: pip install google-api-python-client google-auth",
            file=sys.stderr,
        )
        sys.exit(1)

    # Parse service account
    try:
        sa_info = json.loads(service_account_json)
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid service account JSON: {e}", file=sys.stderr)
        sys.exit(1)

    credentials = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=["https://www.googleapis.com/auth/androidpublisher"],
    )

    service = google_build("androidpublisher", "v3", credentials=credentials)

    # Create edit
    edit = service.edits().insert(packageName=package_name).execute()
    edit_id = edit["id"]
    print(f"[upload_google_play] Created edit: {edit_id}")

    # Upload
    mime_type = "application/octet-stream"
    if artifact_path.suffix == ".aab":
        mime_type = "application/octet-stream"
        upload_fn = service.edits().bundles().upload
        upload_kwargs = dict(
            packageName=package_name,
            editId=edit_id,
            media_body=MediaFileUpload(str(artifact_path), mimetype=mime_type),
        )
    else:
        upload_fn = service.edits().apks().upload
        upload_kwargs = dict(
            packageName=package_name,
            editId=edit_id,
            media_body=MediaFileUpload(str(artifact_path), mimetype=mime_type),
        )

    print(f"[upload_google_play] Uploading {artifact_path.name} ({artifact_path.stat().st_size // 1024} KB)...")
    upload_response = upload_fn(**upload_kwargs).execute()
    print(f"[upload_google_play] Upload response: {upload_response}")

    vcode = version_code or upload_response.get("versionCode", 0)

    # Build release object
    release = {
        "versionCodes": [str(vcode)],
    }
    if 0.0 < rollout_fraction < 1.0:
        release["status"] = "inProgress"
        release["userFraction"] = rollout_fraction
        print(f"[upload_google_play] Staged rollout: {rollout_fraction * 100:.0f}%")
    else:
        release["status"] = "completed"

    # Attach release notes if provided
    if release_notes_file:
        notes_path = Path(release_notes_file)
        if notes_path.is_file():
            with open(notes_path) as nf:
                notes_data = json.load(nf)
            # Format: {"en-US": "text", "vi": "text"} or [{"language": "en-US", "text": "..."}]
            if isinstance(notes_data, dict):
                release["releaseNotes"] = [
                    {"language": lang, "text": text}
                    for lang, text in notes_data.items()
                ]
            elif isinstance(notes_data, list):
                release["releaseNotes"] = notes_data
            print(f"[upload_google_play] Release notes loaded from {notes_path.name}")

    # Assign to track
    track_body = {
        "track": track,
        "releases": [release],
    }
    service.edits().tracks().update(
        packageName=package_name,
        editId=edit_id,
        track=track,
        body=track_body,
    ).execute()
    print(f"[upload_google_play] Assigned versionCode {vcode} to track '{track}'")

    # Commit edit
    commit_response = service.edits().commit(packageName=package_name, editId=edit_id).execute()
    print(f"[upload_google_play] Edit committed: {commit_response}")

    return commit_response


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload build to Google Play")
    parser.add_argument("--build-path", required=True, help="Directory containing build artifact")
    parser.add_argument("--environment", default="staging", help="Build environment")
    parser.add_argument("--version", required=True, help="Build version string")
    parser.add_argument("--track", default="internal", help="Google Play track (internal/alpha/beta/production)")
    parser.add_argument("--package-name", default="", help="Android package name (overrides config)")
    parser.add_argument(
        "--rollout-fraction",
        type=float,
        default=1.0,
        help="Rollout fraction 0.0-1.0 (default: 1.0 = full rollout)",
    )
    parser.add_argument(
        "--release-notes-file",
        default="",
        help="Path to JSON release notes file ({lang: text} or [{language, text}])",
    )
    args = parser.parse_args()

    service_account_json = os.environ.get("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON", "")
    if not service_account_json:
        print("ERROR: GOOGLE_PLAY_SERVICE_ACCOUNT_JSON environment variable is required", file=sys.stderr)
        sys.exit(1)

    build_path = Path(args.build_path)
    artifact = find_build_artifact(build_path)
    if not artifact:
        print(f"ERROR: No .aab or .apk found in {build_path}", file=sys.stderr)
        sys.exit(1)

    print(f"[upload_google_play] Found artifact: {artifact.name}")

    # Determine track from environment
    track = args.track
    if not track or track == "auto":
        track_map = {
            "development": "internal",
            "staging": "alpha",
            "production": "production",
        }
        track = track_map.get(args.environment, "internal")

    package_name = args.package_name
    if not package_name:
        # Try to read from build path metadata
        meta_file = build_path / "build-metadata.json"
        if meta_file.is_file():
            with open(meta_file) as f:
                meta = json.load(f)
            package_name = meta.get("android", {}).get("package_name", "")
        if not package_name:
            print("ERROR: --package-name is required when build-metadata.json is absent", file=sys.stderr)
            sys.exit(1)

    upload_to_play(
        artifact_path=artifact,
        service_account_json=service_account_json,
        package_name=package_name,
        track=track,
        version_code=None,
        rollout_fraction=args.rollout_fraction,
        release_notes_file=args.release_notes_file or None,
    )
    print(f"[upload_google_play] Successfully uploaded v{args.version} to {track}")


if __name__ == "__main__":
    main()
