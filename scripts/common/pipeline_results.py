#!/usr/bin/env python3
"""pipeline_results.py -- read the build matrix's results, once, for every report.

unity-pipeline.yml's report jobs (stage 07 Final Report and Notify Discord)
each held their own inline Python over the same per-platform result files
(pipeline-result-build-<Platform>/build-<Platform>.json, written by
reusable-build-platform.yml's "Set job outputs"), and the "fill what is
missing from the build outputs" block was copied verbatim into both jobs.
This script is that logic, testable, in one place.

  fill-missing --results-dir D     write a leg's result from its job output
                                   (RJ_<Platform> env, hex JSON) when its
                                   result artifact did not upload
  final-report --results-dir D     stage 07: verdict, failed stage, step
                                   summary; outputs overall / failed-stage /
                                   platform-summary; exit 1 on a failed run
  discord-status                   unpack final-report's platform-summary into
                                   the Discord action's per-platform inputs
  attachable --results-dir D --max-bytes N
                                   the artifact names small enough to attach,
                                   as a download-artifact pattern
  diagnostics --results-dir D --build-type T [--artifact-map F]
                                   per-platform errors, warnings, log / binary
                                   artifact ids, artifact name and download
                                   links for the Discord embed

Every subcommand reads its inputs from the environment the calling step sets
and writes $GITHUB_OUTPUT. Python 3.8-compatible, stdlib only.
"""
import argparse
import json
import os
import pathlib
import re
import sys


def fill_missing(results_dir):
    root = pathlib.Path(results_dir)
    have = {p.name for p in root.rglob("build-*.json")} if root.is_dir() else set()
    for key, raw in sorted(os.environ.items()):
        if not key.startswith("RJ_") or not raw.strip():
            continue
        try:
            # hex (see reusable-build-platform.yml); raw JSON
            # from an older toolkit is accepted too.
            text = raw.strip()
            if not text.startswith("{"):
                text = bytes.fromhex(text).decode("utf-8")
            row = json.loads(text)
            raw = text
        except (ValueError, UnicodeDecodeError):
            print(f"::warning::{key} is not a result; ignored")
            continue
        name = f"build-{row.get('platform') or key[3:]}.json"
        if name in have:
            continue
        target = root / "from-outputs" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(raw)
        print(f"[results] {name} taken from the build output (artifact missing)")


