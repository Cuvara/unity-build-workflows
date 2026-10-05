#!/usr/bin/env python3
"""Stop Unity processes that still have this project open.

A self-hosted runner reuses one workspace per repository. When a run is
cancelled while Unity is building, the job ends but the editor can survive
it, still holding the project; the next job then dies at once with

    Aborting batchmode due to fatal error:
    It looks like another Unity instance is running with this project open.

One runner runs one job at a time, so a Unity process with *this* project
open at the start of a job is always a leftover. This script stops exactly
those -- matched by the -projectPath on their command line, never by name
alone -- and removes Temp/UnityLockfile once no process holds it.

Run before the first Unity step and again (if: always()) at the end of the
job, so a cancelled job also cleans up after itself.

Usage: stop_stale_unity.py --project <unity-project-path> [--dry-run]
Exit code is 0 unless a matching process could not be stopped.
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Tuple

TAG = "[stop_stale_unity]"
GRACE_SECONDS = 15


def log(message: str) -> None:
    print(f"{TAG} {message}", file=sys.stderr, flush=True)


def normalise(path: str) -> str:
    """Comparable form of a path: forward slashes, no trailing slash, and
    case-folded on Windows and macOS (both default to case-insensitive)."""
    p = path.strip().strip('"').strip("'").replace("\\", "/").rstrip("/")
    if os.name == "nt" or sys.platform == "darwin":
        p = p.lower()
    return p


def project_path_of(command_line: str) -> str:
    """The -projectPath argument of a Unity command line, or ''."""
    match = re.search(r'-projectPath\s+("([^"]+)"|\'([^\']+)\'|(\S+))', command_line, re.IGNORECASE)
    if not match:
        return ""
    return match.group(2) or match.group(3) or match.group(4) or ""


def unity_processes() -> List[Tuple[int, str]]:
    """(pid, command line) of every running Unity editor process."""
    if os.name == "nt":
        script = ("Get-CimInstance Win32_Process -Filter \"Name='Unity.exe'\" | "
                  "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
        result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, timeout=60)
        raw = result.stdout.strip()
        if not raw:
            return []
        data = json.loads(raw)
        rows = data if isinstance(data, list) else [data]
        return [(int(r["ProcessId"]), r.get("CommandLine") or "") for r in rows]
    # -A -ww -o pid= -o args= reads the same on macOS (BSD ps) and Linux (procps);
    # -ww: never cut a long command line (piped output defaults to 80 columns).
    result = subprocess.run(["ps", "-A", "-ww", "-o", "pid=", "-o", "args="],
                            capture_output=True, text=True, timeout=60)
    found = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            continue
        pid, command = int(parts[0]), parts[1]
        # The editor binary itself, named "Unity" on macOS and Linux
        if re.search(r"(^|/)Unity(\.exe)?(\s|$)", command):
            found.append((pid, command))
    return found


def matching(project: str) -> List[Tuple[int, str]]:
    target = normalise(os.path.realpath(project))
    own = {os.getpid(), os.getppid()}
    hits = []
    for pid, command in unity_processes():
        if pid in own:
            continue
        opened = project_path_of(command)
        if not opened:
            continue
        opened_path = Path(opened).expanduser()
        if not opened_path.is_absolute():
            # CI starts Unity with a path relative to the workspace, which is
            # this job's working directory too (one workspace per repository).
            opened_path = Path.cwd() / opened_path
        if normalise(os.path.realpath(str(opened_path))) == target:
            hits.append((pid, command))
    return hits


def alive(pid: int) -> bool:
    if os.name == "nt":
        result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                capture_output=True, text=True, timeout=30)
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A stopped process its parent has not reaped yet is a zombie: gone for
    # our purposes, though kill(pid, 0) still finds it.
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                           capture_output=True, text=True, timeout=30).stdout.strip()
    return bool(state) and not state.startswith("Z")


def stop(pid: int) -> bool:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=60)
        time.sleep(2)
        return not alive(pid)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + GRACE_SECONDS
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    time.sleep(1)
    return not alive(pid)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--project", required=True, help="Unity project path (relative or absolute)")
    parser.add_argument("--dry-run", action="store_true", help="Report, change nothing")
    args = parser.parse_args()

    project = str(Path(args.project).resolve())
    try:
        hits = matching(project)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        log(f"Cannot list Unity processes ({exc}); continuing.")
        hits = []

    failed = []
    for pid, command in hits:
        log(f"Unity PID {pid} still has {project} open (left by an earlier, cancelled job).")
        if args.dry_run:
            continue
        if stop(pid):
            log(f"Stopped PID {pid}.")
        else:
            failed.append(pid)
    if failed:
        print(f"::error::{TAG} Could not stop Unity PID(s) {', '.join(map(str, failed))} holding "
              f"{project}. Stop them on the runner, then re-run.", flush=True)
        return 1
    if not hits:
        log(f"No Unity process has {project} open.")

    lockfile = Path(project, "Temp", "UnityLockfile")
    if lockfile.exists() and not args.dry_run:
        try:
            lockfile.unlink()
            log(f"Removed {lockfile}.")
        except OSError as exc:
            log(f"Could not remove {lockfile}: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
