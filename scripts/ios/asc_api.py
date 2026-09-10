#!/usr/bin/env python3
"""
asc_api.py
App Store Connect API v2 REST client for CI/CD automation.

Subcommands:
  wait-for-build    — Poll until a TestFlight build finishes processing
  submit-for-review — Create App Store version, attach build, submit for review
  check-review      — Poll review status for a version
  enable-phased     — Enable phased release on an approved version

Requires: PyJWT, cryptography

Auth: JWT signed with ES256 using App Store Connect API key (.p8).
Env vars: ASC_KEY_ID, ASC_ISSUER_ID, ASC_PRIVATE_KEY (or ASC_PRIVATE_KEY_PATH)
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ASC_BASE = "https://api.appstoreconnect.apple.com/v1"


# ---------------------------------------------------------------------------
# JWT Auth
# ---------------------------------------------------------------------------

def generate_jwt(key_id: str, issuer_id: str, private_key: str) -> str:
    """Generate a short-lived JWT for ASC API."""
    try:
        import jwt  # type: ignore  # PyJWT
    except ImportError:
        print("ERROR: PyJWT not installed. Run: pip install PyJWT cryptography", file=sys.stderr)
        sys.exit(1)

    now = int(time.time())
    payload = {
        "iss": issuer_id,
        "iat": now,
        "exp": now + 1200,  # 20 min max
        "aud": "appstoreconnect-v1",
    }
    token = jwt.encode(payload, private_key, algorithm="ES256", headers={"kid": key_id})
    return token if isinstance(token, str) else token.decode("utf-8")


def get_credentials() -> tuple[str, str, str]:
    """Read ASC credentials from environment."""
    key_id = os.environ.get("ASC_KEY_ID", os.environ.get("APP_STORE_CONNECT_KEY_ID", ""))
    issuer_id = os.environ.get("ASC_ISSUER_ID", os.environ.get("APP_STORE_CONNECT_ISSUER_ID", ""))
    private_key = os.environ.get("ASC_PRIVATE_KEY", os.environ.get("APP_STORE_CONNECT_PRIVATE_KEY", ""))

    if not private_key:
        key_path = os.environ.get("ASC_PRIVATE_KEY_PATH", "")
        if key_path and Path(key_path).is_file():
            private_key = Path(key_path).read_text()

    if not all([key_id, issuer_id, private_key]):
        print(
            "ERROR: ASC credentials required. Set ASC_KEY_ID, ASC_ISSUER_ID, and "
            "ASC_PRIVATE_KEY (or ASC_PRIVATE_KEY_PATH) environment variables.",
            file=sys.stderr,
        )
        sys.exit(1)

    return key_id, issuer_id, private_key


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def asc_request(method: str, url: str, token: str, data: dict | None = None) -> dict:
    """Make an authenticated request to ASC API."""
    if not url.startswith("http"):
        url = f"{ASC_BASE}{url}"

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    body = json.dumps(data).encode() if data else None
    req = Request(url, data=body, headers=headers, method=method)

    try:
        with urlopen(req) as resp:
            resp_body = resp.read().decode()
            return json.loads(resp_body) if resp_body else {}
    except HTTPError as e:
        error_body = e.read().decode() if e.fp else ""
        print(f"ERROR: ASC API {method} {url} -> {e.code}: {error_body}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_wait_for_build(args) -> None:
    """Poll until build finishes processing on TestFlight."""
    key_id, issuer_id, private_key = get_credentials()
    token = generate_jwt(key_id, issuer_id, private_key)

    app_id = args.app_id
    build_version = args.build_version
    timeout = args.timeout
    interval = 30  # start at 30s
    max_interval = 300  # cap at 5min
    elapsed = 0

    print(f"[asc_api] Waiting for build {build_version} to finish processing (timeout: {timeout}s)...")

    while elapsed < timeout:
        # Re-generate token if close to expiry (every 15 min)
        if elapsed > 0 and elapsed % 900 == 0:
            token = generate_jwt(key_id, issuer_id, private_key)

        url = (
            f"{ASC_BASE}/builds?"
            f"filter[app]={app_id}&"
            f"filter[version]={build_version}&"
            f"fields[builds]=processingState,version,uploadedDate"
        )
        resp = asc_request("GET", url, token)
        builds = resp.get("data", [])

        if builds:
            state = builds[0]["attributes"]["processingState"]
            print(f"[asc_api] Build {build_version}: processingState={state} (elapsed: {elapsed}s)")

            if state == "VALID":
                build_id = builds[0]["id"]
                print(f"[asc_api] Build ready! ID: {build_id}")
                _set_output("build-id", build_id)
                _set_output("processing-status", "valid")
                return
            elif state in ("INVALID", "FAILED"):
                print(f"ERROR: Build processing {state}", file=sys.stderr)
                _set_output("processing-status", state.lower())
                sys.exit(1)
        else:
            print(f"[asc_api] Build {build_version} not yet visible (elapsed: {elapsed}s)")

        time.sleep(interval)
        elapsed += interval
        # Exponential backoff capped at max_interval
        interval = min(interval * 2, max_interval)

    print(f"ERROR: Timeout after {timeout}s waiting for build processing", file=sys.stderr)
    _set_output("processing-status", "timeout")
    sys.exit(1)


def cmd_submit_for_review(args) -> None:
    """Create App Store version, attach build, submit for review."""
    key_id, issuer_id, private_key = get_credentials()
    token = generate_jwt(key_id, issuer_id, private_key)

    app_id = args.app_id
    build_id = args.build_id
    version_string = args.version_string
    platform = args.platform

    print(f"[asc_api] Submitting build {build_id} as version {version_string} for review...")

    # Step 1: Create or find App Store version
    existing_url = (
        f"{ASC_BASE}/apps/{app_id}/appStoreVersions?"
        f"filter[versionString]={version_string}&"
        f"filter[platform]={platform}"
    )
    existing = asc_request("GET", existing_url, token)
    existing_versions = existing.get("data", [])

    if existing_versions:
        version_id = existing_versions[0]["id"]
        version_state = existing_versions[0]["attributes"]["appStoreState"]
        print(f"[asc_api] Found existing version {version_string}: {version_id} (state: {version_state})")
    else:
        # Create new version
        create_data = {
            "data": {
                "type": "appStoreVersions",
                "attributes": {
                    "versionString": version_string,
                    "platform": platform,
                },
                "relationships": {
                    "app": {"data": {"type": "apps", "id": app_id}},
                },
            }
        }
        resp = asc_request("POST", f"{ASC_BASE}/appStoreVersions", token, create_data)
        version_id = resp["data"]["id"]
        print(f"[asc_api] Created version {version_string}: {version_id}")

    # Step 2: Attach build to version
    build_data = {
        "data": {"type": "builds", "id": build_id},
    }
    asc_request(
        "PATCH",
        f"{ASC_BASE}/appStoreVersions/{version_id}/relationships/build",
        token,
        build_data,
    )
    print(f"[asc_api] Attached build {build_id} to version {version_id}")

    # Step 3: Set release notes if provided
    if args.release_notes_file:
        notes_path = Path(args.release_notes_file)
        if notes_path.is_file():
            notes_text = notes_path.read_text().strip()
            # Get localization to update
            localizations_url = f"{ASC_BASE}/appStoreVersions/{version_id}/appStoreVersionLocalizations"
            loc_resp = asc_request("GET", localizations_url, token)
            for loc in loc_resp.get("data", []):
                loc_id = loc["id"]
                locale = loc["attributes"]["locale"]
                update_data = {
                    "data": {
                        "type": "appStoreVersionLocalizations",
                        "id": loc_id,
                        "attributes": {"whatsNew": notes_text},
                    }
                }
                asc_request("PATCH", f"{ASC_BASE}/appStoreVersionLocalizations/{loc_id}", token, update_data)
                print(f"[asc_api] Updated whatsNew for locale {locale}")

    # Step 4: Enable phased release if requested
    if args.phased_release:
        phased_data = {
            "data": {
                "type": "appStoreVersionPhasedReleases",
                "attributes": {"phasedReleaseState": "ACTIVE"},
                "relationships": {
                    "appStoreVersion": {"data": {"type": "appStoreVersions", "id": version_id}},
                },
            }
        }
        asc_request("POST", f"{ASC_BASE}/appStoreVersionPhasedReleases", token, phased_data)
        print("[asc_api] Phased release enabled (7-day rollout)")

    # Step 5: Submit for review
    submission_data = {
        "data": {
            "type": "appStoreVersionSubmissions",
            "relationships": {
                "appStoreVersion": {"data": {"type": "appStoreVersions", "id": version_id}},
            },
        }
    }
    asc_request("POST", f"{ASC_BASE}/appStoreVersionSubmissions", token, submission_data)
    print(f"[asc_api] Version {version_string} submitted for App Store review")

    _set_output("version-id", version_id)
    _set_output("submission-status", "submitted")


def cmd_check_review(args) -> None:
    """Check review status for an App Store version."""
    key_id, issuer_id, private_key = get_credentials()
    token = generate_jwt(key_id, issuer_id, private_key)

    version_id = args.version_id
    resp = asc_request("GET", f"{ASC_BASE}/appStoreVersions/{version_id}", token)
    state = resp["data"]["attributes"]["appStoreState"]
    print(f"[asc_api] Version {version_id}: appStoreState={state}")

    _set_output("review-status", state)

    # Map states to pass/fail
    if state in ("READY_FOR_SALE", "PENDING_DEVELOPER_RELEASE"):
        print("[asc_api] Review APPROVED")
    elif state == "REJECTED":
        print("[asc_api] Review REJECTED", file=sys.stderr)
        sys.exit(1)
    elif state in ("IN_REVIEW", "WAITING_FOR_REVIEW"):
        print("[asc_api] Still in review")


def cmd_enable_phased(args) -> None:
    """Enable or pause phased release on an approved version."""
    key_id, issuer_id, private_key = get_credentials()
    token = generate_jwt(key_id, issuer_id, private_key)

    version_id = args.version_id
    state = "ACTIVE" if not args.pause else "PAUSE"

    # Get existing phased release
    url = f"{ASC_BASE}/appStoreVersions/{version_id}/appStoreVersionPhasedRelease"
    resp = asc_request("GET", url, token)

    if resp.get("data"):
        phased_id = resp["data"]["id"]
        update_data = {
            "data": {
                "type": "appStoreVersionPhasedReleases",
                "id": phased_id,
                "attributes": {"phasedReleaseState": state},
            }
        }
        asc_request("PATCH", f"{ASC_BASE}/appStoreVersionPhasedReleases/{phased_id}", token, update_data)
    else:
        create_data = {
            "data": {
                "type": "appStoreVersionPhasedReleases",
                "attributes": {"phasedReleaseState": state},
                "relationships": {
                    "appStoreVersion": {"data": {"type": "appStoreVersions", "id": version_id}},
                },
            }
        }
        asc_request("POST", f"{ASC_BASE}/appStoreVersionPhasedReleases", token, create_data)

    print(f"[asc_api] Phased release state set to {state}")
    _set_output("phased-release-state", state.lower())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _set_output(key: str, value: str) -> None:
    """Write key=value to GITHUB_OUTPUT if available."""
    output_file = os.environ.get("GITHUB_OUTPUT", "")
    if output_file:
        with open(output_file, "a") as f:
            f.write(f"{key}={value}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="App Store Connect API client for CI/CD"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # wait-for-build
    p_wait = subparsers.add_parser("wait-for-build", help="Wait for build processing")
    p_wait.add_argument("--app-id", required=True, help="App Store Connect app ID")
    p_wait.add_argument("--build-version", required=True, help="Build version string (CFBundleVersion)")
    p_wait.add_argument("--timeout", type=int, default=1800, help="Timeout in seconds (default: 1800)")
    p_wait.set_defaults(func=cmd_wait_for_build)

    # submit-for-review
    p_submit = subparsers.add_parser("submit-for-review", help="Submit version for App Store review")
    p_submit.add_argument("--app-id", required=True, help="App Store Connect app ID")
    p_submit.add_argument("--build-id", required=True, help="Build ID from wait-for-build")
    p_submit.add_argument("--version-string", required=True, help="Version string (e.g., 1.2.3)")
    p_submit.add_argument("--platform", default="IOS", help="Platform (default: IOS)")
    p_submit.add_argument("--phased-release", action="store_true", help="Enable 7-day phased release")
    p_submit.add_argument("--release-notes-file", default="", help="Path to release notes text file")
    p_submit.set_defaults(func=cmd_submit_for_review)

    # check-review
    p_check = subparsers.add_parser("check-review", help="Check review status")
    p_check.add_argument("--version-id", required=True, help="App Store version ID")
    p_check.set_defaults(func=cmd_check_review)

    # enable-phased
    p_phased = subparsers.add_parser("enable-phased", help="Enable/pause phased release")
    p_phased.add_argument("--version-id", required=True, help="App Store version ID")
    p_phased.add_argument("--pause", action="store_true", help="Pause instead of activate")
    p_phased.set_defaults(func=cmd_enable_phased)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