def final_report(results_dir):

    env = os.environ
    results_dir = pathlib.Path(results_dir)

    # ── Collect what the matrix legs reported ────────────────────────
    builds, validations = {}, {}
    for path in sorted(results_dir.rglob("*.json")) if results_dir.is_dir() else []:
        try:
            row = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        target = builds if row.get("stage") == "03" else validations
        target[row.get("platform", "?")] = row

    # Platforms the pipeline INTENDED to build. One present here but absent
    # from `builds` did not report its result.
    #
    # Whether that is a failure depends on the matrix's own verdict. If
    # the build matrix succeeded, a missing file is a REPORTING gap and
    # must not fail an otherwise green run — that exact bug failed
    # NDCUnityTemplate run 34567145749, where Android and WebGL both
    # built and validated and only the report went red. If the matrix did
    # not succeed, a missing file means that leg died, and it counts.
    try:
        planned = [r["platform"] for r in json.loads(env.get("BUILD_MATRIX") or "[]")]
    except json.JSONDecodeError:
        planned = []
    matrix_result = env.get("R_BUILD", "")
    unreported = "unreported" if matrix_result == "success" else ""
    for platform in planned:
        builds.setdefault(platform, {"platform": platform, "stage": "03",
                                     "result": unreported, "artifactType": "",
                                     "artifactSizeBytes": "0", "durationSeconds": "0"})

    EMOJI = {"success": "✅", "failure": "❌", "skipped": "⏭️",
             "cancelled": "🚫", "blocked": "⛔", "unreported": "➖"}
    def emoji(result):
        return EMOJI.get(result, "❓")

    def mib(value):
        try:
            n = int(value)
        except (TypeError, ValueError):
            return "—"
        return f"{n / 1048576:.1f} MB" if n else "—"

    def dur(value):
        try:
            n = int(value)
        except (TypeError, ValueError):
            return "—"
        return f"{n // 60}m {n % 60}s" if n else "—"

    def passed(result):
        # "unreported" = the leg's result file is missing but the matrix
        # succeeded. Surfaced in the table, never fatal.
        return result in ("success", "skipped", "unreported")

    # ── Overall verdict + the stage that broke it ────────────────────
    failed_stage = ""
    ordered = [
        # First, because everything else is skipped when it fails and
        # `skipped` counts as a pass.
        ("01 PREPARE / Resolve Build Config",   env.get("R_RESOLVE", "")),
        ("01 PREPARE / Validate Unity Project", env.get("R_VALIDATE", "")),
        ("01 PREPARE / Validate Unity License", env.get("R_LICENSE", "")),
        ("02 QUALITY GATE / Unity Tests",       env.get("R_TESTS", "")),
        ("02 QUALITY GATE",                     env.get("R_GATE", "")),
        ("03 BUILD ARTIFACTS / Addressables",   env.get("R_ADDR", "")),
    ]
    for label, result in ordered:
        if not passed(result) and not failed_stage:
            failed_stage = f"{label} ({result or 'no result'})"
    if not failed_stage and env.get("GATE_BLOCKER"):
        failed_stage = f"02 QUALITY GATE / {env['GATE_BLOCKER']}"
    for platform, row in sorted(builds.items()):
        if not passed(row.get("result", "")) and not failed_stage:
            failed_stage = f"03 BUILD ARTIFACTS / {platform} ({row.get('result') or 'no result'})"
    for platform, row in sorted(validations.items()):
        if not passed(row.get("result", "")) and not failed_stage:
            failed_stage = f"04 ARTIFACT VALIDATION / {platform} ({row.get('result') or 'no result'})"

    overall = "failure" if failed_stage else "success"

    # ── Step summary ─────────────────────────────────────────────────
    build_type = env.get("BUILD_TYPE") or "development"
    out = []
    w = out.append

    # GitHub renders no progress indicator on a workflow node, so the
    # closest honest thing is to draw the pipeline's shape here: which
    # stages ran, which are green, and where it stopped.
    def bar(done, total, width=24):
        if total <= 0:
            return "░" * width
        filled = max(0, min(width, round(width * done / total)))
        return "█" * filled + "░" * (width - filled)

    STAGES = [
        ("01 Prepare", [env.get("R_RESOLVE", ""), env.get("R_VALIDATE", ""),
                        env.get("R_LICENSE", "")]),
        ("02 Quality Gate", [env.get("R_TESTS", ""), env.get("R_GATE", "")]),
        ("03 Build", [env.get("R_ADDR", "")] + [r.get("result", "") for r in builds.values()]),
        ("04 Validate", [r.get("result", "") for r in validations.values()]),
    ]

    w("## Unity Build — Final Report")
    w("")
    w(f"**{emoji(overall)} {overall.upper()}**"
      + (f" — failed at **{failed_stage}**" if failed_stage else ""))
    w("")
    w("```text")
    for label, results in STAGES:
        results = [r for r in results if r != ""]
        if not results:
            w(f"{label:<16} {bar(0, 1)}  —")
            continue
        ok = sum(1 for r in results if r in ("success", "skipped", "unreported"))
        bad = [r for r in results if r not in ("success", "skipped", "unreported")]
        mark = "FAILED" if bad else "ok"
        w(f"{label:<16} {bar(ok, len(results))}  {ok}/{len(results)} {mark}")
    w("```")
    w("")
    w("| Field | Value |")
    w("|---|---|")
    w(f"| Environment | `{env.get('ENVIRONMENT') or 'unknown'}` "
      f"({env.get('CONFIGURATION') or '—'}) |")
    w(f"| Version | `{env.get('APP_VERSION') or 'unknown'}` |")
    w(f"| Build number | `{env.get('BUILD_NUMBER')}` |")
    w(f"| Commit | `{(env.get('COMMIT') or '')[:8]}` |")
    w(f"| Branch | `{env.get('REF_NAME')}` |")
    w(f"| Flow | `{env.get('FLOW_TYPE') or 'unknown'}` |")
    w(f"| Unity | `{env.get('UNITY_VERSION') or 'unknown'}` |")
    w(f"| Signing | `{env.get('SIGNING') or 'none'}` |")
    w(f"| Run | [{env.get('RUN_ID')}]({env.get('RUN_URL')}) |")
    w("")
    w("### 01–02 — Prepare & Quality Gate")
    w("| Node | Result |")
    w("|---|---|")
    for label, key in (("01 / Resolve Build Config", "R_RESOLVE"),
                       ("01 / Validate Unity Project", "R_VALIDATE"),
                       ("01 / Validate Unity License", "R_LICENSE"),
                       (f"02 / Unity Tests ({env.get('TEST_MODE') or 'N/A'})", "R_TESTS"),
                       ("02 / Quality Gate", "R_GATE")):
        result = env.get(key, "")
        w(f"| {label} | {emoji(result)} `{result or 'no result'}` |")
    if env.get("GATE_BLOCKER"):
        w("")
        w(f"> ⛔ Gate closed at **{env['GATE_BLOCKER']}** — no platform build was started.")
    w("")
    w("### 03 — Build Artifacts")
    if builds:
        done = sum(1 for r in builds.values()
                   if r.get("result") in ("success", "skipped", "unreported"))
        w("")
        w("```text")
        for platform, row in sorted(builds.items()):
            result = row.get("result", "")
            glyph = {"success": "████████ done",
                     "failure": "██░░░░░░ FAILED",
                     "cancelled": "██░░░░░░ cancelled",
                     "skipped": "░░░░░░░░ skipped"}.get(result, "░░░░░░░░ " + (result or "no result"))
            w(f"  {platform:<14} {glyph}")
        w(f"  {'':<14} {done}/{len(builds)} platforms")
        w("```")
        w("")
        w("| Platform | Result | Type | Size | Duration | Errors | Warnings | Artifact |")
        w("|---|---|---|---|---|---|")
        addr = env.get("R_ADDR", "")
        w(f"| Addressables | {emoji(addr)} `{addr or 'no result'}` | — | — | — | "
          f"`{build_type}-addressables` |")
        for platform, row in sorted(builds.items()):
            result = row.get("result", "")
            errs = int(row.get("errorCount") or 0)
            warns = int(row.get("warningCount") or 0)
            w(f"| {platform} | {emoji(result)} `{result or 'no result'}` | "
              f"`{row.get('artifactType') or '—'}` | {mib(row.get('artifactSizeBytes'))} | "
              f"{dur(row.get('durationSeconds'))} | "
              f"{'**' + str(errs) + '**' if errs else '0'} | {warns} | "
              f"`{row.get('artifactName') or '—'}` |")
        # A count tells you something is wrong; the message tells you
        # what. Stage 07 is where someone looks after a red run, so the
        # first error belongs here rather than in a log artifact.
        failed_with_reason = [(p, r) for p, r in sorted(builds.items())
                              if r.get("firstError")]
        if failed_with_reason:
            w("")
            w("**First error per platform**")
            w("")
            for platform, row in failed_with_reason:
                w(f"- **{platform}** — {row['firstError']}")
    else:
        w("_No platform build ran._")
    w("")
    w("### 04 — Artifact Validation")
    if validations:
        w("| Platform | Result | Report |")
        w("|---|---|---|")
        for platform, row in sorted(validations.items()):
            result = row.get("result", "")
            artifact = builds.get(platform, {}).get("artifactName", "")
            report_name = f"{artifact}-validation-report" if artifact else "—"
            w(f"| {platform} | {emoji(result)} `{result or 'no result'}` | "
              f"`{report_name}` |")
    else:
        w("_No artifact validation ran._")
    w("")
    # A Build / Release run produces immutable artifacts and stops. Nothing
    # in the graph said what to do with them, which is why the release layer
    # looked absent — this hands the reader straight to it.
    RELEASE_LANES = {
        "Android": ("Release / Android", "20-release-android.yml", "Google Play"),
        "iOS": ("Release / iOS", "21-release-ios.yml", "App Store Connect"),
        "WebGL": ("Release / WebGL", "22-release-webgl.yml", "hosting / CDN"),
        "Windows64": ("Release / Windows", "23-release-windows.yml", "Steam"),
        "Linux64": ("Release / Linux", "24-release-linux.yml", "Steam"),
    }
    if build_type == "release" and not failed_stage:
        shippable = [p for p, row in sorted(builds.items())
                     if row.get("result") == "success" and p in RELEASE_LANES]
        if shippable:
            w("### Next: promote these artifacts")
            w("")
            w("These are immutable. The release workflows publish *this* binary — "
              "nothing is rebuilt, so what QA approves is what ships.")
            w("")
            w("| Platform | Artifact | Run | Publishes to |")
            w("|---|---|---|---|")
            for platform in shippable:
                lane, wf, dest = RELEASE_LANES[platform]
                artifact = builds[platform].get("artifactName", "")
                w(f"| {platform} | `{artifact}` | **{lane}** (`{wf}`) | {dest} |")
            w("")
            # The release workflows never build. They pull the artifact
            # from THIS run, so the run id is not optional context — it is
            # how the promotion finds the binary.
            first = shippable[0]
            lane, wf, _ = RELEASE_LANES[first]
            w("```bash")
            w(f"gh workflow run {wf} --ref main \\")
            w(f"  -f build-version={env.get('APP_VERSION') or '<version>'} \\")
            w(f"  -f artifact-name={builds[first].get('artifactName', '')} \\")
            w(f"  -f source-run-id={env.get('RUN_ID', '')}")
            w("```")
            w("")
    elif build_type == "development":
        w("> This is a development build. It is not signed for release and "
          "cannot be published — use **Build / Release** for that.")
        w("")
    w(f"> Logs: `{build_type}-<platform>-<artifact>-logs`. "
      f"Manifests: `{build_type}-<platform>-<artifact>-manifest`.")
    w("> Publishing is a separate pipeline — see `pipeline-android-release.yml`, "
      "`pipeline-ios-release.yml`, `pipeline-webgl-release.yml`.")

    summary = "\n".join(out)
    with open(env["GITHUB_STEP_SUMMARY"], "a") as fh:
        fh.write(summary + "\n")

    # ── Outputs for the Discord stage ────────────────────────────────
    compact = [
        {"platform": p,
         "build": builds[p].get("result", ""),
         "validation": validations.get(p, {}).get("result", ""),
         "artifactType": builds[p].get("artifactType", ""),
         "artifactSizeBytes": builds[p].get("artifactSizeBytes", "0")}
        for p in sorted(builds)
    ]
    with open(env["GITHUB_OUTPUT"], "a") as fh:
        fh.write(f"overall={overall}\n")
        fh.write(f"failed-stage={failed_stage}\n")
        fh.write("platform-summary=" + json.dumps(compact) + "\n")

    if failed_stage:
        print(f"::error::Pipeline failed at {failed_stage}. See the matching job logs.")
        return 1
    return 0


