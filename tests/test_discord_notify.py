"""
test_discord_notify.py
=======================
Static + subprocess tests for .github/actions/discord-notify/action.yml.

All tests are pure static analysis or local subprocess calls — no network access,
no Discord credentials, no GitHub Actions runner context required.

Covered checks
--------------
A1  action.yml is valid YAML and parses without errors
A2  All expected inputs declared
A3  Required input 'status' marked required: true; all others have defaults
A4  Action uses 'composite' runner with bash shell

C1  Webhook masking: ::add-mask:: present in run script
C2  Secret protection: set +x present (xtrace off before webhook use)
C3  No-op guard: DISCORD_WEBHOOK_URL guard present in run script
C4  Thread routing: thread_id URL parameter construction present
C5  Thread ID validation: snowflake format check present
C6  Query string handling: both ? and & separators for thread_id
C7  Never exit 1: no bare 'exit 1' that could fail the pipeline
C8  Failure mode: only exit 0 used for early returns
C9  Temp file cleanup: rm -f cleanup step present
C10 curl retry flags present (--retry)
C11 Webhook URL never used directly for curl after thread routing
C12 Diagnostic destination label present

T1  URL construction: no thread ID -> bare URL
T2  URL construction: valid thread ID -> ?thread_id=<id>
T3  URL construction: URL with existing query -> &thread_id=<id>
T4  URL construction: empty thread ID -> bare URL
T5  URL construction: malformed thread ID -> bare URL (warning)
T6  URL construction: whitespace-only thread ID -> bare URL
"""

import re
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
ACTION_FILE = REPO_ROOT / ".github" / "actions" / "discord-notify" / "action.yml"

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
        """A1: action.yml is valid YAML."""
        assert isinstance(action_yaml, dict), "Parsed YAML is not a dict"
        assert "name" in action_yaml, "Missing 'name' key in action"

    def test_a2_all_expected_inputs_declared(self, action_yaml):
        """A2: All expected inputs are present."""
        declared = set(action_yaml.get("inputs", {}).keys())
        expected = {
            "status",
            "platform",
            "environment",
            "build-version",
            "run-url",
            "artifact-name",
            "extra-text",
        }
        missing = expected - declared
        assert not missing, f"A2: Missing inputs in action.yml: {missing}"

    def test_a3_status_is_required(self, action_yaml):
        """A3: 'status' input is marked required: true."""
        inputs = action_yaml.get("inputs", {})
        assert inputs.get("status", {}).get("required") is True

    def test_a3_platform_is_required(self, action_yaml):
        """A3: 'platform' input is marked required: true."""
        inputs = action_yaml.get("inputs", {})
        assert inputs.get("platform", {}).get("required") is True

    def test_a3_optional_inputs_have_defaults(self, action_yaml):
        """A3: All optional inputs have a 'default' value."""
        inputs = action_yaml.get("inputs", {})
        no_default = [
            name
            for name, cfg in inputs.items()
            if not cfg.get("required") and cfg.get("default") is None
        ]
        assert not no_default, (
            f"A3: Optional inputs without defaults: {no_default}"
        )

    def test_a4_composite_runner(self, action_yaml):
        """A4: Action uses 'composite' runner."""
        using = action_yaml.get("runs", {}).get("using", "")
        assert using == "composite"

    def test_a4_step_uses_bash(self, action_yaml):
        """A4: The main step uses bash shell."""
        steps = action_yaml.get("runs", {}).get("steps", [])
        assert steps
        assert steps[0].get("shell") == "bash"


# ---------------------------------------------------------------------------
# C1-C12: Static checks on the run script body
# ---------------------------------------------------------------------------


