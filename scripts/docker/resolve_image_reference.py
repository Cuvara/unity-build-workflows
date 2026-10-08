#!/usr/bin/env python3
"""
resolve_image_reference.py
Resolve a target platform to an image variant and full image reference.

Responsibilities:
- Map build target platform → Docker image variant
- Load and parse image manifest (from file or registry)
- Enforce immutable digest references in release mode
- Validate image contract version compatibility
- Reject macOS-executor targets (iOS) with executor-routing guidance
- Reject explicitly unsupported targets (Windows64) with clear errors

Usage (standalone):
  python3 scripts/docker/resolve_image_reference.py \
    --target-platform Android \
    --unity-version 2022.3.21f1 \
    --image-namespace myorg \
    [--image-registry ghcr.io] \
    [--manifest-path .docker/image-manifest.json] \
    [--release-mode] \
    [--image-variant android] \
    [--image-digest sha256:...]

  # Legacy: --registry still accepted as a convenience passthrough (combined prefix)
  python3 scripts/docker/resolve_image_reference.py \
    --target-platform Android \
    --unity-version 2022.3.21f1 \
    --registry ghcr.io/myorg

BREAKING CHANGE (v2): prefer --image-namespace (required) + --image-registry (optional,
default ghcr.io) over the legacy --registry combined-prefix arg.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Constants ──────────────────────────────────────────────────────────────

IMAGE_CONTRACT_VERSION = "1"

# Platform → variant mapping
PLATFORM_VARIANT_MAP: Dict[str, str] = {
    "Android": "android",
    "WebGL": "webgl",
    "StandaloneLinux64": "linux",
    "LinuxServer": "linux",
    # Supported variants without a specific platform target
    "Linux64": "linux",
}

# Platforms that must use the macOS executor — Docker image resolution is not
# applicable.  These are NOT "unsupported builds"; they have their own
# executor path.  Callers that reach this Docker-path script for these
# platforms have made an executor routing error.
MACOS_EXECUTOR_PLATFORMS: Dict[str, str] = {
    "iOS": (
        "iOS requires the macos-unity-xcode executor, not a Docker image. "
        "Route iOS builds through the ios-build.yml workflow on an approved macOS runner. "
        "Use scripts/common/resolve_platform_executor.py to determine the correct executor."
    ),
}

# Platforms that are explicitly unsupported on ALL executors.
UNSUPPORTED_PLATFORMS: Dict[str, str] = {
    "Windows64": (
        "Windows64 IL2CPP cross-compilation is not yet available in Docker. "
        "Use the windows-build runner or a Windows agent."
    ),
}

SUPPORTED_VARIANTS = {"base", "android", "webgl", "linux"}

# Digest must be exactly sha256: followed by 64 lowercase hex characters.
DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

# Minimum image contract version this tooling can consume
MIN_CONTRACT_VERSION = 1


# ── Image manifest helpers ─────────────────────────────────────────────────

def load_manifest_from_file(manifest_path: Path) -> Dict[str, Any]:
    """Load image manifest JSON from a local file."""
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Image manifest not found: {manifest_path}")
    try:
        with open(manifest_path) as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in image manifest {manifest_path}: {exc}") from exc


def load_manifest_from_label(image_ref: str) -> Optional[Dict[str, Any]]:
    """
    Attempt to pull image manifest data from the Docker image label
    'org.unity.build.manifest'.  Returns None if docker is unavailable or
    the label is absent.
    """
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format",
             "{{index .Config.Labels \"org.unity.build.manifest\"}}", image_ref],
            capture_output=True,
            text=True,
            timeout=30,
        )
        label_value = result.stdout.strip()
        if label_value:
            return json.loads(label_value)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return None


# ── Validation helpers ─────────────────────────────────────────────────────

def validate_contract_version(manifest: Dict[str, Any]) -> None:
    """Raise if the manifest's contract version is incompatible."""
    contract_version_str = manifest.get("image_contract_version", "1")
    try:
        contract_version = int(str(contract_version_str).split(".")[0])
    except ValueError:
        raise ValueError(
            f"image_contract_version '{contract_version_str}' is not a valid integer."
        )
    if contract_version < MIN_CONTRACT_VERSION:
        raise ValueError(
            f"Image contract version {contract_version} is below the minimum "
            f"required version {MIN_CONTRACT_VERSION}. Rebuild the image."
        )


