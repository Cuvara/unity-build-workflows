#!/usr/bin/env python3
"""
summarise_unity_log.py
Pull the errors and warnings out of a Unity Editor log and put them where a
person will actually see them.

A failed Unity build currently tells you "Process completed with exit code 1"
and leaves an eight-megabyte Editor.log in an artifact. The reason is in there —
usually one `error CS0103` line among a hundred thousand — and finding it means
downloading a zip and searching it. Meanwhile the run page, which is where
everyone looks first, says nothing.

This reads the log and produces three things:

  * GitHub **annotations** (`::error file=…,line=…::`), which surface at the top
    of the run and, for compiler diagnostics, inline on the changed file
  * a **job summary** table — the first few errors in full, then counts
  * a machine-readable **JSON report** for the final report stage

The parsing is deliberately narrow. Unity logs contain the word "error" in
hundreds of benign places — shader variant messages, package resolution notes,
il2cpp progress — so matching on the word alone produces noise that trains
people to ignore the annotations. These patterns match the shapes that mean a
build actually went wrong.

Stdlib only. Never fails the build: a parsing problem prints a warning and
exits 0, because a diagnostic tool that breaks the pipeline is worse than one
that says nothing.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# ── What counts as a diagnostic ──────────────────────────────────────────────
#
# C# compiler output. Unity prints these as:
#   Assets/Scripts/Player.cs(42,17): error CS0103: The name 'foo' does not …
# The file and line are what make an inline annotation possible.
COMPILER = re.compile(
    r"^(?P<file>[^\s(]+\.cs)\((?P<line>\d+),(?P<col>\d+)\):\s+"
    r"(?P<severity>error|warning)\s+(?P<code>CS\d+):\s+(?P<message>.*)$"
)

# Unity's own log levels, and the exceptions that end a batchmode run.
UNITY_ERROR = re.compile(
    r"^\s*(?:\[Error\]|ERROR:|Error:)\s*(?P<message>.+)$|"
    r"^(?P<exc>\w*(?:Exception|Error))\s*:\s*(?P<exc_message>.+)$"
)
UNITY_WARNING = re.compile(r"^\s*(?:\[Warning\]|WARNING:|Warning:)\s*(?P<message>.+)$")

# Build-level failures. These are the lines that explain an exit code when the
# compiler was perfectly happy.
BUILD_FAILURE = re.compile(
    r"(Build completed with a result of '(?:Failed|Cancelled)'"
    r"|Error building Player"
    r"|BuildFailedException"
    r"|Failed to build"
    r"|Unable to locate .*licen[cs]e"
    r"|No valid Unity Editor license found)",
    re.I,
)

# Noise that matches the patterns above but means nothing is wrong. Each entry
# earns its place by having produced a false annotation.
IGNORE = re.compile(
    r"(Trying to reload asset from disk"
    r"|error CS0618"                       # obsolete API — a warning in practice
    r"|Shader Unsupported"                 # every project has hundreds
    r"|will be ignored by the compiler)",
    re.I,
)


def classify(line):
    """Return (severity, payload) for a diagnostic line, or None."""
    if IGNORE.search(line):
        return None

    match = COMPILER.match(line)
    if match:
        return match.group("severity"), {
            "file": match.group("file"),
            "line": int(match.group("line")),
            "col": int(match.group("col")),
            "code": match.group("code"),
            "message": match.group("message").strip(),
        }

    if BUILD_FAILURE.search(line):
        return "error", {"message": line.strip()[:400]}

    match = UNITY_ERROR.match(line)
    if match:
        message = match.group("message") or match.group("exc_message") or ""
        exception = match.group("exc")
        if exception:
            message = f"{exception}: {message}"
        if message.strip():
            return "error", {"message": message.strip()[:400]}

    match = UNITY_WARNING.match(line)
    if match:
        return "warning", {"message": match.group("message").strip()[:400]}

    return None


def parse(paths, max_lines=400_000):
    """Read the logs once and collect deduplicated diagnostics in order."""
    errors, warnings = [], []
    seen = set()
    scanned = 0
    for path in paths:
        try:
            with open(path, "r", errors="replace") as fh:
                for line in fh:
                    scanned += 1
                    if scanned > max_lines:
                        break
                    result = classify(line.rstrip("\n"))
                    if not result:
                        continue
                    severity, payload = result
                    # A Unity log repeats the same compiler error once per
                    # assembly it tried to build. Twelve copies of one typo is
                    # not twelve problems.
                    key = (severity, payload.get("file"), payload.get("line"),
                           payload.get("message"))
                    if key in seen:
                        continue
                    seen.add(key)
                    (errors if severity == "error" else warnings).append(payload)
        except OSError as exc:
            print(f"::warning::Could not read {path}: {exc}")
    return errors, warnings


def annotate(items, severity, limit):
    """Emit GitHub annotations, which is what puts these on the run page."""
    for item in items[:limit]:
        location = ""
        if item.get("file"):
            location = f" file={item['file']},line={item['line']},col={item['col']}"
        code = f"{item['code']}: " if item.get("code") else ""
        message = item["message"].replace("\n", " ")
        print(f"::{severity}{location}::{code}{message}")
    if len(items) > limit:
        print(f"::{severity}::… and {len(items) - limit} more {severity}s — "
              f"see the uploaded logs artifact")


def render(errors, warnings, platform, show):
    lines = [f"### {platform} — Unity log", ""]
    if not errors and not warnings:
        lines += ["No errors or warnings found in the Editor log.", ""]
        return "\n".join(lines)

    lines += [f"**{len(errors)} error(s), {len(warnings)} warning(s)**", ""]

    for title, items in (("Errors", errors), ("Warnings", warnings)):
        if not items:
            continue
        lines += [f"#### {title}", ""]
        for item in items[:show]:
            where = ""
            if item.get("file"):
                where = f"`{item['file']}:{item['line']}` — "
            code = f"**{item['code']}** " if item.get("code") else ""
            lines.append(f"- {where}{code}{item['message']}")
        if len(items) > show:
            lines.append(f"- … and {len(items) - show} more")
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Summarise a Unity Editor log")
    parser.add_argument("logs", nargs="*", help="Log files; missing ones are skipped")
    parser.add_argument("--search-root", default="",
                        help="Also scan every *.log under here")
    parser.add_argument("--platform", default="Unity")
    parser.add_argument("--annotate-errors", type=int, default=20)
    parser.add_argument("--annotate-warnings", type=int, default=10,
                        help="Kept low on purpose: a wall of warnings is a wall "
                             "people learn to scroll past")
    parser.add_argument("--summary-items", type=int, default=15)
    parser.add_argument("--report", default="", help="Write a JSON report here")
    parser.add_argument("--github-output", action="store_true")
    args = parser.parse_args(argv)

    paths = [Path(p) for p in args.logs if Path(p).is_file()]
    if args.search_root:
        root = Path(args.search_root)
        if root.is_dir():
            paths += sorted(p for p in root.rglob("*.log") if p.is_file())
    # Same file reachable by two paths is common (Logs/ and the project root).
    unique, seen_paths = [], set()
    for path in paths:
        resolved = path.resolve()
        if resolved not in seen_paths:
            seen_paths.add(resolved)
            unique.append(path)

    if not unique:
        print("::warning::No Unity log found to summarise")
        return 0

    print(f"[unity-log] scanning: {', '.join(str(p) for p in unique)}")
    errors, warnings = parse(unique)

    annotate(errors, "error", args.annotate_errors)
    annotate(warnings, "warning", args.annotate_warnings)

    summary = render(errors, warnings, args.platform, args.summary_items)
    print(summary)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write(summary + "\n")

    report = {
        "platform": args.platform,
        "errorCount": len(errors),
        "warningCount": len(warnings),
        "errors": errors[:100],
        "warnings": warnings[:100],
    }
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, indent=2))

    if args.github_output and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
            fh.write(f"error-count={len(errors)}\n")
            fh.write(f"warning-count={len(warnings)}\n")
            first = errors[0]["message"].replace("\n", " ")[:200] if errors else ""
            fh.write(f"first-error={first}\n")

    # Reporting a problem is not the same as being one: the build step already
    # decided whether the run fails.
    return 0


if __name__ == "__main__":
    sys.exit(main())
