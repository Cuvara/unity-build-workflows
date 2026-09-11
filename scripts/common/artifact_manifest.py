#!/usr/bin/env python3
"""
artifact_manifest.py
Produce a first-class artifact manifest for a completed platform build.

Stage 03 (BUILD ARTIFACTS) writes this file into the build output so that
stage 04 (ARTIFACT VALIDATION), stage 05 (PUBLISH) and stage 07 (REPORT) never
have to rediscover where the artifact lives or what it is.

The manifest reuses scripts/common/build_metadata.generate_metadata() for the
shared build fields and adds the artifact-identity block the later stages need:

    platform, configuration, artifactType, artifactPath, artifactSizeBytes,
    artifactSha256, version, buildNumber, commit, branch, tag, timestamp,
    runId / runUrl

Stdlib only — the pipeline runs this on GitHub-hosted and self-hosted runners
without a pip install step.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_metadata import generate_metadata  # noqa: E402

# Manifest file name, written inside the build output directory so it travels
# with the uploaded artifact.
MANIFEST_FILENAME = "artifact-manifest.json"

# Which file in a build output directory *is* the artifact, per artifact type.
# Ordered globs — the first match wins. Keep these aligned with the artifact
# types accepted by --artifact-type.
ARTIFACT_PATTERNS = {
    "AAB": ["*.aab"],
    "APK": ["*.apk"],
    "IPA": ["*.ipa"],
    "XCODEPROJ": ["*.xcodeproj", "Unity-iPhone.xcodeproj"],
    "ZIP": ["*.zip"],
    "WEBGL": ["index.html"],
    "EXE": ["*.exe"],
    "LINUX": ["*.x86_64"],
    "ADDRESSABLES": ["catalog*.json", "*.bundle"],
}

# Artifact types that are a step on the way to a shippable artifact, never the
# shippable artifact itself. An Xcode project is the clearest case: Unity emits
# one for iOS, but what ships is the signed IPA exported from it, and a Release
# Set that listed the project would be promising to promote something nobody
# can install. Marking them here keeps the judgement in one place instead of
# spread across the workflows that happen to know about iOS.
INTERMEDIATE_ARTIFACT_TYPES = {"XCODEPROJ"}


# Platform → the artifact type a build produces when the caller does not say.
# Android is deliberately absent: APK vs AAB is a build-configuration decision,
# never a default.
DEFAULT_ARTIFACT_TYPE = {
    "iOS": "IPA",
    "WebGL": "WEBGL",
    "Windows64": "EXE",
    "Linux64": "LINUX",
    "LinuxServer": "LINUX",
    "Addressables": "ADDRESSABLES",
}


def run_git(args, cwd):
    """Run a git command, returning stdout or '' when git is unavailable."""
    try:
        result = subprocess.run(
            ["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        pass
    return ""


def discover_artifact(search_root, artifact_type):
    """Return the artifact path inside search_root for artifact_type, or None.

    A directory is walked breadth-first so that a top-level match beats a
    nested one — Unity leaves intermediate copies in subdirectories and the
    shallowest hit is the shipped artifact.
    """
    root = Path(search_root)
    if root.is_file():
        return root

    patterns = ARTIFACT_PATTERNS.get(str(artifact_type).upper())
    if not patterns or not root.is_dir():
        return None

    candidates = []
    for path in root.rglob("*"):
        name = path.name
        for pattern in patterns:
            if fnmatch.fnmatch(name, pattern):
                candidates.append(path)
                break

    if not candidates:
        return None

    # Shallowest first, then alphabetical, so the result is deterministic.
    candidates.sort(key=lambda p: (len(p.relative_to(root).parts), str(p)))
    return candidates[0]


def sha256_of(path, chunk_size=1024 * 1024):
    """Hex sha256 of a file, or '' for a directory / unreadable path."""
    p = Path(path)
    if not p.is_file():
        return ""
    digest = hashlib.sha256()
    try:
        with p.open("rb") as fh:
            for chunk in iter(lambda: fh.read(chunk_size), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def size_of(path):
    """Byte size of a file, or the recursive total for a directory."""
    p = Path(path)
    if p.is_file():
        return p.stat().st_size
    if p.is_dir():
        return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    return 0


def ci_context(env=None):
    """Extract the CI identity block from the environment."""
    env = os.environ if env is None else env
    run_id = env.get("GITHUB_RUN_ID", "")
    repo = env.get("GITHUB_REPOSITORY", "")
    server = env.get("GITHUB_SERVER_URL", "https://github.com")
    run_url = f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else ""
    return {
        "provider": "github-actions" if env.get("GITHUB_ACTIONS") else "unknown",
        "runId": run_id,
        "runNumber": env.get("GITHUB_RUN_NUMBER", ""),
        "runAttempt": env.get("GITHUB_RUN_ATTEMPT", ""),
        "runUrl": run_url,
        "repository": repo,
        "actor": env.get("GITHUB_ACTOR", ""),
        "workflow": env.get("GITHUB_WORKFLOW", ""),
        "job": env.get("GITHUB_JOB", ""),
    }


# I-008. Two honest strengths, never one dressed as the other:
#
#   immutable — the builder is pinned to content (a digest). Re-running the
#               same reference gets the same environment.
#   auditable — the builder is identified but could move: a tag, or whatever
#               Unity a runner happens to have installed.
#
# A release must reach at least `auditable`. `unknown` means the build could
# not say what produced it, and the invariant check treats that as a failure
# rather than quietly accepting a hole in the provenance.
PROVENANCE_STRENGTHS = ("immutable", "auditable", "unknown")


def classify_provenance(image_digest="", image_reference="", unity_version=""):
    """Strength this provenance actually has — not the one we would prefer.

    A digest pins content, so it is immutable. A bare tag, or a runner's own
    Unity install, identifies the builder well enough to audit but not well
    enough to reproduce. Nothing at all is `unknown`, and saying so is the
    point: a silent empty field reads like "fine" to a human skimming a
    manifest, and `unknown` does not.
    """
    if image_digest:
        return "immutable"
    if image_reference or unity_version:
        return "auditable"
    return "unknown"


def build_provenance(
    builder="",
    builder_kind="",
    image_reference="",
    image_digest="",
    provenance_strength="",
    runner="",
    unity_version="",
    env=None,
):
    """The 'what produced this binary?' block, answerable months later."""
    env = os.environ if env is None else env

    # Normalise a digest to the bare sha256:… even when handed repo@sha256:….
    digest = image_digest.split("@")[-1] if image_digest else ""
    if digest and not digest.startswith("sha256:"):
        digest = f"sha256:{digest}" if len(digest) == 64 else digest

    strength = provenance_strength or classify_provenance(
        image_digest=digest, image_reference=image_reference, unity_version=unity_version
    )
    if strength not in PROVENANCE_STRENGTHS:
        strength = "unknown"
    # Never let a caller label a mutable reference immutable.
    if strength == "immutable" and not digest:
        strength = classify_provenance(
            image_reference=image_reference, unity_version=unity_version
        )

    return {
        "builder": builder or "",
        # docker | native — which lane, so a reader knows whether an absent
        # image is a gap or simply not applicable.
        "builderKind": builder_kind or "",
        "imageReference": image_reference or "",
        "imageDigest": digest,
        "unityVersion": unity_version or "",
        "runner": runner or f"{env.get('RUNNER_OS', '')}/{env.get('RUNNER_ARCH', '')}".strip("/"),
        "runnerName": env.get("RUNNER_NAME", ""),
        "provenanceStrength": strength,
    }


def build_manifest(
    platform,
    configuration,
    artifact_type,
    artifact_path=None,
    project_path=".",
    version="0.0.0",
    build_number="0",
    unity_version="",
    commit="",
    branch="",
    tag="",
    artifact_name="",
    builder="",
    builder_kind="",
    image_reference="",
    image_digest="",
    provenance_strength="",
    runner="",
    intermediate=False,
    env=None,
):
    """Assemble the artifact manifest dictionary.

    artifact_path may be None when the build produced nothing (a failed or
    blocked build still gets a manifest so the report stage can say *why*
    there is no artifact rather than showing a hole).
    """
    env = os.environ if env is None else env

    commit_sha = commit or run_git(["rev-parse", "HEAD"], project_path)
    resolved_branch = branch or env.get("GITHUB_REF_NAME", "") or run_git(
        ["rev-parse", "--abbrev-ref", "HEAD"], project_path
    )
    resolved_tag = tag or run_git(["describe", "--tags", "--exact-match", "HEAD"], project_path)

    abs_path = str(Path(artifact_path).resolve()) if artifact_path else ""
    rel_path = ""
    if artifact_path:
        try:
            rel_path = str(Path(artifact_path).resolve().relative_to(Path.cwd()))
        except ValueError:
            rel_path = abs_path

    size_bytes = size_of(artifact_path) if artifact_path else 0

    metadata = generate_metadata(
        project=env.get("GITHUB_REPOSITORY", "").split("/")[-1] or None,
        git_commit=commit_sha,
        git_branch=resolved_branch,
        git_tag=resolved_tag,
        unity_version=unity_version,
        platform=platform,
        environment=configuration,
        version=version,
        build_number=build_number,
        artifact=artifact_name or (Path(artifact_path).name if artifact_path else None),
        artifact_size_bytes=size_bytes,
        success=bool(artifact_path),
    )

    metadata["schemaVersion"] = 1
    metadata["artifactType"] = str(artifact_type).upper()
    # A Release Set skips these, so an iOS release set contains the signed IPA
    # produced by stage 03b and not the Xcode project it was built from.
    metadata["intermediate"] = bool(
        intermediate or metadata["artifactType"] in INTERMEDIATE_ARTIFACT_TYPES
    )
    metadata["configuration"] = configuration
    metadata["platform"] = platform
    metadata["artifactPath"] = rel_path or abs_path
    metadata["artifactAbsolutePath"] = abs_path
    metadata["artifactSha256"] = sha256_of(artifact_path) if artifact_path else ""
    metadata["artifactName"] = artifact_name or metadata.get("artifact") or ""
    metadata["timestamp"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    metadata["ci"] = ci_context(env)
    metadata["builderProvenance"] = build_provenance(
        builder=builder,
        builder_kind=builder_kind,
        image_reference=image_reference,
        image_digest=image_digest,
        provenance_strength=provenance_strength,
        runner=runner,
        unity_version=unity_version,
        env=env,
    )

    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate an artifact manifest")
    parser.add_argument("--platform", required=True)
    parser.add_argument("--configuration", default="production")
    parser.add_argument(
        "--artifact-type",
        default="",
        help="AAB/APK/IPA/WEBGL/EXE/LINUX/ZIP/ADDRESSABLES (inferred from platform if omitted)",
    )
    parser.add_argument(
        "--search-root",
        default="build",
        help="Directory (or file) the artifact is discovered in",
    )
    parser.add_argument("--artifact-path", default="", help="Skip discovery, use this path")
    parser.add_argument("--project-path", default=".")
    parser.add_argument("--version", default="0.0.0")
    parser.add_argument("--build-number", default="0")
    parser.add_argument("--unity-version", default="")
    parser.add_argument("--commit", default="")
    parser.add_argument("--branch", default="")
    parser.add_argument("--tag", default="")
    parser.add_argument("--artifact-name", default="", help="CI artifact (upload) name")
    # Builder provenance (I-008). All optional: a lane that cannot determine a
    # field records it empty rather than guessing, and the strength is
    # downgraded to match what was actually established.
    parser.add_argument("--builder", default="",
                        help="builder identity, e.g. the build action and its version")
    parser.add_argument("--builder-kind", default="", help="docker | native")
    parser.add_argument("--image-reference", default="", help="e.g. unityci/editor:...")
    parser.add_argument("--image-digest", default="", help="sha256:… when resolvable")
    parser.add_argument("--provenance-strength", default="",
                        help="immutable | auditable | unknown (derived when omitted)")
    parser.add_argument("--runner", default="", help="runner identity, e.g. Linux/X64")
    parser.add_argument(
        "--intermediate", action="store_true",
        help="This artifact is an input to a later stage, not a shippable "
             "artifact — a Release Set will skip it. XCODEPROJ is marked "
             "automatically.")
    parser.add_argument("--output", default="", help=f"Default: <search-root>/{MANIFEST_FILENAME}")
    parser.add_argument(
        "--github-output",
        action="store_true",
        help="Also append artifact-path/type/size/sha256 to $GITHUB_OUTPUT",
    )
    args = parser.parse_args(argv)

    artifact_type = args.artifact_type or DEFAULT_ARTIFACT_TYPE.get(args.platform, "ZIP")

    artifact_path = args.artifact_path or None
    if artifact_path is None:
        found = discover_artifact(args.search_root, artifact_type)
        artifact_path = str(found) if found else None

    if artifact_path is None:
        print(
            f"[artifact_manifest] WARNING: no {artifact_type} artifact found under "
            f"{args.search_root} — writing an empty manifest",
            file=sys.stderr,
        )

    manifest = build_manifest(
        platform=args.platform,
        configuration=args.configuration,
        artifact_type=artifact_type,
        artifact_path=artifact_path,
        project_path=args.project_path,
        version=args.version,
        build_number=args.build_number,
        unity_version=args.unity_version,
        commit=args.commit,
        branch=args.branch,
        tag=args.tag,
        artifact_name=args.artifact_name,
        builder=args.builder,
        builder_kind=args.builder_kind,
        image_reference=args.image_reference,
        image_digest=args.image_digest,
        provenance_strength=args.provenance_strength,
        runner=args.runner,
        intermediate=args.intermediate,
    )

    output = Path(args.output) if args.output else Path(args.search_root) / MANIFEST_FILENAME
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2))
    print(f"[artifact_manifest] Written: {output}", file=sys.stderr)

    if args.github_output and os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
            fh.write(f"artifact-path={manifest['artifactPath']}\n")
            fh.write(f"artifact-type={manifest['artifactType']}\n")
            fh.write(f"artifact-size-bytes={manifest['artifactSizeBytes']}\n")
            fh.write(f"artifact-sha256={manifest['artifactSha256']}\n")
            fh.write(f"manifest-path={output}\n")
            fh.write(
                f"provenance-strength="
                f"{manifest['builderProvenance']['provenanceStrength']}\n"
            )

    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
