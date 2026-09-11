"""
test_platform_selection.py
==========================
Validates the `if:` selection logic in the Unity pipeline workflow
(.github/workflows/unity-pipeline.yml) without running GitHub Actions.

The pipeline workflow is the reusable toolkit-local orchestration file that
contains the full job graph (resolve-config → validate → tests → builds →
final-report → notify-discord).  The thin consumer (unity-build.yml) just
calls it via workflow_call.

Strategy
--------
1. Parse unity-pipeline.yml to extract each job's `if:` expression.
2. Evaluate those expressions with mock inputs/needs via a tiny GHA-subset
   evaluator that handles the exact operators used in the workflow.
3. Assert the rules from EXPLICIT_PLATFORM_FLOW_SPEC.md §6 + §3.3 and
   BRANCH_FLOW_CONTRACT.md (outputs-based gating).

Gating-form tolerance
---------------------
The pipeline gates on `needs.resolve-config.outputs.build-<platform>` (new
form).  `_ctx()` injects both input and outputs-based context so the evaluator
is tolerant of any future migration:

  Legacy:  inputs.platform == 'All' || inputs.platform == 'Android'
  New:     needs.resolve-config.outputs.build-android == 'true'

Covered rules
-------------
R1  platform=All          → Android/WebGL/Linux64/LinuxServer/Windows64 True; iOS False
R2  platform=Android      → only build-android True; others False
R3  platform=WebGL        → only build-webgl True
R4  platform=Linux64      → only build-linux64 True
R4w platform=Windows64    → only build-windows64 True
R5  platform=LinuxServer  → only build-linuxserver True
R6  platform=iOS          → only build-ios True
R7  run-tests=false       → unity-tests if: False (skipped)
R8  run-tests=true        → unity-tests if: True (runs)
R9  build-addressables=false → build-addressables if: False;
                               platform builds still True (accept 'skipped')
R10 UNITY_LICENSE declared optional (required: false) in all reusable workflows
R11 No UNITY_SERIAL anywhere in unity-pipeline.yml or reusable-build-platform.yml
R12 final-report if: always() evaluates True regardless of context
R13 validate-project failure → all platform build if: False
R14 notify-discord job exists with if: always()
R15 unity-pipeline.yml on: declares workflow_call (it is a reusable workflow)
"""

import re
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent          # …/unity-build-workflows/
PIPELINE_WF = REPO_ROOT / ".github" / "workflows" / "unity-pipeline.yml"
REUSABLE_BUILD = REPO_ROOT / ".github" / "workflows" / "reusable-build-platform.yml"
REUSABLE_TESTS = REPO_ROOT / ".github" / "workflows" / "reusable-unity-tests.yml"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def consumer_workflow():
    """Load unity-pipeline.yml — the toolkit-local reusable pipeline workflow.

    This file lives inside the toolkit repo itself (REPO_ROOT/.github/workflows/),
    so it is always present in a checkout of unity-build-workflows/.
    Skip gracefully if somehow absent (e.g. shallow clone without the workflows/ dir).
    """
    if not PIPELINE_WF.exists():
        pytest.skip(f"Pipeline workflow not found: {PIPELINE_WF}")
    return yaml.safe_load(PIPELINE_WF.read_text())


@pytest.fixture(scope="module")
def job_ifs(consumer_workflow):
    """Dict mapping job_id → raw if-expression string (or None)."""
    jobs = consumer_workflow.get("jobs", {})
    return {job_id: job.get("if") for job_id, job in jobs.items()}


@pytest.fixture(scope="module")
def reusable_build_wf():
    assert REUSABLE_BUILD.exists(), f"Missing: {REUSABLE_BUILD}"
    return yaml.safe_load(REUSABLE_BUILD.read_text())


@pytest.fixture(scope="module")
def reusable_tests_wf():
    assert REUSABLE_TESTS.exists(), f"Missing: {REUSABLE_TESTS}"
    return yaml.safe_load(REUSABLE_TESTS.read_text())


# ---------------------------------------------------------------------------
# GHA expression evaluator
# ---------------------------------------------------------------------------

