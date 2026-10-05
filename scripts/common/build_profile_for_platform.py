#!/usr/bin/env python3
"""The Unity Build Profile one matrix platform builds with.

The pipeline's `build-profiles` input maps platforms to Build Profiles:

    Android=Android-Staging,iOS=iOS-dev

This prints the profile for RL_PLATFORM (escaped for a JSON string inside a
matrix row, like matrix_runner_labels.py), or nothing when the platform is not
listed or is set to ProjectSettings -- the project's own Player Settings, which
is what every build used before profiles existed.

Usage (env):
    BUILD_PROFILES=<mapping> BP_PLATFORM=Android python3 build_profile_for_platform.py
    BUILD_PROFILES=<mapping> python3 build_profile_for_platform.py --validate

A malformed mapping or an unknown platform exits 1 with the reason: a typo must
not silently build the wrong configuration.
"""
import os
import sys
from typing import Dict

PLATFORMS = ("Android", "iOS", "WebGL", "Linux64", "LinuxServer", "Windows64")
_ALIASES = {p.lower(): p for p in PLATFORMS}
_ALIASES.update({"windows": "Windows64", "linux": "Linux64", "ios": "iOS"})

# Values that mean "no profile": the project's Player Settings.
NO_PROFILE = frozenset({"", "projectsettings", "project-settings", "none", "default", "auto"})


class MappingError(ValueError):
    pass


def parse(mapping: str) -> Dict[str, str]:
    """Return {Platform: profile} with no-profile entries dropped."""
    result: Dict[str, str] = {}
    for raw in (mapping or "").split(","):
        entry = raw.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise MappingError(f"build-profiles entry '{entry}' is not Platform=Profile "
                               f"(e.g. Android=Android-Staging).")
        key, _, value = entry.partition("=")
        platform = _ALIASES.get(key.strip().lower())
        if platform is None:
            raise MappingError(f"build-profiles names unknown platform '{key.strip()}'. "
                               f"Allowed: {', '.join(PLATFORMS)}.")
        if platform in result:
            raise MappingError(f"build-profiles lists {platform} twice.")
        value = value.strip()
        if value.lower() in NO_PROFILE:
            continue
        if '"' in value or "\\" in value:
            raise MappingError(f"build-profiles value for {platform} must not contain quotes "
                               f"or backslashes: {value!r}.")
        result[platform] = value
    return result


def main() -> int:
    try:
        mapping = parse(os.environ.get("BUILD_PROFILES", ""))
    except MappingError as exc:
        print(f"::error title=Invalid build-profiles::{exc}", file=sys.stderr)
        return 1
    if "--validate" in sys.argv[1:]:
        for platform, profile in sorted(mapping.items()):
            print(f"[build-profiles] {platform}: {profile}", file=sys.stderr)
        return 0
    platform = _ALIASES.get(os.environ.get("BP_PLATFORM", "").strip().lower(), "")
    sys.stdout.write(mapping.get(platform, ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
