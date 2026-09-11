#!/usr/bin/env python3
"""
check_build_number.py
Refuse to upload an Android artifact whose versionCode the store has already
seen, or has moved past.

Google Play rejects a duplicate versionCode, but it does so *after* the upload
and after the edit has been opened — the failure is late, noisy, and easy to
misread as a credentials problem. Worse, a versionCode that is merely lower
than the current one uploads fine and then cannot be promoted, which surfaces
days later during a rollout.

This asks the store what it already has and compares before anything is sent.

    store max = 1041, release = 1042  -> PASS
    store max = 1041, release = 1041  -> FAIL (duplicate)
    store max = 1041, release = 1039  -> FAIL (goes backwards)

Fails closed on an API error: if the store cannot be reached, whether the
number is safe is unknown, and an unknown answer must not be treated as yes.
`--allow-unverified` exists for a first upload to a package that does not exist
in the console yet, and says so in the log.

Exit 0 = safe to upload, 1 = not safe, 2 = usage error.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Every track a versionCode could already be sitting on. A code used on
# internal still cannot be reused for production.
TRACKS = ("internal", "alpha", "beta", "production")


def load_credentials(raw_json):
    from google.oauth2 import service_account  # type: ignore

    try:
        info = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"service account JSON is not valid JSON: {exc}") from exc
    return service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/androidpublisher"]
    )


def highest_version_code(service, package_name):
    """Highest versionCode across every track, or None when the app has none."""
    edit = service.edits().insert(packageName=package_name).execute()
    edit_id = edit["id"]
    highest = None
    try:
        for track in TRACKS:
            try:
                info = service.edits().tracks().get(
                    packageName=package_name, editId=edit_id, track=track
                ).execute()
            except Exception as exc:  # noqa: BLE001 — a missing track is normal
                if "404" in str(exc) or "not found" in str(exc).lower():
                    continue
                raise
            for release in info.get("releases", []) or []:
                for code in release.get("versionCodes", []) or []:
                    try:
                        value = int(code)
                    except (TypeError, ValueError):
                        continue
                    if highest is None or value > highest:
                        highest = value
    finally:
        # Read-only: never commit. Leaving the edit open would block later ones.
        try:
            service.edits().delete(packageName=package_name, editId=edit_id).execute()
        except Exception:  # noqa: BLE001 — cleanup must not mask the real result
            pass
    return highest


def summarise(lines):
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return
    with open(target, "a") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Guard against a reused Android versionCode")
    parser.add_argument("--package-name", required=True)
    parser.add_argument("--build-number", required=True,
                        help="versionCode this release intends to upload")
    parser.add_argument("--service-account-json", default="",
                        help="Defaults to GOOGLE_PLAY_SERVICE_ACCOUNT_JSON")
    parser.add_argument("--allow-unverified", action="store_true",
                        help="Proceed when the store cannot be queried (first upload)")
    args = parser.parse_args(argv)

    try:
        candidate = int(args.build_number)
    except (TypeError, ValueError):
        print(f"::error::build-number '{args.build_number}' is not an integer", file=sys.stderr)
        return 2
    if candidate <= 0:
        print(f"::error::build-number {candidate} must be positive", file=sys.stderr)
        return 2

    # Strip first: an unset secret interpolates as an empty string, and a
    # placeholder secret is usually a stray newline. Both reached the JSON
    # parser and came back as "service account JSON is not valid JSON", which
    # reads like a corrupt key rather than a missing one.
    raw = (args.service_account_json or
           os.environ.get("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON", "")).strip()
    if not raw:
        message = "no Google Play service account provided, so the versionCode could not be checked"
        if args.allow_unverified:
            print(f"::warning::{message} — proceeding because --allow-unverified was set")
            summarise(["### Android build number", "",
                       f"⚠️ `{candidate}` **unverified** — {message}."])
            return 0
        print(f"::error::{message}", file=sys.stderr)
        return 1

    try:
        from googleapiclient.discovery import build as google_build  # type: ignore

        service = google_build("androidpublisher", "v3",
                               credentials=load_credentials(raw), cache_discovery=False)
        highest = highest_version_code(service, args.package_name)
    except Exception as exc:  # noqa: BLE001 — any failure means "unknown"
        message = f"could not read existing versionCodes from Google Play: {exc}"
        if args.allow_unverified:
            print(f"::warning::{message} — proceeding because --allow-unverified was set")
            summarise(["### Android build number", "",
                       f"⚠️ `{candidate}` **unverified** — {message}"])
            return 0
        print(f"::error::{message}", file=sys.stderr)
        print("::error::Refusing to upload: whether this versionCode is safe is unknown.",
              file=sys.stderr)
        return 1

    if highest is None:
        print(f"[check_build_number] No existing versionCode on {args.package_name}; "
              f"{candidate} will be the first.")
        summarise(["### Android build number", "",
                   f"✅ `{candidate}` — first upload for `{args.package_name}`."])
        return 0

    print(f"[check_build_number] store max = {highest}, release = {candidate}")
    if candidate > highest:
        summarise(["### Android build number", "",
                   f"✅ `{candidate}` is above the store's highest (`{highest}`)."])
        return 0

    reason = ("has already been used" if candidate == highest
              else f"is below the store's highest ({highest})")
    print(f"::error::versionCode {candidate} {reason}. Google Play requires a strictly "
          "increasing versionCode; this upload would be rejected, or would upload and "
          "then be impossible to promote.", file=sys.stderr)
    summarise(["### Android build number", "",
               f"❌ `{candidate}` {reason}. Store highest is `{highest}`."])
    return 1


if __name__ == "__main__":
    sys.exit(main())
