#!/usr/bin/env python3
"""
validate_xcode_project.py
Stage 04 — ARTIFACT VALIDATION for the iOS *build* artifact.

The pipeline's iOS build job (stage 03) emits a Unity-exported Xcode project,
not an .ipa — the archive/export/sign steps belong to the release pipeline
(pipeline-ios-release.yml), which validates the resulting IPA with
scripts/ios/validate_ipa.sh.

So this validates what stage 03 actually produced:

    * a *.xcodeproj with a readable project.pbxproj
    * the Unity-generated trees Xcode needs (Classes/, Libraries/, Data/)
    * Info.plist exists and carries the bundle identifier / version
    * the bundle id and version match what the pipeline expects
    * the export is not a truncated stub

Runs on any OS — pbxproj and Info.plist are read as text/plist, so a Linux
reporting job can validate a project a macOS runner built.

Exit 0 = passed, 1 = validation failure, 2 = usage error.
"""

import argparse
import json
import os
import plistlib
import sys
from pathlib import Path

# Unity always emits these next to the .xcodeproj. A missing one means the
# export was interrupted, which otherwise only surfaces as an Xcode error
# minutes into the release pipeline.
REQUIRED_TREES = ["Classes", "Libraries", "Data"]


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


def find_xcodeproj(search_root):
    """Return the shallowest *.xcodeproj under search_root, or None."""
    root = Path(search_root)
    if not root.is_dir():
        return None
    if root.suffix == ".xcodeproj":
        return root
    candidates = [p for p in root.rglob("*.xcodeproj") if p.is_dir()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: (len(p.parts), str(p)))
    return candidates[0]


def read_info_plist(export_root):
    """Load the Unity-exported Info.plist, or {} when absent/unreadable."""
    for candidate in (export_root / "Info.plist", export_root / "Unity-iPhone" / "Info.plist"):
        if candidate.is_file():
            try:
                with candidate.open("rb") as fh:
                    return plistlib.load(fh), candidate
            except (OSError, plistlib.InvalidFileException, ValueError):
                return {}, candidate
    matches = sorted(export_root.rglob("Info.plist"), key=lambda p: len(p.parts))
    for candidate in matches:
        try:
            with candidate.open("rb") as fh:
                return plistlib.load(fh), candidate
        except (OSError, plistlib.InvalidFileException, ValueError):
            continue
    return {}, None


def validate(args):
    result = Result()

    project = find_xcodeproj(args.search_root)
    if project is None:
        result.fail("xcodeproj-present", f"no *.xcodeproj found under {args.search_root}")
        return result

    export_root = project.parent
    result.facts["xcodeproj"] = str(project)
    result.facts["exportRoot"] = str(export_root)
    result.ok("xcodeproj-present", project.name)

    pbxproj = project / "project.pbxproj"
    if not pbxproj.is_file():
        result.fail("pbxproj-readable", "project.pbxproj is missing")
    else:
        text = pbxproj.read_text(errors="replace")
        if "PBXProject" not in text:
            result.fail("pbxproj-readable", "project.pbxproj has no PBXProject section — truncated export")
        else:
            result.ok("pbxproj-readable", f"{len(text)} bytes")

    missing = [t for t in REQUIRED_TREES if not (export_root / t).exists()]
    if missing:
        result.fail("unity-trees", f"missing Unity export directories: {', '.join(missing)}")
    else:
        result.ok("unity-trees", ", ".join(REQUIRED_TREES))

    plist, plist_path = read_info_plist(export_root)
    if plist_path is None:
        result.fail("info-plist", "no Info.plist found in the export")
        return result
    if not plist:
        result.fail("info-plist", f"Info.plist unreadable: {plist_path}")
        return result

    result.ok("info-plist", str(plist_path))
    bundle_id = str(plist.get("CFBundleIdentifier", ""))
    version = str(plist.get("CFBundleShortVersionString", ""))
    build_number = str(plist.get("CFBundleVersion", ""))
    result.facts.update(
        {"bundleId": bundle_id, "version": version, "buildNumber": build_number}
    )

    # Unity writes $(PRODUCT_BUNDLE_IDENTIFIER) into the exported plist and lets
    # Xcode substitute it at archive time, so a build-setting placeholder here is
    # correct, not a defect — say so instead of failing the gate.
    def check(field, actual, expected):
        if not actual:
            result.fail(f"identity-{field}", f"{field} is empty in Info.plist")
            return
        if actual.startswith("$("):
            result.warn(f"identity-{field}", f"{actual} — resolved by Xcode at archive time")
            return
        if expected and actual != expected:
            result.fail(f"identity-{field}", f"expected {expected!r}, project says {actual!r}")
        else:
            result.ok(f"identity-{field}", actual)

    check("bundleId", bundle_id, args.expected_bundle_id)
    check("version", version, args.expected_version)
    if args.expected_build_number:
        check("buildNumber", build_number, args.expected_build_number)

    total = sum(f.stat().st_size for f in export_root.rglob("*") if f.is_file())
    total_mb = round(total / (1024 * 1024), 2)
    result.facts["sizeBytes"] = total
    result.facts["sizeMB"] = total_mb
    if total < args.min_size_bytes:
        result.fail(
            "artifact-size",
            f"{total} bytes is below the {args.min_size_bytes}-byte floor — truncated export",
        )
    else:
        result.ok("artifact-size", f"{total_mb} MB")

    return result


def write_summary(result, path_env="GITHUB_STEP_SUMMARY"):
    target = os.environ.get(path_env)
    if not target:
        return
    lines = ["### iOS Xcode Project Validation", "", "| Check | Status | Detail |", "|---|---|---|"]
    icon = {"pass": "✅", "fail": "❌", "warn": "⚠️"}
    for status, name, detail in result.checks:
        lines.append(f"| `{name}` | {icon[status]} | {detail} |")
    lines.append("")
    with open(target, "a") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate a Unity-exported Xcode project")
    parser.add_argument("--search-root", default="build-artifact")
    parser.add_argument("--expected-bundle-id", default="")
    parser.add_argument("--expected-version", default="")
    parser.add_argument("--expected-build-number", default="")
    parser.add_argument("--min-size-bytes", type=int, default=1_000_000)
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
                print(f"::error::iOS Xcode project validation — {name}: {detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
