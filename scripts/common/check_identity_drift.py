#!/usr/bin/env python3
"""Fail when BuildConfig and ProjectSettings.asset disagree about the app's identity.

Why this exists
---------------
A consumer shipped Android builds for weeks carrying
`com.UnityTechnologies.com.unity.template.urpblank` and version `0.4.2`, while
`BuildConfig/base.json` said `com.cuvara.indierpgmmo` and `0.5.0` and the
repository's own changelog said `0.5.0`. Every build was green.

Two things made it invisible:

  * On the Docker / game-ci lane, **BuildConfig never reaches PlayerSettings.**
    `Company.BuildPipeline.BuildCommand.Execute` runs only when `build-method`
    is set — the self-hosted/local lane. game-ci supplies its own builder and
    stamps whatever `ProjectSettings.asset` holds. So the config can say
    anything at all and the binary will not care.
  * Nothing compared the two. A config that is both inert and wrong looks
    exactly like a config that is working.

So the config being right is not evidence, and neither is a green build. This
compares them directly, which is the only check that fails when they part.

`productName` is deliberately excluded: environment overlays legitimately differ
(`… [DEV]`), and flagging that would train people to ignore the gate.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# What ships: the identifier a store sees, the version a user reads, and the
# publisher name attached to both.
FIELDS = (
    ("applicationId", "applicationIdentifier.Android"),
    ("bundleVersion", "bundleVersion"),
    ("companyName", "companyName"),
)


def read_project_settings(path: Path) -> dict:
    """Pull the three identity values out of ProjectSettings.asset.

    Parsed by hand rather than with a YAML loader: the file opens with Unity's
    `%TAG !u! tag:unity3d.com,2011:` directive and uses unregistered tags, which
    a strict loader refuses outright.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    out = {}

    for key in ("companyName", "bundleVersion"):
        match = re.search(rf"^\s{{2}}{key}:\s*(.*?)\s*$", text, re.M)
        if match:
            out[key] = match.group(1)

    # applicationIdentifier is a nested map keyed by build target.
    block = re.search(r"^\s{2}applicationIdentifier:\s*$((?:\n\s{4}.*)*)", text, re.M)
    if block:
        android = re.search(r"^\s{4}Android:\s*(.*?)\s*$", block.group(1), re.M)
        if android:
            out["applicationIdentifier.Android"] = android.group(1)

    return out


def read_build_config(config_dir: Path, environment: str) -> dict:
    """base.json merged with the environment overlay, one level deep.

    The schema requires `companyName` and `bundleVersion` in every file, so the
    overlays are complete configs rather than deltas — but they are merged the
    same way the loader merges them, so this reads what the build would read.
    """
    merged: dict = {}
    for name in ("base", environment):
        path = config_dir / f"{name}.json"
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        for key, value in data.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key].update(value)
            else:
                merged[key] = value

    android = merged.get("android") or {}
    return {
        "companyName": merged.get("companyName"),
        "bundleVersion": merged.get("bundleVersion"),
        "applicationId": android.get("applicationId"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-path", default=".")
    parser.add_argument("--config-dir", default=None,
                        help="defaults to <project-path>/BuildConfig")
    parser.add_argument("--environment", default="production")
    parser.add_argument("--mode", default=os.environ.get("IDENTITY_DRIFT", "error"),
                        choices=("error", "warn", "off"))
    args = parser.parse_args()

    if args.mode == "off":
        print("Identity drift check disabled (IDENTITY_DRIFT=off).")
        return 0

    project = Path(args.project_path)
    settings_path = project / "ProjectSettings" / "ProjectSettings.asset"
    config_dir = Path(args.config_dir) if args.config_dir else project / "BuildConfig"

    if not settings_path.exists():
        print(f"::warning::No ProjectSettings.asset at {settings_path} — nothing to compare.")
        return 0
    if not (config_dir / "base.json").exists():
        print(f"::warning::No {config_dir}/base.json — nothing to compare.")
        return 0

    settings = read_project_settings(settings_path)
    config = read_build_config(config_dir, args.environment)

    drift = []
    variants = []
    for config_key, settings_key in FIELDS:
        want = config.get(config_key)
        have = settings.get(settings_key)
        # An absent value is not a disagreement — it is a config that declines
        # to have an opinion, which is allowed.
        if want is None or have is None:
            continue
        if str(want) == str(have):
            continue
        # `com.acme.game.dev` against `com.acme.game` is a deliberate variant, not
        # drift: suffixed identifiers are how a QA build installs alongside
        # production instead of over it. A gate that reddens every development
        # build is a gate that gets switched off, so this is reported rather than
        # failed — and it is worth reporting, because on the Docker lane the
        # suffix never reaches the binary either.
        if config_key == "applicationId" and str(want).startswith(str(have) + "."):
            variants.append((settings_key, want, have))
            continue
        drift.append((config_key, settings_key, want, have))

    for settings_key, want, have in variants:
        print(f"::notice::BuildConfig/{args.environment} asks for '{want}' but "
              f"ProjectSettings.asset holds '{have}'. Treated as a deliberate "
              "variant, not drift — but on the Docker lane the suffix is not "
              "applied, so the build installs as the base identifier.")

    if not drift:
        print(f"Identity matches between BuildConfig ({args.environment}) and "
              "ProjectSettings.asset.")
        return 0

    level = "error" if args.mode == "error" else "warning"
    for config_key, settings_key, want, have in drift:
        print(f"::{level}::{settings_key} is '{have}' in ProjectSettings.asset but "
              f"'{want}' in BuildConfig/{args.environment}. On the Docker lane the "
              f"ProjectSettings value is the one that ships.")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write("## App identity drift\n\n")
            handle.write(f"`BuildConfig/{args.environment}` and `ProjectSettings.asset` "
                         "disagree. **On the Docker / game-ci lane the ProjectSettings "
                         "value is what gets stamped into the binary** — the config is "
                         "not applied there at all.\n\n")
            handle.write("| Field | BuildConfig says | ProjectSettings says (ships) |\n")
            handle.write("|---|---|---|\n")
            for config_key, settings_key, want, have in drift:
                handle.write(f"| `{settings_key}` | `{want}` | `{have}` |\n")

    return 1 if args.mode == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
