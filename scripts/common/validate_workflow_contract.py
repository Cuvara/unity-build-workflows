#!/usr/bin/env python3
"""
validate_workflow_contract.py
Validate that a project's BuildConfig satisfies the workflow contract
(required inputs, expected structure, platform-specific constraints).
"""

import argparse
import json
import re
import sys
from pathlib import Path

# ── iOS validation constants ───────────────────────────────────────────────

# Reverse-DNS bundle identifier: at least 2 dot-separated components
# (matches the schema pattern {1,}).  Starts with a letter.
_IOS_BUNDLE_ID_RE = re.compile(
    r"^[a-zA-Z][a-zA-Z0-9_\-]*(\.[a-zA-Z][a-zA-Z0-9_\-]*){1,}$"
)

# Semantic version: MAJOR.MINOR.PATCH
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

# iOS deployment target: MAJOR.MINOR
_IOS_VERSION_RE = re.compile(r"^\d+\.\d+$")

# Minimum supported iOS deployment target
_IOS_MIN_TARGET = (14, 0)

_IOS_SUPPORTED_ARCHITECTURES = {"ARM64", "Universal"}
# "app-store" kept for backward compat with existing templates;
# "app-store-connect" is the canonical new name.
_IOS_SUPPORTED_EXPORT_METHODS = {"app-store", "app-store-connect", "ad-hoc", "development", "enterprise"}
_IOS_SUPPORTED_SIGNING_STYLES = {"manual", "automatic"}

PLATFORM_CONTRACTS: dict[str, dict] = {
    "Android": {
        "required_secrets_hint": [
            "ANDROID_KEYSTORE_BASE64",
            "ANDROID_KEYSTORE_PASS",
            "ANDROID_KEY_ALIAS",
            "ANDROID_KEY_PASS",
        ],
        "required_config_fields": [
            "bundleVersion",
        ],
        "required_nested_fields": {
            "android": ["applicationId", "minSdkVersion", "targetSdkVersion"],
        },
    },
    "iOS": {
        "required_secrets_hint": [
            "IOS_DISTRIBUTION_CERTIFICATE_BASE64",
            "IOS_DISTRIBUTION_CERTIFICATE_PASSWORD",
            "IOS_PROVISIONING_PROFILE_BASE64",
            "APP_STORE_CONNECT_KEY_ID",
            "APP_STORE_CONNECT_ISSUER_ID",
            "APP_STORE_CONNECT_PRIVATE_KEY",
        ],
        # Top-level fields required in the merged config
        "required_config_fields": [],
        "required_nested_fields": {},
        # The 'ios' block is validated separately by _validate_ios_block()
    },
    "Windows64": {
        "required_secrets_hint": [],
        "required_config_fields": ["bundleVersion", "productName"],
        "required_nested_fields": {},
    },
    "WebGL": {
        "required_secrets_hint": [],
        "required_config_fields": ["bundleVersion", "productName"],
        "required_nested_fields": {},
    },
    "Linux64": {
        "required_secrets_hint": [],
        "required_config_fields": ["bundleVersion"],
        "required_nested_fields": {},
    },
    "LinuxServer": {
        "required_secrets_hint": [],
        "required_config_fields": ["bundleVersion"],
        "required_nested_fields": {},
    },
}