def enforce_immutable_reference(
    image_ref: str,
    digest: Optional[str],
    release_mode: bool,
) -> str:
    """
    In release mode, reject mutable tags (latest, edge, etc.) and require a digest.
    Returns the pinned image reference (with digest appended if provided separately).
    """
    if not release_mode:
        return image_ref

    # Validate format of --image-digest when provided.
    if digest and not DIGEST_PATTERN.match(digest):
        raise ValueError(
            f"--image-digest '{digest}' is not a valid image digest. "
            "Must match sha256:[0-9a-f]{64} (64 lowercase hex characters). "
            "Example: sha256:abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789ab"
        )

    # A digest reference contains '@sha256:'
    has_digest_in_ref = "@sha256:" in image_ref
    has_separate_digest = bool(digest)

    if not has_digest_in_ref and not has_separate_digest:
        raise ValueError(
            "release-mode requires an immutable image reference. "
            "Provide --image-digest sha256:<hash> or use an @sha256: pinned reference."
        )

    # Reject obviously mutable tags
    mutable_tags = {"latest", "edge", "main", "master", "dev", "development", "staging"}
    if ":" in image_ref and "@" not in image_ref:
        tag = image_ref.rsplit(":", 1)[-1]
        if tag.lower() in mutable_tags:
            raise ValueError(
                f"release-mode forbids mutable tag '{tag}'. "
                "Pin the image with a digest (@sha256:...) instead."
            )

    # Append digest if provided separately and not already embedded
    if has_separate_digest and not has_digest_in_ref:
        # Strip any existing tag before appending digest
        base = image_ref.split(":")[0] if ":" in image_ref else image_ref
        return f"{base}@{digest}"

    return image_ref


# ── Core resolution logic ──────────────────────────────────────────────────

