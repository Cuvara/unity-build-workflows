#!/usr/bin/env python3
"""
run_unity_container.py
THE single entry point for running Unity inside Docker.

Both CI (GitHub Actions) and local developer workflows use this script.
It resolves the correct image, constructs a hardened `docker run` command,
manages cache volumes, injects credentials via environment variables (never
command-line flags), runs the container, and exports logs/reports.

Usage:
  python3 scripts/docker/run_unity_container.py \\
    --project-path . \\
    --unity-version 2022.3.21f1 \\
    --target-platform Android \\
    --environment development \\
    --build-config-path BuildConfig/ \\
    --output-path ./build/android \\
    --image-namespace myorg \\
    [--image-registry ghcr.io] \\
    [--image-variant android] \\
    [--image-digest sha256:...] \\
    [--cache-mode safe] \\
    [--release-mode] \\
    [--dry-run]

BREAKING CHANGE (v2): --image-registry now takes only the registry hostname
(e.g. ghcr.io). The namespace/organisation component is now a separate REQUIRED
arg --image-namespace (e.g. myorg). Full image prefix = <registry>/<namespace>.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Constants ──────────────────────────────────────────────────────────────

CACHE_SCHEMA_VERSION = "v1"
DEFAULT_REGISTRY_HOST = "ghcr.io"  # Hostname only; org/namespace is --image-namespace (required)
DEFAULT_TIMEOUT = 3600  # seconds

# Platforms that must not run through this Docker-invocation path.
#
# iOS IMPORTANT: iOS native invocation is permitted ONLY via the approved
# macos-unity-xcode workflow components (ios-build.yml and its composite
# actions).  This Docker runner must never attempt to execute Unity for iOS,
# even on a macOS host.  The macOS workflow handles Unity IL2CPP compilation,
# Xcode archive, signing, and IPA export in its own tightly controlled steps.
#
# The error message for iOS is the exact contract string checked by
# resolve_platform_executor.py and downstream gating scripts.
DOCKER_UNSUPPORTED_PLATFORMS = {
    "iOS": (
        "Target `iOS` requires an approved macOS runner with Xcode and "
        "Unity iOS Build Support. Linux Docker execution is not supported."
    ),
    "Windows64": (
        "Windows64 IL2CPP cross-compilation is not yet supported in Docker. "
        "Use a Windows runner or the windows-build.yml workflow."
    ),
}

# Secret environment variable names that are never printed
SECRET_ENV_VARS = {
    "UNITY_LICENSE",
    "UNITY_EMAIL",
    "UNITY_PASSWORD",
    "ANDROID_KEYSTORE_BASE64",
    "ANDROID_KEYSTORE_PASS",
    "ANDROID_KEY_ALIAS",
    "ANDROID_KEY_PASS",
}


# ── Helpers ────────────────────────────────────────────────────────────────

def _abort(message: str, exit_code: int = 1) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(exit_code)


def _log(message: str) -> None:
    print(f"[run_unity_container] {message}", flush=True)


def _sanitize_volume_name(s: str) -> str:
    """Replace characters not safe for Docker volume names with dashes."""
    return re.sub(r"[^a-zA-Z0-9_.-]", "-", s)


def _cache_volume_name(project_path: Path, unity_version: str, platform: str) -> str:
    """
    Deterministic cache volume name, unique per:
      repo root + unity version + platform + cache schema version.
    Format: unity-lib-<hash8>-<version>-<platform>-<schema>
    """
    canon = str(project_path.resolve())
    fingerprint = hashlib.sha256(canon.encode()).hexdigest()[:8]
    safe_version = _sanitize_volume_name(unity_version)
    safe_platform = _sanitize_volume_name(platform.lower())
    return f"unity-lib-{fingerprint}-{safe_version}-{safe_platform}-{CACHE_SCHEMA_VERSION}"


def _gradle_cache_volume_name(project_path: Path) -> str:
    """Named volume for the Gradle/Android user cache."""
    canon = str(project_path.resolve())
    fingerprint = hashlib.sha256(canon.encode()).hexdigest()[:8]
    return f"unity-gradle-{fingerprint}-{CACHE_SCHEMA_VERSION}"


def _require_docker() -> str:
    """Return docker executable path or abort."""
    docker = shutil.which("docker")
    if not docker:
        _abort(
            "docker executable not found in PATH. "
            "Install Docker Desktop or Docker Engine and ensure it is running."
        )
    return docker  # type: ignore[return-value]


def _docker_version_ok(docker: str) -> None:
    """Warn if Docker version is below a known-good baseline."""
    try:
        result = subprocess.run(
            [docker, "version", "--format", "{{.Client.Version}}"],
            capture_output=True, text=True, timeout=10,
        )
        version_str = result.stdout.strip()
        parts = version_str.split(".")
        major = int(parts[0]) if parts else 0
        if major < 20:
            print(
                f"WARNING: Docker {version_str} is old. Recommend Docker 20+ for full feature support.",
                file=sys.stderr,
            )
    except Exception:
        pass  # Non-fatal — just a hint


def _resolve_image(args: argparse.Namespace) -> Dict:
    """
    Delegate image resolution to resolve_image_reference.py.
    Returns the resolution dict.
    """
    # Build full registry prefix: <registry-host>/<namespace>
    registry_prefix = f"{args.image_registry}/{args.image_namespace}"
    resolver = Path(__file__).parent / "resolve_image_reference.py"
    cmd = [
        sys.executable, str(resolver),
        "--target-platform", args.target_platform,
        "--unity-version", args.unity_version,
        "--registry", registry_prefix,
        "--output-json",
    ]
    if args.image_name and args.image_name != "unity-build":
        cmd += ["--image-name", args.image_name]
    if args.image_variant:
        cmd += ["--image-variant", args.image_variant]
    if args.image_digest:
        cmd += ["--image-digest", args.image_digest]
    if args.manifest_path:
        cmd += ["--manifest-path", args.manifest_path]
    if args.release_mode:
        cmd += ["--release-mode"]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        _abort("Image resolution timed out after 30 s.")

    if result.returncode != 0:
        stderr = result.stderr.strip()
        _abort(f"Image resolution failed:\n{stderr}")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        _abort(f"Could not parse image resolution output: {exc}")
    return {}  # unreachable


def _validate_image(docker: str, image_ref: str, args: argparse.Namespace) -> None:
    """
    Run validate_unity_image.py to confirm the resolved image is healthy.
    Only fails the build for hard errors; warnings are printed.
    """
    validator = Path(__file__).parent / "validate_unity_image.py"
    if not validator.is_file():
        return

    cmd = [
        sys.executable, str(validator),
        "--image-ref", image_ref,
        "--target-platform", args.target_platform,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.stdout:
            for line in result.stdout.splitlines():
                _log(f"[validate] {line}")
        if result.returncode != 0:
            stderr = result.stderr.strip()
            _abort(f"Image validation failed:\n{stderr}\nFix or rebuild the image before proceeding.")
    except subprocess.TimeoutExpired:
        print("WARNING: Image validation timed out — skipping.", file=sys.stderr)


def _ensure_output_dirs(args: argparse.Namespace) -> Tuple[Path, Path, Path]:
    """Create host-side output, reports, and log directories."""
    output_path = Path(args.output_path).resolve()
    reports_path = output_path.parent / "BuildReports"
    logs_path = output_path.parent / "Logs"
    for d in (output_path, reports_path, logs_path):
        d.mkdir(parents=True, exist_ok=True)
    return output_path, reports_path, logs_path


def _build_docker_command(
    docker: str,
    image_ref: str,
    args: argparse.Namespace,
    output_path: Path,
    reports_path: Path,
    logs_path: Path,
    cache_volume: str,
    env_vars: Dict[str, str],
) -> List[str]:
    """
    Construct the full `docker run` command.
    Secrets are passed via -e NAME (value pulled from environment at runtime),
    never embedded in the command list.
    """
    project_path = Path(args.project_path).resolve()

    cmd: List[str] = [docker, "run"]

    # ── Lifecycle ──────────────────────────────────────────────────────────
    cmd += ["--rm", "--init"]

    # ── User mapping (rootless-compatible) ────────────────────────────────
    try:
        uid = os.getuid()
        gid = os.getgid()
        cmd += ["--user", f"{uid}:{gid}"]
    except AttributeError:
        # Windows fallback — no getuid
        pass

    # ── Working directory ─────────────────────────────────────────────────
    cmd += ["--workdir", "/workspace"]

    # ── Bind mounts ───────────────────────────────────────────────────────
    cmd += [
        "--mount", f"type=bind,source={project_path},target=/workspace,readonly=false",
        "--mount", f"type=bind,source={output_path},target=/workspace/Builds",
        "--mount", f"type=bind,source={reports_path},target=/workspace/BuildReports",
        "--mount", f"type=bind,source={logs_path},target=/workspace/Logs",
    ]

    # ── Unity license directory (pre-activated on host) ───────────────
    # Mount the host's Unity license directory so the container can use
    # licenses activated by buildalon/activate-unity-license or similar.
    unity_license_dir = os.path.expanduser("~/.local/share/unity3d")
    if os.path.isdir(unity_license_dir):
        cmd += [
            "--mount", f"type=bind,source={unity_license_dir},target=/tmp/unity-home/.local/share/unity3d,readonly",
        ]

    # ── Library cache volume ───────────────────────────────────────────────
    cache_mode = args.cache_mode.lower()
    if cache_mode != "off":
        cmd += [
            "--mount", f"type=volume,source={cache_volume},target=/workspace/Library",
        ]

    # ── Gradle cache (Android only) ────────────────────────────────────────
    if args.target_platform == "Android":
        gradle_vol = _gradle_cache_volume_name(Path(args.project_path))
        cmd += [
            "--mount", f"type=volume,source={gradle_vol},target=/root/.gradle",
        ]

    # ── Environment variables (non-secret) ────────────────────────────────
    cmd += [
        "-e", f"BUILD_CONFIG={args.build_config_path}",
        "-e", f"ENVIRONMENT={args.environment}",
        "-e", f"TARGET_PLATFORM={args.target_platform}",
        "-e", f"UNITY_VERSION={args.unity_version}",
        "-e", f"BUILD_OUTPUT=/workspace/Builds",
    ]

    if args.build_addressables:
        cmd += ["-e", "BUILD_ADDRESSABLES=true"]
    if args.clean_build:
        cmd += ["-e", "CLEAN_BUILD=true"]
    if args.test_level:
        cmd += ["-e", f"TEST_LEVEL={args.test_level}"]
    if args.release_mode:
        cmd += ["-e", "RELEASE_MODE=true"]
    if args.cache_mode:
        cmd += ["-e", f"CACHE_MODE={args.cache_mode}"]

    # ── Secret environment variables (injected by name only — never printed) ─
    # The values come from the caller's environment; we just tell Docker which
    # names to forward.  If a required secret is absent we warn but continue —
    # Unity itself will surface the licensing error with a better message.
    for secret_var in ["UNITY_LICENSE", "UNITY_EMAIL", "UNITY_PASSWORD"]:
        if os.environ.get(secret_var):
            cmd += ["-e", secret_var]
        # else: Docker will not set the var (not fail)

    # Activation strategy override (not a secret — safe to log)
    if os.environ.get("UNITY_ACTIVATION_STRATEGY"):
        cmd += ["-e", f"UNITY_ACTIVATION_STRATEGY={os.environ['UNITY_ACTIVATION_STRATEGY']}"]

    # Additional non-secret env pass-throughs provided by caller
    for key, value in env_vars.items():
        if key not in SECRET_ENV_VARS:
            cmd += ["-e", f"{key}={value}"]

    # ── Security hardening ─────────────────────────────────────────────────
    # Never use --privileged, never mount docker.sock
    cmd += [
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
    ]

    # ── Resource limits ────────────────────────────────────────────────────
    if args.cpus:
        cmd += ["--cpus", str(args.cpus)]
    if args.memory:
        cmd += ["--memory", args.memory]

    # ── Container timeout ─────────────────────────────────────────────────
    timeout = args.container_timeout or DEFAULT_TIMEOUT
    cmd += ["--stop-timeout", str(timeout)]

    # ── Image ─────────────────────────────────────────────────────────────
    cmd += [image_ref]

    # ── Entrypoint command + arguments ────────────────────────────────────
    # The container ENTRYPOINT is entrypoint.sh; the first positional arg is
    # the command (build, build-addressables, validate, test-editmode, etc.).
    # Without this, the container falls back to CMD ["inspect"] and produces
    # no build output.
    cmd += [args.command]
    if args.target_platform:
        cmd += ["--target-platform", args.target_platform]
    if args.build_config_path:
        cmd += ["--build-config", args.build_config_path]
    if args.environment:
        cmd += ["--environment", args.environment]
    cmd += ["--output-path", "/workspace/Builds"]
    cmd += ["--log-dir", "/workspace/Logs"]

    return cmd


def _safe_command_repr(cmd: List[str]) -> str:
    """
    Return a printable representation of the command with secret env values
    masked.  Only the -e NAME=VALUE form leaks values; -e NAME (no value)
    is safe and printed as-is.
    """
    parts = []
    i = 0
    while i < len(cmd):
        token = cmd[i]
        if token == "-e" and i + 1 < len(cmd):
            env_token = cmd[i + 1]
            if "=" in env_token:
                name, _ = env_token.split("=", 1)
                if name in SECRET_ENV_VARS:
                    parts += ["-e", f"{name}=***"]
                else:
                    parts += ["-e", env_token]
            else:
                # -e NAME without value — safe
                parts += ["-e", env_token]
            i += 2
        else:
            parts.append(token)
            i += 1
    return " \\\n  ".join(parts)


def _stream_process(proc: "subprocess.Popen[str]") -> int:
    """Stream container stdout/stderr to our stdout in real time."""
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(line)
        sys.stdout.flush()
    proc.wait()
    return proc.returncode


def _print_failure_guidance(exit_code: int, target_platform: str, logs_path: Path) -> None:
    print("", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print(f"  Unity container exited with code {exit_code}", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    print("Troubleshooting steps:", file=sys.stderr)
    print(f"  1. Check Unity logs in: {logs_path}", file=sys.stderr)
    print("  2. Ensure UNITY_LICENSE / UNITY_EMAIL / UNITY_PASSWORD are set.", file=sys.stderr)
    if target_platform == "Android":
        print("  3. Verify Android SDK/NDK paths inside the image.", file=sys.stderr)
    print("  4. Re-run with --dry-run to inspect the full docker command.", file=sys.stderr)
    print("  5. Run validate_unity_image.py to check image health.", file=sys.stderr)
    print("", file=sys.stderr)


# ── Argument parsing ───────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a Unity build inside a Docker container.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Local development build
  python3 scripts/docker/run_unity_container.py \\
    --project-path . --unity-version 2022.3.21f1 \\
    --target-platform Android --environment development \\
    --image-namespace myorg

  # CI release build with pinned digest
  python3 scripts/docker/run_unity_container.py \\
    --project-path . --unity-version 2022.3.21f1 \\
    --target-platform Android --environment production \\
    --image-namespace myorg \\
    --release-mode --image-digest sha256:abc123...

  # Dry run to inspect the docker command
  python3 scripts/docker/run_unity_container.py \\
    --project-path . --unity-version 2022.3.21f1 \\
    --target-platform WebGL \\
    --image-namespace myorg --dry-run
""",
    )

    # ── Required ───────────────────────────────────────────────────────────
    parser.add_argument("--project-path", required=True,
                        help="Unity project root directory (host path)")
    parser.add_argument("--unity-version", required=True,
                        help="Unity Editor version string (e.g. 2022.3.21f1)")
    parser.add_argument("--target-platform", required=True,
                        help="Build target: Android | WebGL | StandaloneLinux64 | LinuxServer")

    # ── Build config ───────────────────────────────────────────────────────
    parser.add_argument("--environment", default="development",
                        choices=["development", "staging", "production"],
                        help="Build environment")
    parser.add_argument("--build-config-path", default="BuildConfig",
                        help="Path to BuildConfig directory or JSON file (host path)")
    parser.add_argument("--output-path", default="./build",
                        help="Host directory where build artefacts will be written")
    # The action passes --command to select the Unity operation (build, test-*, etc.).
    # The value is forwarded to the Unity invocation layer; unknown values are
    # passed through and validated at Unity startup rather than here.
    parser.add_argument("--command", default="build",
                        help=(
                            "Unity operation to run "
                            "(build, test-editmode, test-playmode, validate, "
                            "build-addressables). Passed through to Unity."
                        ))

    # ── Image selection ────────────────────────────────────────────────────
    parser.add_argument("--image",
                        default=None,
                        help=(
                            "Pre-resolved full image reference "
                            "(<registry>/<namespace>/<name>:<tag> or @sha256:...). "
                            "When provided, internal resolution is skipped and "
                            "--image-namespace is not required (a caller that already "
                            "resolved the reference, e.g. with resolve_image_reference.py)."
                        ))
    parser.add_argument("--image-registry", default=DEFAULT_REGISTRY_HOST,
                        help=(
                            "Docker registry hostname only "
                            f"(default: {DEFAULT_REGISTRY_HOST}). "
                            "Combined with --image-namespace to form the full image prefix."
                        ))
    parser.add_argument("--image-namespace",
                        default=None,
                        help=(
                            "REQUIRED. Container registry namespace / organisation "
                            "(e.g. myorg). Full image prefix = <registry>/<namespace>."
                        ))
    parser.add_argument("--image-name", default="unity-build",
                        help=(
                            "Image repository name (default: unity-build). "
                            "Overrides the name component: <registry>/<namespace>/<image-name>:<tag>."
                        ))
    parser.add_argument("--image-variant",
                        help="Force image variant (base/android/webgl/linux)")
    parser.add_argument("--image-digest",
                        help="Immutable sha256 digest for pinning (sha256:...)")
    parser.add_argument("--manifest-path",
                        help="Path to local image-manifest.json")

    # ── Cache ──────────────────────────────────────────────────────────────
    parser.add_argument("--cache-mode", default="safe",
                        choices=["off", "safe", "aggressive"],
                        help=(
                            "Library cache strategy: "
                            "off=no cache, safe=named volume, aggressive=aggressive retention"
                        ))
    # --clean-build is a boolean flag that the action may pass as a string
    # "true" or "false" (GitHub Actions bool inputs → string env vars).
    # nargs='?' lets the flag appear with no value (const=True) or with an
    # explicit "true"/"false" string value from the action's shell env block.
    parser.add_argument("--clean-build",
                        nargs="?", const=True, default=False,
                        type=lambda v: v.lower() not in ("false", "0", "no", "off", ""),
                        help=(
                            "Wipe Library cache volume before building. "
                            "Accepts flag-only or an explicit true/false string "
                            "(for compatibility with GitHub Actions bool inputs)."
                        ))

    # ── Build options ──────────────────────────────────────────────────────
    parser.add_argument("--test-level",
                        choices=["editmode", "playmode"],
                        help="Run Unity tests instead of a build")
    parser.add_argument("--build-addressables", action="store_true",
                        help="Build Addressable Asset bundles as part of the build")
    # --release-mode follows the same bool-string convention as --clean-build.
    parser.add_argument("--release-mode",
                        nargs="?", const=True, default=False,
                        type=lambda v: v.lower() not in ("false", "0", "no", "off", ""),
                        help=(
                            "Enforce release safety checks (immutable image, no dev flags). "
                            "Accepts flag-only or an explicit true/false string "
                            "(for compatibility with GitHub Actions bool inputs)."
                        ))

    # ── Runtime controls ───────────────────────────────────────────────────
    # --timeout is the canonical name (the former run-unity-container action
    # passed it). --container-timeout is the legacy long form (kept for back-compat).
    # Both set the same dest; whichever is supplied last wins.
    parser.add_argument("--container-timeout", "--timeout",
                        dest="container_timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"Hard container timeout in seconds (default: {DEFAULT_TIMEOUT})")
    parser.add_argument("--cpus", type=float,
                        help="CPU limit for the container (e.g. 4.0)")
    parser.add_argument("--memory",
                        help="Memory limit for the container (e.g. 8g)")
    # Directories for structured log and report output (wired by the action's
    # setup-dirs step and forwarded here so the script can write artefacts to
    # the correct host paths).  Optional: defaults keep previous behaviour.
    parser.add_argument("--log-path", default=None,
                        help="Host directory for Unity Editor log output")
    parser.add_argument("--report-path", default=None,
                        help="Host directory for build/test reports")

    # ── Mode flags ─────────────────────────────────────────────────────────
    parser.add_argument("--dry-run", action="store_true",
                        help="Print docker command without executing")
    parser.add_argument("--skip-image-validation", action="store_true",
                        help="Skip validate_unity_image.py check (faster, less safe)")

    return parser.parse_args()


# ── Main ───────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    # ── Namespace validation ───────────────────────────────────────────────
    # --image-namespace is required ONLY when the caller does not supply a
    # pre-resolved --image reference. A caller that resolved the reference
    # already passes --image, so namespace is not needed there; local
    # invocations that let this script resolve the
    # image internally must provide --image-namespace (no generic default).
    if not args.image and not args.image_namespace:
        raise ValueError(
            "image namespace is required; pass --image-namespace <org/namespace> "
            "(or pass a pre-resolved --image <reference>). "
            "No game/org-specific default is baked in."
        )

    # ── Early platform check ───────────────────────────────────────────────
    # Docker regression guard: reject any platform that must not run through
    # this Docker-invocation path.  iOS uses the macos-unity-xcode executor;
    # Windows64 is explicitly unsupported.  Android, WebGL, and Linux* are
    # permitted here and must not bypass Docker (enforced by the executor
    # resolver; this script IS the Docker path for those platforms).
    if args.target_platform in DOCKER_UNSUPPORTED_PLATFORMS:
        # Emit the exact contract error string stored in the dict (no prefix)
        # so downstream log parsers can match it unambiguously.
        _abort(DOCKER_UNSUPPORTED_PLATFORMS[args.target_platform])

    # ── Validate project path ──────────────────────────────────────────────
    project_path = Path(args.project_path)
    if not project_path.is_dir():
        _abort(f"Project path not found: {project_path}")
    if not (project_path / "Assets").is_dir():
        _abort(
            f"No Assets/ directory in {project_path}. "
            "Is this a Unity project? Check --project-path."
        )

    # ── Check Docker is available ──────────────────────────────────────────
    docker = _require_docker()
    _docker_version_ok(docker)

    # ── Resolve image reference ────────────────────────────────────────────
    if args.image:
        # Pre-resolved reference supplied by the caller. Use it directly;
        # do not re-resolve. In release mode a pinned
        # digest is still required.
        image_ref = args.image
        if args.image_digest and "@sha256:" not in image_ref:
            image_ref = f"{image_ref}@{args.image_digest}"
        if args.release_mode and "@sha256:" not in image_ref:
            _abort(
                "Release mode requires an immutable digest-pinned image. "
                "Pass --image-digest sha256:<hash> or an @sha256: pinned --image."
            )
        _log(f"Image : {image_ref} (pre-resolved)")
    else:
        _log(f"Resolving image for {args.target_platform} / Unity {args.unity_version} …")
        resolution = _resolve_image(args)
        image_ref = resolution["image_ref"]
        _log(f"Image : {image_ref}")
        if resolution.get("digest"):
            _log(f"Digest: {resolution['digest']}")

    # ── Pre-pull image ─────────────────────────────────────────────────────
    # Pull before validate so that validate_unity_image.py's docker commands
    # hit the local cache rather than racing against a slow network pull.
    if not args.dry_run:
        _log("Pulling image (ensures local cache for validation and run) …")
        try:
            pull_result = subprocess.run(
                [docker, "pull", image_ref],
                timeout=600,  # 10 minutes — large Unity images can be 4 GB+
            )
            if pull_result.returncode != 0:
                print(
                    f"WARNING: docker pull exited {pull_result.returncode} — "
                    "will continue and let docker run attempt the pull.",
                    file=sys.stderr,
                )
        except subprocess.TimeoutExpired:
            print(
                "WARNING: Image pull timed out after 10 minutes — continuing anyway.",
                file=sys.stderr,
            )

    # ── Optionally validate image ──────────────────────────────────────────
    if not args.skip_image_validation and not args.dry_run:
        _log("Validating image …")
        _validate_image(docker, image_ref, args)

    # ── Prepare host output directories ───────────────────────────────────
    output_path, reports_path, logs_path = _ensure_output_dirs(args)

    # ── Cache volume management ────────────────────────────────────────────
    cache_volume = _cache_volume_name(project_path, args.unity_version, args.target_platform)
    _log(f"Cache volume: {cache_volume} (mode={args.cache_mode})")

    if args.clean_build and args.cache_mode != "off":
        _log("--clean-build: removing Library cache volume …")
        if not args.dry_run:
            subprocess.run([docker, "volume", "rm", "--force", cache_volume],
                           capture_output=True)

    # ── Build docker command ───────────────────────────────────────────────
    docker_cmd = _build_docker_command(
        docker=docker,
        image_ref=image_ref,
        args=args,
        output_path=output_path,
        reports_path=reports_path,
        logs_path=logs_path,
        cache_volume=cache_volume,
        env_vars={},
    )

    _log("Docker command:")
    print(_safe_command_repr(docker_cmd))
    print()

    # ── Dry run ────────────────────────────────────────────────────────────
    if args.dry_run:
        _log("DRY RUN — not executing.")
        return

    # ── Warn if no activation credentials are available ─────────────────
    has_license = bool(os.environ.get("UNITY_LICENSE"))
    has_serial = bool(os.environ.get("UNITY_SERIAL"))
    has_email = bool(os.environ.get("UNITY_EMAIL"))
    has_password = bool(os.environ.get("UNITY_PASSWORD"))
    forced_strategy = os.environ.get("UNITY_ACTIVATION_STRATEGY", "auto")

    if not has_license and not has_serial and not (has_email and has_password):
        if forced_strategy not in ("preactivated", "none"):
            print(
                "WARNING: No Unity activation credentials found. "
                "Set UNITY_EMAIL+UNITY_PASSWORD (Personal/free), "
                "UNITY_LICENSE (.ulf), or UNITY_SERIAL+credentials (Pro/Plus). "
                "See: https://game.ci/docs/github/activation",
                file=sys.stderr,
            )

    # ── Run the container ──────────────────────────────────────────────────
    _log(f"Starting container (timeout={args.container_timeout}s) …")
    try:
        proc = subprocess.Popen(
            docker_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except OSError as exc:
        _abort(f"Failed to start docker: {exc}")

    exit_code = _stream_process(proc)  # type: ignore[arg-type]

    # ── Export summary ─────────────────────────────────────────────────────
    _log(f"Container exited with code {exit_code}")
    _log(f"Build artefacts : {output_path}")
    _log(f"Build reports   : {reports_path}")
    _log(f"Unity logs      : {logs_path}")

    if exit_code != 0:
        _print_failure_guidance(exit_code, args.target_platform, logs_path)
        sys.exit(exit_code)

    _log("Build completed successfully.")


if __name__ == "__main__":
    main()