def _validate_ios_block(
    ios: dict,
    errors: list,
    warnings: list,
) -> None:
    """
    Validate the contents of the 'ios' BuildConfig block.

    All signing credentials must live in GitHub secrets — this function
    validates only the non-secret build-metadata fields.

    Handles both signingStyle (new) and automaticSigning (legacy boolean).
    Accepts developmentTeam and developmentTeamId as equivalent aliases.
    """
    # ── bundleIdentifier ───────────────────────────────────────────────────
    bundle_id = ios.get("bundleIdentifier", "")
    if not bundle_id:
        errors.append(
            "ios.bundleIdentifier is required and must be a reverse-DNS string "
            "(e.g. com.company.game)."
        )
    elif not _IOS_BUNDLE_ID_RE.match(bundle_id):
        errors.append(
            f"ios.bundleIdentifier '{bundle_id}' is not a valid reverse-DNS identifier. "
            "Expected at least two dot-separated components starting with a letter "
            "(e.g. com.company.game)."
        )

    # ── buildNumber ────────────────────────────────────────────────────────
    build_number = ios.get("buildNumber")
    if build_number is not None:
        if not re.match(r"^\d+$", str(build_number)):
            errors.append(
                f"ios.buildNumber '{build_number}' must be a numeric string (e.g. '42')."
            )

    # ── marketingVersion ───────────────────────────────────────────────────
    marketing_version = ios.get("marketingVersion")
    if marketing_version is not None:
        if not _SEMVER_RE.match(str(marketing_version)):
            errors.append(
                f"ios.marketingVersion '{marketing_version}' must follow semver "
                "MAJOR.MINOR.PATCH format (e.g. '1.2.3')."
            )

    # ── targetOSVersion ────────────────────────────────────────────────────
    target_os = ios.get("targetOSVersion", "14.0")
    if not _IOS_VERSION_RE.match(str(target_os)):
        errors.append(
            f"ios.targetOSVersion '{target_os}' must be in MAJOR.MINOR format (e.g. '14.0')."
        )
    else:
        parts = str(target_os).split(".")
        try:
            major, minor = int(parts[0]), int(parts[1])
            if (major, minor) < _IOS_MIN_TARGET:
                errors.append(
                    f"ios.targetOSVersion '{target_os}' is below the minimum supported "
                    f"deployment target {'.'.join(str(v) for v in _IOS_MIN_TARGET)}."
                )
        except (ValueError, IndexError):
            errors.append(
                f"ios.targetOSVersion '{target_os}' could not be parsed as a version number."
            )

    # ── architecture ───────────────────────────────────────────────────────
    architecture = ios.get("architecture")
    if architecture is not None and architecture not in _IOS_SUPPORTED_ARCHITECTURES:
        errors.append(
            f"ios.architecture '{architecture}' is not supported. "
            f"Supported values: {', '.join(sorted(_IOS_SUPPORTED_ARCHITECTURES))}."
        )

    # ── exportMethod ───────────────────────────────────────────────────────
    export_method = ios.get("exportMethod", "app-store")
    if export_method not in _IOS_SUPPORTED_EXPORT_METHODS:
        errors.append(
            f"ios.exportMethod '{export_method}' is not supported. "
            f"Supported values: {', '.join(sorted(_IOS_SUPPORTED_EXPORT_METHODS))}."
        )

    # ── signingStyle / automaticSigning compatibility ──────────────────────
    # New field: signingStyle (string enum).
    # Legacy field: automaticSigning (boolean) — maps true → "automatic".
    # If both are present, signingStyle takes precedence.
    if "signingStyle" in ios:
        signing_style = ios["signingStyle"]
        if signing_style not in _IOS_SUPPORTED_SIGNING_STYLES:
            errors.append(
                f"ios.signingStyle '{signing_style}' is not valid. "
                f"Supported values: {', '.join(sorted(_IOS_SUPPORTED_SIGNING_STYLES))}."
            )
        else:
            # automaticSigning present + signingStyle contradicts it → warn
            if "automaticSigning" in ios:
                auto = ios["automaticSigning"]
                expected = "automatic" if auto else "manual"
                if signing_style != expected:
                    warnings.append(
                        f"ios.signingStyle='{signing_style}' and "
                        f"ios.automaticSigning={auto} are contradictory. "
                        "signingStyle takes precedence; remove automaticSigning."
                    )
    elif "automaticSigning" in ios:
        # Legacy path: derive signing_style from the boolean
        signing_style = "automatic" if ios["automaticSigning"] else "manual"
    else:
        signing_style = "manual"  # schema default

    # Manual signing requires both provisioningProfileSpecifier and codeSignIdentity
    if signing_style == "manual":
        if not ios.get("provisioningProfileSpecifier"):
            errors.append(
                "ios signingStyle is 'manual' but ios.provisioningProfileSpecifier is missing. "
                "Provide the provisioning profile name or UUID (the actual profile is "
                "supplied via the IOS_PROVISIONING_PROFILE_BASE64 secret)."
            )
        if not ios.get("codeSignIdentity"):
            errors.append(
                "ios signingStyle is 'manual' but ios.codeSignIdentity is missing. "
                "Provide the code signing identity string (e.g. 'iPhone Distribution')."
            )

    # ── uploadToTestFlight advisory ────────────────────────────────────────
    if ios.get("uploadToTestFlight") is True:
        warnings.append(
            "ios.uploadToTestFlight is set to true. This is a request flag only — "
            "the actual TestFlight upload is gated by the workflow deploy step and "
            "requires APP_STORE_CONNECT_KEY_ID and APP_STORE_CONNECT_PRIVATE_KEY "
            "secrets to be configured, plus APP_STORE_CONNECT_ISSUER_ID for a team "
            "key (an individual key has no issuer ID)."
        )

    # ── enableBitcode advisory ─────────────────────────────────────────────
    if ios.get("enableBitcode") is True:
        warnings.append(
            "ios.enableBitcode is true. Bitcode was deprecated by Apple in Xcode 14 "
            "and is no longer accepted by App Store Connect. Set enableBitcode to false "
            "unless you have a specific reason to keep it."
        )


def load_configs(config_path: Path) -> dict:
    """Load and merge all JSON files in config directory."""
    merged = {}
    if config_path.is_file():
        with open(config_path) as f:
            return json.load(f)
    if config_path.is_dir():
        for json_file in sorted(config_path.glob("*.json")):
            try:
                with open(json_file) as f:
                    merged.update(json.load(f))
            except (json.JSONDecodeError, IOError) as e:
                print(f"WARNING: Could not parse {json_file}: {e}", file=sys.stderr)
    return merged


