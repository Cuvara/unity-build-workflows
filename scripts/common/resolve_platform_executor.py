#!/usr/bin/env python3
"""
resolve_platform_executor.py
Single source of truth for mapping a Unity build target platform to its
required CI executor.

Executors
---------
  docker-unity          — Linux container (Android, WebGL, Linux64, LinuxServer)
  macos-unity-xcode     — Approved macOS runner with Xcode + Unity iOS Build Support (iOS only)

Windows64 is explicitly unsupported; no executor is assigned.

Usage:
  python3 scripts/common/resolve_platform_executor.py \\
    --target-platform iOS [--runner-os macos]
  python3 scripts/common/resolve_platform_executor.py \\
    --target-platform Android [--runner-os linux]

Exit 0 and prints executor name on success.
Exit 1 and prints contract error to stderr on violation.
"""

import argparse
import sys

# ── Executor names ─────────────────────────────────────────────────────────

EXECUTOR_DOCKER: str = "docker-unity"
EXECUTOR_MACOS: str = "macos-unity-xcode"

# ── Platform classification ────────────────────────────────────────────────

# Platforms that MUST execute inside the Docker Unity executor (Linux).
# No other executor is permitted for these targets.
DOCKER_PLATFORMS = frozenset({"Android", "WebGL", "StandaloneLinux64", "LinuxServer", "Linux64"})

# Platforms that MUST execute on the approved macOS runner with Xcode.
# Linux/Docker execution is explicitly prohibited.
MACOS_PLATFORMS = frozenset({"iOS"})

# Platforms that are explicitly unsupported — no executor is assigned and
# the pipeline must reject them at the earliest possible gate.
UNSUPPORTED_PLATFORMS = frozenset({"Windows64"})

# Runner OS identifiers that map to "Linux / Docker" for cross-validation.
_LINUX_RUNNER_ALIASES = frozenset({"linux", "ubuntu", "docker"})


# ── Contract error factories ───────────────────────────────────────────────

def _ios_on_linux_error(platform: str = "iOS") -> str:
    """
    Exact contract message when a macOS-only platform is requested on Linux.

    The literal string is tested by downstream guards in run_unity_container.py
    and any workflow-level gating scripts — do not change without updating those.
    """
    return (
        f"Target `{platform}` requires an approved macOS runner with Xcode and "
        f"Unity iOS Build Support. Linux Docker execution is not supported."
    )


def _docker_platform_on_native_error(platform: str) -> str:
    """
    Exact contract message when a Docker-only platform is run natively.

    The literal string is tested by downstream guards — do not change without
    updating those.
    """
    return (
        f"Target `{platform}` must use the Docker Unity executor. "
        f"Native Unity execution is prohibited."
    )


# ── Core resolver ──────────────────────────────────────────────────────────

