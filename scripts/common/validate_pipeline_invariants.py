#!/usr/bin/env python3
"""
validate_pipeline_invariants.py
Static enforcement of the pipeline invariants in
.github/pipeline-policy/invariants.md.

These are properties that do not fail a build when broken. They produce a
pipeline that looks healthy and ships the wrong bytes — signing that moved
after the artifact boundary, a promotion that quietly rebuilds, a release with
no checksum to verify against. Every one of these checks exists because the
corresponding mistake was made, shipped, and found by running a real release.

Reads workflow YAML only; no network, no GitHub API. Exit 0 = all invariants
hold, 1 = at least one violated, 2 = usage error.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

# Anything that produces or alters a binary. Matched against a job's `uses`
# and every step's `uses`/`run`, so a shell call is caught like an action.
MUTATING_OPERATIONS = [
    ("unity-build-", "runs a Unity build"),
    ("reusable-build-platform", "runs a Unity build"),
    ("game-ci/unity-builder", "runs a Unity build"),
    ("ios-setup-signing", "installs a signing identity"),
    ("ios-archive-export", "archives and exports an IPA"),
    ("xcodebuild", "invokes Xcode"),
    ("xcode_archive.sh", "archives an Xcode project"),
    ("xcode_export.sh", "exports an IPA"),
    ("sign_android_build.sh", "signs an Android artifact"),
    ("gradlew", "runs a Gradle build"),
    ("compress_webgl.sh", "rewrites the WebGL payload"),
    ("apply_define_symbols.sh", "changes build configuration"),
]

PROMOTION_PREFIX = "pipeline-"
PROMOTION_SUFFIX = "-release.yml"


class Report:
    """Collects violations so one run reports every problem, not just the first."""

    def __init__(self):
        self.violations = []
        self.checked = []

    def ok(self, invariant, detail):
        self.checked.append((invariant, detail))

    def fail(self, invariant, detail):
        self.violations.append((invariant, detail))

    @property
    def failed(self):
        return bool(self.violations)


def load_workflow(path):
    with path.open() as fh:
        return yaml.safe_load(fh)


def triggers(workflow):
    """PyYAML parses a bare `on:` key as the boolean True."""
    return workflow.get(True, workflow.get("on", {})) or {}


def job_text(job):
    """Everything in a job that could invoke an operation."""
    parts = [str(job.get("uses", ""))]
    for step in job.get("steps") or []:
        parts.append(str(step.get("uses", "")))
        parts.append(str(step.get("run", "")))
    return "\n".join(parts)


def promotion_workflows(workflows_dir):
    return sorted(
        p for p in workflows_dir.glob(f"{PROMOTION_PREFIX}*{PROMOTION_SUFFIX}")
    )


# ---------------------------------------------------------------------------
# I-005 / I-004 — the immutable artifact boundary
# ---------------------------------------------------------------------------

def check_promotion_never_mutates(workflows_dir, report):
    for path in promotion_workflows(workflows_dir):
        workflow = load_workflow(path)
        for job_id, job in (workflow.get("jobs") or {}).items():
            haystack = job_text(job)
            for token, why in MUTATING_OPERATIONS:
                if token in haystack:
                    report.fail(
                        "I-005",
                        f"{path.name}:{job_id} {why} (`{token}`). Promotion may only "
                        "download, verify, test, approve and publish — the binary QA "
                        "approved must be the binary that ships.",
                    )
        report.ok("I-005", f"{path.name} contains no build or signing operation")


def check_signing_before_boundary(workflows_dir, report):
    """I-004: iOS production signing must live in the build lane."""
    pipeline = workflows_dir / "unity-pipeline.yml"
    if not pipeline.exists():
        report.fail("I-004", "unity-pipeline.yml is missing")
        return
    jobs = (load_workflow(pipeline).get("jobs") or {})
    signer = next((j for jid, j in jobs.items() if jid == "sign-ios"), None)
    if signer is None:
        report.fail(
            "I-004",
            "unity-pipeline.yml has no sign-ios job, so a signed IPA can only be "
            "produced during promotion — after QA has approved something else.",
        )
        return
    uses = job_text(signer)
    if "ios-archive-export" not in uses or "ios-setup-signing" not in uses:
        report.fail("I-004", "sign-ios does not perform archive/export signing")
    else:
        report.ok("I-004", "iOS production signing runs in the build lane (stage 03b)")


# ---------------------------------------------------------------------------
# I-010 / I-017 / I-007 — promotion consumes an exact, verified artifact
# ---------------------------------------------------------------------------

def check_promotion_pins_its_source(workflows_dir, report):
    for path in promotion_workflows(workflows_dir):
        workflow = load_workflow(path)
        call = (triggers(workflow).get("workflow_call") or {})
        inputs = call.get("inputs") or {}

        source = inputs.get("source-run-id")
        if not source:
            report.fail("I-010", f"{path.name} has no source-run-id input")
        elif not source.get("required"):
            report.fail(
                "I-010",
                f"{path.name}: source-run-id is optional, so a promotion can look "
                "for the artifact in the wrong run",
            )
        else:
            report.ok("I-010", f"{path.name} pins an exact source run")

        for job_id, job in (workflow.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                if not str(step.get("uses", "")).startswith("actions/download-artifact"):
                    continue
                with_block = step.get("with") or {}
                name = str(with_block.get("name", ""))
                if "release-notes" in name:
                    continue  # produced inside the promotion run
                if "source-run-id" not in str(with_block.get("run-id", "")):
                    report.fail(
                        "I-017",
                        f"{path.name}:{job_id} downloads {name or '<unnamed>'} from the "
                        "current run, not the Release Set",
                    )
                elif not with_block.get("github-token"):
                    report.fail(
                        "I-017",
                        f"{path.name}:{job_id} has no token for a cross-run download",
                    )


def check_promotion_verifies_identity(workflows_dir, report):
    """I-007: a filename is not an identity."""
    for path in promotion_workflows(workflows_dir):
        body = path.read_text()
        if "release_manifest.py verify" not in body:
            report.fail(
                "I-007",
                f"{path.name} never verifies the artifact against the release "
                "manifest — it trusts the artifact name alone",
            )
        else:
            report.ok("I-007", f"{path.name} verifies artifact identity and checksum")


# ---------------------------------------------------------------------------
# I-006 / I-009 — the Release Set
# ---------------------------------------------------------------------------

def check_release_manifest(workflows_dir, report):
    pipeline = workflows_dir / "unity-pipeline.yml"
    if not pipeline.exists():
        return
    body = pipeline.read_text()
    if "release_manifest.py" not in body:
        report.fail(
            "I-006",
            "unity-pipeline.yml never generates a release manifest, so a Release "
            "Set cannot be identified or verified later",
        )
    else:
        report.ok("I-006", "Build / Release generates a release manifest")


def check_one_identity_per_release_set(workflows_dir, report):
    """I-009: every platform build must take its identity from stage 01."""
    pipeline = workflows_dir / "unity-pipeline.yml"
    if not pipeline.exists():
        return
    jobs = (load_workflow(pipeline).get("jobs") or {})
    build = jobs.get("build")
    if not build:
        report.fail("I-009", "unity-pipeline.yml has no build job")
        return
    with_block = build.get("with") or {}
    for field in ("unity-version", "app-version", "build-number"):
        value = str(with_block.get(field, ""))
        if "needs.resolve-config.outputs" not in value:
            report.fail(
                "I-009",
                f"build job takes {field} from {value or '<nothing>'} rather than "
                "stage 01, so platforms in one Release Set could disagree",
            )
    report.ok("I-009", "all platform builds share one resolved identity")


# ---------------------------------------------------------------------------
# I-001 — CI builds nothing
# ---------------------------------------------------------------------------

def check_ci_builds_nothing(templates_dir, report):
    ci = templates_dir / "consumer-01-ci.yml"
    if not ci.exists():
        report.fail("I-001", "no CI entry template found")
        return
    job = next(iter((load_workflow(ci).get("jobs") or {}).values()), {})
    if str((job.get("with") or {}).get("platform")) != "None":
        report.fail(
            "I-001",
            "the CI entry point does not pass platform: None, so a merge check "
            "pays for player builds",
        )
    else:
        report.ok("I-001", "CI builds no player artifacts")


# ---------------------------------------------------------------------------
# I-011 — production is protected
# ---------------------------------------------------------------------------

def check_production_is_gated(workflows_dir, report):
    for path in promotion_workflows(workflows_dir):
        workflow = load_workflow(path)
        production = [
            jid for jid, job in (workflow.get("jobs") or {}).items()
            if "production" in jid or "Production" in str(job.get("name", ""))
        ]
        if not production:
            continue
        for job_id in production:
            job = workflow["jobs"][job_id]
            if job.get("environment") != "production":
                report.fail(
                    "I-011",
                    f"{path.name}:{job_id} publishes to production without the "
                    "`production` Environment, so nothing enforces approval",
                )
            else:
                report.ok("I-011", f"{path.name}:{job_id} is Environment-protected")


# ---------------------------------------------------------------------------
# I-012 / I-014 / I-016 — build number and platform capabilities
# ---------------------------------------------------------------------------

def check_build_number_is_resolved(workflows_dir, report):
    pipeline = workflows_dir / "unity-pipeline.yml"
    if not pipeline.exists():
        return
    jobs = (load_workflow(pipeline).get("jobs") or {})
    outputs = (jobs.get("resolve-config") or {}).get("outputs") or {}
    if "build-number" not in outputs:
        report.fail("I-012", "stage 01 does not resolve a build number")
        return
    engine = workflows_dir / "reusable-build-platform.yml"
    if engine.exists():
        body = engine.read_text()
        if "androidVersionCode: ${{ inputs.build-number }}" not in body:
            report.fail(
                "I-012",
                "the Android version code is not passed to the builder, so game-ci "
                "generates its own and nothing guarantees it increases",
            )
        elif "BUILD_NUMBER:" not in body:
            report.fail("I-012", "BUILD_NUMBER never reaches the iOS builder")
        else:
            report.ok("I-012", "build number is resolved once and applied to both stores")


def check_platform_capabilities(scripts_dir, report):
    resolver = scripts_dir / "common" / "resolve_build_flow.sh"
    if not resolver.exists():
        report.fail("I-014", "resolve_build_flow.sh is missing")
        return
    body = resolver.read_text()
    if "platform_enabled" not in body or "PLATFORM_CAPABILITIES" not in body:
        report.fail(
            "I-014",
            "the resolver has no platform capability gate, so a project would "
            "build platforms it does not support",
        )
        return
    if "! platform_enabled" not in body:
        report.fail("I-016", "the capability gate is defined but never applied")
        return
    report.ok("I-014", "platform capabilities come from the PLATFORMS variable")
    report.ok("I-016", "disabled platforms are filtered before any job is created")


# ---------------------------------------------------------------------------
# I-008 — immutable build image
# ---------------------------------------------------------------------------

def _release_mode_enabled(node):
    """True when any `release-mode` in this tree is switched on.

    Greping for the key alone flagged `release-mode: false` — a nightly build
    that is explicitly NOT in release mode. The value is what matters.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "release-mode":
                text = str(value).strip().lower()
                if text in ("true", "yes", "on"):
                    return True
                # An expression could evaluate either way at runtime; treat a
                # literal false as off and anything dynamic as on, so the check
                # errs toward demanding a pinned image.
                if text not in ("false", "no", "off", ""):
                    return True
            elif _release_mode_enabled(value):
                return True
    elif isinstance(node, list):
        return any(_release_mode_enabled(v) for v in node)
    return False