def resolve(
    target_platform: str,
    unity_version: str,
    registry: str,
    image_name: str = "unity-build",
    image_variant: Optional[str] = None,
    image_digest: Optional[str] = None,
    manifest_path: Optional[Path] = None,
    release_mode: bool = False,
) -> Dict[str, Any]:
    """
    Resolve target platform to a full Docker image reference.

    Returns a dict with keys:
      image_ref       – full pullable image reference
      digest          – sha256 digest (if known)
      variant         – image variant name (android/webgl/linux/base)
      supported_targets – list of platforms this image supports
      unity_version   – Unity version embedded in image
    """
    # 1a. Reject macOS-executor platforms — they are not unsupported builds, but
    #     they must NOT use a Docker image.  Give the caller a routing hint.
    if target_platform in MACOS_EXECUTOR_PLATFORMS:
        raise ValueError(
            f"Platform '{target_platform}' does not use the Docker executor. "
            + MACOS_EXECUTOR_PLATFORMS[target_platform]
        )

    # 1b. Reject explicitly unsupported platforms with clear guidance
    if target_platform in UNSUPPORTED_PLATFORMS:
        raise ValueError(
            f"Platform '{target_platform}' is not supported in Docker builds. "
            + UNSUPPORTED_PLATFORMS[target_platform]
        )

    # 2. Resolve variant
    if image_variant:
        variant = image_variant
        if variant not in SUPPORTED_VARIANTS:
            raise ValueError(
                f"Unknown image variant '{variant}'. "
                f"Supported variants: {', '.join(sorted(SUPPORTED_VARIANTS))}"
            )
    else:
        variant = PLATFORM_VARIANT_MAP.get(target_platform)
        if variant is None:
            raise ValueError(
                f"No Docker variant mapping for platform '{target_platform}'. "
                f"Supported platforms: {', '.join(sorted(PLATFORM_VARIANT_MAP.keys()))}. "
                "Use --image-variant to specify manually."
            )

    # 3. Load manifest (file takes priority, then registry label probe)
    manifest: Optional[Dict[str, Any]] = None
    if manifest_path:
        manifest = load_manifest_from_file(manifest_path)
    else:
        # Try a default location
        default_manifest = Path(".docker/image-manifest.json")
        if default_manifest.is_file():
            manifest = load_manifest_from_file(default_manifest)

    # 4. Build image tag from manifest or convention
    sanitized_version = unity_version.replace(".", "-")
    default_tag = f"{unity_version}-{variant}"

    if manifest:
        validate_contract_version(manifest)
        images = manifest.get("images", {})

        # Fail fast if the requested variant is not in the manifest.
        # Silently falling back would produce a build-time failure inside Unity
        # with no diagnostic — this error surfaces the problem immediately.
        if images and variant not in images:
            available = sorted(images.keys())
            raise ValueError(
                f"Image variant '{variant}' (required for platform '{target_platform}') "
                f"is not present in the image manifest. "
                f"Available variants: {', '.join(available) if available else '(none)'}. "
                "Build or push the missing variant first, then update the manifest."
            )

        variant_info = images.get(variant, {})
        tag = variant_info.get("tag", default_tag)
        digest_from_manifest = variant_info.get("digest", "")
        supported_targets = variant_info.get("supported_targets", [target_platform])
        embedded_unity_version = variant_info.get("unity_version", unity_version)
        registry_from_manifest = manifest.get("registry", registry)

        # Validate unity version match
        if embedded_unity_version and embedded_unity_version != unity_version:
            raise ValueError(
                f"Unity version mismatch: requested {unity_version} but "
                f"manifest specifies {embedded_unity_version} for variant '{variant}'. "
                "Rebuild the image or update the manifest."
            )

        # Use digest from manifest if not provided explicitly
        if not image_digest and digest_from_manifest:
            image_digest = digest_from_manifest

        image_ref = f"{registry_from_manifest}/{image_name}:{tag}"
    else:
        # No manifest — build reference from convention
        image_ref = f"{registry}/{image_name}:{default_tag}"
        supported_targets = list(
            k for k, v in PLATFORM_VARIANT_MAP.items() if v == variant
        )

    # 5. Enforce immutability in release mode
    image_ref = enforce_immutable_reference(image_ref, image_digest, release_mode)

    return {
        "image_ref": image_ref,
        "digest": image_digest or "",
        "variant": variant,
        "supported_targets": supported_targets,
        "unity_version": unity_version,
    }


