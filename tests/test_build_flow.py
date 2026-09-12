"""
test_build_flow.py
==================
Validates resolve_build_flow.sh by invoking it via subprocess with controlled
environment and asserting KEY=value outputs against the BRANCH_FLOW_CONTRACT.md
rules table.

Covered scenarios
-----------------
F1   PR → develop    flow-type=pr-develop,  validation only, tests
F2   PR → staging    flow-type=pr-staging,  validation only, tests
F3   PR → release-*  flow-type=pr-release,  addressables, tests, no binary builds
F4   push → develop  flow-type=push-develop, android+webgl, env=development
F5   push → staging  flow-type=push-staging, android+webgl+linux64+linuxserver+windows64
F6   push → release-1.2   flow-type=push-release, all 5 platforms + signing
F7   push → release/1.2   same as F6 (alternate branch naming convention)
F8   dispatch All          5 core platforms true (incl. windows64), ios false
F8w  dispatch Windows64    only build-windows64 true
F9   dispatch Android      only build-android true
F10  dispatch iOS          only build-ios true
F11  run-tests=false forces test-mode=None
F12  push → main (no-match) flow-type=none, all build-* false
F13  build-ios is never true except manual iOS dispatch
F14  build-windows64 key always present in output

Repository Variable scenarios
-----------------------------
V1   no variables → defaults used, platform-source=default
V2   DEVELOP variable override → only listed platforms built
V3   STAGING variable override → only listed platforms built
V4   RELEASE variable override → only listed platforms built
V5   invalid platform value → script exits non-zero
V6   manual dispatch overrides repository variables (platform-source=dispatch)
V7   optional run-tests variable overrides branch default
V8   optional build-addressables variable overrides branch default
V9   platform-source emitted for all flows
V10  invalid run-tests variable → script exits non-zero
V11  invalid build-addressables variable → script exits non-zero
"""

import os
import subprocess
from pathlib import Path
from typing import Dict

import pytest

REPO_ROOT = Path(__file__).parent.parent
FLOW_SCRIPT = REPO_ROOT / "scripts" / "common" / "resolve_build_flow.sh"

EXPECTED_KEYS = {
    "flow-type",
    "environment",
    "run-tests",
    "test-mode",
    "build-addressables",
    "build-android",
    "build-webgl",
    "build-linux64",
    "build-linuxserver",
    "build-windows64",
    "build-ios",
    "signing",
    "platform-source",
    "android-export-type",
}

