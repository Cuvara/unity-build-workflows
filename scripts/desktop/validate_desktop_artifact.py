#!/usr/bin/env python3
"""
validate_desktop_artifact.py
Stage 04 — ARTIFACT VALIDATION for the standalone desktop players.

Windows, Linux and the Linux dedicated server all ship the same shape: an
executable plus a `<Product>_Data` directory beside it. Miss either half and
the build "succeeds", uploads, passes a size check, and then refuses to start
on a player's machine with no useful message — which is the class of failure
stage 04 exists to catch before an artifact becomes immutable.

What it checks:

    * an executable exists, and it is the platform's kind
      (.exe for Windows, an ELF binary for Linux)
    * the matching `<name>_Data` directory exists next to it
    * the engine payload inside `_Data` is present: globalgamemanagers or
      data.unity3d, plus at least one level/resource file
    * on Linux, the executable bit is actually set — a Unity build copied
      through a zip or an artifact upload routinely loses it, and the game
      then cannot be launched at all
    * `UnityPlayer` runtime library is present for the platform
    * a dedicated server build is headless: no UnityPlayer graphics payload
      expected, checked more loosely
    * total size is plausible

Nothing here modifies the artifact. Exit 0 = passed, 1 = validation failure,
2 = usage error.
"""

import argparse
import json
import os
import stat
import sys
from pathlib import Path

# Files Unity always writes into <Product>_Data for a standalone player. One of
# these two must exist: which one depends on the Unity version and whether the
# data files were bundled.
ENGINE_MARKERS = ("globalgamemanagers", "data.unity3d")

# Names that are never the game's executable, even though they end in .exe.
# UnityCrashHandler ships beside the player and would otherwise be picked as
# the executable in a directory listing.
EXECUTABLE_DENYLIST = ("unitycrashhandler",)

PLATFORM_ALIASES = {
    "windows": "Windows64",
    "windows64": "Windows64",
    "linux": "Linux64",
    "linux64": "Linux64",
    "linuxserver": "LinuxServer",
}


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


def is_elf(path):
    """True when the file starts with the ELF magic number."""
    try:
        with path.open("rb") as fh:
            return fh.read(4) == b"\x7fELF"
    except OSError:
        return False


def find_executable(root, platform):
    """Return the game executable inside root, or None.

    The uploaded artifact may wrap the player a directory or two deep, so this
    searches rather than assuming a fixed layout — the same reason the WebGL
    validator hunts for index.html.
    """
    candidates = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name.lower().startswith(EXECUTABLE_DENYLIST):
            continue
        if platform == "Windows64":
            if path.suffix.lower() == ".exe":
                candidates.append(path)
        else:
            # A Linux player has no extension, or .x86_64. Checking the ELF
            # magic keeps a stray shell script or a README from qualifying.
            if path.suffix.lower() in ("", ".x86_64") and is_elf(path):
                candidates.append(path)
    if not candidates:
        return None
    # Shallowest first: Unity leaves intermediate copies in subdirectories and
    # the top-level one is the shipped executable.
    candidates.sort(key=lambda p: (len(p.relative_to(root).parts), str(p)))
    return candidates[0]