# ── CLI entry point ────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resolve a Unity build target platform to a Docker image reference.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--target-platform", required=True,
                        help="Unity target platform (Android, WebGL, StandaloneLinux64, …)")
    parser.add_argument("--unity-version", required=True,
                        help="Unity Editor version (e.g. 2022.3.21f1)")
    # New split args (v2). --image-namespace is required; --image-registry defaults to ghcr.io.
    parser.add_argument("--image-registry", default="ghcr.io",
                        help=(
                            "Docker registry hostname (default: ghcr.io). "
                            "Combined with --image-namespace: <registry>/<namespace>."
                        ))
    parser.add_argument("--image-namespace", default=None,
                        help=(
                            "REQUIRED (unless --registry is used). "
                            "Container registry namespace / organisation (e.g. myorg)."
                        ))
    # Legacy passthrough: accept --registry <host>/<ns> for callers (e.g. run_unity_container.py)
    # that already build the combined prefix.
    parser.add_argument("--registry", default=None,
                        help=(
                            "Combined registry prefix (e.g. ghcr.io/myorg). "
                            "Deprecated in favour of --image-registry + --image-namespace."
                        ))
    parser.add_argument("--image-name", default="unity-build",
                        help=(
                            "Image repository name (default: unity-build). "
                            "Overrides the hardcoded name component of the image reference: "
                            "<registry>/<namespace>/<image-name>:<tag>."
                        ))
    parser.add_argument("--image-variant",
                        help="Force a specific image variant (base/android/webgl/linux)")
    # --variant / --digest: short aliases (the former resolve-unity-image action used them)
    parser.add_argument("--variant", dest="image_variant",
                        help="Alias for --image-variant")
    parser.add_argument("--image-digest",
                        help="Expected sha256 digest for pinning (e.g. sha256:abc123…)")
    parser.add_argument("--digest", dest="image_digest",
                        help="Alias for --image-digest")
    parser.add_argument("--manifest-path",
                        help="Path to image manifest JSON file")
    # --release-mode accepts an optional boolean string value ("true"/"false") or acts
    # as a store_true flag when called without a value.  The action passes the value as
    # a string (e.g. --release-mode false) so we use nargs='?' to handle both forms.
    parser.add_argument("--release-mode", nargs="?", const="true", default="false",
                        help="Enforce immutable digest references ('true'/'false', default false)")
    parser.add_argument("--output-json", action="store_true",
                        help="Output result as JSON (machine-readable)")
    # --output-format github-actions writes outputs to $GITHUB_OUTPUT
    parser.add_argument("--output-format", choices=["github-actions", "json", "text"],
                        default=None,
                        help="Output format: github-actions (writes $GITHUB_OUTPUT), json, or text")
    args = parser.parse_args()

    # Build the combined registry prefix.
    # --registry (legacy combined form) takes precedence if provided.
    # Otherwise require --image-namespace and combine with --image-registry.
    if args.registry:
        registry = args.registry.lower()
    elif args.image_namespace:
        registry = f"{args.image_registry}/{args.image_namespace}".lower()
    else:
        raise ValueError(
            "image namespace is required; pass --image-namespace <org/namespace> "
            "(no game/org-specific default is baked in)."
        )

    manifest_path = Path(args.manifest_path) if args.manifest_path else None

    # Normalise --release-mode: accept "true"/"false" string or bare flag
    release_mode_val = args.release_mode
    if isinstance(release_mode_val, str):
        release_mode = release_mode_val.lower() in ("true", "1", "yes")
    else:
        release_mode = bool(release_mode_val)

    # Normalise --image-name: treat empty string as absent (fall back to default)
    image_name = args.image_name if args.image_name else "unity-build"

    use_github_output = args.output_format == "github-actions"
    use_json = args.output_json or args.output_format == "json"

    try:
        result = resolve(
            target_platform=args.target_platform,
            unity_version=args.unity_version,
            registry=registry,
            image_name=image_name,
            image_variant=args.image_variant,
            image_digest=args.image_digest,
            manifest_path=manifest_path,
            release_mode=release_mode,
        )
    except (ValueError, FileNotFoundError) as exc:
        if use_json:
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if use_github_output:
        # Write outputs to $GITHUB_OUTPUT for use in subsequent action steps
        github_output_path = os.environ.get("GITHUB_OUTPUT", "")
        supported = ",".join(result["supported_targets"])
        if github_output_path:
            with open(github_output_path, "a") as f:
                f.write(f"image-reference={result['image_ref']}\n")
                f.write(f"image-digest={result['digest']}\n")
                f.write(f"image-variant={result['variant']}\n")
                f.write(f"supported-targets={supported}\n")
        # Also print to stdout for visibility in logs
        print(f"[resolve_image_reference] image-reference={result['image_ref']}")
        print(f"[resolve_image_reference] image-variant={result['variant']}")
        print(f"[resolve_image_reference] supported-targets={supported}")
        if result["digest"]:
            print(f"[resolve_image_reference] image-digest={result['digest']}")
    elif use_json:
        print(json.dumps(result, indent=2))
    else:
        print(f"[resolve_image_reference] Resolved:")
        print(f"  Platform  : {args.target_platform}")
        print(f"  Variant   : {result['variant']}")
        print(f"  Image ref : {result['image_ref']}")
        if result["digest"]:
            print(f"  Digest    : {result['digest']}")
        print(f"  Supported : {', '.join(result['supported_targets'])}")


if __name__ == "__main__":
    main()
