#!/usr/bin/env python3
"""
generate_release_notes.py
Generate store-formatted release notes from git conventional commits.

Output formats:
  - google-play-json: {"en-US": "text"} for Google Play API releaseNotes
  - app-store-text: plain text for App Store Connect whatsNew field
  - markdown: GitHub Release-style markdown

Reads git log between two tags (or HEAD) and groups by commit type.
"""

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict

# Conventional commit type → human label
TYPE_LABELS = {
    "feat": "New Features",
    "fix": "Bug Fixes",
    "perf": "Performance",
    "refactor": "Improvements",
    "docs": "Documentation",
    "chore": "Maintenance",
    "ci": "CI/CD",
    "test": "Tests",
    "style": "Style",
    "build": "Build",
}

# Types shown in store release notes (skip internal-only types)
STORE_TYPES = {"feat", "fix", "perf", "refactor"}

CC_PATTERN = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]+)\))?(?P<breaking>!)?:\s*(?P<desc>.+)$"
)


def git_log_between(from_ref: str, to_ref: str) -> list[str]:
    """Get commit subjects between two refs."""
    range_spec = f"{from_ref}..{to_ref}" if from_ref else to_ref
    result = subprocess.run(
        ["git", "log", "--pretty=format:%s", range_spec],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"WARNING: git log failed: {result.stderr.strip()}", file=sys.stderr)
        return []
    return [line for line in result.stdout.strip().split("\n") if line]


def parse_commits(subjects: list[str]) -> dict[str, list[str]]:
    """Group commit subjects by conventional commit type."""
    grouped = defaultdict(list)
    for subject in subjects:
        match = CC_PATTERN.match(subject)
        if match:
            ctype = match.group("type")
            desc = match.group("desc")
            scope = match.group("scope")
            prefix = f"**{scope}**: " if scope else ""
            breaking = " [BREAKING]" if match.group("breaking") else ""
            grouped[ctype].append(f"{prefix}{desc}{breaking}")
        else:
            grouped["other"].append(subject)
    return dict(grouped)


def format_markdown(grouped: dict[str, list[str]]) -> str:
    """Format as markdown (all types)."""
    lines = []
    for ctype, items in grouped.items():
        label = TYPE_LABELS.get(ctype, ctype.capitalize())
        lines.append(f"### {label}")
        for item in items:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines).strip()


def format_store_text(grouped: dict[str, list[str]], max_length: int = 4000) -> str:
    """Format as plain text for store listings (user-facing types only)."""
    lines = []
    for ctype in STORE_TYPES:
        items = grouped.get(ctype, [])
        if not items:
            continue
        label = TYPE_LABELS.get(ctype, ctype.capitalize())
        lines.append(f"{label}:")
        for item in items:
            # Strip markdown bold from scope
            clean = item.replace("**", "")
            lines.append(f"  - {clean}")
        lines.append("")

    text = "\n".join(lines).strip()
    if not text:
        text = "Bug fixes and improvements."
    if len(text) > max_length:
        text = text[: max_length - 3] + "..."
    return text


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate release notes from conventional commits"
    )
    parser.add_argument(
        "--from-tag",
        default="",
        help="Start tag/ref (empty = all commits up to --to-ref)",
    )
    parser.add_argument(
        "--to-ref",
        default="HEAD",
        help="End ref (default: HEAD)",
    )
    parser.add_argument(
        "--format",
        choices=["google-play-json", "app-store-text", "markdown"],
        default="google-play-json",
        help="Output format",
    )
    parser.add_argument(
        "--language",
        default="en-US",
        help="Language code for store notes (default: en-US)",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output file path (default: stdout)",
    )
    args = parser.parse_args()

    subjects = git_log_between(args.from_tag, args.to_ref)
    if not subjects:
        print("WARNING: No commits found in range", file=sys.stderr)

    grouped = parse_commits(subjects)

    if args.format == "markdown":
        output = format_markdown(grouped)
    elif args.format == "app-store-text":
        output = format_store_text(grouped)
    elif args.format == "google-play-json":
        text = format_store_text(grouped, max_length=500)
        notes = {args.language: text}
        output = json.dumps(notes, indent=2, ensure_ascii=False)

    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
        print(f"[release_notes] Written to {args.output}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