def resolve_executor(target_platform: str, runner_os: str = None) -> str:
    """
    Resolve the required CI executor for a Unity build target platform.

    Parameters
    ----------
    target_platform : str
        Unity build target (e.g. "Android", "iOS", "WebGL", "StandaloneLinux64").
    runner_os : str, optional
        The OS of the requesting runner (e.g. "linux", "macos", "windows").
        When provided, cross-validates that the runner is compatible with the
        resolved executor and raises a contract error if not.

    Returns
    -------
    str
        "docker-unity" for Docker-mandatory platforms.
        "macos-unity-xcode" for iOS.

    Raises
    ------
    ValueError
        With the exact contract error string when:
        - An iOS platform is requested on a Linux/Docker runner.
        - A Docker-only platform is requested on a non-Linux (native) runner.
        - The platform is explicitly unsupported (Windows64).
        - The platform is unknown.
    """
    runner_os_lower = (runner_os or "").lower().strip()

    # ── Explicitly unsupported ─────────────────────────────────────────────
    if target_platform in UNSUPPORTED_PLATFORMS:
        raise ValueError(
            f"Target `{target_platform}` is explicitly unsupported in this pipeline. "
            "Windows64 IL2CPP cross-compilation is not supported on any executor."
        )

    # ── macOS-only platforms (iOS) ─────────────────────────────────────────
    if target_platform in MACOS_PLATFORMS:
        # Cross-validate: if caller is explicitly on Linux/Docker, contract violation
        if runner_os_lower and runner_os_lower in _LINUX_RUNNER_ALIASES:
            raise ValueError(_ios_on_linux_error(target_platform))
        return EXECUTOR_MACOS

    # ── Docker-only platforms (Android, WebGL, Linux*) ─────────────────────
    if target_platform in DOCKER_PLATFORMS:
        # Cross-validate: non-Linux native runner is a contract violation
        if runner_os_lower and runner_os_lower not in _LINUX_RUNNER_ALIASES:
            raise ValueError(_docker_platform_on_native_error(target_platform))
        return EXECUTOR_DOCKER

    # ── Unknown platform ───────────────────────────────────────────────────
    supported = sorted(DOCKER_PLATFORMS | MACOS_PLATFORMS)
    raise ValueError(
        f"Unknown target platform '{target_platform}'. "
        f"Supported platforms: {', '.join(supported)}. "
        f"Windows64 is explicitly unsupported."
    )


# ── Runner OS compatibility (runner scheduling) ────────────────────────────
#
# Which runner operating systems can execute a job, given the build engine and
# the workflow lane that runs it. This is the hard platform-safety gate the
# runner scheduler (runner_scheduler.py) applies before it looks at any policy:
# a policy may narrow this set, never widen it.
#
# The table is derived from the steps that actually exist, not from what Unity
# could do in principle:
#
#   pipeline lane (reusable-build-platform.yml / reusable-unity-tests.yml)
#     docker  — game-ci container actions run on Linux only; a Windows host in
#               Linux-container mode is driven by a separate `docker run` step
#               that supports Android/WebGL/Linux64/LinuxServer and nothing else.
#     local   — Unity Hub build steps exist for Windows and macOS only. There is
#               no Linux + local step, so Linux is not a local target.
#     iOS     — macOS + local, always. There is no docker path for Xcode.
#
#   standalone-docker lane (unity-build-{android,webgl,linux}.yml, unity-test.yml)
#     run-unity-container is a bash `docker run`: Linux + docker only.
#
#   standalone-native lane (unity-build-ios.yml, unity-release-ios.yml,
#   unity-test-ios.yml)
#     iOS on macOS + local only.
#
# resolve_executor() above is the legacy single-runner contract and is left
# untouched; its callers and tests predate per-engine routing.

LANE_PIPELINE: str = "pipeline"
LANE_STANDALONE_DOCKER: str = "standalone-docker"
LANE_STANDALONE_NATIVE: str = "standalone-native"
LANES = (LANE_PIPELINE, LANE_STANDALONE_DOCKER, LANE_STANDALONE_NATIVE)

ENGINE_DOCKER: str = "docker"
ENGINE_LOCAL: str = "local"
ENGINES = (ENGINE_DOCKER, ENGINE_LOCAL)

OS_LINUX: str = "linux"
OS_MACOS: str = "macos"
OS_WINDOWS: str = "windows"
RUNNER_OSES = (OS_LINUX, OS_MACOS, OS_WINDOWS)

# Matrix platform names, plus the two non-player Unity jobs of the pipeline.
JOB_UNITY_TESTS: str = "UnityTests"
JOB_ADDRESSABLES: str = "Addressables"
PLAYER_JOBS = ("Android", "WebGL", "Linux64", "LinuxServer", "Windows64", "iOS")
SCHEDULABLE_JOBS = PLAYER_JOBS + (JOB_UNITY_TESTS, JOB_ADDRESSABLES)