ALL_BUILD_PLATFORM_KEYS = [
    "build-android",
    "build-webgl",
    "build-linux64",
    "build-linuxserver",
    "build-windows64",
    "build-ios",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run_flow(env_overrides: dict, timeout: int = 10) -> subprocess.CompletedProcess:
    """Run the flow resolver with a clean, controlled environment."""
    env: Dict[str, str] = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
    }
    env.update({k: str(v) for k, v in env_overrides.items()})
    return subprocess.run(
        ["bash", str(FLOW_SCRIPT)],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def parse_outputs(stdout: str) -> Dict[str, str]:
    """Parse KEY=value lines from script stdout."""
    result: Dict[str, str] = {}
    for line in stdout.splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def assert_all_builds_false(outputs: Dict[str, str], msg: str = ""):
    """Assert every build-<platform> output is 'false'."""
    for key in ALL_BUILD_PLATFORM_KEYS:
        assert outputs.get(key) == "false", (
            f"{msg} — expected {key}=false, got {outputs.get(key)!r}"
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _require_script():
    if not FLOW_SCRIPT.exists():
        pytest.skip(f"Flow script not found: {FLOW_SCRIPT}")


@pytest.fixture(scope="module")
def _check_script_once():
    if not FLOW_SCRIPT.exists():
        pytest.skip(f"Flow script not found: {FLOW_SCRIPT}")


# ---------------------------------------------------------------------------
# Smoke — script runs and emits all expected keys
# ---------------------------------------------------------------------------

class TestScriptSmoke:
    def test_exits_zero_for_unknown_event(self):
        r = run_flow({"EVENT_NAME": "schedule"})
        assert r.returncode == 0

    def test_emits_all_expected_output_keys(self):
        r = run_flow({"EVENT_NAME": "push", "REF_NAME": "develop"})
        assert r.returncode == 0
        outputs = parse_outputs(r.stdout)
        missing = EXPECTED_KEYS - set(outputs.keys())
        assert not missing, f"Script missing output keys: {missing}"

    def test_all_values_are_non_empty(self):
        r = run_flow({"EVENT_NAME": "push", "REF_NAME": "develop"})
        outputs = parse_outputs(r.stdout)
        empty = [k for k in EXPECTED_KEYS if outputs.get(k, "") == ""]
        assert not empty, f"Empty output values for keys: {empty}"

    def test_diagnostic_goes_to_stderr_not_stdout(self):
        r = run_flow({"EVENT_NAME": "push", "REF_NAME": "develop"})
        # stdout must contain ONLY KEY=value lines (no log lines)
        for line in r.stdout.splitlines():
            line = line.strip()
            if line:
                assert "=" in line, (
                    f"Non KEY=value line found in stdout: {line!r}"
                )


# ---------------------------------------------------------------------------
# F1 – PR → develop
# ---------------------------------------------------------------------------

class TestPRDevelop:
    @pytest.fixture(autouse=True)
    def _result(self):
        r = run_flow({"EVENT_NAME": "pull_request", "BASE_REF": "develop"})
        assert r.returncode == 0
        self.out = parse_outputs(r.stdout)

    def test_flow_type(self):
        assert self.out["flow-type"] == "pr-develop"

    def test_environment_is_development(self):
        assert self.out["environment"] == "development"

    def test_run_tests_true(self):
        assert self.out["run-tests"] == "true"

    def test_no_binary_builds(self):
        assert_all_builds_false(self.out, "F1 PR→develop")

    def test_android_export_type_apk(self):
        """PR → develop: android-export-type defaults to apk (not a release build)."""
        assert self.out["android-export-type"] == "apk"

    def test_build_addressables_false(self):
        assert self.out["build-addressables"] == "false"

    def test_signing_none(self):
        assert self.out["signing"] == "none"


# ---------------------------------------------------------------------------
# F2 – PR → staging
# ---------------------------------------------------------------------------

class TestPRStaging:
    @pytest.fixture(autouse=True)
    def _result(self):
        r = run_flow({"EVENT_NAME": "pull_request", "BASE_REF": "staging"})
        assert r.returncode == 0
        self.out = parse_outputs(r.stdout)

    def test_flow_type(self):
        assert self.out["flow-type"] == "pr-staging"

    def test_environment_is_staging(self):
        assert self.out["environment"] == "staging"

    def test_run_tests_true(self):
        assert self.out["run-tests"] == "true"

    def test_no_binary_builds(self):
        assert_all_builds_false(self.out, "F2 PR→staging")

    def test_android_export_type_apk(self):
        """PR → staging: android-export-type defaults to apk."""
        assert self.out["android-export-type"] == "apk"

    def test_signing_none(self):
        assert self.out["signing"] == "none"


# ---------------------------------------------------------------------------
# F3 – PR → release-*  (using release-1.2)
# ---------------------------------------------------------------------------

class TestPRRelease:
    @pytest.fixture(autouse=True)
    def _result(self):
        r = run_flow({"EVENT_NAME": "pull_request", "BASE_REF": "release-1.2"})
        assert r.returncode == 0
        self.out = parse_outputs(r.stdout)

    def test_flow_type(self):
        assert self.out["flow-type"] == "pr-release"

    def test_environment_is_production(self):
        assert self.out["environment"] == "production"

    def test_run_tests_true(self):
        assert self.out["run-tests"] == "true"

    def test_build_addressables_true(self):
        assert self.out["build-addressables"] == "true"

    def test_no_binary_builds(self):
        assert_all_builds_false(self.out, "F3 PR→release-1.2")

    def test_android_export_type_apk(self):
        """PR → release: no binary builds, still defaults to apk (not a release push)."""
        assert self.out["android-export-type"] == "apk"

    def test_signing_none(self):
        assert self.out["signing"] == "none"


# ---------------------------------------------------------------------------
# F4 – push → develop
# ---------------------------------------------------------------------------

class TestPushDevelop:
    @pytest.fixture(autouse=True)
    def _result(self):
        r = run_flow({"EVENT_NAME": "push", "REF_NAME": "develop"})
        assert r.returncode == 0
        self.out = parse_outputs(r.stdout)

    def test_flow_type(self):
        assert self.out["flow-type"] == "push-develop"

    def test_environment_is_development(self):
        assert self.out["environment"] == "development"

    def test_run_tests_true(self):
        assert self.out["run-tests"] == "true"

    def test_build_android_true(self):
        assert self.out["build-android"] == "true"

    def test_build_webgl_true(self):
        assert self.out["build-webgl"] == "true"

    def test_build_linux64_false(self):
        assert self.out["build-linux64"] == "false"

    def test_build_linuxserver_false(self):
        assert self.out["build-linuxserver"] == "false"

    def test_build_ios_false(self):
        assert self.out["build-ios"] == "false"

    def test_build_windows64_false(self):
        """F4: push→develop does not build Windows64 (not in develop defaults)."""
        assert self.out["build-windows64"] == "false"

    def test_android_export_type_apk(self):
        """F4: push→develop produces APK (not a release build)."""
        assert self.out["android-export-type"] == "apk"

    def test_build_addressables_false(self):
        assert self.out["build-addressables"] == "false"

    def test_signing_none(self):
        assert self.out["signing"] == "none"

    def test_platform_source_default(self):
        assert self.out["platform-source"] == "default"


# ---------------------------------------------------------------------------
# F5 – push → staging
# ---------------------------------------------------------------------------

class TestPushStaging:
    @pytest.fixture(autouse=True)
    def _result(self):
        r = run_flow({"EVENT_NAME": "push", "REF_NAME": "staging"})
        assert r.returncode == 0
        self.out = parse_outputs(r.stdout)

    def test_flow_type(self):
        assert self.out["flow-type"] == "push-staging"

    def test_environment_is_staging(self):
        assert self.out["environment"] == "staging"

    def test_run_tests_true(self):
        assert self.out["run-tests"] == "true"

    def test_build_android_true(self):
        assert self.out["build-android"] == "true"

    def test_build_webgl_true(self):
        assert self.out["build-webgl"] == "true"

    def test_build_linux64_true(self):
        assert self.out["build-linux64"] == "true"

    def test_build_linuxserver_true(self):
        assert self.out["build-linuxserver"] == "true"

    def test_build_ios_false(self):
        assert self.out["build-ios"] == "false"

    def test_build_windows64_true(self):
        """F5: push→staging includes Windows64 in default staging platforms."""
        assert self.out["build-windows64"] == "true"

    def test_android_export_type_apk(self):
        """F5: push→staging produces APK (not a release push)."""
        assert self.out["android-export-type"] == "apk"

    def test_build_addressables_false(self):
        assert self.out["build-addressables"] == "false"

    def test_signing_none(self):
        assert self.out["signing"] == "none"

    def test_platform_source_default(self):
        assert self.out["platform-source"] == "default"


# ---------------------------------------------------------------------------
# F6 – push → release-1.2
# ---------------------------------------------------------------------------

class TestPushReleaseDash:
    """push → release-1.2 (hyphen-separated branch name)."""

    @pytest.fixture(autouse=True)
    def _result(self):
        r = run_flow({"EVENT_NAME": "push", "REF_NAME": "release-1.2"})
        assert r.returncode == 0
        self.out = parse_outputs(r.stdout)

    def test_flow_type(self):
        assert self.out["flow-type"] == "push-release"

    def test_environment_is_production(self):
        assert self.out["environment"] == "production"

    def test_run_tests_true(self):
        assert self.out["run-tests"] == "true"

    def test_build_addressables_true(self):
        assert self.out["build-addressables"] == "true"

    def test_build_android_true(self):
        assert self.out["build-android"] == "true"

    def test_build_webgl_true(self):
        assert self.out["build-webgl"] == "true"

    def test_build_linux64_true(self):
        assert self.out["build-linux64"] == "true"

    def test_build_linuxserver_true(self):
        assert self.out["build-linuxserver"] == "true"

    def test_build_ios_false(self):
        assert self.out["build-ios"] == "false"

    def test_build_windows64_true(self):
        """F6: push→release-1.2 includes Windows64 in default release platforms."""
        assert self.out["build-windows64"] == "true"

    def test_android_export_type_aab(self):
        """F6: push→release produces AAB for Play Store delivery."""
        assert self.out["android-export-type"] == "aab"

    def test_signing_android_release(self):
        assert self.out["signing"] == "android-release"

    def test_platform_source_default(self):
        assert self.out["platform-source"] == "default"


# ---------------------------------------------------------------------------
# F7 – push → release/1.2  (slash-separated branch name)
# ---------------------------------------------------------------------------

class TestPushReleaseSlash:
    """push → release/1.2 (slash-separated — alternate naming convention)."""

    @pytest.fixture(autouse=True)
    def _result(self):
        r = run_flow({"EVENT_NAME": "push", "REF_NAME": "release/1.2"})
        assert r.returncode == 0
        self.out = parse_outputs(r.stdout)

    def test_flow_type(self):
        assert self.out["flow-type"] == "push-release"

    def test_environment_is_production(self):
        assert self.out["environment"] == "production"

    def test_all_core_platforms_true(self):
        for key in ["build-android", "build-webgl", "build-linux64", "build-linuxserver", "build-windows64"]:
            assert self.out[key] == "true", f"Expected {key}=true for push→release/1.2"

    def test_build_ios_false(self):
        assert self.out["build-ios"] == "false"

    def test_android_export_type_aab(self):
        """F7: push→release/1.2 (slash form) also produces AAB."""
        assert self.out["android-export-type"] == "aab"

    def test_signing_android_release(self):
        assert self.out["signing"] == "android-release"


# ---------------------------------------------------------------------------
# F8 – workflow_dispatch / IN_PLATFORM=All
# ---------------------------------------------------------------------------

class TestDispatchAll:
    @pytest.fixture(autouse=True)
    def _result(self):
        r = run_flow({
            "EVENT_NAME": "workflow_dispatch",
            "IN_PLATFORM": "All",
            "IN_ENVIRONMENT": "production",
            "IN_RUN_TESTS": "false",
            "IN_TEST_MODE": "All",
            "IN_BUILD_ADDRESSABLES": "false",
        })
        assert r.returncode == 0
        self.out = parse_outputs(r.stdout)

    def test_flow_type_manual(self):
        assert self.out["flow-type"] == "manual"

    def test_environment_passed_through(self):
        assert self.out["environment"] == "production"

    def test_build_android_true(self):
        assert self.out["build-android"] == "true"

    def test_build_webgl_true(self):
        assert self.out["build-webgl"] == "true"

    def test_build_linux64_true(self):
        assert self.out["build-linux64"] == "true"

    def test_build_linuxserver_true(self):
        assert self.out["build-linuxserver"] == "true"

    def test_build_ios_false_for_all(self):
        """iOS must be excluded from 'All' — never auto-built."""
        assert self.out["build-ios"] == "false"

    def test_build_windows64_true_for_all(self):
        """F8: dispatch All includes Windows64."""
        assert self.out["build-windows64"] == "true"

    def test_platform_source_is_where_the_list_came_from(self):
        """`All` delegates to the environment's platform list, so with no
        variable set the source is the default. The dispatch input chose which
        list to read, not what was in it."""
        assert self.out["platform-source"] == "default"


# ---------------------------------------------------------------------------
# F9 – workflow_dispatch / IN_PLATFORM=Android
# ---------------------------------------------------------------------------

class TestDispatchAndroid:
    @pytest.fixture(autouse=True)
    def _result(self):
        r = run_flow({
            "EVENT_NAME": "workflow_dispatch",
            "IN_PLATFORM": "Android",
            "IN_ENVIRONMENT": "development",
            "IN_RUN_TESTS": "false",
            "IN_TEST_MODE": "All",
            "IN_BUILD_ADDRESSABLES": "false",
        })
        assert r.returncode == 0
        self.out = parse_outputs(r.stdout)

    def test_only_android_true(self):
        assert self.out["build-android"] == "true"

    def test_other_platforms_false(self):
        for key in ["build-webgl", "build-linux64", "build-linuxserver", "build-windows64", "build-ios"]:
            assert self.out[key] == "false", f"Expected {key}=false for Android dispatch"


# ---------------------------------------------------------------------------
# F10 – workflow_dispatch / IN_PLATFORM=iOS
# ---------------------------------------------------------------------------

class TestDispatchIOS:
    @pytest.fixture(autouse=True)
    def _result(self):
        r = run_flow({
            "EVENT_NAME": "workflow_dispatch",
            "IN_PLATFORM": "iOS",
            "IN_ENVIRONMENT": "production",
            "IN_RUN_TESTS": "false",
            "IN_TEST_MODE": "All",
            "IN_BUILD_ADDRESSABLES": "false",
        })
        assert r.returncode == 0
        self.out = parse_outputs(r.stdout)

    def test_build_ios_true(self):
        """Only manual iOS dispatch should set build-ios=true."""
        assert self.out["build-ios"] == "true"

    def test_core_platforms_false(self):
        for key in ["build-android", "build-webgl", "build-linux64", "build-linuxserver", "build-windows64"]:
            assert self.out[key] == "false", f"Expected {key}=false for iOS dispatch"


# ---------------------------------------------------------------------------
# F11 – run-tests=false forces test-mode=None
# ---------------------------------------------------------------------------

class TestRunTestsFalseNormalization:
    def test_push_develop_run_tests_false_test_mode_none(self):
        """F11: Even automatic push-develop, if run-tests were false, test-mode=None."""
        # Simulate push→develop but force run-tests context by setting env
        # (script always sets run_tests=true for push-develop;
        # the normalisation rule fires when run_tests=false regardless of how it's set)
        # Use dispatch with run-tests=false to exercise the normalisation path.
        r = run_flow({
            "EVENT_NAME": "workflow_dispatch",
            "IN_PLATFORM": "Android",
            "IN_ENVIRONMENT": "development",
            "IN_RUN_TESTS": "false",
            "IN_TEST_MODE": "EditMode",  # would be overridden to None
            "IN_BUILD_ADDRESSABLES": "false",
        })
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["run-tests"] == "false"
        assert out["test-mode"] == "None", (
            f"F11: test-mode should be 'None' when run-tests=false, got {out['test-mode']!r}"
        )

    def test_push_develop_preserves_test_mode_when_run_tests_true(self):
        """F11 inverse: push→develop sets run-tests=true → test-mode=All (not None)."""
        r = run_flow({"EVENT_NAME": "push", "REF_NAME": "develop"})
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["run-tests"] == "true"
        assert out["test-mode"] != "None", (
            f"test-mode should not be None when run-tests=true, got {out['test-mode']!r}"
        )


# ---------------------------------------------------------------------------
# F12 – push → main (no-match branch) → flow-type=none, all builds false
# ---------------------------------------------------------------------------

class TestNoMatchBranch:
    @pytest.fixture(autouse=True)
    def _result(self):
        r = run_flow({"EVENT_NAME": "push", "REF_NAME": "main"})
        assert r.returncode == 0
        self.out = parse_outputs(r.stdout)

    def test_flow_type_none(self):
        assert self.out["flow-type"] == "none"

    def test_all_builds_false(self):
        assert_all_builds_false(self.out, "F12 push→main")

    def test_build_addressables_false(self):
        assert self.out["build-addressables"] == "false"

    def test_run_tests_false(self):
        assert self.out["run-tests"] == "false"

    def test_signing_none(self):
        assert self.out["signing"] == "none"


# ---------------------------------------------------------------------------
# F13 – build-ios is never true except manual iOS dispatch
# ---------------------------------------------------------------------------

class TestIOSNeverAutomatic:
    """F13: build-ios must be false for all automatic triggers."""

    @pytest.mark.parametrize("env", [
        {"EVENT_NAME": "push", "REF_NAME": "develop"},
        {"EVENT_NAME": "push", "REF_NAME": "staging"},
        {"EVENT_NAME": "push", "REF_NAME": "release-1.2"},
        {"EVENT_NAME": "push", "REF_NAME": "release/2.0"},
        {"EVENT_NAME": "pull_request", "BASE_REF": "develop"},
        {"EVENT_NAME": "pull_request", "BASE_REF": "staging"},
        {"EVENT_NAME": "pull_request", "BASE_REF": "release-3.0"},
        {"EVENT_NAME": "workflow_dispatch", "IN_PLATFORM": "All",
         "IN_ENVIRONMENT": "production", "IN_RUN_TESTS": "false",
         "IN_TEST_MODE": "All", "IN_BUILD_ADDRESSABLES": "false"},
        {"EVENT_NAME": "workflow_dispatch", "IN_PLATFORM": "Android",
         "IN_ENVIRONMENT": "production", "IN_RUN_TESTS": "false",
         "IN_TEST_MODE": "All", "IN_BUILD_ADDRESSABLES": "false"},
    ])
    def test_build_ios_false(self, env):
        r = run_flow(env)
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["build-ios"] == "false", (
            f"F13: build-ios must be false for {env}, got {out['build-ios']!r}"
        )

    def test_build_ios_true_only_for_ios_dispatch(self):
        r = run_flow({
            "EVENT_NAME": "workflow_dispatch",
            "IN_PLATFORM": "iOS",
            "IN_ENVIRONMENT": "production",
            "IN_RUN_TESTS": "false",
            "IN_TEST_MODE": "All",
            "IN_BUILD_ADDRESSABLES": "false",
        })
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["build-ios"] == "true", (
            "F13: build-ios should be true for explicit iOS dispatch"
        )


# ---------------------------------------------------------------------------
# Dispatch — all single-platform variants
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("platform,expected_true", [
    ("WebGL",       "build-webgl"),
    ("Linux64",     "build-linux64"),
    ("LinuxServer", "build-linuxserver"),
    ("Windows64",   "build-windows64"),
])
def test_dispatch_single_platform_exclusive(platform, expected_true):
    """Each single-platform dispatch enables exactly one build-* key."""
    r = run_flow({
        "EVENT_NAME": "workflow_dispatch",
        "IN_PLATFORM": platform,
        "IN_ENVIRONMENT": "development",
        "IN_RUN_TESTS": "false",
        "IN_TEST_MODE": "All",
        "IN_BUILD_ADDRESSABLES": "false",
    })
    assert r.returncode == 0
    out = parse_outputs(r.stdout)
    assert out[expected_true] == "true", f"Expected {expected_true}=true for {platform}"
    for key in ALL_BUILD_PLATFORM_KEYS:
        if key != expected_true:
            assert out[key] == "false", f"Expected {key}=false for {platform} dispatch"


# ---------------------------------------------------------------------------
# F8w – workflow_dispatch / IN_PLATFORM=Windows64
# ---------------------------------------------------------------------------

class TestDispatchWindows64:
    """F8w: Explicit Windows64 dispatch → only build-windows64 true."""

    @pytest.fixture(autouse=True)
    def _result(self):
        r = run_flow({
            "EVENT_NAME": "workflow_dispatch",
            "IN_PLATFORM": "Windows64",
            "IN_ENVIRONMENT": "development",
            "IN_RUN_TESTS": "false",
            "IN_TEST_MODE": "All",
            "IN_BUILD_ADDRESSABLES": "false",
        })
        assert r.returncode == 0
        self.out = parse_outputs(r.stdout)

    def test_build_windows64_true(self):
        assert self.out["build-windows64"] == "true"

    def test_other_platforms_false(self):
        for key in ["build-android", "build-webgl", "build-linux64", "build-linuxserver", "build-ios"]:
            assert self.out[key] == "false", f"Expected {key}=false for Windows64 dispatch"

    def test_flow_type_manual(self):
        assert self.out["flow-type"] == "manual"

    def test_platform_source_dispatch(self):
        assert self.out["platform-source"] == "dispatch"


# ---------------------------------------------------------------------------
# android-export-type dispatch variants
# ---------------------------------------------------------------------------

class TestAndroidExportType:
    """Assert android-export-type for all dispatch / branch scenarios."""

    def _dispatch(self, platform="Android", export=""):
        env = {
            "EVENT_NAME": "workflow_dispatch",
            "IN_PLATFORM": platform,
            "IN_ENVIRONMENT": "development",
            "IN_RUN_TESTS": "false",
            "IN_TEST_MODE": "All",
            "IN_BUILD_ADDRESSABLES": "false",
        }
        if export:
            env["IN_ANDROID_EXPORT"] = export
        return run_flow(env)

    def test_dispatch_export_aab(self):
        """Dispatch with IN_ANDROID_EXPORT=aab → android-export-type=aab."""
        r = self._dispatch(export="aab")
        assert r.returncode == 0
        assert parse_outputs(r.stdout)["android-export-type"] == "aab"

    def test_dispatch_export_apk_explicit(self):
        """Dispatch with IN_ANDROID_EXPORT=apk → android-export-type=apk."""
        r = self._dispatch(export="apk")
        assert r.returncode == 0
        assert parse_outputs(r.stdout)["android-export-type"] == "apk"

    def test_dispatch_export_unset_defaults_to_apk(self):
        """Dispatch without IN_ANDROID_EXPORT → defaults to apk."""
        r = self._dispatch()  # no export kwarg → IN_ANDROID_EXPORT absent
        assert r.returncode == 0
        assert parse_outputs(r.stdout)["android-export-type"] == "apk"

    def test_key_always_present(self):
        """android-export-type key present in every flow."""
        flows = [
            {"EVENT_NAME": "push", "REF_NAME": "develop"},
            {"EVENT_NAME": "push", "REF_NAME": "staging"},
            {"EVENT_NAME": "push", "REF_NAME": "release-1.0"},
            {"EVENT_NAME": "pull_request", "BASE_REF": "develop"},
            {"EVENT_NAME": "pull_request", "BASE_REF": "release-2.0"},
            {"EVENT_NAME": "push", "REF_NAME": "main"},
        ]
        for env in flows:
            r = run_flow(env)
            out = parse_outputs(r.stdout)
            assert "android-export-type" in out, (
                f"android-export-type missing for {env}"
            )


# ===========================================================================
# Repository Variable tests (V1–V11)
# ===========================================================================

# ---------------------------------------------------------------------------
# V1 – no variables → defaults used, platform-source=default
# ---------------------------------------------------------------------------

class TestRepoVarDefaults:
    """V1: Without any VAR_* env, defaults are used."""

    def test_push_develop_defaults(self):
        r = run_flow({"EVENT_NAME": "push", "REF_NAME": "develop"})
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["build-android"] == "true"
        assert out["build-webgl"] == "true"
        assert out["build-linux64"] == "false"
        assert out["build-linuxserver"] == "false"
        assert out["platform-source"] == "default"

    def test_push_staging_defaults(self):
        r = run_flow({"EVENT_NAME": "push", "REF_NAME": "staging"})
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["build-android"] == "true"
        assert out["build-webgl"] == "true"
        assert out["build-linux64"] == "true"
        assert out["build-linuxserver"] == "true"
        assert out["build-windows64"] == "true"
        assert out["platform-source"] == "default"

    def test_push_release_defaults(self):
        r = run_flow({"EVENT_NAME": "push", "REF_NAME": "release-1.0"})
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["build-android"] == "true"
        assert out["build-webgl"] == "true"
        assert out["build-linux64"] == "true"
        assert out["build-linuxserver"] == "true"
        assert out["build-windows64"] == "true"
        assert out["platform-source"] == "default"


# ---------------------------------------------------------------------------
# V2 – DEVELOP variable override
# ---------------------------------------------------------------------------

class TestRepoVarDevelopOverride:
    """V2: VAR_DEVELOP_BUILD_PLATFORMS overrides develop defaults."""

    def test_develop_android_only(self):
        r = run_flow({
            "EVENT_NAME": "push",
            "REF_NAME": "develop",
            "VAR_DEVELOP_BUILD_PLATFORMS": "Android",
        })
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["build-android"] == "true"
        assert out["build-webgl"] == "false"
        assert out["build-linux64"] == "false"
        assert out["build-linuxserver"] == "false"
        assert out["platform-source"] == "variable-legacy"

    def test_develop_all_four(self):
        r = run_flow({
            "EVENT_NAME": "push",
            "REF_NAME": "develop",
            "VAR_DEVELOP_BUILD_PLATFORMS": "Android,WebGL,Linux64,LinuxServer",
        })
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["build-android"] == "true"
        assert out["build-webgl"] == "true"
        assert out["build-linux64"] == "true"
        assert out["build-linuxserver"] == "true"
        assert out["platform-source"] == "variable-legacy"

    def test_develop_webgl_only(self):
        r = run_flow({
            "EVENT_NAME": "push",
            "REF_NAME": "develop",
            "VAR_DEVELOP_BUILD_PLATFORMS": "WebGL",
        })
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["build-android"] == "false"
        assert out["build-webgl"] == "true"
        assert out["build-linux64"] == "false"
        assert out["platform-source"] == "variable-legacy"


# ---------------------------------------------------------------------------
# V3 – STAGING variable override
# ---------------------------------------------------------------------------

class TestRepoVarStagingOverride:
    """V3: VAR_STAGING_BUILD_PLATFORMS overrides staging defaults."""

    def test_staging_android_webgl_only(self):
        r = run_flow({
            "EVENT_NAME": "push",
            "REF_NAME": "staging",
            "VAR_STAGING_BUILD_PLATFORMS": "Android,WebGL",
        })
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["build-android"] == "true"
        assert out["build-webgl"] == "true"
        assert out["build-linux64"] == "false"
        assert out["build-linuxserver"] == "false"
        assert out["platform-source"] == "variable-legacy"


# ---------------------------------------------------------------------------
# V4 – RELEASE variable override
# ---------------------------------------------------------------------------

class TestRepoVarReleaseOverride:
    """V4: VAR_RELEASE_BUILD_PLATFORMS overrides release defaults."""

    def test_release_android_only(self):
        r = run_flow({
            "EVENT_NAME": "push",
            "REF_NAME": "release-2.0",
            "VAR_RELEASE_BUILD_PLATFORMS": "Android",
        })
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["build-android"] == "true"
        assert out["build-webgl"] == "false"
        assert out["build-linux64"] == "false"
        assert out["build-linuxserver"] == "false"
        assert out["platform-source"] == "variable-legacy"
        # signing still applies for release
        assert out["signing"] == "android-release"


# ---------------------------------------------------------------------------
# V5 – invalid platform value → script exits non-zero
# ---------------------------------------------------------------------------

class TestRepoVarInvalidPlatform:
    """V5: Invalid platform name in variable causes script to fail."""

    def test_invalid_develop_platform(self):
        r = run_flow({
            "EVENT_NAME": "push",
            "REF_NAME": "develop",
            "VAR_DEVELOP_BUILD_PLATFORMS": "Android,InvalidPlatform",
        })
        assert r.returncode != 0, "Script should fail on invalid platform name"
        assert "InvalidPlatform" in r.stderr

    def test_invalid_staging_platform(self):
        r = run_flow({
            "EVENT_NAME": "push",
            "REF_NAME": "staging",
            "VAR_STAGING_BUILD_PLATFORMS": "Windoze",
        })
        assert r.returncode != 0
        assert "Windoze" in r.stderr

    def test_invalid_release_platform(self):
        r = run_flow({
            "EVENT_NAME": "push",
            "REF_NAME": "release-1.0",
            "VAR_RELEASE_BUILD_PLATFORMS": "android",  # wrong case
        })
        assert r.returncode != 0
        assert "android" in r.stderr


# ---------------------------------------------------------------------------
# V6 – manual dispatch overrides repository variables
# ---------------------------------------------------------------------------

class TestDispatchOverridesRepoVars:
    """V6: workflow_dispatch always uses dispatch inputs, not repo variables."""

    def test_dispatch_ignores_develop_var(self):
        r = run_flow({
            "EVENT_NAME": "workflow_dispatch",
            "IN_PLATFORM": "Android",
            "IN_ENVIRONMENT": "development",
            "IN_RUN_TESTS": "false",
            "IN_TEST_MODE": "All",
            "IN_BUILD_ADDRESSABLES": "false",
            # These should be ignored for dispatch
            "VAR_DEVELOP_BUILD_PLATFORMS": "WebGL,Linux64",
        })
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["build-android"] == "true"
        assert out["build-webgl"] == "false"
        assert out["platform-source"] == "dispatch"

    def test_dispatch_all_honours_the_environment_variable(self):
        """`All` used to mean a hardcoded five, ignoring the project's
        configuration — so a project set up for Android that picked "All" paid
        for five builds. The form always said "All uses *_BUILD_PLATFORMS";
        now it does."""
        r = run_flow({
            "EVENT_NAME": "workflow_dispatch",
            "IN_PLATFORM": "All",
            "IN_ENVIRONMENT": "staging",
            "IN_RUN_TESTS": "false",
            "IN_TEST_MODE": "All",
            "IN_BUILD_ADDRESSABLES": "false",
            "VAR_STAGING_BUILD_PLATFORMS": "Android",
        })
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["build-android"] == "true"
        for absent in ("webgl", "linux64", "linuxserver", "windows64"):
            assert out[f"build-{absent}"] == "false", absent
        # The list came from the variable, and platform-source says so rather
        # than crediting the dispatch input that merely said "All".
        assert out["platform-source"] == "variable-legacy"


# ---------------------------------------------------------------------------
# V7 – optional run-tests variable overrides branch default
# ---------------------------------------------------------------------------

class TestRepoVarRunTests:
    """V7: VAR_*_RUN_TESTS overrides branch default for run-tests."""

    def test_develop_disable_tests(self):
        r = run_flow({
            "EVENT_NAME": "push",
            "REF_NAME": "develop",
            "VAR_DEVELOP_RUN_TESTS": "false",
        })
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["run-tests"] == "false"
        assert out["test-mode"] == "None"

    def test_staging_disable_tests(self):
        r = run_flow({
            "EVENT_NAME": "push",
            "REF_NAME": "staging",
            "VAR_STAGING_RUN_TESTS": "false",
        })
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["run-tests"] == "false"

    def test_release_disable_tests(self):
        r = run_flow({
            "EVENT_NAME": "push",
            "REF_NAME": "release-1.0",
            "VAR_RELEASE_RUN_TESTS": "false",
        })
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["run-tests"] == "false"

    def test_develop_enable_tests_explicitly(self):
        """Even though default is true, setting the var to true should work."""
        r = run_flow({
            "EVENT_NAME": "push",
            "REF_NAME": "develop",
            "VAR_DEVELOP_RUN_TESTS": "true",
        })
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["run-tests"] == "true"

    def test_pr_develop_run_tests_override(self):
        """PR flows also respect run-tests variable."""
        r = run_flow({
            "EVENT_NAME": "pull_request",
            "BASE_REF": "develop",
            "VAR_DEVELOP_RUN_TESTS": "false",
        })
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["run-tests"] == "false"


# ---------------------------------------------------------------------------
# V8 – optional build-addressables variable overrides branch default
# ---------------------------------------------------------------------------

class TestRepoVarBuildAddressables:
    """V8: VAR_*_BUILD_ADDRESSABLES overrides branch default."""

    def test_develop_enable_addressables(self):
        r = run_flow({
            "EVENT_NAME": "push",
            "REF_NAME": "develop",
            "VAR_DEVELOP_BUILD_ADDRESSABLES": "true",
        })
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["build-addressables"] == "true"

    def test_release_disable_addressables(self):
        r = run_flow({
            "EVENT_NAME": "push",
            "REF_NAME": "release-1.0",
            "VAR_RELEASE_BUILD_ADDRESSABLES": "false",
        })
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["build-addressables"] == "false"

    def test_staging_enable_addressables(self):
        r = run_flow({
            "EVENT_NAME": "push",
            "REF_NAME": "staging",
            "VAR_STAGING_BUILD_ADDRESSABLES": "true",
        })
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["build-addressables"] == "true"

    def test_pr_release_addressables_override(self):
        """PR → release default is build-addressables=true, can be overridden."""
        r = run_flow({
            "EVENT_NAME": "pull_request",
            "BASE_REF": "release-1.0",
            "VAR_RELEASE_BUILD_ADDRESSABLES": "false",
        })
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["build-addressables"] == "false"


# ---------------------------------------------------------------------------
# V9 – platform-source emitted for all flows
# ---------------------------------------------------------------------------

class TestPlatformSourceEmitted:
    """V9: platform-source output is always present."""

    @pytest.mark.parametrize("env,expected_source", [
        ({"EVENT_NAME": "push", "REF_NAME": "develop"}, "default"),
        ({"EVENT_NAME": "push", "REF_NAME": "staging"}, "default"),
        ({"EVENT_NAME": "push", "REF_NAME": "release-1.0"}, "default"),
        ({"EVENT_NAME": "push", "REF_NAME": "main"}, "default"),
        ({"EVENT_NAME": "pull_request", "BASE_REF": "develop"}, "default"),
        # `All` delegates to the environment's platform list, so with no
        # variable set the source is the default — not the dispatch input,
        # which only chose *which* list to read.
        ({
            "EVENT_NAME": "workflow_dispatch",
            "IN_PLATFORM": "All",
            "IN_ENVIRONMENT": "production",
            "IN_RUN_TESTS": "false",
            "IN_TEST_MODE": "All",
            "IN_BUILD_ADDRESSABLES": "false",
        }, "default"),
        # An explicit platform IS the dispatch's own decision.
        ({
            "EVENT_NAME": "workflow_dispatch",
            "IN_PLATFORM": "Android",
            "IN_ENVIRONMENT": "production",
            "IN_RUN_TESTS": "false",
            "IN_TEST_MODE": "All",
            "IN_BUILD_ADDRESSABLES": "false",
        }, "dispatch"),
        ({
            "EVENT_NAME": "push",
            "REF_NAME": "develop",
            "VAR_DEVELOP_BUILD_PLATFORMS": "Android",
        }, "variable-legacy"),
    ])
    def test_platform_source_value(self, env, expected_source):
        r = run_flow(env)
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["platform-source"] == expected_source


# ---------------------------------------------------------------------------
# V10 – invalid run-tests variable → script exits non-zero
# ---------------------------------------------------------------------------

class TestRepoVarInvalidRunTests:
    """V10: Invalid run-tests variable value causes script to fail."""

    def test_invalid_develop_run_tests(self):
        r = run_flow({
            "EVENT_NAME": "push",
            "REF_NAME": "develop",
            "VAR_DEVELOP_RUN_TESTS": "yes",
        })
        assert r.returncode != 0
        assert "yes" in r.stderr

    def test_invalid_staging_run_tests(self):
        r = run_flow({
            "EVENT_NAME": "push",
            "REF_NAME": "staging",
            "VAR_STAGING_RUN_TESTS": "1",
        })
        assert r.returncode != 0


# ---------------------------------------------------------------------------
# V11 – invalid build-addressables variable → script exits non-zero
# ---------------------------------------------------------------------------

class TestRepoVarInvalidBuildAddressables:
    """V11: Invalid build-addressables variable value causes script to fail."""

    def test_invalid_develop_build_addressables(self):
        r = run_flow({
            "EVENT_NAME": "push",
            "REF_NAME": "develop",
            "VAR_DEVELOP_BUILD_ADDRESSABLES": "yes",
        })
        assert r.returncode != 0
        assert "yes" in r.stderr

    def test_invalid_release_build_addressables(self):
        r = run_flow({
            "EVENT_NAME": "push",
            "REF_NAME": "release-1.0",
            "VAR_RELEASE_BUILD_ADDRESSABLES": "0",
        })
        assert r.returncode != 0


# ---------------------------------------------------------------------------
# V12 – iOS in repo variable is ignored for branch flows
# ---------------------------------------------------------------------------

class TestRepoVarIOSIgnored:
    """iOS in platform variable is silently ignored for branch flows."""

    def test_ios_in_develop_var_ignored(self):
        r = run_flow({
            "EVENT_NAME": "push",
            "REF_NAME": "develop",
            "VAR_DEVELOP_BUILD_PLATFORMS": "Android,iOS",
        })
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["build-android"] == "true"
        assert out["build-ios"] == "false"
        assert out["platform-source"] == "variable-legacy"

    def test_ios_in_release_var_ignored(self):
        r = run_flow({
            "EVENT_NAME": "push",
            "REF_NAME": "release-1.0",
            "VAR_RELEASE_BUILD_PLATFORMS": "Android,WebGL,iOS",
        })
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["build-ios"] == "false"
        assert out["build-android"] == "true"
        assert out["build-webgl"] == "true"


# ---------------------------------------------------------------------------
# V13 – whitespace in CSV is handled
# ---------------------------------------------------------------------------

class TestRepoVarWhitespace:
    """Whitespace around platform names in CSV is trimmed."""

    def test_whitespace_trimmed(self):
        r = run_flow({
            "EVENT_NAME": "push",
            "REF_NAME": "develop",
            "VAR_DEVELOP_BUILD_PLATFORMS": " Android , WebGL ",
        })
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["build-android"] == "true"
        assert out["build-webgl"] == "true"
        assert out["platform-source"] == "variable-legacy"


# ── GitHub deployment environment (gh-environment) ──────────────────────────

class TestGitHubEnvironment:
    """gh-environment: empty for PR/none flows, the env for push/manual."""

    def test_pr_develop_no_gh_environment(self):
        out = parse_outputs(run_flow(
            {"EVENT_NAME": "pull_request", "BASE_REF": "develop", "REF_NAME": "x"}).stdout)
        assert out["gh-environment"] == "", "PRs must not target a GitHub environment"

    def test_pr_release_no_production(self):
        out = parse_outputs(run_flow(
            {"EVENT_NAME": "pull_request", "BASE_REF": "release-2.0", "REF_NAME": "x"}).stdout)
        assert out["gh-environment"] == "", "PR into release-* must NOT deploy to production"

    def test_push_develop_development(self):
        out = parse_outputs(run_flow({"EVENT_NAME": "push", "REF_NAME": "develop"}).stdout)
        assert out["gh-environment"] == "development"

    def test_push_staging_staging(self):
        out = parse_outputs(run_flow({"EVENT_NAME": "push", "REF_NAME": "staging"}).stdout)
        assert out["gh-environment"] == "staging"

    def test_push_release_production(self):
        out = parse_outputs(run_flow({"EVENT_NAME": "push", "REF_NAME": "release-1.2"}).stdout)
        assert out["gh-environment"] == "production"

    def test_manual_dispatch_has_no_gh_environment(self):
        # workflow_dispatch is an ad-hoc build (often from main); it must NOT target
        # a protected GitHub Environment, or env protection rules block it.
        out = parse_outputs(run_flow(
            {"EVENT_NAME": "workflow_dispatch", "IN_PLATFORM": "All", "IN_ENVIRONMENT": "staging"}).stdout)
        assert out["gh-environment"] == ""
        assert out["environment"] == "staging"

    def test_no_match_branch_no_environment(self):
        out = parse_outputs(run_flow({"EVENT_NAME": "push", "REF_NAME": "main"}).stdout)
        assert out["gh-environment"] == ""


# ---------------------------------------------------------------------------
# Per-branch Scripting Define Symbols (VAR_*_DEFINE_SYMBOLS → define-symbols)
# ---------------------------------------------------------------------------

class TestDefineSymbols:
    """define-symbols output resolved from per-branch repo variables."""

    def test_key_always_emitted(self):
        out = parse_outputs(run_flow({"EVENT_NAME": "push", "REF_NAME": "develop"}).stdout)
        assert "define-symbols" in out

    def test_empty_when_no_variable(self):
        out = parse_outputs(run_flow({"EVENT_NAME": "push", "REF_NAME": "develop"}).stdout)
        assert out["define-symbols"] == ""

    def test_push_develop_variable(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "VAR_DEVELOP_DEFINE_SYMBOLS": "DEV;PROFILER",
        }).stdout)
        assert out["define-symbols"] == "DEV;PROFILER"

    def test_push_staging_variable(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "staging",
            "VAR_STAGING_DEFINE_SYMBOLS": "STAGING",
        }).stdout)
        assert out["define-symbols"] == "STAGING"

    def test_push_release_variable(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "release-1.2",
            "VAR_RELEASE_DEFINE_SYMBOLS": "PRODUCTION;LIVE",
        }).stdout)
        assert out["define-symbols"] == "PRODUCTION;LIVE"

    def test_branch_variables_are_isolated(self):
        # develop var must NOT leak into a staging build
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "staging",
            "VAR_DEVELOP_DEFINE_SYMBOLS": "DEV_ONLY",
        }).stdout)
        assert out["define-symbols"] == ""

    def test_pr_develop_variable_applies(self):
        # PR validation compiles with the branch's symbols too
        out = parse_outputs(run_flow({
            "EVENT_NAME": "pull_request", "BASE_REF": "develop",
            "VAR_DEVELOP_DEFINE_SYMBOLS": "DEV",
        }).stdout)
        assert out["define-symbols"] == "DEV"

    def test_manual_dispatch_input_override(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "workflow_dispatch", "IN_PLATFORM": "Android",
            "IN_ENVIRONMENT": "staging", "IN_DEFINE_SYMBOLS": "MANUAL_SYM",
        }).stdout)
        assert out["define-symbols"] == "MANUAL_SYM"