class OutputsProxy(dict):
    """Returns '' for missing output keys (avoids KeyError in expressions)."""
    def __missing__(self, key):
        return ""


class NeedsProxy(dict):
    """Returns a safe default for jobs not explicitly in the dict.

    Default: {'result': 'skipped', 'outputs': {}}
    This means a missing job is treated as skipped (not failure) and has no outputs.
    """
    def __missing__(self, key):
        # outputs must be an OutputsProxy too: a job absent from the mock context
        # is still allowed to be read for an output it does not define
        # (e.g. needs.quality-gate.outputs.passed before the gate is seeded).
        return {"result": "skipped", "outputs": OutputsProxy({})}


def eval_gha_expr(expr: Any, context: dict) -> bool:
    """
    Evaluate the GitHub Actions expression subset used in this workflow's if: fields.

    Supported:
      - inputs.<name>                        (hyphenated names OK)
      - needs.<job>.result                   (hyphenated job names OK)
      - needs.<job>.outputs.<name>           (resolve-config.outputs.build-android etc.)
      - 'string' / "string" literals
      - true / false
      - always()                             → True
      - cancelled()                          → False (non-cancelled context assumed)
      - !cancelled()                         → True
      - ==, !=
      - &&, ||, !
      - (...)

    context = {
        'inputs': {'platform': 'All', 'run-tests': False, ...},
        'needs':  {
            'validate-project':  {'result': 'success'},
            'resolve-config':    {'result': 'success', 'outputs': {'build-android': 'true', ...}},
            'build-addressables': {'result': 'skipped'},
        },
    }
    """
    if expr is None:
        # No if: condition → job always runs
        return True
    if isinstance(expr, bool):
        return expr

    s = str(expr)
    # Collapse folded-YAML newlines / extra whitespace
    s = " ".join(s.split())

    # ── Protect string literals so 'true'/'false' inside them are not mangled ──
    # Any single- or double-quoted string is replaced with a placeholder, then
    # restored after all keyword substitutions.  This prevents 'true' → 'True'
    # inside literals like == 'true' (used in outputs-based gating expressions).
    _literals: list[str] = []

    def _protect(m: re.Match) -> str:
        _literals.append(m.group(0))
        return f"__STR{len(_literals) - 1}__"

    s = re.sub(r"'[^']*'|\"[^\"]*\"", _protect, s)

    # GHA status-check functions — replace before keyword substitution
    s = re.sub(r"\balways\(\)", "__TRUE__", s)
    s = re.sub(r"\bcancelled\(\)", "__FALSE__", s)

    # true / false bare keywords (not inside string literals, which are protected)
    s = re.sub(r"\btrue\b", "True", s, flags=re.IGNORECASE)
    s = re.sub(r"\bfalse\b", "False", s, flags=re.IGNORECASE)

    # Restore status-check placeholders
    s = s.replace("__TRUE__", "True").replace("__FALSE__", "False")

    # Restore string literals
    for idx, lit in enumerate(_literals):
        s = s.replace(f"__STR{idx}__", lit)

    # && → and,  || → or
    s = s.replace("&&", " and ").replace("||", " or ")

    # Logical NOT: ! not followed by = → not
    s = re.sub(r"!(?!=)", "not ", s)

    # needs.<job>.outputs.<name> → _needs['<job>']['outputs']['<name>']
    # Must come BEFORE needs.<job>.result to avoid partial overlap issues.
    s = re.sub(
        r"\bneeds\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)\b",
        lambda m: f"_needs['{m.group(1)}']['outputs']['{m.group(2)}']",
        s,
    )

    # needs.<job>.result → _needs['<job>']['result']
    s = re.sub(
        r"\bneeds\.([A-Za-z0-9_-]+)\.result\b",
        lambda m: f"_needs['{m.group(1)}']['result']",
        s,
    )

    # inputs.<name> → _inputs['<name>']  (names may contain hyphens)
    s = re.sub(
        r"\binputs\.([A-Za-z0-9_-]+)",
        lambda m: f"_inputs['{m.group(1)}']",
        s,
    )

    _inputs = context.get("inputs", {})
    # Wrap each needs entry's outputs in OutputsProxy for safe key access
    raw_needs = context.get("needs", {})
    _needs = NeedsProxy({
        job: {
            "result": data.get("result", "skipped"),
            "outputs": OutputsProxy(data.get("outputs", {})),
        }
        for job, data in raw_needs.items()
    })

    try:
        result = eval(  # noqa: S307  (controlled expression from own YAML)
            s,
            {"__builtins__": {}, "True": True, "False": False},
            {"_inputs": _inputs, "_needs": _needs},
        )
        return bool(result)
    except Exception as exc:
        raise ValueError(
            f"GHA evaluator failed on expression:\n  original: {str(expr)!r}\n  rewritten: {s!r}"
        ) from exc


