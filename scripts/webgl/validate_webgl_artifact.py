#!/usr/bin/env python3
"""
validate_webgl_artifact.py
Stage 04 — ARTIFACT VALIDATION for WebGL.

Verifies a Unity WebGL output directory before stage 05 (DEPLOY):

    * index.html exists and references the loader
    * the Build/ directory exists with the four files a Unity WebGL player
      needs: .loader.js, .framework.js, .data, .wasm
    * the compression variant is what the build was configured to emit
      (gzip / brotli / none) and is consistent across the four files
    * StreamingAssets is present when the build declares it
    * total size is plausible and under any declared ceiling

Unity names the compressed variants by suffix — `foo.wasm.gz`, `foo.wasm.br` —
so the compression check is a filename check, not a decompression pass. A
half-compressed output (loader plain, wasm brotli) is a server-configuration
trap that only shows up as a blank canvas in the browser, which is exactly the
class of failure this stage exists to catch before deploy.

Exit 0 = passed, 1 = validation failure, 2 = usage error.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Unity emits exactly these four roles into Build/.
REQUIRED_ROLES = {
    "loader": ".loader.js",
    "framework": ".framework.js",
    "data": ".data",
    "wasm": ".wasm",
}

# The loader is deliberately excluded from the compression-consistency check.
# It is the file the browser fetches and executes *before* any decompression
# logic exists, so Unity leaves it uncompressed even when everything else is
# brotli — `loader=none, data=brotli, framework=brotli, wasm=brotli` is the
# normal, correct output of a brotli WebGL build, not a defect.
COMPRESSED_ROLES = ("framework", "data", "wasm")

COMPRESSION_SUFFIX = {"gzip": ".gz", "brotli": ".br", "none": ""}


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


def find_web_root(search_root):
    """Return the directory that holds index.html, or None.

    The uploaded artifact may wrap the player one or two directories deep
    (build/WebGL/<ProductName>/index.html), so this searches rather than
    assuming a fixed depth.
    """
    root = Path(search_root)
    if not root.is_dir():
        return None
    if (root / "index.html").is_file():
        return root
    candidates = sorted(root.rglob("index.html"), key=lambda p: len(p.parts))
    return candidates[0].parent if candidates else None


def classify_build_files(build_dir):
    """Map role → (path, compression) for the files present in Build/."""
    found = {}
    if not build_dir.is_dir():
        return found
    for path in sorted(build_dir.iterdir()):
        if not path.is_file():
            continue
        name = path.name
        compression = "none"
        stem = name
        if name.endswith(".gz"):
            compression, stem = "gzip", name[:-3]
        elif name.endswith(".br"):
            compression, stem = "brotli", name[:-3]
        for role, suffix in REQUIRED_ROLES.items():
            if stem.endswith(suffix) and role not in found:
                found[role] = (path, compression)
                break
    return found


def dir_size(path):
    return sum(f.stat().st_size for f in Path(path).rglob("*") if f.is_file())


def validate(args):
    result = Result()

    web_root = find_web_root(args.search_root)
    if web_root is None:
        result.fail("index-html", f"no index.html found under {args.search_root}")
        return result

    result.facts["webRoot"] = str(web_root)
    result.ok("index-html", str(web_root / "index.html"))

    index_text = ""
    try:
        index_text = (web_root / "index.html").read_text(errors="replace")
    except OSError as exc:
        result.fail("index-html", f"unreadable: {exc}")

    if index_text and ".loader.js" not in index_text:
        result.fail("index-html", "index.html does not reference a *.loader.js — not a Unity player")
    elif index_text:
        result.ok("index-html-loader", "loader referenced")

    build_dir = web_root / "Build"
    if not build_dir.is_dir():
        result.fail("build-dir", "Build/ directory is missing")
        return result
    result.ok("build-dir", str(build_dir))

    found = classify_build_files(build_dir)
    missing = sorted(set(REQUIRED_ROLES) - set(found))
    if missing:
        result.fail("build-files", f"missing player files: {', '.join(missing)}")
    else:
        result.ok("build-files", ", ".join(sorted(found)))

    compressions = {role: comp for role, (_, comp) in found.items()}
    result.facts["compression"] = compressions

    payload = {r: c for r, c in compressions.items() if r in COMPRESSED_ROLES}
    distinct = set(payload.values())

    if len(distinct) > 1:
        # A genuinely half-compressed payload — plain framework next to a
        # brotli wasm — renders as a blank canvas in the browser, which is
        # exactly the class of failure this stage exists to catch before deploy.
        result.fail(
            "compression-consistent",
            "mixed compression across player files: "
            + ", ".join(f"{r}={c}" for r, c in sorted(payload.items())),
        )
    elif distinct:
        actual = distinct.pop()
        result.facts["compressionMode"] = actual
        if args.expected_compression and args.expected_compression != actual:
            result.fail(
                "compression-expected",
                f"expected {args.expected_compression}, artifact is {actual}",
            )
        else:
            result.ok("compression-consistent", actual)

    # A COMPRESSED loader is the case worth flagging: it only works when the
    # host negotiates Content-Encoding, and it fails silently when it does not.
    loader_compression = compressions.get("loader", "none")
    result.facts["loaderCompression"] = loader_compression
    if loader_compression != "none":
        result.warn(
            "loader-compression",
            f"the loader is {loader_compression}-compressed — the browser fetches it "
            "before any decompression exists, so the host must serve it with a "
            "matching Content-Encoding",
        )

    if args.require_streaming_assets:
        if (web_root / "StreamingAssets").is_dir():
            result.ok("streaming-assets", "present")
        else:
            result.fail("streaming-assets", "StreamingAssets/ missing but required")

    total = dir_size(web_root)
    total_mb = round(total / (1024 * 1024), 2)
    result.facts["sizeBytes"] = total
    result.facts["sizeMB"] = total_mb
    if total < args.min_size_bytes:
        result.fail(
            "artifact-size",
            f"{total} bytes is below the {args.min_size_bytes}-byte floor — truncated build",
        )
    elif args.max_size_mb and total_mb > args.max_size_mb:
        result.fail("artifact-size", f"{total_mb} MB exceeds the {args.max_size_mb} MB ceiling")
    else:
        result.ok("artifact-size", f"{total_mb} MB")

    return result


def write_summary(result, path_env="GITHUB_STEP_SUMMARY"):
    target = os.environ.get(path_env)
    if not target:
        return
    lines = ["### WebGL Artifact Validation", "", "| Check | Status | Detail |", "|---|---|---|"]
    icon = {"pass": "✅", "fail": "❌", "warn": "⚠️"}
    for status, name, detail in result.checks:
        lines.append(f"| `{name}` | {icon[status]} | {detail} |")
    lines.append("")
    with open(target, "a") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate a Unity WebGL build artifact")
    parser.add_argument("--search-root", default="build-artifact")
    parser.add_argument(
        "--expected-compression",
        default="",
        choices=["", "gzip", "brotli", "none"],
        help="Fail when the artifact's compression differs (empty = accept any)",
    )
    parser.add_argument("--require-streaming-assets", action="store_true")
    parser.add_argument("--min-size-bytes", type=int, default=1_000_000)
    parser.add_argument("--max-size-mb", type=float, default=0.0, help="0 disables the ceiling")
    parser.add_argument("--report", default="", help="Write the JSON report here")
    args = parser.parse_args(argv)

    result = validate(args)

    report = result.to_dict()
    print(json.dumps(report, indent=2))
    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, indent=2))
    write_summary(result)

    if result.failed:
        for status, name, detail in result.checks:
            if status == "fail":
                print(f"::error::WebGL artifact validation — {name}: {detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