# ===========================================================================
# Grouped-config resolver tests (NEW_/LEG_/IN_ env styles, per CONFIG_CONTRACT.md)
# ===========================================================================
#
# G1  No variables -> all defaults
# G2  Legacy-only vars resolve (LEG_/VAR_/bare) -> platform-source=variable-legacy
# G3  New-only vars resolve (NEW_/bare-new) -> platform-source=variable-new
# G4  Mixed new+legacy -> new wins
# G5  Priority resolution for tests/addressables/define-symbols/runner-mode
# G6  Invalid platform / runner mode -> nonzero exit
# G7  Boolean parsing (valid + invalid)
# G8  Unity version resolution (repo var / ProjectVersion.txt / default)
# G9  BUILD_CLEAN + IN_CLEAN_BUILD priority
# G10 Timeout / retention / compression validation
# G11 Runner labels
# G12 test-mode derivation from editmode/playmode toggles


class TestGroupedConfigDefaults:
    """G1: With zero grouped-config env set, every new output uses its documented default."""

    @pytest.fixture(autouse=True)
    def _result(self):
        r = run_flow({"EVENT_NAME": "push", "REF_NAME": "develop"})
        assert r.returncode == 0
        self.out = parse_outputs(r.stdout)

    def test_platform_source_default(self):
        assert self.out["platform-source"] == "default"

    def test_unity_version_default(self):
        # No repo var, no ProjectVersion.txt at project-path "." within the toolkit
        # tree that would resolve -> falls through to toolkit default.
        assert self.out["unity-version"] == "6000.0.26f1"

    def test_project_path_default(self):
        assert self.out["project-path"] == "."

    def test_runner_mode_default(self):
        assert self.out["runner-mode"] == "docker"

    def test_clean_build_default(self):
        assert self.out["clean-build"] == "false"
        assert self.out["clean-build-source"] == "default"

    def test_timeout_default(self):
        assert self.out["build-timeout-minutes"] == "120"

    def test_caches_default_true(self):
        for key in ["cache-library", "cache-gradle", "cache-addressables", "cache-nuget"]:
            assert self.out[key] == "true", f"{key} should default true"

    def test_retention_default(self):
        assert self.out["artifact-retention-days"] == "30"

    def test_compression_default(self):
        assert self.out["artifact-compression"] == "zip"

    def test_runner_labels_default(self):
        assert self.out["runner-windows-label"] == "self-hosted-windows"
        assert self.out["runner-macos-label"] == "self-hosted-macos"
        assert self.out["runner-linux-label"] == "ubuntu-latest"

    def test_test_toggles_default_true(self):
        assert self.out["test-editmode"] == "true"
        assert self.out["test-playmode"] == "true"
        assert self.out["test-fail-fast"] == "false"