class TestRunScriptStatic:
    def test_c1_webhook_masking_present(self, run_script):
        """C1: ::add-mask:: instruction masks the webhook URL."""
        assert "::add-mask::" in run_script

    def test_c2_xtrace_disabled(self, run_script):
        """C2: set +x disables xtrace."""
        assert "set +x" in run_script

    def test_c3_webhook_guard_present(self, run_script):
        """C3: Guard exits 0 when DISCORD_WEBHOOK_URL is unset."""
        has_guard = (
            "-z" in run_script and "DISCORD_WEBHOOK_URL" in run_script
        ) or ("${DISCORD_WEBHOOK_URL:-}" in run_script)
        assert has_guard

    def test_c3_guard_exits_zero(self, run_script):
        """C3: The guard block uses 'exit 0' not 'exit 1'."""
        guard_start = run_script.find("DISCORD_WEBHOOK_URL")
        assert guard_start != -1
        guard_window = run_script[guard_start : guard_start + 300]
        assert "exit 0" in guard_window

    def test_c4_thread_id_routing_present(self, run_script):
        """C4: thread_id routing appends ?thread_id= to the webhook URL."""
        assert "thread_id" in run_script
        assert "?thread_id=" in run_script

    def test_c5_thread_id_snowflake_validation(self, run_script):
        """C5: Thread ID is validated as a numeric snowflake."""
        # Must check for digit-only pattern (Discord snowflake)
        assert "^[0-9]" in run_script, (
            "C5: No numeric snowflake validation found for DISCORD_THREAD_ID"
        )

    def test_c6_query_string_separator_handling(self, run_script):
        """C6: Both ? and & separators are handled for existing query strings."""
        # Must handle case where URL already has a query string
        assert "&thread_id=" in run_script, (
            "C6: '&thread_id=' not found — existing query params not handled"
        )
        assert "?thread_id=" in run_script, (
            "C6: '?thread_id=' not found — bare URL case not handled"
        )

    def test_c7_no_bare_exit_1(self, run_script):
        """C7: No 'exit 1' in the script — action must never fail the pipeline."""
        hits = re.findall(r"\bexit\s+1\b", run_script)
        assert not hits, f"C7: Found bare 'exit 1' — action must not fail pipeline: {hits}"

    def test_c8_only_exit_zero_used(self, run_script):
        """C8: All explicit exits use exit 0."""
        exits = re.findall(r"\bexit\s+(\d+)\b", run_script)
        non_zero = [e for e in exits if e != "0"]
        assert not non_zero, f"C8: Non-zero exit codes found: {non_zero}"

    def test_c9_temp_file_cleanup(self, run_script):
        """C9: Temp files are cleaned up after use."""
        assert "rm -f" in run_script

    def test_c10_curl_retry_flag(self, run_script):
        """C10: curl uses --retry for transient network failures."""
        assert "--retry" in run_script

    def test_c10_curl_timeout_flag(self, run_script):
        """C10: curl uses --max-time to prevent indefinite hangs."""
        assert "--max-time" in run_script

    def test_c11_curl_uses_final_webhook_url(self, run_script):
        """C11: curl uses FINAL_WEBHOOK_URL (thread-aware), not raw DISCORD_WEBHOOK_URL."""
        # Find the curl command and ensure it references FINAL_WEBHOOK_URL
        curl_start = run_script.find("curl")
        assert curl_start != -1, "No curl command found"
        curl_section = run_script[curl_start:]
        # The curl command should end at the next blank line or rm -f
        curl_end = curl_section.find("rm -f")
        if curl_end == -1:
            curl_end = len(curl_section)
        curl_block = curl_section[:curl_end]
        assert "FINAL_WEBHOOK_URL" in curl_block, (
            "C11: curl must use FINAL_WEBHOOK_URL, not raw DISCORD_WEBHOOK_URL"
        )

    def test_c12_diagnostic_destination_label(self, run_script):
        """C12: Success/error messages include destination label (channel vs thread)."""
        assert "webhook channel" in run_script, "Missing 'webhook channel' destination label"
        assert "thread" in run_script.lower(), "Missing thread destination label"

    def test_c_warning_annotation_on_error(self, run_script):
        """Errors produce ::warning:: annotations (not ::error::)."""
        assert "::warning::" in run_script
        warning_count = len(re.findall(r"::warning::", run_script))
        assert warning_count >= 2, (
            f"Expected >=2 ::warning:: annotations, got {warning_count}"
        )

    def test_webhook_url_from_env_not_input(self, action_yaml):
        """DISCORD_WEBHOOK_URL is read from environment, never declared as an input."""
        declared_input_keys = set(action_yaml.get("inputs", {}).keys())
        webhook_inputs = {k for k in declared_input_keys if "webhook" in k.lower()}
        assert not webhook_inputs, (
            f"DISCORD_WEBHOOK_URL must NOT be an action input — found: {webhook_inputs}"
        )

    def test_thread_id_from_env_not_input(self, action_yaml):
        """DISCORD_THREAD_ID is read from environment, never declared as an input."""
        declared_input_keys = set(action_yaml.get("inputs", {}).keys())
        thread_inputs = {k for k in declared_input_keys if "thread" in k.lower()}
        assert not thread_inputs, (
            f"DISCORD_THREAD_ID must NOT be an action input for discord-notify — found: {thread_inputs}"
        )


# ---------------------------------------------------------------------------
# T1-T6: URL construction tests (subprocess)
# ---------------------------------------------------------------------------
# These tests run a minimal bash snippet that extracts just the URL-building
# logic from the action, sets env vars, and prints the resulting URL.
# No network call is made.


