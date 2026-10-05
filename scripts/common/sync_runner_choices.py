#!/usr/bin/env python3
"""Keep a caller workflow's "Runner" dropdown equal to the real runners.

A workflow_dispatch `choice` input is static YAML — GitHub cannot fill it from
an API when the form opens — and `runs-on` matches labels, never runner names.
So a scheduled job reads the registered self-hosted runners and rewrites two
marked lines in each caller workflow:

    options: ['auto', 'Mac-mini-1', 'win-build'] # sync-runner-choices
    runner-labels: ${{ inputs.runner != 'auto' && toJSON(fromJSON('{...}')[inputs.runner]) || '' }} # sync-runner-choices

The dropdown shows real runner names; the mapping turns the chosen name into
the labels that reach exactly that machine. A runner's own label set is used
when no other runner carries all of those labels; otherwise GitHub could start
the job elsewhere, so its name is added as a label too and the runner is
reported as needing a label equal to its name.

Input: --runners FILE, the GitHub API runner objects (a JSON array of
{name, os, status, busy, labels: [{name}]} — `gh api .../actions/runners`).
Prints the choices, writes `changed=true|false` to $GITHUB_OUTPUT, exits 1
when a workflow has no marked lines (nothing to keep in sync).
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

MARKER = "sync-runner-choices"


def label_names(runner):
    out = []
    for label in runner.get("labels") or []:
        name = label.get("name") if isinstance(label, dict) else label
        if name and name not in out:
            out.append(str(name))
    return out


def choices(runners):
    """[(name, labels, needs_name_label, os, status)] sorted by name."""
    entries = []
    sets = {}
    for r in runners:
        name = str(r.get("name", "")).strip()
        if not name:
            continue
        sets[name] = set(label_names(r))
        entries.append(r)
    result = []
    for r in sorted(entries, key=lambda x: str(x["name"]).casefold()):
        name = str(r["name"]).strip()
        labels = label_names(r)
        mine = sets[name]
        ambiguous = any(mine <= theirs for other, theirs in sets.items() if other != name)
        if ambiguous and name not in labels:
            labels = labels + [name]
        result.append((name, labels, ambiguous and name not in sets[name],
                       str(r.get("os", "")), str(r.get("status", ""))))
    return result


def _yaml_quote(s):
    return "'" + s.replace("'", "''") + "'"


def options_line(indent, names):
    items = ", ".join(_yaml_quote(n) for n in ["auto"] + names)
    return f"{indent}options: [{items}] # {MARKER}"


def labels_line(indent, mapping):
    # JSON inside an expression string literal: single quotes are doubled.
    blob = json.dumps(mapping, separators=(",", ":"), sort_keys=True).replace("'", "''")
    return (f"{indent}runner-labels: ${{{{ inputs.runner != 'auto' && "
            f"toJSON(fromJSON('{blob}')[inputs.runner]) || '' }}}} # {MARKER}")


def rewrite(text, names, mapping):
    """Return (new_text, replaced_count)."""
    out, count = [], 0
    for line in text.splitlines(keepends=True):
        ending = "\n" if line.endswith("\n") else ""
        body = line.rstrip("\r\n")
        if f"# {MARKER}" in body:
            m = re.match(r"^(\s*)(options|runner-labels):", body)
            if m:
                indent, key = m.group(1), m.group(2)
                body = options_line(indent, names) if key == "options" else labels_line(indent, mapping)
                count += 1
        out.append(body + ending)
    return "".join(out), count


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runners", required=True, help="JSON array of GitHub runner objects")
    parser.add_argument("--workflow", action="append", required=True,
                        help="caller workflow to update (repeatable)")
    args = parser.parse_args()

    runners = json.loads(Path(args.runners).read_text(encoding="utf-8") or "[]")
    entries = choices(runners)
    names = [e[0] for e in entries]
    mapping = {e[0]: e[1] for e in entries}

    changed = False
    for wf in args.workflow:
        path = Path(wf)
        text = path.read_text(encoding="utf-8")
        new, count = rewrite(text, names, mapping)
        if count == 0:
            print(f"::error::{wf} has no '# {MARKER}' lines (options: / runner-labels:)",
                  file=sys.stderr)
            sys.exit(1)
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed = True
            print(f"[sync_runner_choices] updated {wf}")

    for name, labels, needs_label, osname, status in entries:
        print(f"[sync_runner_choices] {name}  {osname}  {status}  -> runs-on {labels}")
        if needs_label:
            print(f"::warning title=Runner needs its own label::{name}'s labels also match another "
                  f"runner, so the job could start elsewhere. Add the label '{name}' to it "
                  "(Settings > Actions > Runners).", file=sys.stderr)
    if not entries:
        print("::warning::no self-hosted runners found — the dropdown offers only 'auto'",
              file=sys.stderr)

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"changed={'true' if changed else 'false'}\n")


if __name__ == "__main__":
    main()
