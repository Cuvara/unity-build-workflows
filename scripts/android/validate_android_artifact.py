#!/usr/bin/env python3
"""
validate_android_artifact.py
Stage 04 — ARTIFACT VALIDATION for Android.

Verifies a built AAB/APK before it is allowed anywhere near stage 05 (PUBLISH):

    * the artifact exists and is a readable zip
    * it is the artifact type the build was asked to produce (AAB vs APK)
    * required entries are present (AndroidManifest, dex/base module)
    * it is signed (v1 JAR entries and/or the v2+ APK Signing Block)
    * package id / versionName / versionCode match what the pipeline expects
    * the file size is plausible and under any declared ceiling

Identity fields (package, version) are read in this order:
    1. `aapt2 dump badging` / `bundletool dump manifest`, when on PATH
    2. the compiled AndroidManifest.xml inside an APK (scripts/android/axml.py)
    3. artifact-manifest.json shipped with the build

An identity field that none of those can supply is reported as `unverified`
and, unless --require-identity is passed, does not fail the job — a missing
Android SDK on a self-hosted runner is not an artifact defect.

Exit 0 = passed, 1 = validation failure, 2 = usage error.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from axml import AxmlError, parse_manifest_attributes
except ImportError:  # pragma: no cover - only if the file is missing
    AxmlError = Exception

    def parse_manifest_attributes(_data):
        raise AxmlError("axml.py unavailable")


UNVERIFIED = "unverified"

# The 16-byte magic that terminates the APK Signing Block (v2/v3/v4 signing).
APK_SIG_BLOCK_MAGIC = b"APK Sig Block 42"

# v1 (JAR) signature entries.
V1_SIGNATURE_SUFFIXES = (".RSA", ".DSA", ".EC")


class Result:
    """Accumulates checks so one run reports every problem, not just the first."""

    def __init__(self):
        self.checks = []
        self.facts = {}

    def ok(self, name, detail=""):
        self.checks.append(("pass", name, detail))

    def fail(self, name, detail=""):
        self.checks.append(("fail", name, detail))

    def warn(self, name, detail=""):
        self.checks.append(("warn", name, detail))

    @property
    def failed(self):
        return any(status == "fail" for status, _, _ in self.checks)

    def to_dict(self):
        return {
            "status": "failure" if self.failed else "success",
            "checks": [
                {"status": s, "name": n, "detail": d} for s, n, d in self.checks
            ],
            "facts": self.facts,
        }


def find_artifact(search_root, expected_type):
    """Locate the AAB/APK under search_root. Returns (path, actual_type)."""
    root = Path(search_root)
    if root.is_file():
        return root, root.suffix.lstrip(".").upper()

    aabs = sorted(root.rglob("*.aab")) if root.is_dir() else []
    apks = sorted(root.rglob("*.apk")) if root.is_dir() else []

    # Prefer whatever the build was configured to emit, so a stray
    # intermediate APK next to the real AAB cannot shadow it.
    if expected_type == "AAB" and aabs:
        return aabs[0], "AAB"
    if expected_type == "APK" and apks:
        return apks[0], "APK"
    if aabs:
        return aabs[0], "AAB"
    if apks:
        return apks[0], "APK"
    return None, ""


def read_shipped_manifest(search_root):
    """Load artifact-manifest.json / build-metadata.json from the artifact."""
    root = Path(search_root)
    root = root.parent if root.is_file() else root
    for name in ("artifact-manifest.json", "build-metadata.json"):
        for candidate in sorted(root.rglob(name)):
            try:
                return json.loads(candidate.read_text())
            except (OSError, json.JSONDecodeError):
                continue
    return {}


def identity_from_tool(path, artifact_type):
    """Identity via aapt2 (APK) or bundletool (AAB), when either is installed."""
    if artifact_type == "APK":
        aapt2 = shutil.which("aapt2") or shutil.which("aapt")
        if not aapt2:
            return {}
        try:
            out = subprocess.run(
                [aapt2, "dump", "badging", str(path)],
                capture_output=True, text=True, timeout=120,
            )
        except (subprocess.SubprocessError, OSError):
            return {}
        if out.returncode != 0:
            return {}
        for line in out.stdout.splitlines():
            if line.startswith("package:"):
                fields = {}
                for token in ("name", "versionCode", "versionName"):
                    marker = f"{token}='"
                    if marker in line:
                        start = line.index(marker) + len(marker)
                        fields[token] = line[start:line.index("'", start)]
                return {
                    "package": fields.get("name", ""),
                    "versionCode": fields.get("versionCode", ""),
                    "versionName": fields.get("versionName", ""),
                }
        return {}

    bundletool = shutil.which("bundletool")
    if not bundletool:
        return {}
    try:
        out = subprocess.run(
            [bundletool, "dump", "manifest", "--bundle", str(path)],
            capture_output=True, text=True, timeout=120,
        )
    except (subprocess.SubprocessError, OSError):
        return {}
    if out.returncode != 0:
        return {}
    import re

    text = out.stdout
    def attr(name):
        m = re.search(rf'{name}="([^"]*)"', text)
        return m.group(1) if m else ""

    return {
        "package": attr("package"),
        "versionCode": attr("android:versionCode"),
        "versionName": attr("android:versionName"),
    }


def identity_from_axml(zf):
    """Identity by parsing the compiled AndroidManifest.xml inside an APK."""
    try:
        raw = zf.read("AndroidManifest.xml")
    except KeyError:
        return {}
    try:
        attrs = parse_manifest_attributes(raw)
    except AxmlError:
        return {}
    return {
        "package": str(attrs.get("package", "")),
        "versionCode": str(attrs.get("versionCode", "")),
        "versionName": str(attrs.get("versionName", "")),
    }


def identity_from_manifest_json(meta):
    """Identity from the manifest the build stage shipped with the artifact."""
    return {
        "package": meta.get("bundleId") or meta.get("packageName") or "",
        "versionCode": str(meta.get("buildNumber") or ""),
        "versionName": str(meta.get("version") or ""),
    }


def merge_identity(*sources):
    """First non-empty value per field wins."""
    merged = {"package": "", "versionCode": "", "versionName": ""}
    provenance = {}
    for name, src in sources:
        for key in merged:
            if not merged[key] and src.get(key):
                merged[key] = str(src[key])
                provenance[key] = name
    for key, value in merged.items():
        if not value:
            merged[key] = UNVERIFIED
            provenance[key] = "none"
    return merged, provenance


def has_v2_signature(path):
    """True when the file carries an APK Signing Block (v2/v3/v4)."""
    try:
        with open(path, "rb") as fh:
            # The block sits immediately before the central directory; the
            # magic is within the last ~64 KiB even with a zip comment.
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            window = min(size, 1024 * 1024)
            fh.seek(size - window)
            return APK_SIG_BLOCK_MAGIC in fh.read(window)
    except OSError:
        return False


def check_signing(zf, path, result, require_signed):
    v1 = [
        n for n in zf.namelist()
        if n.upper().startswith("META-INF/") and n.upper().endswith(V1_SIGNATURE_SUFFIXES)
    ]
    v2 = has_v2_signature(path)
    scheme = []
    if v1:
        scheme.append("v1")
    if v2:
        scheme.append("v2+")
    signed = bool(scheme)
    result.facts["signed"] = signed
    result.facts["signingSchemes"] = scheme or ["none"]

    if signed:
        result.ok("signing", f"signed ({', '.join(scheme)})")
    elif require_signed:
        result.fail("signing", "artifact is unsigned — a release artifact must be signed")
    else:
        result.warn("signing", "artifact is unsigned")


def validate(args):
    result = Result()
    expected_type = (args.expected_type or "").upper()

    path, actual_type = find_artifact(args.search_root, expected_type)
    if path is None:
        result.fail("artifact-present", f"no .aab or .apk found under {args.search_root}")
        return result

    result.facts["artifactPath"] = str(path)
    result.facts["artifactType"] = actual_type
    result.ok("artifact-present", str(path))

    if expected_type and actual_type != expected_type:
        result.fail(
            "artifact-type",
            f"expected {expected_type}, found {actual_type} ({path.name})",
        )
    elif expected_type:
        result.ok("artifact-type", actual_type)

    size = path.stat().st_size
    result.facts["sizeBytes"] = size
    result.facts["sizeMB"] = round(size / (1024 * 1024), 2)
    if size < args.min_size_bytes:
        result.fail(
            "artifact-size",
            f"{size} bytes is below the {args.min_size_bytes}-byte floor — truncated build",
        )
    elif args.max_size_mb and result.facts["sizeMB"] > args.max_size_mb:
        result.fail(
            "artifact-size",
            f"{result.facts['sizeMB']} MB exceeds the {args.max_size_mb} MB ceiling",
        )
    else:
        result.ok("artifact-size", f"{result.facts['sizeMB']} MB")

    if not zipfile.is_zipfile(path):
        result.fail("archive-readable", "not a readable zip archive")
        return result

    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        result.ok("archive-readable", f"{len(names)} entries")

        if actual_type == "AAB":
            required = ["BundleConfig.pb", "base/manifest/AndroidManifest.xml"]
        else:
            required = ["AndroidManifest.xml"]
        missing = [r for r in required if r not in names]
        if missing:
            result.fail("archive-structure", f"missing entries: {', '.join(missing)}")
        else:
            result.ok("archive-structure", ", ".join(required))

        if actual_type == "APK" and not any(n.endswith(".dex") for n in names):
            result.fail("archive-structure", "no .dex found in APK")
        if actual_type == "AAB" and not any(n.startswith("base/dex/") for n in names):
            result.fail("archive-structure", "no base/dex/ entries found in AAB")

        check_signing(zf, path, result, args.require_signed)

        shipped = read_shipped_manifest(args.search_root)
        identity, provenance = merge_identity(
            ("tool", identity_from_tool(path, actual_type)),
            ("axml", identity_from_axml(zf) if actual_type == "APK" else {}),
            ("manifest-json", identity_from_manifest_json(shipped)),
        )

    result.facts.update(identity)
    result.facts["identityProvenance"] = provenance

    for field, expected in (
        ("package", args.expected_package),
        ("versionName", args.expected_version_name),
        ("versionCode", args.expected_version_code),
    ):
        actual = identity[field]
        if actual == UNVERIFIED:
            detail = f"{field} could not be read (no aapt2/bundletool, no shipped manifest)"
            if args.require_identity:
                result.fail(f"identity-{field}", detail)
            else:
                result.warn(f"identity-{field}", detail)
            continue
        if expected and actual != str(expected):
            result.fail(f"identity-{field}", f"expected {expected!r}, artifact says {actual!r}")
        else:
            result.ok(f"identity-{field}", actual)

    return result


def write_summary(result, path_env="GITHUB_STEP_SUMMARY"):
    target = os.environ.get(path_env)
    if not target:
        return
    lines = ["### Android Artifact Validation", "", "| Check | Status | Detail |", "|---|---|---|"]
    icon = {"pass": "✅", "fail": "❌", "warn": "⚠️"}
    for status, name, detail in result.checks:
        lines.append(f"| `{name}` | {icon[status]} | {detail} |")
    lines.append("")
    with open(target, "a") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate an Android build artifact")
    parser.add_argument("--search-root", default="build-artifact")
    parser.add_argument("--expected-type", default="", help="AAB or APK")
    parser.add_argument("--expected-package", default="")
    parser.add_argument("--expected-version-name", default="")
    parser.add_argument("--expected-version-code", default="")
    parser.add_argument("--min-size-bytes", type=int, default=1_000_000)
    parser.add_argument("--max-size-mb", type=float, default=0.0, help="0 disables the ceiling")
    parser.add_argument("--require-signed", action="store_true")
    parser.add_argument("--require-identity", action="store_true")
    parser.add_argument("--report", default="", help="Write the JSON report here")
    args = parser.parse_args(argv)

    result = validate(args)

    report = result.to_dict()
    print(json.dumps(report, indent=2))
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, indent=2))
    write_summary(result)

    if result.failed:
        for status, name, detail in result.checks:
            if status == "fail":
                print(f"::error::Android artifact validation — {name}: {detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