class TestGroupedConfigLegacyOnly:
    """G2: Legacy-tier-only env (LEG_/VAR_/bare) resolves platforms; source=variable-legacy."""

    def test_leg_prefixed(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "staging",
            "LEG_STAGING_BUILD_PLATFORMS": "WebGL",
        }).stdout)
        assert out["build-webgl"] == "true"
        assert out["build-android"] == "false"
        assert out["platform-source"] == "variable-legacy"

    def test_var_prefixed(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "staging",
            "VAR_STAGING_BUILD_PLATFORMS": "WebGL",
        }).stdout)
        assert out["build-webgl"] == "true"
        assert out["build-android"] == "false"
        assert out["platform-source"] == "variable-legacy"

    def test_bare_legacy_name(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "staging",
            "STAGING_BUILD_PLATFORMS": "WebGL",
        }).stdout)
        assert out["build-webgl"] == "true"
        assert out["build-android"] == "false"
        assert out["platform-source"] == "variable-legacy"


class TestGroupedConfigNewOnly:
    """G3: New-tier-only env resolves platforms; source=variable-new."""

    def test_new_prefixed(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "staging",
            "NEW_BUILD_STAGING_PLATFORMS": "WebGL",
        }).stdout)
        assert out["build-webgl"] == "true"
        assert out["build-android"] == "false"
        assert out["platform-source"] == "variable-new"

    def test_bare_new_name(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "staging",
            "BUILD_STAGING_PLATFORMS": "WebGL",
        }).stdout)
        assert out["build-webgl"] == "true"
        assert out["build-android"] == "false"
        assert out["platform-source"] == "variable-new"


