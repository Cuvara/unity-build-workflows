#!/usr/bin/env python3
"""
update_rollout.py
Update the rollout fraction for an existing staged rollout on Google Play,
or halt a bad rollout entirely.

Requires: google-api-python-client, google-auth
"""

import argparse
import json
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update Google Play staged rollout fraction"
    )
    parser.add_argument("--package-name", required=True, help="Android package name")
    parser.add_argument(
        "--track",
        default="production",
        help="Track to update (default: production)",
    )
    parser.add_argument(
        "--rollout-fraction",
        type=float,
        default=None,
        help="New rollout fraction 0.0-1.0 (use 1.0 to complete rollout)",
    )
    parser.add_argument(
        "--halt",
        action="store_true",
        help="Halt the rollout (sets status to halted)",
    )
    args = parser.parse_args()

    if not args.halt and args.rollout_fraction is None:
        print("ERROR: Either --rollout-fraction or --halt is required", file=sys.stderr)
        sys.exit(1)

    service_account_json = os.environ.get("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON", "")
    if not service_account_json:
        print(
            "ERROR: GOOGLE_PLAY_SERVICE_ACCOUNT_JSON environment variable is required",
            file=sys.stderr,
        )
        sys.exit(1)

    # Reuse the service builder from promote script
    from promote_google_play import get_play_service

    service = get_play_service(service_account_json)

    edit = service.edits().insert(packageName=args.package_name).execute()
    edit_id = edit["id"]
    print(f"[update_rollout] Created edit: {edit_id}")

    # Read current track state
    current = (
        service.edits()
        .tracks()
        .get(packageName=args.package_name, editId=edit_id, track=args.track)
        .execute()
    )
    releases = current.get("releases", [])
    if not releases:
        print(f"ERROR: No releases found on track '{args.track}'", file=sys.stderr)
        service.edits().delete(packageName=args.package_name, editId=edit_id).execute()
        sys.exit(1)

    latest = releases[0]
    current_status = latest.get("status", "unknown")
    print(f"[update_rollout] Current status: {current_status}")

    if args.halt:
        latest["status"] = "halted"
        latest.pop("userFraction", None)
        print("[update_rollout] Halting rollout")
    elif args.rollout_fraction >= 1.0:
        latest["status"] = "completed"
        latest.pop("userFraction", None)
        print("[update_rollout] Completing rollout (100%)")
    else:
        latest["status"] = "inProgress"
        latest["userFraction"] = args.rollout_fraction
        print(f"[update_rollout] Updating rollout to {args.rollout_fraction * 100:.0f}%")

    track_body = {
        "track": args.track,
        "releases": [latest],
    }
    service.edits().tracks().update(
        packageName=args.package_name,
        editId=edit_id,
        track=args.track,
        body=track_body,
    ).execute()

    commit = service.edits().commit(packageName=args.package_name, editId=edit_id).execute()
    print(f"[update_rollout] Edit committed: {commit}")


if __name__ == "__main__":
    main()