def discord_status():

    env = os.environ
    try:
        summary = json.loads(env.get("PLATFORM_SUMMARY") or "[]")
    except json.JSONDecodeError:
        summary = []

    by_platform = {row.get("platform", ""): row for row in summary}

    # `ok` exists to detect a PARTIAL build — some platforms up, some down.
    # A passing test run is not a platform that built, so tests/validate/
    # Addressables count toward `fail` but never toward `ok`.
    fail = ok = False
    cancelled = env.get("R_BUILD") == "cancelled" or env.get("V_ALL") == "cancelled"
    for key in ("R_VALIDATE", "R_TESTS", "R_ADDR"):
        value = env.get(key, "")
        if value == "cancelled":
            cancelled = True
        elif value == "failure":
            fail = True

    lines = []
    for platform, row in by_platform.items():
        build = row.get("build", "")
        validation = row.get("validation", "")
        if build == "cancelled" or validation == "cancelled":
            cancelled = True
        if build == "failure" or validation == "failure":
            fail = True
        if build == "success":
            ok = True
        lines.append(f"result-{platform.lower()}={build or 'skipped'}")
        if validation:
            lines.append(f"validation-{platform.lower()}={validation}")

    if cancelled:
        overall = "cancelled"
    elif fail and ok:
        overall = "partial"
    elif fail:
        overall = "failure"
    else:
        # Trust stage 07 when it saw something this step cannot.
        overall = "failure" if env.get("REPORT_OVERALL") == "failure" else "success"

    with open(env["GITHUB_OUTPUT"], "a") as fh:
        fh.write(f"overall={overall}\n")
        for line in lines:
            fh.write(line + "\n")

    print(f"Overall Discord status: {overall}")
    if env.get("REPORT_FAILED_STAGE"):
        print(f"Failed at: {env['REPORT_FAILED_STAGE']}")