class TestGroupedConfigNewBeatsLegacy:
    """G4: When both new and legacy tiers are set, new always wins."""

    def test_platforms_new_wins(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "staging",
            "NEW_BUILD_STAGING_PLATFORMS": "WebGL",
            "VAR_STAGING_BUILD_PLATFORMS": "Android",
        }).stdout)
        assert out["build-webgl"] == "true"
        assert out["build-android"] == "false"
        assert out["platform-source"] == "variable-new"


class TestGroupedConfigPriorityResolution:
    """G5: New beats legacy beats default for tests-enabled, addressables,
    define-symbols, and runner-mode."""

    def test_run_tests_new_beats_legacy(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_TEST_DEVELOP_ENABLED": "false",
            "VAR_DEVELOP_RUN_TESTS": "true",
        }).stdout)
        assert out["run-tests"] == "false"

    def test_addressables_new_beats_legacy(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_ADDRESSABLES_DEVELOP_ENABLED": "true",
            "VAR_DEVELOP_BUILD_ADDRESSABLES": "false",
        }).stdout)
        assert out["build-addressables"] == "true"

    def test_define_symbols_new_beats_legacy(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_DEVELOP_DEFINE_SYMBOLS": "NEWSYM",
            "VAR_DEVELOP_DEFINE_SYMBOLS": "OLDSYM",
        }).stdout)
        assert out["define-symbols"] == "NEWSYM"

    def test_runner_mode_new_beats_legacy(self):
        """RUNNER_DEFAULT_MODE is legacy-only now (no NEW_RUNNER_DEFAULT_MODE "new"
        semantics); it maps to runner-type/build-engine. The NEW_/bare tier of
        RUNNER_DEFAULT_MODE still beats the VAR_/bare-legacy tier *within* the
        legacy-mapping resolution itself (see resolve_setting call for
        _legacy_mode_raw), so NEW_RUNNER_DEFAULT_MODE=auto wins over
        VAR_DEFAULT_RUNNER_MODE=self-hosted-macos and maps to
        github-hosted+docker."""
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_RUNNER_DEFAULT_MODE": "auto",
            "VAR_DEFAULT_RUNNER_MODE": "self-hosted-macos",
        }).stdout)
        assert out["runner-type"] == "github-hosted"
        assert out["build-engine"] == "docker"
        assert out["runner-mode"] == "docker"
        assert out["runner-type-source"] == "variable-legacy"
        assert out["build-engine-source"] == "variable-legacy"

    def test_runner_mode_legacy_beats_default(self):
        """VAR_DEFAULT_RUNNER_MODE=self-hosted-macos (legacy-only, no new axis
        vars set) maps to runner-type=self-hosted, build-engine=local, with
        macOS default labels."""
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "VAR_DEFAULT_RUNNER_MODE": "self-hosted-macos",
        }).stdout)
        assert out["runner-type"] == "self-hosted"
        assert out["build-engine"] == "local"
        assert out["runner-mode"] == "self-hosted-windows"
        assert out["runner-labels"] == '["self-hosted","macOS"]'
        assert out["runner-type-source"] == "variable-legacy"
        assert out["build-engine-source"] == "variable-legacy"


