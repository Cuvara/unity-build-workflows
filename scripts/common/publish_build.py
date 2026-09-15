#!/usr/bin/env python3
"""
publish_build.py
Put a finished build somewhere a person can download it by clicking a link.

A GitHub Actions artifact is not that place. Its URL 404s for anyone who is not
signed in with access to the repository — on a public repo too — so the link in
a Discord message is useless to everyone except the people who could already
find the build themselves.

This copies the artifact to a host that serves it, and prints the URL. Three
providers, chosen by `BUILD_DELIVERY`:

    r2        Cloudflare R2 over the S3 API. Free egress, which is the reason to
              prefer it: a 33 MB build fetched by a team several times a day is
              real bandwidth, and R2 does not bill for it.

    local     A directory on a self-hosted runner, served by a web server you
              already run. The toolkit copies the file and composes the URL; it
              does not install or configure the server, and cannot verify the
              file is reachable.

    firebase  Firebase App Distribution. Uploads the build via the Firebase CLI
              and returns the tester link. Testers must be invited to the
              Firebase project or a tester group to access it. Supports APK,
              AAB and IPA.

    none      Default. Nothing is published and nothing fails — a project that
              has not configured delivery still builds.

Delivery is not the build. A publish failure is reported and does not fail the
job: a green build that could not be copied somewhere is still a green build.
Misconfiguration is different — asking for `r2` without credentials is a
mistake worth stopping for, and it fails closed.

Secrets are read from the environment and never printed, never passed on a
command line. SigV4 is signed here with hmac/hashlib rather than shelling out
to aws-cli, which is absent on most self-hosted runners.
"""

import argparse
import datetime
import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

CONTENT_TYPES = {
    ".apk": "application/vnd.android.package-archive",
    ".aab": "application/octet-stream",
    ".ipa": "application/octet-stream",
    ".zip": "application/zip",
    ".exe": "application/octet-stream",
}


def env(name, default=""):
    return (os.environ.get(name) or default).strip()


def fail(message):
    print(f"::error::{message}", file=sys.stderr)
    return 1


# ── S3 / R2 ──────────────────────────────────────────────────────────────────