# ---------------------------------------------------------------------------
# Helper to build mock contexts
# ---------------------------------------------------------------------------

PLATFORM_BUILD_JOBS = [
    "build-android",
    "build-webgl",
    "build-linux64",
    "build-linuxserver",
    "build-windows64",
    "build-ios",
]

# build-ios only triggers on explicit 'iOS' dispatch, not on 'All'.
# The consumer workflow intentionally omits iOS from the All path until a
# self-hosted macOS runner is provisioned (spec §5 / §3.3 iOS note).
PLATFORM_BUILD_JOBS_FOR_ALL = [
    "build-android",
    "build-webgl",
    "build-linux64",
    "build-linuxserver",
    "build-windows64",
]

# Baseline needs when validate-project succeeded + build-addressables skipped.
# resolve-config always carries outputs so the new outputs-based gating form works too.
#
# quality-gate (stage 02) is the single edge every stage-03 build now depends on,
# so it is part of the baseline: `gate_passed=False` models a red test suite and
# must leave every platform build unstarted.
def _base_needs(validate="success", addressables="skipped",
                rc_outputs: dict | None = None, gate_passed=None):
    # The gate closes on a failed stage-01 validation, so a context that models
    # "validate-project failed" must also model a closed gate — that is the edge
    # that now stops the builds.
    if gate_passed is None:
        gate_passed = validate == "success"
    return {
        "resolve-config": {
            "result": "success",
            "outputs": rc_outputs or {},
        },
        "validate-project":   {"result": validate},
        "build-addressables": {"result": addressables},
        "quality-gate": {
            "result": "success" if gate_passed else "failure",
            "outputs": {"passed": "true" if gate_passed else "false"},
        },
    }


def _ctx(platform="All", run_tests=False, build_addressables_input=False,
         validate="success", addressables_result="skipped", gate_passed=None):
    """
    Build a mock evaluation context that satisfies BOTH gating forms:

      Legacy (inputs-based):
        inputs.platform == 'All' || inputs.platform == 'Android'
        inputs.run-tests == true
        inputs.build-addressables == true

      New (resolve-config outputs-based, per BRANCH_FLOW_CONTRACT.md):
        needs.resolve-config.outputs.build-android == 'true'
        needs.resolve-config.outputs.run-tests == 'true'
        needs.resolve-config.outputs.build-addressables == 'true'

    Both are provided so the test suite passes regardless of which gating
    form is present in the consumer workflow at the time it runs.
    """
    # Mirror the resolve_build_flow.sh platform logic for 'All' and single platforms
    build_android    = platform in ("All", "Android")
    build_webgl      = platform in ("All", "WebGL")
    build_linux64    = platform in ("All", "Linux64")
    build_linuxserver = platform in ("All", "LinuxServer")
    build_windows64  = platform in ("All", "Windows64")
    build_ios        = (platform == "iOS")  # never included in 'All'

    rc_outputs = {
        "build-android":    "true" if build_android    else "false",
        "build-webgl":      "true" if build_webgl      else "false",
        "build-linux64":    "true" if build_linux64    else "false",
        "build-linuxserver": "true" if build_linuxserver else "false",
        "build-windows64":  "true" if build_windows64  else "false",
        "build-ios":        "true" if build_ios        else "false",
        "run-tests":        "true" if run_tests        else "false",
        "build-addressables": "true" if build_addressables_input else "false",
        "environment":      "development",
    }

    return {
        "inputs": {
            "platform": platform,
            "run-tests": run_tests,
            "build-addressables": build_addressables_input,
            # Windows64 as direct input too (tolerant dual-context)
            "build-windows64": "true" if build_windows64 else "false",
        },
        "needs": _base_needs(
            validate=validate,
            addressables=addressables_result,
            rc_outputs=rc_outputs,
            gate_passed=gate_passed,
        ),
    }