class TestGroupedConfigValidation:
    """G6/G7/G10: Invalid values fail fast with nonzero exit; valid values pass."""

    def test_invalid_platform_fails(self):
        r = run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_BUILD_DEVELOP_PLATFORMS": "Nope",
        })
        assert r.returncode != 0
        assert "Nope" in r.stderr

    def test_invalid_runner_mode_fails(self):
        r = run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_RUNNER_DEFAULT_MODE": "bogus",
        })
        assert r.returncode != 0
        assert "bogus" in r.stderr

    def test_invalid_test_fail_fast_fails(self):
        r = run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_TEST_FAIL_FAST": "yes",
        })
        assert r.returncode != 0

    def test_invalid_cache_library_fails(self):
        r = run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_CACHE_LIBRARY_ENABLED": "nope",
        })
        assert r.returncode != 0

    def test_valid_bool_true_accepted(self):
        r = run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_TEST_FAIL_FAST": "true",
        })
        assert r.returncode == 0
        assert parse_outputs(r.stdout)["test-fail-fast"] == "true"

    def test_valid_bool_false_accepted(self):
        r = run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_TEST_FAIL_FAST": "false",
        })
        assert r.returncode == 0
        assert parse_outputs(r.stdout)["test-fail-fast"] == "false"

    def test_timeout_override(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_BUILD_TIMEOUT_MINUTES": "45",
        }).stdout)
        assert out["build-timeout-minutes"] == "45"

    def test_timeout_invalid_non_integer_fails(self):
        r = run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_BUILD_TIMEOUT_MINUTES": "abc",
        })
        assert r.returncode != 0

    def test_timeout_invalid_zero_fails(self):
        r = run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_BUILD_TIMEOUT_MINUTES": "0",
        })
        assert r.returncode != 0

    def test_retention_override(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_ARTIFACT_RETENTION_DAYS": "7",
        }).stdout)
        assert out["artifact-retention-days"] == "7"

    def test_retention_invalid_zero_fails(self):
        r = run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_ARTIFACT_RETENTION_DAYS": "0",
        })
        assert r.returncode != 0

    def test_retention_invalid_non_integer_fails(self):
        r = run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_ARTIFACT_RETENTION_DAYS": "many",
        })
        assert r.returncode != 0

    def test_compression_invalid_fails(self):
        r = run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_ARTIFACT_COMPRESSION": "gzip",
        })
        assert r.returncode != 0

    def test_compression_zip_passes(self):
        r = run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_ARTIFACT_COMPRESSION": "zip",
        })
        assert r.returncode == 0
        assert parse_outputs(r.stdout)["artifact-compression"] == "zip"


