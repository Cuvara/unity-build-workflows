#!/usr/bin/env python3
"""Point a Unity-generated Xcode project at the CI provisioning profile.

The pipeline's iOS lane builds the player with the consuming project's own
PlayerSettings, which often carry no Apple team and automatic signing. A manual
`xcodebuild archive` of that project fails: the app target has no team and no
provisioning profile. Everything signing needs is already in the profile, so
this script reads it from there instead of asking the project for it:

  team ID       TeamIdentifier[0]
  profile       Name and UUID
  bundle ID     the app target's PRODUCT_BUNDLE_IDENTIFIER, checked against the
                profile's application-identifier

and writes manual signing into the app target's build configurations
(CODE_SIGN_STYLE, DEVELOPMENT_TEAM, PROVISIONING_PROFILE_SPECIFIER,
CODE_SIGN_IDENTITY). Other native targets (UnityFramework, extensions without a
profile) get the team and manual style only.

Inputs:
  --project-dir      directory containing <name>.xcodeproj
  --target           app target name (default Unity-iPhone)
  --profile-plist    decoded profile plist (tests); otherwise the base64
                     profile is read from IOS_PROVISIONING_PROFILE_BASE64 and
                     decoded with `security cms -D` (macOS)

Writes development-team, bundle-identifier, profile-name, profile-uuid and
export-method (derived from the profile) to stdout and $GITHUB_OUTPUT. The
profile itself is never printed.
"""
import argparse
import base64
import os
import plistlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

TEAM_RE = re.compile(r"^[A-Z0-9]{10}$")


def fail(message):
    print(f"::error::configure_xcode_signing: {message}", file=sys.stderr)
    sys.exit(1)


def load_profile(profile_plist):
    if profile_plist:
        return plistlib.loads(Path(profile_plist).read_bytes())
    encoded = os.environ.get("IOS_PROVISIONING_PROFILE_BASE64", "").strip()
    if not encoded:
        fail("IOS_PROVISIONING_PROFILE_BASE64 is empty — set it in the build's GitHub "
             "Environment (see docs/MIGRATING_TO_ENVIRONMENT_SECRETS.md).")
    try:
        raw = base64.b64decode(encoded, validate=False)
    except (ValueError, TypeError):
        fail("IOS_PROVISIONING_PROFILE_BASE64 is not valid base64.")
    with tempfile.NamedTemporaryFile(suffix=".mobileprovision", delete=False) as tmp:
        tmp.write(raw)
        path = tmp.name
    try:
        os.chmod(path, 0o600)
        decoded = subprocess.run(["security", "cms", "-D", "-i", path],
                                 capture_output=True, check=False).stdout
    finally:
        os.unlink(path)
    if not decoded:
        fail("could not decode the provisioning profile (security cms -D) — "
             "check IOS_PROVISIONING_PROFILE_BASE64.")
    return plistlib.loads(decoded)


def export_method(profile):
    if profile.get("ProvisionsAllDevices"):
        return "enterprise"
    if profile.get("ProvisionedDevices"):
        if profile.get("Entitlements", {}).get("get-task-allow"):
            return "development"
        return "ad-hoc"
    return "app-store"


def profile_identity(profile):
    teams = profile.get("TeamIdentifier") or []
    team = teams[0] if teams else ""
    if not TEAM_RE.match(team):
        fail(f"the provisioning profile has no valid TeamIdentifier (got '{team}').")
    app_id = profile.get("Entitlements", {}).get("application-identifier", "")
    prefix = f"{team}."
    if app_id.startswith(prefix):
        app_id = app_id[len(prefix):]
    else:
        app_id = app_id.split(".", 1)[1] if "." in app_id else app_id
    name = profile.get("Name", "")
    uuid = profile.get("UUID", "")
    if not name or not uuid:
        fail("the provisioning profile has no Name or UUID.")
    return {"team": team, "app_id": app_id, "name": name, "uuid": uuid,
            "method": export_method(profile)}


def find_pbxproj(project_dir):
    projects = sorted(Path(project_dir).glob("*.xcodeproj"))
    if not projects:
        fail(f"no .xcodeproj in {project_dir}")
    pbx = projects[0] / "project.pbxproj"
    if not pbx.is_file():
        fail(f"{pbx} not found")
    return pbx