# ---------------------------------------------------------------------------
# R1 – platform=All → all 5 build jobs True
# ---------------------------------------------------------------------------

def test_r1_platform_all_enables_core_builds(job_ifs):
    """R1: platform=All → Android/WebGL/Linux64/LinuxServer True; iOS False (omitted from All).

    iOS is intentionally excluded from the All path until a self-hosted macOS runner
    is provisioned (consumer workflow §3.3 iOS note).
    """
    ctx = _ctx(platform="All", addressables_result="skipped")
    for job in PLATFORM_BUILD_JOBS_FOR_ALL:
        assert job in job_ifs, f"Job {job!r} not found in consumer workflow"
        result = eval_gha_expr(job_ifs[job], ctx)
        assert result, (
            f"R1: expected {job} if: True with platform=All, got False\n"
            f"  expression: {job_ifs[job]!r}"
        )
    # build-ios must be False for platform=All (explicit iOS required)
    assert not eval_gha_expr(job_ifs["build-ios"], ctx), (
        "R1: build-ios should be False when platform=All (iOS requires explicit dispatch)"
    )


def test_r1_platform_all_with_addressables_success(job_ifs):
    """R1 variant: build-addressables result=success also enables core platform builds."""
    ctx = _ctx(platform="All", addressables_result="success")
    for job in PLATFORM_BUILD_JOBS_FOR_ALL:
        assert eval_gha_expr(job_ifs[job], ctx), (
            f"R1: {job} should be True when addressables=success"
        )


# ---------------------------------------------------------------------------
# R2-R6 – Single-platform dispatch
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("selected_platform,expected_true_job", [
    ("Android",     "build-android"),
    ("WebGL",       "build-webgl"),
    ("Linux64",     "build-linux64"),
    ("LinuxServer", "build-linuxserver"),
    ("Windows64",   "build-windows64"),
    ("iOS",         "build-ios"),
])
def test_r2_r6_single_platform(job_ifs, selected_platform, expected_true_job):
    """R2-R6: dispatch with a single platform → only that build job is True."""
    ctx = _ctx(platform=selected_platform)
    for job in PLATFORM_BUILD_JOBS:
        result = eval_gha_expr(job_ifs[job], ctx)
        if job == expected_true_job:
            assert result, (
                f"R{PLATFORM_BUILD_JOBS.index(job)+2}: expected {job} True "
                f"when platform={selected_platform}, got False"
            )
        else:
            assert not result, (
                f"R{PLATFORM_BUILD_JOBS.index(job)+2}: expected {job} False "
                f"when platform={selected_platform}, got True"
            )


# ---------------------------------------------------------------------------
# R7/R8 – run-tests gate
# ---------------------------------------------------------------------------

def test_r7_run_tests_false_skips_unity_tests(job_ifs):
    """R7: run-tests=false → unity-tests if: evaluates False (job skipped)."""
    assert "unity-tests" in job_ifs, "unity-tests job not found in consumer workflow"
    ctx = _ctx(run_tests=False, validate="success")
    assert not eval_gha_expr(job_ifs["unity-tests"], ctx), (
        "R7: unity-tests if: should be False when run-tests=false"
    )


def test_r8_run_tests_true_enables_unity_tests(job_ifs):
    """R8: run-tests=true + validate-project success → unity-tests if: True."""
    ctx = _ctx(run_tests=True, validate="success")
    assert eval_gha_expr(job_ifs["unity-tests"], ctx), (
        "R8: unity-tests if: should be True when run-tests=true and validate succeeded"
    )


def test_r8_run_tests_true_but_validate_failed_skips(job_ifs):
    """R8 edge: run-tests=true but validate-project failed → unity-tests still False."""
    ctx = _ctx(run_tests=True, validate="failure")
    assert not eval_gha_expr(job_ifs["unity-tests"], ctx), (
        "unity-tests should not run when validate-project failed"
    )


