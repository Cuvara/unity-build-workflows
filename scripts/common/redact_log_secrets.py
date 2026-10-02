#!/usr/bin/env python3
"""Replace secret values in log files before they are uploaded as artifacts.

GitHub masks secrets in the job console, not in files. Unity writes the whole
process environment into Editor.log when a Gradle build fails
(`CommandInvokationFailure`), so ANDROID_KEYSTORE_PASS and every other secret
exported to the build step ended up in the `-logs` artifact, readable by anyone
who can read the repository.

Usage:
  redact_log_secrets.py --env NAME [--env NAME ...] PATH [PATH ...]

Each NAME is an environment variable holding a secret; each PATH is a file or a
directory (searched recursively). Every occurrence of a secret value is
replaced with `***` in place. Multi-line secrets (an SSH key, a .ulf licence)
are also redacted line by line. Values shorter than MIN_LENGTH characters are
skipped: redacting "1" would shred the log without protecting anything.
Missing paths and unset variables are ignored — this runs in `if: always()`
steps and must not fail the job.
"""
import argparse
import os
import sys
from pathlib import Path

MIN_LENGTH = 4
MASK = b"***"


def secret_values(names):
    values = set()
    for name in names:
        raw = os.environ.get(name, "")
        if not raw:
            continue
        for candidate in [raw.strip()] + [line.strip() for line in raw.splitlines()]:
            if len(candidate) >= MIN_LENGTH:
                values.add(candidate.encode("utf-8"))
    # Longest first, so a secret that contains another is replaced whole.
    return sorted(values, key=len, reverse=True)


def files_under(paths):
    for path in paths:
        p = Path(path)
        if p.is_file():
            yield p
        elif p.is_dir():
            for child in sorted(p.rglob("*")):
                if child.is_file():
                    yield child


def redact_file(path, values):
    try:
        data = path.read_bytes()
    except OSError:
        return 0
    count = 0
    for value in values:
        hits = data.count(value)
        if hits:
            data = data.replace(value, MASK)
            count += hits
    if count:
        try:
            path.write_bytes(data)
        except OSError as exc:
            print(f"::warning::redact_log_secrets: could not rewrite {path}: {exc}", file=sys.stderr)
            return 0
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env", action="append", default=[], metavar="NAME",
                        help="environment variable holding a secret (repeatable)")
    parser.add_argument("paths", nargs="*")
    args = parser.parse_args()

    values = secret_values(args.env)
    if not values:
        print("[redact_log_secrets] no secret values set — nothing to redact")
        return
    total = 0
    for path in files_under(args.paths):
        hits = redact_file(path, values)
        if hits:
            # The file name only — never the value.
            print(f"[redact_log_secrets] {path}: {hits} secret occurrence(s) redacted")
            total += hits
    print(f"[redact_log_secrets] {total} occurrence(s) redacted")


if __name__ == "__main__":
    main()