class TestGroupedConfigUnityVersion:
    """G8: unity-version resolution order: repo var -> ProjectVersion.txt -> default."""

    def test_repo_var_wins(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_UNITY_VERSION": "1.2.3",
        }).stdout)
        assert out["unity-version"] == "1.2.3"

    def test_detected_from_project_version_file(self, tmp_path):
        project_settings = tmp_path / "ProjectSettings"
        project_settings.mkdir()
        (project_settings / "ProjectVersion.txt").write_text(
            "m_EditorVersion: 6000.0.99f1\n"
            "m_EditorVersionWithRevision: 6000.0.99f1 (abcdef123456)\n"
        )
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_UNITY_PROJECT_PATH": str(tmp_path),
        }).stdout)
        assert out["unity-version"] == "6000.0.99f1"
        assert out["project-path"] == str(tmp_path)

    def test_repo_var_wins_over_project_version_file(self, tmp_path):
        project_settings = tmp_path / "ProjectSettings"
        project_settings.mkdir()
        (project_settings / "ProjectVersion.txt").write_text(
            "m_EditorVersion: 6000.0.99f1\n"
        )
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_UNITY_PROJECT_PATH": str(tmp_path),
            "NEW_UNITY_VERSION": "9.9.9f1",
        }).stdout)
        assert out["unity-version"] == "9.9.9f1"

    def test_neither_falls_back_to_toolkit_default(self, tmp_path):
        # Empty project dir -> no ProjectSettings/ProjectVersion.txt present.
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_UNITY_PROJECT_PATH": str(tmp_path),
        }).stdout)
        assert out["unity-version"] == "6000.0.26f1"


class TestGroupedConfigCleanBuild:
    """G9: BUILD_CLEAN / IN_CLEAN_BUILD priority (dispatch > variable > default)."""

    def test_default_false(self):
        out = parse_outputs(run_flow({"EVENT_NAME": "push", "REF_NAME": "develop"}).stdout)
        assert out["clean-build"] == "false"
        assert out["clean-build-source"] == "default"

    def test_variable_true(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_BUILD_CLEAN": "true",
        }).stdout)
        assert out["clean-build"] == "true"
        assert out["clean-build-source"] == "variable-new"

    def test_dispatch_true_overrides_default(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "IN_CLEAN_BUILD": "true",
        }).stdout)
        assert out["clean-build"] == "true"
        assert out["clean-build-source"] == "workflow_dispatch"

    def test_dispatch_false_overrides_variable_true(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_BUILD_CLEAN": "true",
            "IN_CLEAN_BUILD": "false",
        }).stdout)
        assert out["clean-build"] == "false"
        assert out["clean-build-source"] == "workflow_dispatch"

    def test_dispatch_auto_falls_back_to_variable(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_BUILD_CLEAN": "true",
            "IN_CLEAN_BUILD": "auto",
        }).stdout)
        assert out["clean-build"] == "true"
        assert out["clean-build-source"] == "variable-new"

    def test_dispatch_auto_falls_back_to_default(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "IN_CLEAN_BUILD": "auto",
        }).stdout)
        assert out["clean-build"] == "false"
        assert out["clean-build-source"] == "default"