# ---------------------------------------------------------------------------
# R9 – build-addressables=false gate
# ---------------------------------------------------------------------------

def test_r9_build_addressables_false_skips_addressables_job(job_ifs):
    """R9a: build-addressables=false → build-addressables job if: False."""
    assert "build-addressables" in job_ifs, "build-addressables job not found"
    ctx = _ctx(build_addressables_input=False, validate="success")
    assert not eval_gha_expr(job_ifs["build-addressables"], ctx), (
        "R9: build-addressables job if: should be False when input build-addressables=false"
    )


def test_r9_build_addressables_false_platform_builds_still_run(job_ifs):
    """R9b: build-addressables skipped → core platform builds still True (accept 'skipped')."""
    # When build-addressables=false, the job is skipped → result='skipped'
    ctx = _ctx(platform="All", build_addressables_input=False,
               addressables_result="skipped")
    for job in PLATFORM_BUILD_JOBS_FOR_ALL:
        assert eval_gha_expr(job_ifs[job], ctx), (
            f"R9: {job} should run when build-addressables is skipped"
        )


def test_r9_build_addressables_true_enables_job(job_ifs):
    """R9c: build-addressables=true + validate success → build-addressables if: True."""
    ctx = _ctx(build_addressables_input=True, validate="success")
    assert eval_gha_expr(job_ifs["build-addressables"], ctx), (
        "build-addressables job should run when input=true and validate succeeded"
    )


def test_r9_build_addressables_failure_blocks_platform_builds(job_ifs):
    """R9d: build-addressables job failed → core platform builds False."""
    ctx = _ctx(platform="All", addressables_result="failure")
    for job in PLATFORM_BUILD_JOBS_FOR_ALL:
        assert not eval_gha_expr(job_ifs[job], ctx), (
            f"R9: {job} should be blocked when build-addressables failed"
        )


# ---------------------------------------------------------------------------
# R10 – UNITY_LICENSE must be optional in reusable workflow declarations
# ---------------------------------------------------------------------------

def _get_workflow_call_secrets(wf: dict) -> dict:
    """
    Extract on.workflow_call.secrets from a parsed YAML workflow dict.

    PyYAML parses the 'on:' key as the boolean True (YAML keyword), so we
    must probe both True and the string 'on'.
    """
    on_block = wf.get(True, wf.get("on", {})) or {}
    return on_block.get("workflow_call", {}).get("secrets", {})


def test_r10_unity_license_optional_in_reusable_build(reusable_build_wf):
    """R10: reusable-build-platform.yml declares UNITY_LICENSE with required: false."""
    secrets = _get_workflow_call_secrets(reusable_build_wf)
    assert "UNITY_LICENSE" in secrets, (
        "R10: UNITY_LICENSE not declared in reusable-build-platform.yml secrets"
    )
    assert secrets["UNITY_LICENSE"].get("required") is False, (
        "R10: UNITY_LICENSE must be 'required: false' in reusable-build-platform.yml"
    )


def test_r10_unity_license_optional_in_reusable_tests(reusable_tests_wf):
    """R10: reusable-unity-tests.yml declares UNITY_LICENSE with required: false."""
    secrets = _get_workflow_call_secrets(reusable_tests_wf)
    assert "UNITY_LICENSE" in secrets, (
        "R10: UNITY_LICENSE not declared in reusable-unity-tests.yml secrets"
    )
    assert secrets["UNITY_LICENSE"].get("required") is False, (
        "R10: UNITY_LICENSE must be 'required: false' in reusable-unity-tests.yml"
    )


def test_r10_no_unity_license_required_true_in_new_reusables():
    """R10: the two NEW reusable workflow files do not mark UNITY_LICENSE required: true.

    Note: legacy workflows (unity-build-gameci.yml, etc.) are intentionally excluded —
    they predate the Personal/free decision and are kept as fallback per the spec.
    """
    new_reusables = [REUSABLE_BUILD, REUSABLE_TESTS]
    violators = []
    for wf_file in new_reusables:
        wf = yaml.safe_load(wf_file.read_text())
        secrets = _get_workflow_call_secrets(wf)
        lic = secrets.get("UNITY_LICENSE", {}) or {}
        if lic.get("required") is True:
            violators.append(wf_file.name)
    assert not violators, (
        f"R10: UNITY_LICENSE marked required:true in new reusable files: {violators}"
    )


