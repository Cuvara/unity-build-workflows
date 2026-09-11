#!/usr/bin/env python3
"""
resolve_steam_config.py
Resolve and validate the Steam distribution configuration for one platform.

Steam is a DISTRIBUTION PROVIDER, not a platform capability (I-015). A project
can build Windows and Linux release artifacts with no Steam configuration at
all; what Steam configuration gates is *publishing*, and it gates it by failing
loudly rather than by skipping quietly. A deployment job that silently does
nothing and reports success is worse than one that fails: somebody believes the
build shipped.

Configuration is external to the toolkit — no App ID or Depot ID is ever
hardcoded here. It comes from the consuming project, in the same
new-variable-then-legacy shape every other resolver uses:

    STEAM_APP_ID          the app, e.g. "480"
    STEAM_DEPOTS          JSON mapping platform -> depot id, e.g.
                          {"Windows64": "480011", "Linux64": "480012"}
    STEAM_DEPOT_<PLAT>    per-platform override, e.g. STEAM_DEPOT_WINDOWS64

One `STEAM_DEPOTS` object is the preferred form because a Steam app has one
identity with several depots hanging off it; splitting it into unrelated
variables per platform is how Windows and Linux end up pointing at different
apps.

Branch mapping is the release channel, and it is configuration too:

    STEAM_BRANCH_INTERNAL   default "internal"
    STEAM_BRANCH_EXTERNAL   default "beta"
    STEAM_BRANCH_PRODUCTION default "default" (Steam's live branch)

Exit 0 = usable configuration written to $GITHUB_OUTPUT, 1 = configuration is
missing or invalid, 2 = usage error.
"""

import argparse
import json
import os
import re
import sys

# Steam ids are numeric. Catching a non-numeric one here beats watching
# steamcmd fail with an opaque error after it has already logged in.
ID_PATTERN = re.compile(r"^[0-9]{1,12}$")

PHASE_BRANCH_DEFAULTS = {
    "internal": "internal",
    "external": "beta",
    # Steam calls the live branch "default". Publishing to it is the thing an
    # Environment approval should be standing in front of.
    "production": "default",
}

# Platform -> the depot variable a project may set instead of STEAM_DEPOTS.
PLATFORM_KEYS = {
    "Windows64": ("STEAM_DEPOT_WINDOWS64", "STEAM_DEPOT_WINDOWS"),
    "Linux64": ("STEAM_DEPOT_LINUX64", "STEAM_DEPOT_LINUX"),
    "LinuxServer": ("STEAM_DEPOT_LINUXSERVER",),
}


def env(name, default=""):
    return (os.environ.get(name) or default).strip()


def resolve_depot(platform, problems):
    """Depot id for this platform, from STEAM_DEPOTS or a per-platform var."""
    raw = env("STEAM_DEPOTS")
    if raw:
        try:
            depots = json.loads(raw)
        except json.JSONDecodeError as exc:
            problems.append(f"STEAM_DEPOTS is not valid JSON: {exc}")
            depots = {}
        if not isinstance(depots, dict):
            problems.append("STEAM_DEPOTS must be a JSON object of platform -> depot id")
            depots = {}
        # Case-insensitive so "windows64" and "Windows64" both work.
        lowered = {str(k).lower(): str(v) for k, v in depots.items()}
        depot = lowered.get(platform.lower(), "")
        if depot:
            return depot

    for key in PLATFORM_KEYS.get(platform, ()):
        depot = env(key)
        if depot:
            return depot
    return ""


def resolve_branch(phase):
    override = env(f"STEAM_BRANCH_{phase.upper()}")
    return override or PHASE_BRANCH_DEFAULTS[phase]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Resolve Steam configuration")
    parser.add_argument("--platform", required=True,
                        help="Windows64 | Linux64 | LinuxServer")
    parser.add_argument("--phase", required=True,
                        choices=sorted(PHASE_BRANCH_DEFAULTS))
    parser.add_argument("--require-credentials", action="store_true",
                        help="Also require the login secrets to be present")
    args = parser.parse_args(argv)

    if args.platform not in PLATFORM_KEYS:
        print(f"::error::{args.platform} is not a Steam-distributable platform",
              file=sys.stderr)
        return 2

    problems = []

    app_id = env("STEAM_APP_ID")
    if not app_id:
        problems.append(
            "STEAM_APP_ID is not set. Steam is a distribution provider: the "
            "build does not need it, but publishing cannot proceed without it.")
    elif not ID_PATTERN.match(app_id):
        problems.append(f"STEAM_APP_ID {app_id!r} is not a numeric Steam app id")

    depot = resolve_depot(args.platform, problems)
    if not depot:
        problems.append(
            f"no Steam depot configured for {args.platform}. Set STEAM_DEPOTS "
            f'(e.g. {{"{args.platform}": "1234502"}}) or '
            f"{PLATFORM_KEYS[args.platform][0]}.")
    elif not ID_PATTERN.match(depot):
        problems.append(f"depot id {depot!r} for {args.platform} is not numeric")

    if args.require_credentials:
        # Presence only. The values are never read here and never printed —
        # they reach steamcmd through the environment.
        for name in ("STEAM_USERNAME", "STEAM_CONFIG_VDF"):
            if not env(name):
                problems.append(
                    f"{name} is not set. Steam publishing needs a logged-in "
                    "account; see docs/STEAM_DISTRIBUTION.md for generating "
                    "the config.vdf once, locally, with Steam Guard.")

    branch = resolve_branch(args.phase)

    if problems:
        for problem in problems:
            print(f"::error::Steam configuration: {problem}", file=sys.stderr)
        print("::error::Refusing to deploy. A deployment job that skips quietly "
              "and reports success is how a release nobody shipped gets believed.",
              file=sys.stderr)
        return 1

    resolved = {
        "app-id": app_id,
        "depot-id": depot,
        "branch": branch,
        "platform": args.platform,
        "phase": args.phase,
    }
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a") as fh:
            for key, value in resolved.items():
                fh.write(f"{key}={value}\n")

    # App id and depot id are not secrets — they are public in any Steam
    # manifest — so logging them is what makes a deploy auditable.
    print(f"[steam] app {app_id} depot {depot} ({args.platform}) "
          f"-> branch '{branch}' [{args.phase}]")
    print(json.dumps(resolved, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