def load_pbxproj(pbx):
    data = pbx.read_bytes()
    if data.lstrip().startswith(b"<?xml"):
        return plistlib.loads(data)
    # Xcode writes an old-style (OpenStep) plist that plistlib cannot read;
    # plutil converts it, and Xcode reads the XML form back without complaint.
    xml = subprocess.run(["plutil", "-convert", "xml1", "-o", "-", str(pbx)],
                         capture_output=True, check=False).stdout
    if not xml:
        fail(f"could not convert {pbx} with plutil")
    return plistlib.loads(xml)


def bundle_matches(app_id, bundle_id):
    if app_id == "*" or app_id == bundle_id:
        return True
    return app_id.endswith(".*") and bundle_id.startswith(app_id[:-1])


def configure(project, identity, target_name):
    objects = project["objects"]
    targets = {k: v for k, v in objects.items() if v.get("isa") == "PBXNativeTarget"}
    app = [k for k, v in targets.items() if v.get("name") == target_name]
    if not app:
        names = ", ".join(sorted(v.get("name", "?") for v in targets.values()))
        fail(f"target '{target_name}' not found (targets: {names})")

    identity_name = "iPhone Developer" if identity["method"] == "development" else "iPhone Distribution"
    bundle_ids = set()
    for key, target in targets.items():
        config_list = objects[target["buildConfigurationList"]]
        for config_key in config_list.get("buildConfigurations", []):
            settings = objects[config_key].setdefault("buildSettings", {})
            settings["CODE_SIGN_STYLE"] = "Manual"
            settings["DEVELOPMENT_TEAM"] = identity["team"]
            if key in app:
                settings["PROVISIONING_PROFILE_SPECIFIER"] = identity["name"]
                settings["CODE_SIGN_IDENTITY"] = identity_name
                settings["CODE_SIGN_IDENTITY[sdk=iphoneos*]"] = identity_name
                if settings.get("PRODUCT_BUNDLE_IDENTIFIER"):
                    bundle_ids.add(settings["PRODUCT_BUNDLE_IDENTIFIER"])
            else:
                settings.pop("PROVISIONING_PROFILE_SPECIFIER", None)
                settings.pop("PROVISIONING_PROFILE", None)

    if len(bundle_ids) > 1:
        fail(f"target '{target_name}' has several bundle identifiers: {sorted(bundle_ids)}")
    bundle_id = next(iter(bundle_ids), "") or identity["app_id"]
    if not bundle_id or "*" in bundle_id:
        fail("no bundle identifier in the Xcode project and the profile is a wildcard.")
    if not bundle_matches(identity["app_id"], bundle_id):
        fail(f"bundle identifier '{bundle_id}' does not match the provisioning profile "
             f"'{identity['name']}' (application-identifier {identity['app_id']}). "
             "Use a profile for this app, or set the project's bundle identifier.")
    return bundle_id


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--target", default="Unity-iPhone")
    parser.add_argument("--profile-plist", default="")
    parser.add_argument("--export-method", default="",
                        help="Expected export method; fail early if the profile is another kind")
    args = parser.parse_args()

    identity = profile_identity(load_profile(args.profile_plist))
    if args.export_method and args.export_method != identity["method"]:
        fail(f"export method is '{args.export_method}' but the provisioning profile "
             f"'{identity['name']}' is a {identity['method']} profile. Use a matching "
             "profile in this environment, or set ios-export-method.")
    pbx = find_pbxproj(args.project_dir)
    project = load_pbxproj(pbx)
    bundle_id = configure(project, identity, args.target)
    pbx.write_bytes(plistlib.dumps(project, fmt=plistlib.FMT_XML))

    outputs = {
        "development-team": identity["team"],
        "bundle-identifier": bundle_id,
        "profile-name": identity["name"],
        "profile-uuid": identity["uuid"],
        "export-method": identity["method"],
    }
    lines = [f"{k}={v}" for k, v in outputs.items()]
    print("\n".join(lines))
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    print(f"[configure_xcode_signing] {args.target}: team {identity['team']}, "
          f"bundle {bundle_id}, profile '{identity['name']}' ({identity['method']})",
          file=sys.stderr)


if __name__ == "__main__":
    main()