# ---------------------------------------------------------------------------
# R11 – No UNITY_SERIAL anywhere
# ---------------------------------------------------------------------------

def test_r11_no_unity_serial_in_pipeline_workflow():
    """R11: UNITY_SERIAL must not appear in unity-pipeline.yml."""
    if not PIPELINE_WF.exists():
        pytest.skip("Pipeline workflow not found")
    text = PIPELINE_WF.read_text()
    assert "UNITY_SERIAL" not in text, (
        "R11: UNITY_SERIAL found in unity-pipeline.yml — must not be used (Personal/free license)"
    )


def test_r11_no_unity_serial_in_reusable_build():
    """R11: UNITY_SERIAL must not appear in reusable-build-platform.yml."""
    text = REUSABLE_BUILD.read_text()
    assert "UNITY_SERIAL" not in text, (
        "R11: UNITY_SERIAL found in reusable-build-platform.yml"
    )


def test_r11_no_unity_serial_in_reusable_tests():
    """R11: UNITY_SERIAL must not appear in reusable-unity-tests.yml."""
    text = REUSABLE_TESTS.read_text()
    assert "UNITY_SERIAL" not in text, (
        "R11: UNITY_SERIAL found in reusable-unity-tests.yml"
    )


# ---------------------------------------------------------------------------
# R12 – final-report always runs
# ---------------------------------------------------------------------------

def test_r12_final_report_if_is_always(job_ifs):
    """R12: final-report if: is always() and evaluates True in any context."""
    assert "final-report" in job_ifs, "final-report job not found in consumer workflow"
    raw = job_ifs["final-report"]
    assert raw is not None, "final-report has no if: condition — should be always()"
    assert "always()" in str(raw), (
        f"R12: final-report if: expected 'always()', got: {raw!r}"
    )
    # Evaluate in a worst-case failed context — must still be True
    worst = {
        "inputs": {"platform": "Android", "run-tests": False},
        "needs": {
            "validate-project":  {"result": "failure"},
            "build-addressables": {"result": "failure"},
            "build-android":     {"result": "failure"},
            "build-webgl":       {"result": "failure"},
            "build-linux64":     {"result": "failure"},
            "build-linuxserver": {"result": "failure"},
            "build-ios":         {"result": "failure"},
            "unity-tests":       {"result": "failure"},
        },
    }
    assert eval_gha_expr(raw, worst), (
        "R12: final-report if: must evaluate True even when all upstreams failed"
    )


# ---------------------------------------------------------------------------
# R13 – validate-project failure blocks all downstream jobs
# ---------------------------------------------------------------------------

def test_r13_validate_failure_blocks_platform_builds(job_ifs):
    """R13: validate-project=failure → all platform build jobs if: False."""
    ctx = _ctx(platform="All", validate="failure")
    for job in PLATFORM_BUILD_JOBS:
        assert not eval_gha_expr(job_ifs[job], ctx), (
            f"R13: {job} should be False when validate-project failed"
        )
    # iOS explicit too
    ctx_ios = _ctx(platform="iOS", validate="failure")
    assert not eval_gha_expr(job_ifs["build-ios"], ctx_ios), (
        "R13: build-ios should be False when validate-project failed"
    )


def test_r13_validate_failure_blocks_unity_tests(job_ifs):
    """R13: validate-project=failure → unity-tests if: False."""
    ctx = _ctx(run_tests=True, validate="failure")
    assert not eval_gha_expr(job_ifs["unity-tests"], ctx), (
        "R13: unity-tests should not run when validate-project failed"
    )


def test_r13_validate_failure_blocks_build_addressables(job_ifs):
    """R13: validate-project=failure → build-addressables if: False."""
    ctx = _ctx(build_addressables_input=True, validate="failure")
    assert not eval_gha_expr(job_ifs["build-addressables"], ctx), (
        "R13: build-addressables should not run when validate-project failed"
    )