def attachable(results_dir, max_bytes):
    root = pathlib.Path(results_dir)
    limit = int(max_bytes)
    names = set()
    for path in sorted(root.rglob("build-*.json")) if root.is_dir() else []:
        try:
            row = json.loads(path.read_text())
            size = int(row.get("artifactSizeBytes") or 0)
        except (ValueError, OSError):
            continue
        name = (row.get("artifactName") or "").strip()
        if row.get("result") == "success" and name and 0 < size <= limit:
            names.add(name)
    names = sorted(names)
    # One name is a literal; minimatch expands {a,b} only with a comma.
    pattern = names[0] if len(names) == 1 else ("{%s}" % ",".join(names) if names else "")
    print(f"[attach] {', '.join(names) or 'nothing small enough to attach'}")
    with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
        fh.write(f"pattern={pattern}\n")


PLATFORMS = ("Android", "WebGL", "Linux64", "LinuxServer", "Windows64", "iOS")


def _results_by_platform(results_dir):
    rows = {}
    root = pathlib.Path(results_dir)
    for path in sorted(root.rglob("build-*.json")) if root.is_dir() else []:
        try:
            row = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        rows.setdefault(row.get("platform") or path.stem[len("build-"):], row)
    return rows


def diagnostics(results_dir, build_type, artifact_map_file=None):
    """One `Platform=errors,warnings,logsId,binId,artifactName,downloadUrl[|directUrl]`
    line per platform that has an artifact name, for discord-upload-build.

    The counts come from the build leg's own log summary (errorCount /
    warningCount in its result), and only when that leg actually read an
    Editor log (logMeasured). Empty, not 0, otherwise: "we did not read a log"
    and "the log said zero" are different claims, and the Docker / game-ci
    lane writes no Editor.log at all. This used to grep Editor.log out of the
    downloaded log artifacts, which stopped working once the report job
    stopped downloading every artifact of the run.
    """
    # name -> id of this run's artifacts (ARTIFACT_STORAGE=github); empty with
    # firebase storage, where the binary never reaches GitHub.
    ids = {}
    if artifact_map_file and pathlib.Path(artifact_map_file).is_file():
        for line in pathlib.Path(artifact_map_file).read_text().splitlines():
            parts = line.split()
            if len(parts) == 2:
                ids.setdefault(parts[0], parts[1])
    results = _results_by_platform(results_dir)

    lines, total_errors, total_warnings, any_log = [], 0, 0, False
    for platform in PLATFORMS:
        row = results.get(platform, {})
        # Artifacts are named {product}_{version}_{build}_{type}_{platform}[_{kind}].
        pattern = re.compile(r"_%s_%s(_[a-z0-9]+)?$" % (re.escape(build_type),
                                                        re.escape(platform.lower())))
        name = next((n for n in sorted(ids) if pattern.search(n)), "") \
            or (row.get("artifactName") or "")
        if not name:
            continue
        errors = warnings = ""
        if str(row.get("logMeasured", "")).lower() == "true":
            errors = str(int(row.get("errorCount") or 0))
            warnings = str(int(row.get("warningCount") or 0))
            total_errors += int(errors)
            total_warnings += int(warnings)
            any_log = True
        download = row.get("downloadUrl") or ""
        direct = row.get("directDownloadUrl") or ""
        link = download + ("|" + direct if direct else "")
        lines.append("%s=%s,%s,%s,%s,%s,%s" % (platform, errors, warnings,
                                               ids.get(name + "-logs", ""),
                                               ids.get(name, ""), name, link))
        print("[diagnostics] %s: %s (errors %s, warnings %s)"
              % (platform, name, errors or "not measured", warnings or "not measured"))

    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as fh:
            fh.write("platform-diagnostics<<EOF\n")
            for line in lines:
                fh.write(line + "\n")
            fh.write("EOF\n")
            # Empty, so the embed omits the field instead of claiming a zero.
            fh.write("total-errors=%s\n" % (total_errors if any_log else ""))
            fh.write("total-warnings=%s\n" % (total_warnings if any_log else ""))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read the build matrix's results for the report jobs.")
    sub = parser.add_subparsers(dest="command")
    sub.required = True
    p = sub.add_parser("fill-missing")
    p.add_argument("--results-dir", required=True)
    p = sub.add_parser("final-report")
    p.add_argument("--results-dir", required=True)
    sub.add_parser("discord-status")
    p = sub.add_parser("attachable")
    p.add_argument("--results-dir", required=True)
    p.add_argument("--max-bytes", required=True, type=int)
    p = sub.add_parser("diagnostics")
    p.add_argument("--results-dir", required=True)
    p.add_argument("--build-type", required=True)
    p.add_argument("--artifact-map", default=None,
                   help="file of '<artifact name> <id>' lines for this run")
    args = parser.parse_args(argv)

    if args.command == "fill-missing":
        return fill_missing(args.results_dir) or 0
    if args.command == "final-report":
        return final_report(args.results_dir)
    if args.command == "discord-status":
        return discord_status() or 0
    if args.command == "attachable":
        return attachable(args.results_dir, args.max_bytes) or 0
    return diagnostics(args.results_dir, args.build_type, args.artifact_map)


if __name__ == "__main__":
    sys.exit(main())
