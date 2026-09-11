#!/usr/bin/env python3
"""
release_manifest.py
Build and read the Release Set manifest.

A Release Set is one `Build / Release` run: one commit, one version, one build
number, one Unity version, one build image, and the artifacts produced from
them. The manifest is what makes that set identifiable months later, and what
a promotion checks an artifact against before publishing it.

Two subcommands:

    generate   collect per-platform artifact manifests into one Release Set
    verify     check a downloaded artifact IS the one this Release Set declares

`verify` fails closed. A promotion that cannot prove the artifact's identity
must not publish it — the whole point of the immutable boundary is that the
bytes QA approved are the bytes that ship, and a filename does not establish
that.

Stdlib only; runs on any runner without a pip install.
"""

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_FILENAME = "release-manifest.json"
SCHEMA_VERSION = 2

# I-008: a release must be able to answer "what produced these bytes?".
# Strongest first — the Release Set reports the *weakest* of its artifacts,
# because a set is only as auditable as its least auditable member.
PROVENANCE_ORDER = ("immutable", "auditable", "unknown")


def sha256_of(path, chunk_size=1024 * 1024):
    """Hex sha256 of a file, or of a directory's contents in a stable order.

    A WebGL or desktop artifact is a directory, so hashing has to be defined
    for both. Directory hashing folds in the relative path as well as the
    bytes, otherwise renaming a file inside would not change the digest.
    """
    p = Path(path)
    digest = hashlib.sha256()
    if p.is_file():
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(chunk_size), b""):
                digest.update(chunk)
        return digest.hexdigest()
    if p.is_dir():
        for f in sorted(p.rglob("*")):
            if not f.is_file():
                continue
            digest.update(str(f.relative_to(p)).encode())
            with f.open("rb") as fh:
                for chunk in iter(lambda: fh.read(chunk_size), b""):
                    digest.update(chunk)
        return digest.hexdigest()
    return ""


def size_of(path):
    p = Path(path)
    if p.is_file():
        return p.stat().st_size
    if p.is_dir():
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return 0


class AmbiguousReleaseSet(Exception):
    """Two shippable artifacts claim the same platform."""