_DOCKER_ON_WINDOWS = frozenset({"Android", "WebGL", "Linux64", "LinuxServer"})
_STANDALONE_DOCKER_JOBS = frozenset({"Android", "WebGL", "Linux64", "LinuxServer", JOB_UNITY_TESTS})

_NONE: frozenset = frozenset()


def allowed_runner_os(job: str, engine: str, lane: str = LANE_PIPELINE) -> frozenset:
    """Return the runner operating systems that can execute `job` with `engine`.

    An empty set means the combination is unsupported and must be rejected
    before scheduling. Unknown job, engine or lane names raise ValueError: a
    typo in a policy must fail, not silently schedule nothing.
    """
    if job not in SCHEDULABLE_JOBS:
        raise ValueError(
            f"Unknown job '{job}'. Schedulable jobs: {', '.join(SCHEDULABLE_JOBS)}."
        )
    if engine not in ENGINES:
        raise ValueError(f"Unknown build engine '{engine}'. Allowed: {', '.join(ENGINES)}.")
    if lane not in LANES:
        raise ValueError(f"Unknown lane '{lane}'. Allowed: {', '.join(LANES)}.")

    if lane == LANE_STANDALONE_NATIVE:
        if job == "iOS" and engine == ENGINE_LOCAL:
            return frozenset({OS_MACOS})
        return _NONE

    if lane == LANE_STANDALONE_DOCKER:
        if job in _STANDALONE_DOCKER_JOBS and engine == ENGINE_DOCKER:
            return frozenset({OS_LINUX})
        return _NONE

    # pipeline lane
    if job == "iOS":
        return frozenset({OS_MACOS}) if engine == ENGINE_LOCAL else _NONE
    if engine == ENGINE_LOCAL:
        return frozenset({OS_WINDOWS, OS_MACOS})
    if job in _DOCKER_ON_WINDOWS:
        return frozenset({OS_LINUX, OS_WINDOWS})
    return frozenset({OS_LINUX})


def unsupported_combination_reason(job: str, engine: str, lane: str = LANE_PIPELINE) -> str:
    """Human-readable reason for an empty allowed_runner_os() result."""
    if job == "iOS" and engine == ENGINE_DOCKER:
        return _ios_on_linux_error("iOS") + " (build-engine=docker has no iOS path)"
    if lane == LANE_STANDALONE_DOCKER:
        return (f"The standalone docker lane runs `{job}` only with build-engine=docker "
                f"on a Linux runner.")
    if lane == LANE_STANDALONE_NATIVE:
        return (f"The standalone native lane builds iOS only, with build-engine=local "
                f"on a macOS runner; `{job}` with build-engine={engine} is not supported.")
    return f"`{job}` with build-engine={engine} is not supported on the {lane} lane."


# ── CLI entry point ────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Resolve the required CI executor for a Unity build target platform.\n\n"
            "Prints the executor name on stdout and exits 0 on success.\n"
            "Prints a contract error to stderr and exits 1 on violation."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Android → docker-unity
  python3 scripts/common/resolve_platform_executor.py --target-platform Android

  # iOS on macOS → macos-unity-xcode
  python3 scripts/common/resolve_platform_executor.py --target-platform iOS --runner-os macos

  # iOS on Linux → contract error (exit 1)
  python3 scripts/common/resolve_platform_executor.py --target-platform iOS --runner-os linux

  # Android on macOS → contract error (exit 1)
  python3 scripts/common/resolve_platform_executor.py --target-platform Android --runner-os macos
""",
    )
    parser.add_argument(
        "--target-platform",
        required=True,
        help="Unity build target platform (Android, WebGL, iOS, StandaloneLinux64, …)",
    )
    parser.add_argument(
        "--runner-os",
        required=False,
        default=None,
        help="Runner OS for cross-validation (linux, macos, windows). Omit to skip cross-validation.",
    )
    args = parser.parse_args()

    try:
        executor = resolve_executor(args.target_platform, args.runner_os)
        print(executor)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