def validate_contract(
    config: dict,
    platform: str,
) -> tuple[list[str], list[str]]:
    """
    Returns (errors, warnings) based on workflow contract.
    """
    errors: list[str] = []
    warnings: list[str] = []

    contract = PLATFORM_CONTRACTS.get(platform)
    if not contract:
        warnings.append(f"Unknown platform '{platform}' — no contract defined")
        return errors, warnings

    # Check required top-level config fields
    for field in contract["required_config_fields"]:
        if field not in config:
            errors.append(f"Workflow contract violation: missing required config field '{field}' for {platform}")

    # Check required nested config fields (e.g. android.applicationId)
    for block_key, fields in contract.get("required_nested_fields", {}).items():
        block = config.get(block_key, {})
        if not isinstance(block, dict):
            errors.append(f"Workflow contract violation: '{block_key}' must be an object in BuildConfig for {platform}")
            continue
        for field in fields:
            if field not in block:
                errors.append(f"Workflow contract violation: missing required config field '{block_key}.{field}' for {platform}")

    # Warn about required secrets (cannot verify at config-read time)
    if contract["required_secrets_hint"]:
        warnings.append(
            f"Ensure the following GitHub secrets are configured for {platform}: "
            + ", ".join(contract["required_secrets_hint"])
        )

    # iOS-specific: validate the iOS BuildConfig block.
    # 'iOS' is the canonical key; 'ios' is the legacy alias — check canonical first.
    if platform == "iOS":
        ios_block = config.get("iOS") or config.get("ios")
        used_key = "iOS" if "iOS" in config else ("ios" if "ios" in config else None)
        if ios_block is None:
            errors.append(
                "Workflow contract violation: 'iOS' block is required in BuildConfig for iOS builds. "
                "Add an 'iOS' key (or legacy 'ios') with at minimum bundleIdentifier."
            )
        elif not isinstance(ios_block, dict):
            errors.append(
                f"Workflow contract violation: '{used_key}' key in BuildConfig must be an object."
            )
        else:
            if used_key == "ios":
                warnings.append(
                    "BuildConfig uses the legacy 'ios' key — migrate to 'iOS' (canonical)."
                )
            _validate_ios_block(ios_block, errors, warnings)

    # Check Unity version compatibility hint
    unity_version = config.get("unity_version", "")
    if unity_version:
        parts = unity_version.split(".")
        try:
            year = int(parts[0])
            if year < 2021:
                warnings.append(
                    f"Unity {unity_version} may not support all workflow features. "
                    "Recommend Unity 2021 LTS or newer."
                )
        except (ValueError, IndexError):
            warnings.append(f"unity_version '{unity_version}' does not look like a valid Unity version")

    return errors, warnings


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Unity workflow contract")
    parser.add_argument("--config-path", default=None, help="BuildConfig directory or file")
    parser.add_argument("--platform", default=None, help="Target platform")
    # Args passed by the validate workflow (alternative naming)
    parser.add_argument("--project-path", default=None, help="Unity project root path")
    parser.add_argument("--build-config-path", default=None, help="BuildConfig path relative to project-path")
    parser.add_argument("--target-platform", default=None, help="Target platform (alias for --platform)")
    parser.add_argument("--environment", default=None, help="Build environment (informational)")
    parser.add_argument("--output", default="", help="Write warnings to this file")
    args = parser.parse_args()

    # Resolve platform from either --platform or --target-platform
    platform = args.platform or args.target_platform
    if not platform:
        print("ERROR: --platform or --target-platform is required", file=sys.stderr)
        sys.exit(1)

    # Resolve config path: --config-path takes precedence, else project-path/build-config-path
    if args.config_path:
        config_path = Path(args.config_path)
    elif args.project_path and args.build_config_path:
        config_path = Path(args.project_path) / args.build_config_path
    elif args.project_path:
        config_path = Path(args.project_path) / "BuildConfig"
    else:
        print("ERROR: --config-path or --project-path is required", file=sys.stderr)
        sys.exit(1)
    try:
        config = load_configs(config_path)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"ERROR: Could not load config: {e}", file=sys.stderr)
        sys.exit(1)

    errors, warnings = validate_contract(config, platform)

    output_lines = []
    if errors:
        for e in errors:
            line = f"ERROR: {e}"
            print(line, file=sys.stderr)
            output_lines.append(line)
    if warnings:
        for w in warnings:
            line = f"WARNING: {w}"
            print(line, file=sys.stderr)
            output_lines.append(line)

    if not errors and not warnings:
        print(f"OK: Workflow contract satisfied for {platform}")

    if args.output and output_lines:
        Path(args.output).write_text("\n".join(output_lines) + "\n")

    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