# ---------------------------------------------------------------------------
# Structural sanity — all expected jobs exist
# ---------------------------------------------------------------------------

EXPECTED_JOBS = [
    "resolve-config",
    "validate-project",
    "unity-tests",
    "build-addressables",
    "build-android",
    "build-webgl",
    "build-linux64",
    "build-linuxserver",
    "build-windows64",
    "build-ios",
    "final-report",
    "notify-discord",
]


def test_all_expected_jobs_present(consumer_workflow):
    """Sanity: consumer workflow contains all expected job IDs."""
    jobs = set(consumer_workflow.get("jobs", {}).keys())
    missing = set(EXPECTED_JOBS) - jobs
    assert not missing, f"Missing jobs in consumer workflow: {missing}"


def test_job_if_expressions_are_parseable(job_ifs):
    """Smoke: every job if: expression is evaluable by the GHA evaluator."""
    ctx = _ctx(platform="All", run_tests=True, build_addressables_input=True)
    for job_id, expr in job_ifs.items():
        try:
            eval_gha_expr(expr, ctx)
        except Exception as exc:
            pytest.fail(f"Failed to evaluate if: for job {job_id!r}: {exc}")


# ---------------------------------------------------------------------------
# R4w – build-windows64 gates on resolve-config outputs (mirrors Linux64)
# ---------------------------------------------------------------------------

def test_r4w_build_windows64_job_exists(job_ifs):
    """R4w: build-windows64 job is present in unity-pipeline.yml."""
    if "build-windows64" not in job_ifs:
        pytest.skip(
            "build-windows64 job not yet present in unity-pipeline.yml — "
            "waiting for github-actions-engineer to add it."
        )
    # Job exists — subsequent tests will exercise it.


def test_r4w_build_windows64_enabled_when_output_true(job_ifs):
    """R4w: build-windows64 if: True when resolve-config outputs build-windows64=true,
    validate-project success, build-addressables skipped."""
    if "build-windows64" not in job_ifs:
        pytest.skip("build-windows64 job not in pipeline yet")
    ctx = _ctx(platform="Windows64", validate="success", addressables_result="skipped")
    assert eval_gha_expr(job_ifs["build-windows64"], ctx), (
        "R4w: build-windows64 if: should be True when output build-windows64='true' "
        "and validate succeeded"
    )


def test_r4w_build_windows64_enabled_in_all(job_ifs):
    """R4w: build-windows64 if: True when platform=All (Windows64 is in All)."""
    if "build-windows64" not in job_ifs:
        pytest.skip("build-windows64 job not in pipeline yet")
    ctx = _ctx(platform="All", validate="success", addressables_result="skipped")
    assert eval_gha_expr(job_ifs["build-windows64"], ctx), (
        "R4w: build-windows64 should run for platform=All"
    )


def test_r4w_build_windows64_disabled_when_output_false(job_ifs):
    """R4w: build-windows64 if: False when resolve-config outputs build-windows64=false."""
    if "build-windows64" not in job_ifs:
        pytest.skip("build-windows64 job not in pipeline yet")
    ctx = _ctx(platform="Android", validate="success", addressables_result="skipped")
    assert not eval_gha_expr(job_ifs["build-windows64"], ctx), (
        "R4w: build-windows64 if: should be False when build-windows64='false'"
    )


def test_r4w_build_windows64_blocked_when_validate_failed(job_ifs):
    """R4w: validate-project failure → build-windows64 if: False (mirrors R13)."""
    if "build-windows64" not in job_ifs:
        pytest.skip("build-windows64 job not in pipeline yet")
    ctx = _ctx(platform="Windows64", validate="failure")
    assert not eval_gha_expr(job_ifs["build-windows64"], ctx), (
        "R4w: build-windows64 should be False when validate-project failed"
    )