def sign_key(secret, date_stamp, region, service):
    key = ("AWS4" + secret).encode()
    for part in (date_stamp, region, service, "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    return key


def put_object(endpoint, bucket, key, body, content_type,
               access_key, secret_key, region="auto"):
    """Signature Version 4 PUT. Returns the object URL on the endpoint."""
    host = endpoint.replace("https://", "").replace("http://", "").rstrip("/")
    canonical_uri = f"/{bucket}/{key}"
    payload_hash = hashlib.sha256(body).hexdigest()

    now = datetime.datetime.now(datetime.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")

    # Content-Disposition is the difference between the browser downloading the
    # file and displaying it — or, for an .apk, offering a save dialog rather
    # than a wall of bytes.
    disposition = f'attachment; filename="{Path(key).name}"'
    canonical_headers = (
        f"content-disposition:{disposition}\n"
        f"content-type:{content_type}\n"
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = ("content-disposition;content-type;host;"
                      "x-amz-content-sha256;x-amz-date")

    canonical_request = "\n".join([
        "PUT", canonical_uri, "", canonical_headers, signed_headers, payload_hash,
    ])
    scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope,
        hashlib.sha256(canonical_request.encode()).hexdigest(),
    ])
    signature = hmac.new(sign_key(secret_key, date_stamp, region, "s3"),
                         string_to_sign.encode(), hashlib.sha256).hexdigest()

    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    request = urllib.request.Request(
        f"https://{host}{canonical_uri}", data=body, method="PUT",
        headers={
            "Authorization": authorization,
            "Content-Disposition": disposition,
            "Content-Type": content_type,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        },
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        if response.status not in (200, 201):
            raise RuntimeError(f"unexpected status {response.status}")
    return f"https://{host}{canonical_uri}"


def publish_r2(source, key):
    account = env("R2_ACCOUNT_ID") or env("CLOUDFLARE_ACCOUNT_ID")
    bucket = env("R2_BUCKET")
    access_key = env("R2_ACCESS_KEY_ID")
    secret_key = env("R2_SECRET_ACCESS_KEY")
    public_base = env("R2_PUBLIC_BASE_URL").rstrip("/")

    missing = [name for name, value in (
        ("R2_ACCOUNT_ID (or CLOUDFLARE_ACCOUNT_ID)", account),
        ("R2_BUCKET", bucket),
        ("R2_ACCESS_KEY_ID", access_key),
        ("R2_SECRET_ACCESS_KEY", secret_key),
    ) if not value]
    if missing:
        return None, (
            f"BUILD_DELIVERY=r2 but {', '.join(missing)} is not set. "
            "Configure the bucket and its credentials, or set BUILD_DELIVERY=none."
        )

    body = source.read_bytes()
    content_type = CONTENT_TYPES.get(source.suffix.lower(), "application/octet-stream")
    endpoint = f"{account}.r2.cloudflarestorage.com"
    try:
        api_url = put_object(endpoint, bucket, key, body, content_type,
                             access_key, secret_key)
    except (urllib.error.HTTPError, urllib.error.URLError, RuntimeError) as exc:
        detail = getattr(exc, "code", exc)
        return None, f"upload to R2 failed: {detail}"

    # The bucket endpoint is not public. A public bucket or a custom domain is,
    # and that is the link worth handing to a person.
    if public_base:
        return f"{public_base}/{key}", None
    return api_url, (
        "uploaded, but R2_PUBLIC_BASE_URL is not set — the link above is the "
        "private S3 endpoint and will not open in a browser. Set it to the "
        "bucket's public URL or a custom domain."
    )


# ── A directory on your own machine ──────────────────────────────────────────

def publish_local(source, key):
    root = env("BUILD_PUBLISH_DIR")
    base = env("BUILD_PUBLISH_BASE_URL").rstrip("/")
    if not root:
        return None, ("BUILD_DELIVERY=local but BUILD_PUBLISH_DIR is not set. "
                      "Point it at a directory your web server serves.")
    if not base:
        return None, ("BUILD_DELIVERY=local but BUILD_PUBLISH_BASE_URL is not "
                      "set. Without it there is no link to hand anyone.")

    target = Path(root) / key
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    except OSError as exc:
        return None, f"could not copy the build to {target}: {exc}"

    # Whether that path is actually reachable is your web server's business;
    # this cannot check it and does not pretend to.
    return f"{base}/{key}", None


# ── Firebase App Distribution ───────────────────────────────────────────────

def publish_firebase(source, key):
    """Upload to Firebase App Distribution via the Firebase CLI.

    Returns (testing_uri, None) on success, or (None, problem) on failure.
    The testing URI is a stable link any invited tester can use to install
    the build — unlike the binary download URI which expires in one hour.
    """
    app_id = env("FIREBASE_APP_ID")
    creds_json = env("FIREBASE_SERVICE_ACCOUNT_JSON")
    groups = env("FIREBASE_TESTER_GROUPS")
    release_notes = env("FIREBASE_RELEASE_NOTES")

    if not creds_json:
        return None, (
            "BUILD_DELIVERY=firebase but FIREBASE_SERVICE_ACCOUNT_JSON is not "
            "set. Configure a service account with Firebase App Distribution "
            "Admin role, or set BUILD_DELIVERY=none."
        )
    if not app_id:
        return None, (
            "BUILD_DELIVERY=firebase but FIREBASE_APP_ID is not set. "
            "Set it to the Firebase App ID for this platform "
            "(e.g. 1:123456789:android:abcdef)."
        )

    suffix = source.suffix.lower()
    if suffix not in (".apk", ".aab", ".ipa"):
        return None, (
            f"Firebase App Distribution does not support {suffix} files. "
            "Only APK, AAB and IPA are accepted."
        )

    # Write credentials to a temp file — never pass on command line.
    creds_file = None
    try:
        fd, creds_path = tempfile.mkstemp(suffix=".json", prefix="firebase-sa-")
        creds_file = creds_path
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(creds_json)

        cmd = [
            shutil.which("firebase") or "firebase", "appdistribution:distribute", str(source),
            "--app", app_id, "--json", "--non-interactive",
        ]
        if groups:
            cmd.extend(["--groups", groups])
        if release_notes:
            cmd.extend(["--release-notes", release_notes])

        proc_env = {**os.environ, "GOOGLE_APPLICATION_CREDENTIALS": creds_path}
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=600, env=proc_env)

        if proc.returncode != 0:
            # Strip credentials path from error output for safety.
            stderr = proc.stderr.replace(creds_path, "<credentials>")
            return None, f"Firebase CLI failed (exit {proc.returncode}): {stderr.strip()}"

        # Parse the testing URI from CLI output.
        # Firebase CLI prints: "✔ View this release in the Firebase console: <url>"
        # and "Share this release with testers who have access: <testing_uri>"
        testing_uri = _parse_firebase_testing_uri(proc.stdout)
        if not testing_uri:
            # Fallback: upload succeeded but could not parse URI.
            return None, (
                "Firebase upload succeeded but could not extract the tester "
                "link from CLI output. Check the Firebase release in the console."
            )

        return testing_uri, None

    except FileNotFoundError:
        return None, (
            "Firebase CLI (firebase-tools) is not installed. "
            "Install it with: npm install -g firebase-tools"
        )
    except subprocess.TimeoutExpired:
        return None, "Firebase upload timed out after 600 seconds."
    finally:
        if creds_file and os.path.exists(creds_file):
            os.unlink(creds_file)


def _parse_firebase_testing_uri(stdout):
    """Extract the tester link from Firebase CLI stdout."""
    try:
        payload = json.loads(stdout)
        result = payload.get("result", {})
        uri = result.get("testingUri", "") if isinstance(result, dict) else ""
        if isinstance(uri, str) and uri.startswith("https://appdistribution.firebase.google.com/"):
            return uri
    except (ValueError, AttributeError):
        pass
    for line in stdout.splitlines():
        # The CLI outputs various URLs. The testing/sharing URI is the one
        # testers use to install — look for it by common patterns.
        stripped = line.strip()
        if "appdistribution.firebase.google.com" in stripped:
            # Extract URL from the line (may have prefix text)
            for token in stripped.split():
                if token.startswith("https://"):
                    return token
        if stripped.startswith("https://appdistribution.firebase.google.com"):
            return stripped
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Publish a build for download")
    parser.add_argument("--file", required=True, help="The artifact to publish")
    parser.add_argument("--key", required=True,
                        help="Destination path, e.g. develop/42/abc1234/game.apk")
    parser.add_argument("--provider", default="",
                        help="r2 | local | firebase | none (default: $BUILD_DELIVERY)")
    parser.add_argument("--github-output", action="store_true")
    args = parser.parse_args(argv)

    provider = (args.provider or env("BUILD_DELIVERY", "none")).lower()
    if provider in ("", "none", "off", "false"):
        print("[publish] BUILD_DELIVERY is not set — nothing published.")
        return 0

    source = Path(args.file)
    if not source.is_file():
        # Nothing to publish is not a delivery failure; the build step already
        # decided whether producing nothing was a problem.
        print(f"::warning::No file at {source} — nothing to publish")
        return 0

    key = args.key.strip("/")
    size_mb = source.stat().st_size / 1024 / 1024
    print(f"[publish] {provider}: {source.name} ({size_mb:.1f} MB) -> {key}")

    if provider == "r2":
        url, problem = publish_r2(source, key)
    elif provider == "local":
        url, problem = publish_local(source, key)
    elif provider == "firebase":
        url, problem = publish_firebase(source, key)
    else:
        return fail(f"Unknown BUILD_DELIVERY '{provider}'. Valid: r2, local, firebase, none.")

    if url is None:
        # Configuration is wrong, which is worth stopping for: the alternative
        # is a green pipeline that quietly never delivers anything.
        return fail(f"Build delivery ({provider}) — {problem}")

    if problem:
        print(f"::warning::{problem}")

    print(f"[publish] {url}")
    if args.github_output and env("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"download-url={url}\n")
    if env("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(f"\n**Download** — [{source.name}]({url}) "
                     f"({size_mb:.1f} MB, via {provider})\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
