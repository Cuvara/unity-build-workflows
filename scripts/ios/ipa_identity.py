#!/usr/bin/env python3
"""Read the identity of an IPA from its own Info.plist.

  ipa_identity.py --ipa <path-or-directory> [--github-output]

Prints (and appends to $GITHUB_OUTPUT with --github-output):
  ipa-path      the .ipa that was read
  bundle-id     CFBundleIdentifier
  version       CFBundleShortVersionString
  build-number  CFBundleVersion

App Store submission (`ios-asc-submit-review`) needs the bundle id and the
build number of the binary being promoted. They come from the file that was
verified against the Release Set, not from inputs someone typed or from an
earlier job's output. A directory is searched for its single .ipa.
"""
import argparse
import os
import plistlib
import re
import sys
import zipfile
from pathlib import Path

INFO_PLIST = re.compile(r"^Payload/[^/]+\.app/Info\.plist$")


def find_ipa(path: Path) -> Path:
    if path.is_file():
        return path
    found = sorted(path.rglob("*.ipa"))
    if len(found) != 1:
        raise SystemExit(f"::error::expected exactly one .ipa under {path}, found {len(found)}")
    return found[0]


def read_identity(ipa: Path) -> dict:
    with zipfile.ZipFile(ipa) as archive:
        names = [n for n in archive.namelist() if INFO_PLIST.match(n)]
        if len(names) != 1:
            raise SystemExit(f"::error::{ipa} has no single Payload/*.app/Info.plist")
        plist = plistlib.loads(archive.read(names[0]))
    identity = {
        "bundle-id": str(plist.get("CFBundleIdentifier", "")),
        "version": str(plist.get("CFBundleShortVersionString", "")),
        "build-number": str(plist.get("CFBundleVersion", "")),
    }
    missing = [k for k, v in identity.items() if not v]
    if missing:
        raise SystemExit(f"::error::{ipa} Info.plist lacks {', '.join(missing)}")
    return identity


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ipa", required=True, help=".ipa file, or a directory holding one")
    parser.add_argument("--github-output", action="store_true", help="also append to $GITHUB_OUTPUT")
    args = parser.parse_args(argv)

    ipa = find_ipa(Path(args.ipa))
    lines = [f"ipa-path={ipa}"] + [f"{k}={v}" for k, v in read_identity(ipa).items()]
    print("\n".join(lines))
    if args.github_output and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