def test_r4w_build_windows64_blocked_when_addressables_failed(job_ifs):
    """R4w: build-addressables failure → build-windows64 if: False (mirrors Linux64 pattern)."""
    if "build-windows64" not in job_ifs:
        pytest.skip("build-windows64 job not in pipeline yet")
    ctx = _ctx(platform="Windows64", validate="success", addressables_result="failure")
    assert not eval_gha_expr(job_ifs["build-windows64"], ctx), (
        "R4w: build-windows64 should be False when build-addressables failed"
    )


def test_r4w_build_windows64_passes_with_addressables_success(job_ifs):
    """R4w: build-addressables success (not just skipped) also allows build-windows64."""
    if "build-windows64" not in job_ifs:
        pytest.skip("build-windows64 job not in pipeline yet")
    ctx = _ctx(platform="Windows64", validate="success", addressables_result="success")
    assert eval_gha_expr(job_ifs["build-windows64"], ctx), (
        "R4w: build-windows64 should run when addressables succeeded"
    )


# ---------------------------------------------------------------------------
# R14 – notify-discord always runs
# ---------------------------------------------------------------------------

def test_r14_notify_discord_job_exists(job_ifs):
    """R14: notify-discord job is present in the pipeline workflow."""
    assert "notify-discord" in job_ifs, (
        "R14: notify-discord job not found in unity-pipeline.yml"
    )


def test_r14_notify_discord_if_is_always(job_ifs):
    """R14: notify-discord if: is always() and evaluates True in any context."""
    assert "notify-discord" in job_ifs, "notify-discord job not found"
    raw = job_ifs["notify-discord"]
    assert raw is not None, "notify-discord has no if: condition — should be always()"
    assert "always()" in str(raw), (
        f"R14: notify-discord if: expected 'always()', got: {raw!r}"
    )
    # Evaluate in a worst-case failed context — must still be True
    worst = {
        "inputs": {"platform": "Android", "run-tests": False},
        "needs": {
            "resolve-config":    {"result": "failure"},
            "validate-project":  {"result": "failure"},
            "build-addressables": {"result": "failure"},
            "build-android":     {"result": "failure"},
            "build-webgl":       {"result": "failure"},
            "build-linux64":     {"result": "failure"},
            "build-linuxserver": {"result": "failure"},
            "build-ios":         {"result": "failure"},
            "unity-tests":       {"result": "failure"},
            "final-report":      {"result": "failure"},
        },
    }
    assert eval_gha_expr(raw, worst), (
        "R14: notify-discord if: must evaluate True even when all upstreams failed"
    )


# ---------------------------------------------------------------------------
# R15 – unity-pipeline.yml is a reusable workflow (workflow_call trigger)
# ---------------------------------------------------------------------------

def test_r15_pipeline_declares_workflow_call(consumer_workflow):
    """R15: unity-pipeline.yml on: block declares workflow_call trigger.

    PyYAML parses the bare 'on:' key as the boolean True, so probe both.
    """
    on_block = consumer_workflow.get(True, consumer_workflow.get("on", {})) or {}
    assert "workflow_call" in on_block, (
        "R15: unity-pipeline.yml on: does not declare 'workflow_call' — "
        "it must be a reusable workflow callable via uses:"
    )


def test_r15_workflow_call_has_inputs(consumer_workflow):
    """R15: workflow_call block declares inputs (at minimum 'platform')."""
    on_block = consumer_workflow.get(True, consumer_workflow.get("on", {})) or {}
    wc = on_block.get("workflow_call", {}) or {}
    inputs = wc.get("inputs", {})
    assert "platform" in inputs, (
        "R15: workflow_call.inputs must declare 'platform' pass-through"
    )


def test_r15_workflow_call_declares_unity_license_secret(consumer_workflow):
    """R15: workflow_call.secrets declares UNITY_LICENSE as optional."""
    secrets = _get_workflow_call_secrets(consumer_workflow)
    assert "UNITY_LICENSE" in secrets, (
        "R15: unity-pipeline.yml workflow_call.secrets must declare UNITY_LICENSE "
        "(so callers can forward it)"
    )
    assert secrets["UNITY_LICENSE"].get("required") is False, (
        "R15: UNITY_LICENSE must be required: false in unity-pipeline.yml workflow_call.secrets"
    )