class TestGroupedConfigRunnerLabels:
    """G11: Runner label overrides pass through."""

    def test_linux_label_override(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_RUNNER_LINUX_LABEL": "my-linux",
        }).stdout)
        assert out["runner-linux-label"] == "my-linux"

    def test_windows_label_override(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_RUNNER_WINDOWS_LABEL": "my-windows",
        }).stdout)
        assert out["runner-windows-label"] == "my-windows"

    def test_macos_label_override(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_RUNNER_MACOS_LABEL": "my-macos",
        }).stdout)
        assert out["runner-macos-label"] == "my-macos"


class TestGroupedConfigTestModeDerivation:
    """G12: test-mode derives from editmode/playmode toggles; run-tests=false forces None."""

    def test_editmode_only(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_TEST_EDITMODE_ENABLED": "true",
            "NEW_TEST_PLAYMODE_ENABLED": "false",
        }).stdout)
        assert out["test-mode"] == "EditMode"

    def test_playmode_only(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_TEST_EDITMODE_ENABLED": "false",
            "NEW_TEST_PLAYMODE_ENABLED": "true",
        }).stdout)
        assert out["test-mode"] == "PlayMode"

    def test_both_enabled(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_TEST_EDITMODE_ENABLED": "true",
            "NEW_TEST_PLAYMODE_ENABLED": "true",
        }).stdout)
        assert out["test-mode"] == "All"

    def test_neither_enabled(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_TEST_EDITMODE_ENABLED": "false",
            "NEW_TEST_PLAYMODE_ENABLED": "false",
        }).stdout)
        assert out["test-mode"] == "None"

    def test_run_tests_false_forces_none_even_with_toggles_true(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "workflow_dispatch",
            "IN_PLATFORM": "Android",
            "IN_RUN_TESTS": "false",
            "NEW_TEST_EDITMODE_ENABLED": "true",
            "NEW_TEST_PLAYMODE_ENABLED": "true",
        }).stdout)
        assert out["run-tests"] == "false"
        assert out["test-mode"] == "None"


# ===========================================================================
# Runner / Build-Engine separation (RUNNER_ENGINE_CONTRACT.md V2)
# ===========================================================================
#
# Runner  = WHERE the job runs   -> runner-type (github-hosted|self-hosted) + runner-labels
# Engine  = HOW Unity builds     -> build-engine (docker|local)
# Priority: workflow_dispatch (IN_*) > new repo variable (NEW_*/bare) >
# legacy repo variable (RUNNER_DEFAULT_MODE mapping) > toolkit default.

class TestRunnerEngine:
    """Runner-type / build-engine / execution-strategy / runner-labels /
    activation-strategy resolution, per RUNNER_ENGINE_CONTRACT.md."""

    # -- default: no vars set --------------------------------------------

    def test_default_no_vars(self):
        out = parse_outputs(run_flow({"EVENT_NAME": "push", "REF_NAME": "develop"}).stdout)
        assert out["runner-type"] == "github-hosted"
        assert out["build-engine"] == "docker"
        assert out["execution-strategy"] == "github-docker"
        assert out["runner-labels"] == '["ubuntu-latest"]'
        assert out["activation-strategy"] == "auto"
        assert out["runner-type-source"] == "default"
        assert out["build-engine-source"] == "default"

    # -- self-hosted + local -----------------------------------------------

    def test_self_hosted_local(self):
        r = run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_RUNNER_TYPE": "self-hosted",
            "NEW_BUILD_ENGINE": "local",
        })
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["runner-type"] == "self-hosted"
        assert out["build-engine"] == "local"
        assert out["execution-strategy"] == "selfhosted-local"
        assert out["runner-labels"] == '["self-hosted","windows"]'
        assert out["activation-strategy"] == "none"

    # -- self-hosted + docker -----------------------------------------------

    def test_self_hosted_docker(self):
        r = run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_RUNNER_TYPE": "self-hosted",
            "NEW_BUILD_ENGINE": "docker",
        })
        assert r.returncode == 0
        out = parse_outputs(r.stdout)
        assert out["execution-strategy"] == "selfhosted-docker"
        assert out["activation-strategy"] == "auto"

    # -- github-hosted + local: INVALID --------------------------------------

    def test_github_hosted_local_invalid(self):
        r = run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_RUNNER_TYPE": "github-hosted",
            "NEW_BUILD_ENGINE": "local",
        })
        assert r.returncode != 0
        assert "GitHub-hosted" in r.stderr

    # -- legacy RUNNER_DEFAULT_MODE mapping -----------------------------------

    def test_legacy_self_hosted_windows(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "RUNNER_DEFAULT_MODE": "self-hosted-windows",
        }).stdout)
        assert out["runner-type"] == "self-hosted"
        assert out["build-engine"] == "local"
        assert out["runner-type-source"] == "variable-legacy"
        assert out["runner-labels"] == '["self-hosted","windows"]'

    def test_legacy_docker(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "RUNNER_DEFAULT_MODE": "docker",
        }).stdout)
        assert out["runner-type"] == "github-hosted"
        assert out["build-engine"] == "docker"

    def test_legacy_self_hosted_macos(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "RUNNER_DEFAULT_MODE": "self-hosted-macos",
        }).stdout)
        assert out["runner-type"] == "self-hosted"
        assert out["build-engine"] == "local"
        assert out["runner-labels"] == '["self-hosted","macOS"]'

    # -- dispatch overrides repo var -------------------------------------------

    def test_dispatch_runner_type_overrides_repo_var(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "IN_RUNNER_TYPE": "self-hosted",
            "NEW_RUNNER_TYPE": "github-hosted",
            "NEW_BUILD_ENGINE": "local",
        }).stdout)
        assert out["runner-type"] == "self-hosted"
        assert out["runner-type-source"] == "dispatch"

    def test_dispatch_build_engine_overrides_repo_var(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "IN_BUILD_ENGINE": "local",
            "NEW_BUILD_ENGINE": "docker",
            "NEW_RUNNER_TYPE": "self-hosted",
        }).stdout)
        assert out["build-engine"] == "local"
        assert out["build-engine-source"] == "dispatch"

    # -- runner-labels normalization --------------------------------------------

    def test_labels_default_github_hosted(self):
        out = parse_outputs(run_flow({"EVENT_NAME": "push", "REF_NAME": "develop"}).stdout)
        assert out["runner-labels"] == '["ubuntu-latest"]'

    def test_labels_default_self_hosted(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_RUNNER_TYPE": "self-hosted",
            "NEW_BUILD_ENGINE": "local",
        }).stdout)
        assert out["runner-labels"] == '["self-hosted","windows"]'

    def test_labels_custom(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_RUNNER_TYPE": "self-hosted",
            "NEW_BUILD_ENGINE": "local",
            "NEW_RUNNER_LABELS": "self-hosted,windows,gpu",
        }).stdout)
        assert out["runner-labels"] == '["self-hosted","windows","gpu"]'

    def test_labels_deduplicated(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_RUNNER_TYPE": "self-hosted",
            "NEW_BUILD_ENGINE": "local",
            "NEW_RUNNER_LABELS": "self-hosted,windows,windows",
        }).stdout)
        assert out["runner-labels"] == '["self-hosted","windows"]'

    def test_labels_whitespace_trimmed(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_RUNNER_TYPE": "self-hosted",
            "NEW_BUILD_ENGINE": "local",
            "NEW_RUNNER_LABELS": " self-hosted , windows ",
        }).stdout)
        assert out["runner-labels"] == '["self-hosted","windows"]'

    def test_labels_empty_fails(self):
        r = run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_RUNNER_TYPE": "self-hosted",
            "NEW_BUILD_ENGINE": "local",
            "NEW_RUNNER_LABELS": " , ",
        })
        assert r.returncode != 0

    # -- activation-strategy ------------------------------------------------

    def test_activation_local_is_none(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_RUNNER_TYPE": "self-hosted",
            "NEW_BUILD_ENGINE": "local",
        }).stdout)
        assert out["activation-strategy"] == "none"

    def test_activation_docker_defaults_auto(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_BUILD_ENGINE": "docker",
        }).stdout)
        assert out["activation-strategy"] == "auto"

    def test_activation_docker_dispatch_override(self):
        out = parse_outputs(run_flow({
            "EVENT_NAME": "push", "REF_NAME": "develop",
            "NEW_BUILD_ENGINE": "docker",
            "IN_ACTIVATION_STRATEGY": "manual-license",
        }).stdout)
        assert out["activation-strategy"] == "manual-license"