def collect_artifact_manifests(search_root):
    """Read every per-platform artifact-manifest.json under search_root.

    Intermediates are skipped. iOS produces two artifacts in a release run —
    the Xcode project Unity emits and the signed IPA stage 03b exports from it
    — and both carry a manifest saying `"platform": "iOS"`. Taking whichever
    turned up last is how a Release Set ends up promising to promote an Xcode
    project, which nobody can install.

    Two *shippable* artifacts for one platform is not a preference to resolve,
    it is a broken build: raise rather than pick.
    """
    root = Path(search_root)
    found = {}
    if not root.is_dir():
        return found
    for path in sorted(root.rglob("artifact-manifest.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        platform = data.get("platform")
        if not platform:
            continue
        if data.get("intermediate"):
            continue
        if platform in found:
            raise AmbiguousReleaseSet(
                f"{platform} has two shippable artifacts — "
                f"{found[platform].get('artifactName')} "
                f"({found[platform].get('artifactType')}) and "
                f"{data.get('artifactName')} ({data.get('artifactType')})"
            )
        found[platform] = data
    return found


def weakest_provenance(artifacts):
    """The strength the whole Release Set can honestly claim.

    One artifact built from a digest-pinned image does not make the set
    immutable if the artifact next to it came off a runner's own Unity.
    """
    worst = "immutable"
    for a in artifacts:
        strength = (a.get("builderProvenance") or {}).get("provenanceStrength") or "unknown"
        if strength not in PROVENANCE_ORDER:
            strength = "unknown"
        if PROVENANCE_ORDER.index(strength) > PROVENANCE_ORDER.index(worst):
            worst = strength
    return worst


def build_manifest(args, artifacts):
    env = os.environ
    return {
        "schemaVersion": SCHEMA_VERSION,
        "releaseSet": {
            "runId": args.run_id or env.get("GITHUB_RUN_ID", ""),
            "runNumber": args.run_number or env.get("GITHUB_RUN_NUMBER", ""),
            "runUrl": args.run_url,
            "version": args.version,
            "buildNumber": args.build_number,
            "commit": args.commit,
            "ref": args.ref,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        },
        "buildEnvironment": {
            "unityVersion": args.unity_version,
            # Empty when the lane does not pin one. Recorded either way so the
            # provenance is honest about what is and is not reproducible.
            "imageReference": args.image_reference,
            "imageDigest": args.image_digest,
            # Set-level strength (I-008): the weakest of the artifacts below.
            # `immutable` only when every artifact was built from a pinned
            # digest; `auditable` when the builder is named but could move.
            "provenanceStrength": weakest_provenance(artifacts),
            "buildType": args.build_type,
            "configuration": args.configuration,
            "environment": args.environment,
            "defineSymbols": args.define_symbols,
        },
        "artifacts": artifacts,
    }


def cmd_generate(args):
    try:
        per_platform = collect_artifact_manifests(args.search_root)
    except AmbiguousReleaseSet as exc:
        print(f"::error::Release Set is ambiguous — {exc}. A promotion could not "
              "tell which artifact it is meant to publish.", file=sys.stderr)
        return 1

    artifacts = []
    for platform, data in sorted(per_platform.items()):
        name = data.get("artifactName") or ""
        # The per-platform manifest records a path relative to its own build
        # workspace, which no longer exists here — hash the downloaded copy.
        local = None
        if args.artifacts_root:
            candidate = Path(args.artifacts_root) / name
            if candidate.exists():
                local = candidate
        artifacts.append({
            "platform": platform,
            "artifactName": name,
            "artifactType": data.get("artifactType", ""),
            "sha256": sha256_of(local) if local else data.get("artifactSha256", ""),
            "sizeBytes": size_of(local) if local else data.get("artifactSizeBytes", 0),
            "version": data.get("version", ""),
            "buildNumber": str(data.get("buildNumber", "")),
            "commit": data.get("gitCommit", ""),
            # Carried through per artifact, not merged: two platforms can
            # legitimately build on different lanes (Android in Docker, iOS on
            # a macOS runner) and flattening that would erase the difference.
            "builderProvenance": data.get("builderProvenance") or {},
        })

    manifest = build_manifest(args, artifacts)

    if args.require_artifacts and not artifacts:
        print("::error::Release manifest has no artifacts — nothing to promote",
              file=sys.stderr)
        return 1

    # Every artifact in one Release Set must share the release identity, or the
    # set is not a set. Catching it here is far cheaper than discovering at
    # promotion time that Android and iOS came from different commits.
    inconsistent = []
    for a in artifacts:
        if a["commit"] and args.commit and a["commit"] != args.commit:
            inconsistent.append(f"{a['platform']}: commit {a['commit'][:8]} != {args.commit[:8]}")
        if a["version"] and args.version and a["version"] != args.version:
            inconsistent.append(f"{a['platform']}: version {a['version']} != {args.version}")
    if inconsistent:
        for line in inconsistent:
            print(f"::error::Release Set is inconsistent — {line}", file=sys.stderr)
        return 1

    # A Release Set is identified by version + build number + commit. An empty
    # version is not a cosmetic gap: `verify --expect-version ''` compares
    # nothing and passes, so the promotion's identity check quietly loses one
    # of its three fields. Fail closed on a release; a development set may
    # legitimately have no version.
    if args.build_type == "release" and not args.version:
        print("::error::Release Set has no version. The version is part of the "
              "release identity a promotion verifies; an empty one makes that "
              "check vacuous.", file=sys.stderr)
        return 1

    # I-008. Not "every release must use a digest"; a release must be able to
    # say what built it. `unknown` means the build recorded nothing, and a
    # release nobody can trace back to a builder is not releasable.
    if artifacts and not args.allow_unknown_provenance:
        blind = [a["platform"] for a in artifacts
                 if ((a.get("builderProvenance") or {}).get("provenanceStrength")
                     or "unknown") == "unknown"]
        if blind:
            for platform in blind:
                print(f"::error::{platform} recorded no builder provenance. A release "
                      "must be traceable to what produced it (I-008).", file=sys.stderr)
            return 1

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2))
    print(f"[release_manifest] Written: {out}", file=sys.stderr)
    print(json.dumps(manifest, indent=2))

    if os.environ.get("GITHUB_STEP_SUMMARY"):
        rs = manifest["releaseSet"]
        be = manifest["buildEnvironment"]
        lines = [
            "### Release Set", "",
            f"**{rs['version']}** · build `{rs['buildNumber']}` · run "
            f"[`{rs['runId']}`]({rs['runUrl']})", "",
            "| Field | Value |", "|---|---|",
            f"| Commit | `{(rs['commit'] or '')[:12]}` |",
            f"| Ref | `{rs['ref'] or '—'}` |",
            f"| Unity | `{be['unityVersion'] or 'unknown'}` |",
            f"| Image | `{be['imageDigest'] or be['imageReference'] or 'not pinned'}` |",
            f"| Provenance | `{be['provenanceStrength']}` |",
            "",
            "| Platform | Artifact | Type | SHA-256 | Builder | Provenance |",
            "|---|---|---|---|---|---|",
        ]
        for a in artifacts:
            prov = a.get("builderProvenance") or {}
            strength = prov.get("provenanceStrength") or "unknown"
            icon = {"immutable": "🔒", "auditable": "🔎"}.get(strength, "⚠️")
            lines.append(
                f"| {a['platform']} | `{a['artifactName']}` | `{a['artifactType']}` | "
                f"`{(a['sha256'] or '—')[:16]}…` | "
                f"`{prov.get('builder') or prov.get('builderKind') or '—'}` | "
                f"{icon} `{strength}` |"
            )
        if be["provenanceStrength"] != "immutable":
            lines += ["", "> Provenance is `auditable`, not `immutable`: the builder is "
                      "recorded but is not pinned to a content digest, so re-running is "
                      "not guaranteed to reproduce the same environment."]
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write("\n".join(lines) + "\n")
    return 0