def data_directory_for(executable):
    """The `<name>_Data` directory Unity writes beside the executable."""
    stem = executable.name
    for suffix in (".exe", ".x86_64"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return executable.parent / f"{stem}_Data"


def check_engine_payload(data_dir, result):
    markers = [m for m in ENGINE_MARKERS if (data_dir / m).is_file()]
    if markers:
        result.ok("engine payload", ", ".join(markers))
    else:
        result.fail(
            "engine payload",
            f"neither {' nor '.join(ENGINE_MARKERS)} is present in "
            f"{data_dir.name} — the player has no scenes to load",
        )

    # Managed assemblies live here for both IL2CPP and Mono builds; their
    # absence means the build produced a shell with no game code in it.
    managed = data_dir / "Managed"
    il2cpp = list(data_dir.glob("il2cpp_data")) + list(data_dir.glob("Native"))
    if managed.is_dir() and any(managed.iterdir()):
        result.ok("managed assemblies", f"{len(list(managed.glob('*.dll')))} dll(s)")
    elif il2cpp:
        result.ok("il2cpp payload", ", ".join(p.name for p in il2cpp))
    else:
        result.fail(
            "game code",
            "no Managed/ assemblies and no il2cpp_data — the build contains no "
            "game code",
        )


def check_runtime_library(root, platform, result):
    names = {
        "Windows64": ("UnityPlayer.dll",),
        "Linux64": ("UnityPlayer.so",),
        "LinuxServer": ("UnityPlayer.so",),
    }[platform]
    found = [n for n in names if any(root.rglob(n))]
    if found:
        result.ok("Unity runtime", ", ".join(found))
    elif platform == "LinuxServer":
        # A dedicated server can legitimately be linked without the shared
        # runtime library, so this is not a failure there.
        result.warn("Unity runtime", f"no {names[0]}; normal for some server builds")
    else:
        result.fail(
            "Unity runtime",
            f"{names[0]} is missing — the player cannot start without it",
        )


def check_executable_bit(executable, result):
    """A Linux player that is not executable cannot be launched at all.

    Artifact upload and download does not preserve the bit reliably, so this
    is a real failure mode rather than a theoretical one: the artifact looks
    complete and the game will not run.
    """
    mode = executable.stat().st_mode
    if mode & stat.S_IXUSR:
        result.ok("executable bit", executable.name)
    else:
        result.fail(
            "executable bit",
            f"{executable.name} is not executable ({oct(stat.S_IMODE(mode))}); "
            "the artifact cannot be launched as downloaded",
        )


def validate(search_root, platform, min_size_bytes, max_size_mb):
    result = Result()
    root = Path(search_root)
    result.facts["platform"] = platform
    result.facts["searchRoot"] = str(root)

    if not root.is_dir():
        result.fail("artifact present", f"{root} is not a directory")
        return result

    total = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
    result.facts["sizeBytes"] = total
    if total < min_size_bytes:
        result.fail(
            "size",
            f"{total} bytes is below the {min_size_bytes}-byte floor — this is "
            "not a complete player build",
        )
    elif max_size_mb and total > max_size_mb * 1024 * 1024:
        result.fail("size", f"{total / 1024 / 1024:.1f} MB exceeds {max_size_mb} MB")
    else:
        result.ok("size", f"{total / 1024 / 1024:.1f} MB")

    executable = find_executable(root, platform)
    if executable is None:
        kind = ".exe" if platform == "Windows64" else "ELF binary"
        result.fail("executable", f"no {kind} found under {root}")
        return result

    result.facts["executable"] = str(executable.relative_to(root))
    result.ok("executable", executable.name)

    if platform != "Windows64":
        check_executable_bit(executable, result)

    data_dir = data_directory_for(executable)
    if data_dir.is_dir():
        result.facts["dataDirectory"] = data_dir.name
        result.ok("data directory", data_dir.name)
        check_engine_payload(data_dir, result)
    else:
        result.fail(
            "data directory",
            f"{data_dir.name} is missing beside {executable.name} — a player "
            "without its _Data directory cannot start",
        )

    check_runtime_library(root, platform, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate a standalone desktop player artifact")
    parser.add_argument("--search-root", default="build-artifact")
    parser.add_argument("--platform", required=True,
                        help="Windows64 | Linux64 | LinuxServer")
    parser.add_argument("--min-size-bytes", type=int, default=5_000_000)
    parser.add_argument("--max-size-mb", type=float, default=0.0,
                        help="0 disables the ceiling")
    parser.add_argument("--report", default="", help="Write the JSON report here")
    args = parser.parse_args(argv)

    platform = PLATFORM_ALIASES.get(args.platform.strip().lower())
    if platform is None:
        print(f"::error::Unknown desktop platform '{args.platform}'", file=sys.stderr)
        return 2

    result = validate(args.search_root, platform,
                      args.min_size_bytes, args.max_size_mb)
    report = result.to_dict()
    print(json.dumps(report, indent=2))
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, indent=2))

    if os.environ.get("GITHUB_STEP_SUMMARY"):
        icon = {"pass": "✅", "warn": "⚠️", "fail": "❌"}
        lines = [f"### {platform} artifact", "", "| | Check | Detail |", "|---|---|---|"]
        lines += [f"| {icon[s]} | {n} | {d} |" for s, n, d in result.checks]
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write("\n".join(lines) + "\n")

    for status, name, detail in result.checks:
        if status == "fail":
            print(f"::error::{name}: {detail}", file=sys.stderr)

    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main())