_URL_BUILD_SCRIPT = textwrap.dedent("""\
    set +x
    DISCORD_WEBHOOK_URL="${TEST_WEBHOOK_URL}"
    FINAL_WEBHOOK_URL="${DISCORD_WEBHOOK_URL}"
    _THREAD_ID="${DISCORD_THREAD_ID:-}"
    _THREAD_ID="${_THREAD_ID#"${_THREAD_ID%%[![:space:]]*}"}"
    _THREAD_ID="${_THREAD_ID%"${_THREAD_ID##*[![:space:]]}"}"

    if [ -n "${_THREAD_ID}" ]; then
      if ! printf '%s' "${_THREAD_ID}" | grep -qE '^[0-9]{17,20}$'; then
        echo "WARN:invalid"
      else
        case "${FINAL_WEBHOOK_URL}" in
          *\\?*) FINAL_WEBHOOK_URL="${FINAL_WEBHOOK_URL}&thread_id=${_THREAD_ID}" ;;
          *)    FINAL_WEBHOOK_URL="${FINAL_WEBHOOK_URL}?thread_id=${_THREAD_ID}" ;;
        esac
      fi
    fi

    echo "URL:${FINAL_WEBHOOK_URL}"
""")


def _run_url_build(webhook_url, thread_id=None):
    """Run the URL-building logic and return (url, warnings)."""
    import os
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "TEST_WEBHOOK_URL": webhook_url,
    }
    if thread_id is not None:
        env["DISCORD_THREAD_ID"] = thread_id

    result = subprocess.run(
        ["bash", "-c", _URL_BUILD_SCRIPT],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )
    assert result.returncode == 0, f"Script failed: {result.stderr}"

    url = ""
    warnings = []
    for line in result.stdout.strip().split("\n"):
        if line.startswith("URL:"):
            url = line[4:]
        elif line.startswith("WARN:"):
            warnings.append(line[5:])
    return url, warnings


class TestUrlConstruction:
    """T1-T6: Verify URL construction logic in isolation."""

    FAKE_WEBHOOK = "https://discord.com/api/webhooks/1234/abctoken"
    VALID_THREAD_ID = "12345678901234567890"  # 20 digits

    def test_t1_no_thread_id(self):
        """T1: No DISCORD_THREAD_ID -> bare webhook URL."""
        url, warnings = _run_url_build(self.FAKE_WEBHOOK)
        assert url == self.FAKE_WEBHOOK
        assert not warnings

    def test_t2_valid_thread_id(self):
        """T2: Valid thread ID -> ?thread_id=<id> appended."""
        url, warnings = _run_url_build(self.FAKE_WEBHOOK, self.VALID_THREAD_ID)
        assert url == f"{self.FAKE_WEBHOOK}?thread_id={self.VALID_THREAD_ID}"
        assert not warnings

    def test_t3_existing_query_string(self):
        """T3: URL with existing query -> &thread_id=<id>."""
        webhook_with_query = f"{self.FAKE_WEBHOOK}?wait=true"
        url, warnings = _run_url_build(webhook_with_query, self.VALID_THREAD_ID)
        assert url == f"{webhook_with_query}&thread_id={self.VALID_THREAD_ID}"
        assert not warnings

    def test_t4_empty_thread_id(self):
        """T4: Empty DISCORD_THREAD_ID -> bare URL."""
        url, warnings = _run_url_build(self.FAKE_WEBHOOK, "")
        assert url == self.FAKE_WEBHOOK
        assert not warnings

    def test_t5_malformed_thread_id(self):
        """T5: Non-numeric thread ID -> bare URL + warning."""
        url, warnings = _run_url_build(self.FAKE_WEBHOOK, "not-a-snowflake")
        assert url == self.FAKE_WEBHOOK
        assert "invalid" in warnings

    def test_t5_too_short_thread_id(self):
        """T5b: Thread ID with too few digits -> bare URL + warning."""
        url, warnings = _run_url_build(self.FAKE_WEBHOOK, "1234")
        assert url == self.FAKE_WEBHOOK
        assert "invalid" in warnings

    def test_t6_whitespace_only_thread_id(self):
        """T6: Whitespace-only DISCORD_THREAD_ID -> bare URL."""
        url, warnings = _run_url_build(self.FAKE_WEBHOOK, "   ")
        assert url == self.FAKE_WEBHOOK
        assert not warnings

    def test_t6_thread_id_with_surrounding_whitespace(self):
        """T6b: Thread ID with leading/trailing whitespace -> trimmed and used."""
        padded = f"  {self.VALID_THREAD_ID}  "
        url, warnings = _run_url_build(self.FAKE_WEBHOOK, padded)
        assert url == f"{self.FAKE_WEBHOOK}?thread_id={self.VALID_THREAD_ID}"
        assert not warnings

    def test_webhook_url_not_in_output(self):
        """Webhook token must not appear in warning/diagnostic output."""
        # The script only echoes URL: for test purposes; the real action never
        # prints the URL.  Here we verify the warning path doesn't leak it.
        _, warnings = _run_url_build(self.FAKE_WEBHOOK, "not-valid")
        for w in warnings:
            assert "abctoken" not in w, "Webhook token leaked in warning output"
