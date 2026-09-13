#!/usr/bin/env python3
"""
publish_build.py
Put a finished build somewhere a person can download it by clicking a link.

A GitHub Actions artifact is not that place. Its URL 404s for anyone who is not
signed in with access to the repository — on a public repo too — so the link in
a Discord message is useless to everyone except the people who could already
find the build themselves.

This copies the artifact to a host that serves it, and prints the URL. Two
providers, chosen by `BUILD_DELIVERY`:

    r2      Cloudflare R2 over the S3 API. Free egress, which is the reason to
            prefer it: a 33 MB build fetched by a team several times a day is
            real bandwidth, and R2 does not bill for it.

    local   A directory on a self-hosted runner, served by a web server you
            already run. The toolkit copies the file and composes the URL; it
            does not install or configure the server, and cannot verify the
            file is reachable.

    none    Default. Nothing is published and nothing fails — a project that
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
import os
import shutil
import sys
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


def main(argv=None):
    parser = argparse.ArgumentParser(description="Publish a build for download")
    parser.add_argument("--file", required=True, help="The artifact to publish")
    parser.add_argument("--key", required=True,
                        help="Destination path, e.g. develop/42/abc1234/game.apk")
    parser.add_argument("--provider", default="",
                        help="r2 | local | none (default: $BUILD_DELIVERY)")
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
    else:
        return fail(f"Unknown BUILD_DELIVERY '{provider}'. Valid: r2, local, none.")

    if url is None:
        # Configuration is wrong, which is worth stopping for: the alternative
        # is a green pipeline that quietly never delivers anything.
        return fail(f"Build delivery ({provider}) — {problem}")

    if problem:
        print(f"::warning::{problem}")

    print(f"[publish] {url}")
    if args.github_output and env("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
            fh.write(f"download-url={url}\n")
    if env("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write(f"\n**Download** — [{source.name}]({url}) "
                     f"({size_mb:.1f} MB, via {provider})\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