def cmd_verify(args):
    """Fail closed unless the artifact is the one the Release Set declares."""
    manifest_path = Path(args.manifest)
    if not manifest_path.is_file():
        print(f"::error::Release manifest not found at {manifest_path}. A promotion "
              "cannot verify what it is publishing.", file=sys.stderr)
        return 1
    try:
        manifest = json.loads(manifest_path.read_text())
    except json.JSONDecodeError as exc:
        print(f"::error::Release manifest is not readable JSON: {exc}", file=sys.stderr)
        return 1

    rs = manifest.get("releaseSet", {})
    entry = next((a for a in manifest.get("artifacts", [])
                  if a.get("platform") == args.platform), None)
    if entry is None:
        available = ", ".join(sorted(a.get("platform", "?") for a in manifest.get("artifacts", [])))
        print(f"::error::Release Set {rs.get('runId')} has no {args.platform} artifact "
              f"(has: {available or 'none'})", file=sys.stderr)
        return 1

    failures = []

    def expect(label, actual, wanted):
        if wanted and str(actual) != str(wanted):
            failures.append(f"{label}: manifest says {actual!r}, expected {wanted!r}")

    expect("source run", rs.get("runId"), args.expect_run_id)
    expect("version", rs.get("version"), args.expect_version)
    expect("build number", rs.get("buildNumber"), args.expect_build_number)
    expect("commit", rs.get("commit"), args.expect_commit)
    expect("artifact name", entry.get("artifactName"), args.expect_artifact_name)

    # The seven fields that make up an artifact's identity must all be PRESENT,
    # not merely unchallenged. `expect()` only compares when the caller supplied
    # an expectation, so a manifest field that is empty passed every check —
    # which is exactly how a Release Set with no version verified cleanly.
    for label, value in (
        ("source run id", rs.get("runId")),
        ("commit", rs.get("commit")),
        ("version", rs.get("version")),
        ("build number", rs.get("buildNumber")),
        ("platform", entry.get("platform")),
        ("artifact name", entry.get("artifactName")),
    ):
        if not str(value or "").strip():
            failures.append(f"the manifest records no {label} for this artifact")

    # And they must agree with each other. One Release Set is one commit, one
    # version, one build number (I-009); an artifact carrying different ones
    # came from a different build and is not part of this set, whatever the
    # filename says.
    for label, artifact_value, set_value in (
        ("commit", entry.get("commit"), rs.get("commit")),
        ("version", entry.get("version"), rs.get("version")),
        ("build number", entry.get("buildNumber"), rs.get("buildNumber")),
    ):
        if artifact_value and set_value and str(artifact_value) != str(set_value):
            failures.append(
                f"artifact {label} {artifact_value!r} does not match the Release "
                f"Set's {set_value!r} — this artifact is not part of this set"
            )

    # An intermediate is a step on the way to a shippable artifact. Promoting
    # one would ship something nobody can install.
    if entry.get("intermediate"):
        failures.append(
            f"{entry.get('artifactName')} is an intermediate "
            f"({entry.get('artifactType')}), not a shippable artifact"
        )

    # The check a filename cannot give you: are these the same bytes?
    declared = entry.get("sha256") or ""
    if not declared:
        failures.append("the manifest records no checksum for this artifact")
    elif args.artifact_path:
        actual = sha256_of(args.artifact_path)
        if not actual:
            failures.append(f"could not hash {args.artifact_path}")
        elif actual != declared:
            failures.append(
                f"checksum mismatch — the downloaded artifact is NOT the one this "
                f"Release Set produced (declared {declared[:16]}…, got {actual[:16]}…)"
            )

    print(f"[verify] Release Set {rs.get('runId')} · {rs.get('version')} "
          f"build {rs.get('buildNumber')} · {args.platform}")
    print(f"[verify] artifact  : {entry.get('artifactName')}")
    print(f"[verify] declared  : {declared[:32] or '—'}")

    prov = entry.get("builderProvenance") or {}
    strength = prov.get("provenanceStrength") or "unknown"
    print(f"[verify] builder   : {prov.get('builder') or prov.get('builderKind') or '—'} "
          f"({strength})")
    if prov.get("imageDigest"):
        print(f"[verify] image     : {prov.get('imageReference') or '—'} "
              f"@ {prov['imageDigest']}")
    if strength == "unknown" and not args.allow_unknown_provenance:
        failures.append(
            "the Release Set records no builder provenance for this artifact — "
            "what produced these bytes cannot be established (I-008)"
        )

    if failures:
        for line in failures:
            print(f"::error::Artifact identity verification failed — {line}", file=sys.stderr)
        return 1

    print("[verify] identity and checksum match — safe to promote")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
            fh.write(
                f"### Artifact verified\n\n"
                f"`{entry.get('artifactName')}` from Release Set "
                f"`{rs.get('runId')}` — version `{rs.get('version')}`, build "
                f"`{rs.get('buildNumber')}`, commit `{(rs.get('commit') or '')[:12]}`.\n\n"
                f"SHA-256 `{declared}` matches the downloaded bytes.\n\n"
                f"Builder: `{prov.get('builder') or prov.get('builderKind') or 'unrecorded'}` "
                f"— provenance `{strength}`"
                + (f", image `{prov['imageDigest']}`.\n\n" if prov.get("imageDigest")
                   else " (not pinned to a digest).\n\n")
            )
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Release Set manifest")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate", help="collect artifact manifests into a Release Set")
    gen.add_argument("--search-root", default="artifacts")
    gen.add_argument("--artifacts-root", default="", help="where the artifacts themselves are")
    gen.add_argument("--output", default=MANIFEST_FILENAME)
    gen.add_argument("--version", default="")
    gen.add_argument("--build-number", default="")
    gen.add_argument("--commit", default="")
    gen.add_argument("--ref", default="")
    gen.add_argument("--run-id", default="")
    gen.add_argument("--run-number", default="")
    gen.add_argument("--run-url", default="")
    gen.add_argument("--unity-version", default="")
    gen.add_argument("--image-reference", default="")
    gen.add_argument("--image-digest", default="")
    gen.add_argument("--build-type", default="")
    gen.add_argument("--configuration", default="")
    gen.add_argument("--environment", default="")
    gen.add_argument("--define-symbols", default="")
    gen.add_argument("--require-artifacts", action="store_true")
    gen.add_argument("--allow-unknown-provenance", action="store_true",
                     help="Permit an artifact that recorded no builder provenance. "
                          "Escape hatch for a lane still being brought up; a real "
                          "release should never need it.")
    gen.set_defaults(func=cmd_generate)

    ver = sub.add_parser("verify", help="verify an artifact against its Release Set")
    ver.add_argument("--manifest", required=True)
    ver.add_argument("--platform", required=True)
    ver.add_argument("--artifact-path", default="")
    ver.add_argument("--expect-run-id", default="")
    ver.add_argument("--expect-version", default="")
    ver.add_argument("--expect-build-number", default="")
    ver.add_argument("--expect-commit", default="")
    ver.add_argument("--expect-artifact-name", default="")
    ver.add_argument("--allow-unknown-provenance", action="store_true",
                     help="Promote an artifact whose builder was never recorded")
    ver.set_defaults(func=cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