def check_no_silent_mutable_fallback(workflows_dir, report):
    """A workflow that builds in release mode must resolve an image reference
    rather than falling back to a mutable tag."""
    for path in sorted(workflows_dir.glob("*.yml")):
        try:
            workflow = load_workflow(path)
        except yaml.YAMLError:
            continue
        if not _release_mode_enabled(workflow):
            continue
        body = path.read_text()
        if "image-digest" in body or "resolve-unity-image" in body:
            report.ok("I-008", f"{path.name} resolves an image reference explicitly")
        else:
            report.fail(
                "I-008",
                f"{path.name} builds in release mode but never resolves an image "
                "digest, so a mutable tag decides what the release was built with",
            )


CHECKS = [
    ("promotion never mutates", lambda ctx, r: check_promotion_never_mutates(ctx["workflows"], r)),
    ("signing before boundary", lambda ctx, r: check_signing_before_boundary(ctx["workflows"], r)),
    ("promotion pins its source", lambda ctx, r: check_promotion_pins_its_source(ctx["workflows"], r)),
    ("promotion verifies identity", lambda ctx, r: check_promotion_verifies_identity(ctx["workflows"], r)),
    ("release manifest", lambda ctx, r: check_release_manifest(ctx["workflows"], r)),
    ("one identity per release set", lambda ctx, r: check_one_identity_per_release_set(ctx["workflows"], r)),
    ("CI builds nothing", lambda ctx, r: check_ci_builds_nothing(ctx["templates"], r)),
    ("production is gated", lambda ctx, r: check_production_is_gated(ctx["workflows"], r)),
    ("build number resolved", lambda ctx, r: check_build_number_is_resolved(ctx["workflows"], r)),
    ("platform capabilities", lambda ctx, r: check_platform_capabilities(ctx["scripts"], r)),
    ("no silent mutable image", lambda ctx, r: check_no_silent_mutable_fallback(ctx["workflows"], r)),
]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate pipeline invariants")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    root = Path(args.repo_root).resolve()
    ctx = {
        "workflows": root / ".github" / "workflows",
        "templates": root / "templates",
        "scripts": root / "scripts",
    }
    if not ctx["workflows"].is_dir():
        print(f"::error::No .github/workflows under {root}", file=sys.stderr)
        return 2

    report = Report()
    for name, check in CHECKS:
        try:
            check(ctx, report)
        except Exception as exc:  # noqa: BLE001 — a crashing check must not pass silently
            report.fail("checker", f"{name} raised {type(exc).__name__}: {exc}")

    if args.json:
        print(json.dumps({
            "status": "failure" if report.failed else "success",
            "violations": [{"invariant": i, "detail": d} for i, d in report.violations],
            "checked": [{"invariant": i, "detail": d} for i, d in report.checked],
        }, indent=2))
    else:
        for invariant, detail in report.checked:
            print(f"  ok   {invariant}  {detail}")
        for invariant, detail in report.violations:
            print(f"  FAIL {invariant}  {detail}")
        print()
        print(f"{len(report.checked)} passed, {len(report.violations)} violated")

    if report.failed:
        for invariant, detail in report.violations:
            print(f"::error title=Pipeline invariant {invariant}::{detail}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
