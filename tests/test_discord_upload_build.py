"""
test_discord_upload_build.py
============================
Static + subprocess tests for .github/actions/discord-upload-build/action.yml.

All tests are pure static analysis or local subprocess calls — no network access,
no Discord credentials, no GitHub Actions runner context required.

Covered checks
--------------
A1  action.yml is valid YAML and parses without errors
A2  All expected inputs declared (from DISCORD_BUILD_DELIVERY_PLAN.md §3.1)
A3  Required input 'status' marked required: true; all others have defaults
A4  action uses 'composite' runner

S1  bash size-check tests pass: runs test_discord_size_check.sh → exit 0
S2  bash size-check script is executable (+x bit set)

C1  Webhook masking: ::add-mask:: present in run script
C2  Secret protection: set +x present (xtrace off before webhook use)
C3  Failure tolerance: set +e present (exit-on-error disabled for entire script)
C4  No-op guard: DISCORD_WEBHOOK_URL guard present in run script
C5  Thread routing: ?thread_id= URL parameter construction present
C6  Never exit 1: no bare 'exit 1' that could fail the pipeline
C7  Failure mode: only exit 0 used for early returns (::warning:: + exit 0 pattern)
C8  Temp file cleanup: rm -f cleanup step present
C9  Sub-1MB normalisation: ZIP_MB=1 assignment present (guards against 0-byte zips)
C10 curl retry flags present (--retry)
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
ACTION_FILE = REPO_ROOT / ".github" / "actions" / "discord-upload-build" / "action.yml"
SIZE_CHECK_SCRIPT = Path(__file__).parent / "test_discord_size_check.sh"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def action_yaml():
    """Parsed action.yml dict."""
    if not ACTION_FILE.exists():
        pytest.skip(f"Action file not found: {ACTION_FILE}")
    return yaml.safe_load(ACTION_FILE.read_text())


@pytest.fixture(scope="module")
def action_text():
    """Raw text of action.yml for grep-style checks."""
    if not ACTION_FILE.exists():
        pytest.skip(f"Action file not found: {ACTION_FILE}")
    return ACTION_FILE.read_text()


@pytest.fixture(scope="module")
def run_script(action_yaml):
    """Extract the shell script body from runs.steps[0].run."""
    steps = action_yaml.get("runs", {}).get("steps", [])
    assert steps, "No steps found in runs.steps"
    run = steps[0].get("run", "")
    assert run, "steps[0].run is empty"
    return run


# ---------------------------------------------------------------------------
# A1-A4: Action structure
# ---------------------------------------------------------------------------

class TestActionStructure:
    def test_a1_parses_as_valid_yaml(self, action_yaml):
        """A1: action.yml is valid YAML (fixture itself proves parse success)."""
        assert isinstance(action_yaml, dict), "Parsed YAML is not a dict"
        assert "name" in action_yaml, "Missing 'name' key in action"

    def test_a1_has_description(self, action_yaml):
        assert "description" in action_yaml, "Missing 'description' in action"

    def test_a2_inputs_key_present(self, action_yaml):
        """A2: action declares an 'inputs' block."""
        assert "inputs" in action_yaml, "Missing 'inputs' block in action"

    def test_a2_all_expected_inputs_declared(self, action_yaml):
        """A2: All inputs from DISCORD_BUILD_DELIVERY_PLAN.md §3.1 are present."""
        declared = set(action_yaml.get("inputs", {}).keys())
        expected = {
            # Run context
            "status",
            "flow-type",
            "environment",
            "run-url",
            "commit-sha",
            "branch",
            "unity-version",
            # Per-platform results
            "result-android",
            "result-webgl",
            "result-linux64",
            "result-linuxserver",
            "result-windows64",
            "result-ios",
            "result-addressables",
            "result-tests",
            # Thread routing
            "thread-id",
            "thread-name",
            # File attachment
            "artifact-dir",
            "attach-size-threshold-mb",
            # Trigger / commit / timing context
            "actor",
            "event",
            "run-number",
            "repository",
            "commit-message",
            "commit-author",
            "build-duration",
            # Diagnostics (error/warning counts + log links)
            "platform-diagnostics",
            "total-errors",
            "total-warnings",
            # Build info (project metadata)
            "product-name",
            "app-version",
            "bundle-id",
            "scripting-backend",
            "android-arch",
            "orientation",
            "store-link",
            "android-export-type",
            "define-symbols-count",
        }
        missing = expected - declared
        assert not missing, f"A2: Missing inputs in action.yml: {missing}"

    def test_a3_status_is_required(self, action_yaml):
        """A3: 'status' input is marked required: true."""
        inputs = action_yaml.get("inputs", {})
        assert inputs.get("status", {}).get("required") is True, (
            "A3: 'status' input must have required: true"
        )

    def test_a3_optional_inputs_have_defaults(self, action_yaml):
        """A3: All optional inputs have a 'default' value."""
        inputs = action_yaml.get("inputs", {})
        no_default = [
            name for name, cfg in inputs.items()
            if not cfg.get("required") and cfg.get("default") is None
        ]
        assert not no_default, (
            f"A3: Optional inputs without defaults: {no_default}"
        )

    def test_a4_composite_runner(self, action_yaml):
        """A4: Action uses 'composite' runner (not docker/node)."""
        using = action_yaml.get("runs", {}).get("using", "")
        assert using == "composite", (
            f"A4: Expected runs.using='composite', got {using!r}"
        )

    def test_a4_has_at_least_one_step(self, action_yaml):
        steps = action_yaml.get("runs", {}).get("steps", [])
        assert len(steps) >= 1, "A4: No steps in runs.steps"

    def test_a4_step_uses_bash(self, action_yaml):
        """A4: The main step uses bash shell."""
        steps = action_yaml.get("runs", {}).get("steps", [])
        assert steps, "No steps"
        shell = steps[0].get("shell", "")
        assert shell == "bash", f"A4: Expected shell='bash', got {shell!r}"


# ---------------------------------------------------------------------------
# S1-S2: Bash size-check tests
# ---------------------------------------------------------------------------

class TestSizeCheckScript:
    def test_s2_script_is_executable(self):
        """S2: test_discord_size_check.sh has the executable bit set."""
        if not SIZE_CHECK_SCRIPT.exists():
            pytest.skip(f"Size-check script not found: {SIZE_CHECK_SCRIPT}")
        mode = SIZE_CHECK_SCRIPT.stat().st_mode
        assert bool(mode & stat.S_IXUSR), (
            f"S2: {SIZE_CHECK_SCRIPT.name} is not executable (mode={oct(mode)})"
        )

    def test_s1_bash_size_checks_all_pass(self):
        """S1: Running test_discord_size_check.sh exits 0 (all cases pass)."""
        if not SIZE_CHECK_SCRIPT.exists():
            pytest.skip(f"Size-check script not found: {SIZE_CHECK_SCRIPT}")
        result = subprocess.run(
            ["bash", str(SIZE_CHECK_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=15,
            env={
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": os.environ.get("HOME", "/tmp"),
            },
        )
        # Print stdout for diagnosis on failure
        if result.returncode != 0:
            print("STDOUT:\n", result.stdout)
            print("STDERR:\n", result.stderr)
        assert result.returncode == 0, (
            f"S1: test_discord_size_check.sh exited {result.returncode}:\n"
            f"{result.stdout}\n{result.stderr}"
        )
        # Ensure no FAILs appear in output (belt-and-suspenders)
        assert "FAIL:" not in result.stdout, (
            f"S1: FAIL lines in output:\n{result.stdout}"
        )


# ---------------------------------------------------------------------------
# C1-C10: Static checks on the run script body
# ---------------------------------------------------------------------------

class TestRunScriptStatic:
    def test_c1_webhook_masking_present(self, run_script):
        """C1: ::add-mask:: instruction masks the webhook URL before first use."""
        assert "::add-mask::" in run_script, (
            "C1: '::add-mask::' not found in run script — webhook URL will leak to logs"
        )

    def test_c2_xtrace_disabled(self, run_script):
        """C2: set +x disables xtrace (prevents webhook URL appearing in debug logs)."""
        assert "set +x" in run_script, (
            "C2: 'set +x' not found — xtrace must be off before any webhook use"
        )

    def test_c3_exit_on_error_disabled(self, run_script):
        """C3: set +e disables exit-on-error so the action never fails the pipeline."""
        assert "set +e" in run_script, (
            "C3: 'set +e' not found — action must not exit-on-error"
        )

    def test_c2_c3_ordering(self, run_script):
        """C2+C3: set +x appears before set +e (xtrace off before any risky operation)."""
        px = run_script.find("set +x")
        pe = run_script.find("set +e")
        assert px != -1 and pe != -1, "set +x and/or set +e missing"
        assert px < pe, (
            f"C2+C3: 'set +x' (pos {px}) should appear before 'set +e' (pos {pe})"
        )

    def test_c4_webhook_guard_present(self, run_script):
        """C4: Guard exits 0 when DISCORD_WEBHOOK_URL is unset (no-op mode)."""
        # Accept either -z or empty-string test form
        has_guard = (
            "-z" in run_script and "DISCORD_WEBHOOK_URL" in run_script
        ) or (
            "${DISCORD_WEBHOOK_URL:-}" in run_script
        )
        assert has_guard, (
            "C4: No-op guard for missing DISCORD_WEBHOOK_URL not found in run script"
        )

    def test_c4_guard_exits_zero(self, run_script):
        """C4: The guard block uses 'exit 0' not 'exit 1'."""
        # Find the guard block area
        guard_start = run_script.find("DISCORD_WEBHOOK_URL")
        assert guard_start != -1, "DISCORD_WEBHOOK_URL not referenced in script"
        # In the ~300 chars after the guard check, exit 0 must appear
        guard_window = run_script[guard_start: guard_start + 300]
        assert "exit 0" in guard_window, (
            "C4: Guard does not contain 'exit 0' — missing no-op exit"
        )

    def test_c5_thread_id_routing_present(self, run_script):
        """C5: thread_id routing appends ?thread_id= to the webhook URL."""
        assert "thread_id" in run_script, (
            "C5: 'thread_id' not found in run script — thread routing not implemented"
        )
        assert "?thread_id=" in run_script, (
            "C5: '?thread_id=' URL parameter not found — thread routing incomplete"
        )

    def test_c6_no_bare_exit_1(self, run_script):
        """C6: No 'exit 1' in the script — action must never fail the pipeline."""
        import re
        # Match 'exit 1' but not 'exit 10', 'exit 11', etc.
        hits = re.findall(r'\bexit\s+1\b', run_script)
        assert not hits, (
            f"C6: Found bare 'exit 1' in run script — action must not fail pipeline: {hits}"
        )

    def test_c7_only_exit_zero_used(self, run_script):
        """C7: All explicit exits use exit 0 (clean early-return, not error)."""
        import re
        exits = re.findall(r'\bexit\s+(\d+)\b', run_script)
        non_zero = [e for e in exits if e != "0"]
        assert not non_zero, (
            f"C7: Non-zero exit codes found: {non_zero} — all exits must be 0"
        )

    def test_c8_temp_file_cleanup(self, run_script):
        """C8: Temp zip/response files are cleaned up after use."""
        assert "rm -f" in run_script, (
            "C8: No 'rm -f' cleanup found in run script"
        )
        assert "/tmp/" in run_script, (
            "C8: No /tmp/ references found — are temp files being used?"
        )

    def test_c9_sub1mb_normalisation(self, run_script):
        """C9: sub-1MB zips are normalised to 1MB so they count against threshold."""
        assert "PLAT_SIZE_MB=1" in run_script, (
            "C9: sub-1MB normalisation (PLAT_SIZE_MB=1) not found in run script"
        )

    def test_c10_curl_retry_flag(self, run_script):
        """C10: curl uses --retry for transient network failures."""
        assert "--retry" in run_script, (
            "C10: '--retry' flag not found in curl call — transient failures unhandled"
        )

    def test_c10_curl_timeout_flag(self, run_script):
        """C10: curl uses --max-time to prevent indefinite hangs."""
        assert "--max-time" in run_script, (
            "C10: '--max-time' not found in curl call — potential pipeline hang"
        )

    def test_c_warning_annotation_on_error(self, run_script):
        """C: Errors produce ::warning:: annotations (not ::error:: which sets red status)."""
        assert "::warning::" in run_script, (
            "Missing '::warning::' annotation — errors should emit warnings, not errors"
        )
        # ::error:: would mark the step failed; must not appear for delivery failures
        import re
        error_annotations = re.findall(r'::error::', run_script)
        # error annotations are acceptable for input validation or programming bugs,
        # but should NOT appear for network/webhook failures.
        # We just ensure ::warning:: is present and is the dominant failure path.
        warning_count = len(re.findall(r'::warning::', run_script))
        assert warning_count >= 3, (
            f"Expected ≥3 ::warning:: annotations (one per failure path), got {warning_count}"
        )


# ---------------------------------------------------------------------------
# Integration: action text-level checks (raw YAML text, pre-parse)
# ---------------------------------------------------------------------------

class TestActionTextLevel:
    def test_action_name_matches_purpose(self, action_text):
        """Action name contains 'Discord' and 'Upload'/'Build'."""
        assert "Discord" in action_text, "Action name/description missing 'Discord'"

    def test_webhook_url_from_env_not_input(self, action_yaml):
        """DISCORD_WEBHOOK_URL is read from environment, never declared as an action input key."""
        # Check the parsed inputs dict — the URL must not be an input name.
        # (It may appear in the action description text, which is expected and fine.)
        declared_input_keys = set(action_yaml.get("inputs", {}).keys())
        webhook_inputs = {k for k in declared_input_keys if "webhook" in k.lower()}
        assert not webhook_inputs, (
            f"DISCORD_WEBHOOK_URL must NOT be an action input — it must come from env. "
            f"Found webhook-related inputs: {webhook_inputs}"
        )

    def test_attach_size_threshold_default_is_8(self, action_yaml):
        """attach-size-threshold-mb default is '8' (guaranteed Discord non-boosted upload limit)."""
        threshold = action_yaml["inputs"]["attach-size-threshold-mb"].get("default")
        assert str(threshold) == "8", (
            f"attach-size-threshold-mb default should be '8', got {threshold!r}"
        )

    def test_retries_embed_only_on_attachment_failure(self, run_script):
        """On a non-2xx response with attachments, the action re-posts embed-only (413 fallback)."""
        assert "retrying embed-only" in run_script, "missing 413 embed-only retry fallback"
        assert "d.pop('attachments'" in run_script, "retry must strip attachments metadata"

    def test_all_result_inputs_default_to_skipped(self, action_yaml):
        """All result-* inputs default to 'skipped' (caller only passes active platforms)."""
        inputs = action_yaml.get("inputs", {})
        result_inputs = {k: v for k, v in inputs.items() if k.startswith("result-")}
        wrong = {
            k: v.get("default")
            for k, v in result_inputs.items()
            if v.get("default") != "skipped"
        }
        assert not wrong, (
            f"result-* inputs with unexpected defaults: {wrong}"
        )


# ---------------------------------------------------------------------------
# The attachment path
# ---------------------------------------------------------------------------
# Discord posted a link for every build, including ones small enough to attach.
# The cause was not the size logic: the action looked for
# ./artifacts/unity-build-<Platform>/, which stopped existing when artifacts
# gained a build-type prefix. No directory, no zip, no attachment, no error —
# just a URL nobody outside the repository can open.

def test_the_attachment_directory_comes_from_the_artifact_name():
    body = (REPO_ROOT / ".github" / "actions" / "discord-upload-build"
            / "action.yml").read_text()
    assert '_PLAT_ARTNAME' in body, "the artifact name is never parsed"
    assert 'ARTIFACT_SUBDIR="${INPUT_ARTIFACT_DIR:-./artifacts}/${_ART_NAME}"' in body


def test_the_old_naming_survives_as_a_fallback():
    """A caller on an older pipeline still passes four fields."""
    body = (REPO_ROOT / ".github" / "actions" / "discord-upload-build"
            / "action.yml").read_text()
    assert 'unity-build-${PLAT}' in body, "no fallback for the four-field form"


def test_the_pipeline_supplies_the_artifact_name():
    """Both halves, or the parse finds an empty field and falls back forever."""
    body = (REPO_ROOT / ".github" / "workflows" / "unity-pipeline.yml").read_text()
    assert '${logsid},${binid},${ART}' in body
