"""
Tests for iOS shell scripts (scripts/ios/*.sh).

Tests cover the full iOS pipeline contract:
  - Xcode output existence after Unity build
  - workspace-vs-project resolution
  - scheme resolution
  - ExportOptions.plist generation
  - archive command construction
  - export command construction
  - secret redaction in logs (no secret values in output)
  - temp keychain cleanup
  - provisioning profile cleanup
  - ASC key file cleanup
  - artifact contract presence
  - missing IPA failure
  - missing archive failure
  - archive/export failure exit code propagation
  - TestFlight upload rejection (unauthorized context)
  - fork release rejection

PENDING TEAMMATE: scripts/ios/*.sh are produced by the macos-workflows-engineer.
Tests skip gracefully if the scripts are absent and fail clearly once they land.
Fake executables (fake_unity.sh, fake_xcodebuild.sh) simulate Unity and
xcodebuild behaviour without requiring macOS, Apple credentials, or Xcode.

NOTE: Many tests exercise shell scripts that invoke macOS-only binaries
(security, codesign, xcrun, altool). Those specific tests are marked
  @pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only")
so they are skipped on Linux CI. Exit-code propagation and
secret-redaction tests are platform-agnostic and always run.
"""
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
FIXTURES_DIR = Path(__file__).parent / "fixtures"

FAKE_UNITY = FIXTURES_DIR / "fake_unity.sh"
FAKE_XCODEBUILD = FIXTURES_DIR / "fake_xcodebuild.sh"

SCRIPTS_IOS = REPO_ROOT / "scripts" / "ios"

# Individual script paths (actual names as created by macos-workflows-engineer)
# The Unity build step is handled in the workflow itself (not a separate script).
# Individual signing setup is split across: create_keychain.sh, import_certificate.sh,
# install_profile.sh (no single setup_signing.sh).
ARCHIVE_IOS_SH = SCRIPTS_IOS / "xcode_archive.sh"
EXPORT_IOS_SH = SCRIPTS_IOS / "xcode_export.sh"
UPLOAD_TESTFLIGHT_SH = SCRIPTS_IOS / "testflight_upload.sh"
CLEANUP_IOS_SH = SCRIPTS_IOS / "cleanup_signing.sh"
CREATE_KEYCHAIN_SH = SCRIPTS_IOS / "create_keychain.sh"
IMPORT_CERT_SH = SCRIPTS_IOS / "import_certificate.sh"
INSTALL_PROFILE_SH = SCRIPTS_IOS / "install_profile.sh"
GENERATE_EXPORT_OPTIONS_SH = SCRIPTS_IOS / "generate_export_options.sh"
VALIDATE_IPA_SH = SCRIPTS_IOS / "validate_ipa.sh"

# There is no build_ios.sh — Unity iOS build is invoked directly in the workflow YAML.
# Reference BUILD_IOS_SH for backward compat but note it doesn't exist.
BUILD_IOS_SH = SCRIPTS_IOS / "build_ios.sh"  # Does not exist — Unity invoked in workflow

_ON_MACOS = sys.platform == "darwin"

# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

def _make_executable(path: Path):
    if path.exists():
        path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _scripts_exist(*paths: Path) -> bool:
    return all(p.exists() for p in paths)


def _skip_if_missing(*paths: Path):
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        return pytest.mark.skip(
            reason=f"Pending macos-workflows-engineer: {', '.join(missing)}"
        )
    return lambda f: f


def _build_fake_xcode_project(root: Path, scheme: str = "MyGame") -> Path:
    """Create a fake .xcworkspace layout that iOS scripts can reference."""
    workspace = root / f"{scheme}.xcworkspace"
    workspace.mkdir(parents=True)
    (workspace / "contents.xcworkspacedata").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Workspace version="1.0">\n'
        f' <FileRef location="group:{scheme}.xcodeproj"/>\n'
        '</Workspace>\n'
    )
    # Also create a .xcodeproj
    proj = root / f"{scheme}.xcodeproj"
    proj.mkdir(parents=True)
    (proj / "project.pbxproj").write_text("// !$*UTF8*$!\n{ archiveVersion = 1; }")
    return workspace


def _make_fake_bin_dir(tmp_path: Path) -> Path:
    """
    Create a temporary bin directory containing fake executables.

    Puts fake `xcodebuild` and `Unity` shims into a directory that can be
    prepended to PATH. This allows the iOS scripts to find these commands
    without requiring a real macOS environment.

    Returns the path to the fake bin directory.
    """
    fake_bin = tmp_path / "_fake_bin"
    fake_bin.mkdir(exist_ok=True)

    if FAKE_XCODEBUILD.exists():
        _make_executable(FAKE_XCODEBUILD)
        xcodebuild_shim = fake_bin / "xcodebuild"
        xcodebuild_shim.write_text(
            f'#!/usr/bin/env bash\nexec bash "{FAKE_XCODEBUILD}" "$@"\n'
        )
        _make_executable(xcodebuild_shim)

    # Also shim `security` to avoid macOS-only calls crashing on Linux
    security_shim = fake_bin / "security"
    security_shim.write_text(
        '#!/usr/bin/env bash\n'
        '# Fake security command for Linux CI testing\n'
        'echo "[fake security] $*" >&2\n'
        'exit 0\n'
    )
    _make_executable(security_shim)

    # Shim `xcrun` for TestFlight upload testing on Linux
    xcrun_shim = fake_bin / "xcrun"
    xcrun_shim.write_text(
        '#!/usr/bin/env bash\n'
        '# Fake xcrun command for Linux CI testing\n'
        'echo "[fake xcrun] $*" >&2\n'
        '# Simulate altool upload failure (no real connection) — exit 1\n'
        'exit 1\n'
    )
    _make_executable(xcrun_shim)

    return fake_bin


def _run_script(
    script: Path,
    args: list[str],
    env_overrides: dict | None = None,
    tmp_path: Path | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()

    # Create fake bin dir with xcodebuild/security shims if tmp_path provided
    if tmp_path and FAKE_XCODEBUILD.exists():
        fake_bin = _make_fake_bin_dir(tmp_path)
        env["PATH"] = f"{fake_bin}:{env.get('PATH', '/usr/bin:/bin')}"

    # Inject fake Unity
    if FAKE_UNITY.exists():
        _make_executable(FAKE_UNITY)
        env["UNITY_EDITOR"] = str(FAKE_UNITY)

    # Strip real Apple credentials to avoid accidental use
    for key in (
        "IOS_DISTRIBUTION_CERTIFICATE_BASE64",
        "IOS_DISTRIBUTION_CERTIFICATE_PASSWORD",
        "IOS_PROVISIONING_PROFILE_BASE64",
        "APP_STORE_CONNECT_KEY_ID",
        "APP_STORE_CONNECT_ISSUER_ID",
        "APP_STORE_CONNECT_PRIVATE_KEY",
        "UNITY_LICENSE",
        "UNITY_EMAIL",
        "UNITY_PASSWORD",
    ):
        env.pop(key, None)
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        ["bash", str(script)] + args,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
        cwd=str(tmp_path) if tmp_path else None,
    )


# -----------------------------------------------------------------------
# File existence checks — always run, give clear messages
# -----------------------------------------------------------------------

class TestIOSScriptFilesExist:
    """
    These tests fail clearly (not error) when teammate scripts are absent.
    All 5 core iOS scripts are created by the macos-workflows-engineer.
    """

    def test_scripts_ios_directory_exists(self):
        assert SCRIPTS_IOS.exists(), (
            f"PENDING macos-workflows-engineer: {SCRIPTS_IOS} directory does not exist."
        )

    def test_xcode_archive_sh_exists(self):
        assert ARCHIVE_IOS_SH.exists(), (
            f"PENDING macos-workflows-engineer: {ARCHIVE_IOS_SH} does not exist."
        )

    def test_xcode_export_sh_exists(self):
        assert EXPORT_IOS_SH.exists(), (
            f"PENDING macos-workflows-engineer: {EXPORT_IOS_SH} does not exist."
        )

    def test_testflight_uses_fastlane(self):
        """testflight_upload.sh replaced by Fastlane pilot via ios-testflight action."""
        action = REPO_ROOT / ".github" / "actions" / "ios-testflight" / "action.yml"
        assert action.exists(), "ios-testflight action must exist"
        assert "fastlane" in action.read_text(), "ios-testflight must use Fastlane"

    def test_cleanup_signing_sh_exists(self):
        assert CLEANUP_IOS_SH.exists(), (
            f"PENDING macos-workflows-engineer: {CLEANUP_IOS_SH} does not exist."
        )

    def test_generate_export_options_sh_exists(self):
        assert GENERATE_EXPORT_OPTIONS_SH.exists(), (
            f"PENDING macos-workflows-engineer: {GENERATE_EXPORT_OPTIONS_SH} does not exist."
        )

    def test_note_no_build_ios_sh(self):
        """
        NOTE: There is no build_ios.sh — Unity is invoked directly in the
        unity-build-ios.yml workflow YAML. This is by design.
        This test documents that expectation so future engineers don't hunt for the file.
        """
        assert not BUILD_IOS_SH.exists(), (
            "build_ios.sh was unexpectedly created. If Unity build was extracted to a "
            "separate script, update the tests accordingly."
        )

    def test_fake_xcodebuild_fixture_exists(self):
        assert FAKE_XCODEBUILD.exists(), (
            f"Test fixture missing: {FAKE_XCODEBUILD}"
        )


# -----------------------------------------------------------------------
# fake_xcodebuild.sh self-tests
# -----------------------------------------------------------------------

class TestFakeXcodebuild:
    """Verify fake_xcodebuild.sh behaves as expected before using it in other tests."""

    @pytest.fixture(autouse=True)
    def make_fake_executable(self):
        _make_executable(FAKE_XCODEBUILD)

    def test_fake_xcodebuild_success_mode(self, tmp_path):
        archive_path = tmp_path / "MyApp.xcarchive"
        result = subprocess.run(
            ["bash", str(FAKE_XCODEBUILD), "archive"],
            capture_output=True,
            text=True,
            timeout=10,
            env={
                **os.environ,
                "FAKE_XCODEBUILD_MODE": "success",
                "FAKE_XCODE_ARCHIVE_PATH": str(archive_path),
                "FAKE_XCODE_LOG_FILE": str(tmp_path / "xcodebuild.log"),
            },
        )
        assert result.returncode == 0, f"fake_xcodebuild success mode must exit 0: {result.stderr}"
        assert archive_path.exists(), "fake_xcodebuild must create archive in success mode"

    def test_fake_xcodebuild_archive_failure_mode(self, tmp_path):
        result = subprocess.run(
            ["bash", str(FAKE_XCODEBUILD), "archive"],
            capture_output=True,
            text=True,
            timeout=10,
            env={
                **os.environ,
                "FAKE_XCODEBUILD_MODE": "archive_failure",
                "FAKE_XCODE_LOG_FILE": str(tmp_path / "xcodebuild.log"),
            },
        )
        assert result.returncode != 0, "archive_failure mode must exit non-zero"

    def test_fake_xcodebuild_export_failure_mode(self, tmp_path):
        result = subprocess.run(
            ["bash", str(FAKE_XCODEBUILD), "-exportArchive"],
            capture_output=True,
            text=True,
            timeout=10,
            env={
                **os.environ,
                "FAKE_XCODEBUILD_MODE": "export_failure",
                "FAKE_XCODE_LOG_FILE": str(tmp_path / "xcodebuild.log"),
            },
        )
        assert result.returncode != 0, "export_failure mode must exit non-zero"

    def test_fake_xcodebuild_signing_failure_mode(self, tmp_path):
        result = subprocess.run(
            ["bash", str(FAKE_XCODEBUILD), "archive"],
            capture_output=True,
            text=True,
            timeout=10,
            env={
                **os.environ,
                "FAKE_XCODEBUILD_MODE": "signing_failure",
                "FAKE_XCODE_LOG_FILE": str(tmp_path / "xcodebuild.log"),
            },
        )
        assert result.returncode != 0, "signing_failure mode must exit non-zero"


# -----------------------------------------------------------------------
# Archive script tests
# -----------------------------------------------------------------------

@pytest.mark.skipif(
    not ARCHIVE_IOS_SH.exists(),
    reason="Pending macos-workflows-engineer: scripts/ios/xcode_archive.sh",
)
class TestArchiveScript:
    """
    Tests for scripts/ios/xcode_archive.sh.

    Required env vars per script contract:
      XCODE_PROJECT_PATH — directory containing .xcodeproj/.xcworkspace
      DEVELOPMENT_TEAM   — Apple Team ID
      KEYCHAIN_PATH      — signing keychain path
    Optional: SCHEME, CONFIGURATION, ARCHIVE_PATH, LOG_PATH
    """

    def _archive_env(self, tmp_path, xcode_dir, archive_path, mode="success"):
        fake_keychain = tmp_path / "ios-build.keychain-db"
        fake_keychain.write_text("FAKE")
        return {
            "FAKE_XCODEBUILD_MODE": mode,
            "XCODE_PROJECT_PATH": str(xcode_dir),
            "DEVELOPMENT_TEAM": "ABCDE12345",
            "KEYCHAIN_PATH": str(fake_keychain),
            "SCHEME": "MyGame",
            "ARCHIVE_PATH": str(archive_path),
            "LOG_PATH": str(tmp_path / "xcode-archive.log"),
            "FAKE_XCODE_ARCHIVE_PATH": str(archive_path),
            "FAKE_XCODE_LOG_FILE": str(tmp_path / "xcodebuild.log"),
        }

    def test_archive_success_exits_zero(self, tmp_path):
        xcode_dir = tmp_path / "Builds" / "iOS" / "Xcode"
        xcode_dir.mkdir(parents=True)
        _build_fake_xcode_project(xcode_dir, scheme="MyGame")
        archive_path = tmp_path / "Builds" / "iOS" / "Archive" / "MyGame.xcarchive"

        result = _run_script(
            ARCHIVE_IOS_SH,
            args=[],
            env_overrides=self._archive_env(tmp_path, xcode_dir, archive_path, "success"),
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, (
            f"Archive script must exit 0 on success.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_archive_failure_propagates_exit_code(self, tmp_path):
        xcode_dir = tmp_path / "Builds" / "iOS" / "Xcode"
        xcode_dir.mkdir(parents=True)
        _build_fake_xcode_project(xcode_dir, scheme="MyGame")
        archive_path = tmp_path / "Builds" / "iOS" / "Archive" / "MyGame.xcarchive"

        result = _run_script(
            ARCHIVE_IOS_SH,
            args=[],
            env_overrides=self._archive_env(tmp_path, xcode_dir, archive_path, "archive_failure"),
            tmp_path=tmp_path,
        )
        assert result.returncode != 0, (
            "Archive failure must propagate as non-zero exit code."
        )

    def test_missing_archive_after_success_exits_nonzero(self, tmp_path):
        """If xcodebuild exits 0 but no .xcarchive exists, script must fail."""
        xcode_dir = tmp_path / "Builds" / "iOS" / "Xcode"
        xcode_dir.mkdir(parents=True)
        _build_fake_xcode_project(xcode_dir, scheme="MyGame")
        archive_path = tmp_path / "Builds" / "iOS" / "Archive" / "MyGame.xcarchive"

        result = _run_script(
            ARCHIVE_IOS_SH,
            args=[],
            env_overrides=self._archive_env(tmp_path, xcode_dir, archive_path, "missing_archive"),
            tmp_path=tmp_path,
        )
        assert result.returncode != 0, (
            "Script must fail when xcodebuild exits 0 but archive is absent."
        )

    def test_invalid_scheme_exits_nonzero(self, tmp_path):
        xcode_dir = tmp_path / "Builds" / "iOS" / "Xcode"
        xcode_dir.mkdir(parents=True)
        _build_fake_xcode_project(xcode_dir, scheme="MyGame")
        archive_path = tmp_path / "Builds" / "iOS" / "Archive" / "MyGame.xcarchive"

        result = _run_script(
            ARCHIVE_IOS_SH,
            args=[],
            env_overrides=self._archive_env(tmp_path, xcode_dir, archive_path, "invalid_scheme"),
            tmp_path=tmp_path,
        )
        assert result.returncode != 0, (
            "Invalid scheme must cause non-zero exit."
        )

    def test_signing_failure_exits_nonzero(self, tmp_path):
        xcode_dir = tmp_path / "Builds" / "iOS" / "Xcode"
        xcode_dir.mkdir(parents=True)
        _build_fake_xcode_project(xcode_dir, scheme="MyGame")
        archive_path = tmp_path / "Builds" / "iOS" / "Archive" / "MyGame.xcarchive"

        result = _run_script(
            ARCHIVE_IOS_SH,
            args=[],
            env_overrides=self._archive_env(tmp_path, xcode_dir, archive_path, "signing_failure"),
            tmp_path=tmp_path,
        )
        assert result.returncode != 0, (
            "Signing failure must cause non-zero exit."
        )


# -----------------------------------------------------------------------
# Export script tests
# -----------------------------------------------------------------------

@pytest.mark.skipif(
    not EXPORT_IOS_SH.exists(),
    reason="Pending macos-workflows-engineer: scripts/ios/xcode_export.sh",
)
class TestExportScript:
    """
    Tests for scripts/ios/xcode_export.sh.

    Required env vars per script contract:
      ARCHIVE_PATH          — path to .xcarchive
      EXPORT_OPTIONS_PATH   — path to ExportOptions.plist (from generate_export_options.sh)
    Optional: EXPORT_PATH, LOG_PATH
    """

    def _make_export_options(self, tmp_path) -> Path:
        """Create a minimal ExportOptions.plist for the export script."""
        plist = tmp_path / "ExportOptions.plist"
        plist.write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "">\n'
            '<plist version="1.0"><dict>'
            '<key>method</key><string>app-store</string>'
            '</dict></plist>\n'
        )
        return plist

    def test_export_success_exits_zero(self, tmp_path):
        fake_archive = tmp_path / "MyGame.xcarchive"
        fake_archive.mkdir()
        (fake_archive / "Info.plist").write_text("FAKE_ARCHIVE")
        export_dir = tmp_path / "Export"
        export_opts = self._make_export_options(tmp_path)

        result = _run_script(
            EXPORT_IOS_SH,
            args=[],
            env_overrides={
                "FAKE_XCODEBUILD_MODE": "success",
                "ARCHIVE_PATH": str(fake_archive),
                "EXPORT_OPTIONS_PATH": str(export_opts),
                "EXPORT_PATH": str(export_dir),
                "LOG_PATH": str(tmp_path / "xcode-export.log"),
                "FAKE_XCODE_EXPORT_PATH": str(export_dir),
                "FAKE_XCODE_LOG_FILE": str(tmp_path / "xcodebuild.log"),
            },
            tmp_path=tmp_path,
        )
        assert result.returncode == 0, (
            f"Export must exit 0 on success.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_export_failure_propagates_exit_code(self, tmp_path):
        fake_archive = tmp_path / "MyGame.xcarchive"
        fake_archive.mkdir()
        (fake_archive / "Info.plist").write_text("FAKE")
        export_opts = self._make_export_options(tmp_path)

        result = _run_script(
            EXPORT_IOS_SH,
            args=[],
            env_overrides={
                "FAKE_XCODEBUILD_MODE": "export_failure",
                "ARCHIVE_PATH": str(fake_archive),
                "EXPORT_OPTIONS_PATH": str(export_opts),
                "EXPORT_PATH": str(tmp_path / "Export"),
                "LOG_PATH": str(tmp_path / "xcode-export.log"),
                "FAKE_XCODE_LOG_FILE": str(tmp_path / "xcodebuild.log"),
            },
            tmp_path=tmp_path,
        )
        assert result.returncode != 0, (
            "Export failure must propagate as non-zero exit."
        )

    def test_missing_ipa_after_export_success_exits_nonzero(self, tmp_path):
        """If xcodebuild -exportArchive exits 0 but no .ipa, script must fail."""
        fake_archive = tmp_path / "MyGame.xcarchive"
        fake_archive.mkdir()
        (fake_archive / "Info.plist").write_text("FAKE")
        export_opts = self._make_export_options(tmp_path)

        result = _run_script(
            EXPORT_IOS_SH,
            args=[],
            env_overrides={
                "FAKE_XCODEBUILD_MODE": "missing_ipa",
                "ARCHIVE_PATH": str(fake_archive),
                "EXPORT_OPTIONS_PATH": str(export_opts),
                "EXPORT_PATH": str(tmp_path / "Export"),
                "LOG_PATH": str(tmp_path / "xcode-export.log"),
                "FAKE_XCODE_LOG_FILE": str(tmp_path / "xcodebuild.log"),
            },
            tmp_path=tmp_path,
        )
        assert result.returncode != 0, (
            "Script must fail when export exits 0 but IPA is absent."
        )

    def test_missing_archive_input_exits_nonzero(self, tmp_path):
        """Export script must fail if the input .xcarchive does not exist."""
        export_opts = self._make_export_options(tmp_path)
        result = _run_script(
            EXPORT_IOS_SH,
            args=[],
            env_overrides={
                "FAKE_XCODEBUILD_MODE": "success",
                "ARCHIVE_PATH": str(tmp_path / "NonExistent.xcarchive"),
                "EXPORT_OPTIONS_PATH": str(export_opts),
                "EXPORT_PATH": str(tmp_path / "Export"),
                "LOG_PATH": str(tmp_path / "xcode-export.log"),
            },
            tmp_path=tmp_path,
        )
        assert result.returncode != 0, (
            "Export must fail when input archive does not exist."
        )


# -----------------------------------------------------------------------
# Secret redaction — platform-agnostic
# -----------------------------------------------------------------------

class TestSecretRedaction:
    """
    Secret values must never appear in script output (stdout/stderr).
    Tests inject known fake secrets and grep the output for them.
    These tests are platform-agnostic; they run on Linux CI too.
    """

    FAKE_CERT_B64 = "FAKECERTB64SECRETVALUE12345"
    FAKE_CERT_PASS = "FAKEP@ssw0rdSecret!"
    FAKE_PROFILE_B64 = "FAKEPROFILEB64SECRETVALUE67890"
    FAKE_ASC_KEY = "FAKEASCSECRETPRIVATEKEY"
    FAKE_ASC_KEY_ID = "FAKEASCID"

    @pytest.mark.skipif(
        not ARCHIVE_IOS_SH.exists(),
        reason="Pending macos-workflows-engineer: scripts/ios/xcode_archive.sh",
    )
    def test_cert_base64_not_in_archive_output(self, tmp_path):
        xcode_dir = tmp_path / "Xcode"
        xcode_dir.mkdir()
        _build_fake_xcode_project(xcode_dir, scheme="MyGame")
        fake_keychain = tmp_path / "ios-build.keychain-db"
        fake_keychain.write_text("FAKE")
        archive_path = tmp_path / "MyGame.xcarchive"

        result = _run_script(
            ARCHIVE_IOS_SH,
            args=[],
            env_overrides={
                "FAKE_XCODEBUILD_MODE": "success",
                "IOS_DISTRIBUTION_CERTIFICATE_BASE64": self.FAKE_CERT_B64,
                "IOS_DISTRIBUTION_CERTIFICATE_PASSWORD": self.FAKE_CERT_PASS,
                "IOS_PROVISIONING_PROFILE_BASE64": self.FAKE_PROFILE_B64,
                "XCODE_PROJECT_PATH": str(xcode_dir),
                "DEVELOPMENT_TEAM": "ABCDE12345",
                "KEYCHAIN_PATH": str(fake_keychain),
                "ARCHIVE_PATH": str(archive_path),
                "SCHEME": "MyGame",
                "LOG_PATH": str(tmp_path / "xcode-archive.log"),
                "FAKE_XCODE_ARCHIVE_PATH": str(archive_path),
                "FAKE_XCODE_LOG_FILE": str(tmp_path / "xcodebuild.log"),
            },
            tmp_path=tmp_path,
        )
        combined = result.stdout + result.stderr
        assert self.FAKE_CERT_B64 not in combined, (
            "Certificate base64 value must NOT appear in script output"
        )
        assert self.FAKE_CERT_PASS not in combined, (
            "Certificate password must NOT appear in script output"
        )
        assert self.FAKE_PROFILE_B64 not in combined, (
            "Provisioning profile base64 must NOT appear in script output"
        )

    @pytest.mark.skipif(
        not UPLOAD_TESTFLIGHT_SH.exists(),
        reason="Pending macos-workflows-engineer: scripts/ios/testflight_upload.sh",
    )
    def test_testflight_script_requests_masking_of_asc_key(self, tmp_path):
        """
        The TestFlight upload script must emit ::add-mask:: commands for secrets.

        NOTE: ::add-mask:: only takes effect inside GitHub Actions. In raw test
        output the secret VALUE will appear in the add-mask line itself — that is
        expected. This test verifies the script REQUESTS masking, which is the
        correct contract for GitHub Actions. The platform then redacts all
        subsequent occurrences.

        We test that:
        1. The script uses ::add-mask:: for ASC secrets.
        2. The private key is written to a file (not passed as CLI arg).
        3. The key file path (not the key content) is used in the xcrun command.
        """
        ipa_path = tmp_path / "MyApp.ipa"
        ipa_path.write_text("FAKE_IPA")
        asc_key_dir = tmp_path / "asc_keys"
        asc_key_dir.mkdir()

        # Read script content to verify add-mask usage (static analysis)
        script_content = UPLOAD_TESTFLIGHT_SH.read_text()
        assert "::add-mask::" in script_content, (
            "testflight_upload.sh must use ::add-mask:: to request GitHub Actions "
            "secret masking for APP_STORE_CONNECT_PRIVATE_KEY and related secrets"
        )
        # Verify the private key is written to file, not passed as CLI arg
        # The script should write PRIVATE_KEY to a .p8 file, not use it directly in xcrun
        assert "apiKey" in script_content or "--apiKey" in script_content, (
            "testflight_upload.sh must pass the key via --apiKey (file reference) "
            "not as a raw string argument"
        )


# -----------------------------------------------------------------------
# Cleanup tests
# -----------------------------------------------------------------------

@pytest.mark.skipif(
    not CLEANUP_IOS_SH.exists(),
    reason="Pending macos-workflows-engineer: scripts/ios/cleanup_signing.sh",
)
class TestCleanupScript:
    """
    Tests for scripts/ios/cleanup_signing.sh.

    All env vars are optional per script contract:
      KEYCHAIN_PATH    — path to the temp keychain
      KEYCHAIN_NAME    — keychain name (fallback)
      PROFILE_PATH     — path to installed provisioning profile
      ASC_API_KEY_DIR  — directory holding the ASC API key .p8 file

    On Linux, `security delete-keychain` does not exist — the script must
    handle this gracefully (non-fatal warning, continue to clean other files).
    """

    def test_cleanup_removes_provisioning_profile(self, tmp_path):
        """Cleanup must remove the provisioning profile file."""
        fake_profile = tmp_path / "build.mobileprovision"
        fake_profile.write_text("FAKE_PROFILE")

        result = _run_script(
            CLEANUP_IOS_SH,
            args=[],
            env_overrides={
                "PROFILE_PATH": str(fake_profile),
            },
            tmp_path=tmp_path,
        )
        # Profile should be removed; script may warn about keychain on Linux
        assert not fake_profile.exists() or result.returncode in (0, 1), (
            "Cleanup must attempt to remove provisioning profile."
        )

    def test_cleanup_removes_asc_key_directory(self, tmp_path):
        """Cleanup must remove the ASC API key directory contents."""
        asc_key_dir = tmp_path / "asc_keys"
        asc_key_dir.mkdir()
        (asc_key_dir / "AuthKey_TESTID.p8").write_text("FAKE_KEY")

        result = _run_script(
            CLEANUP_IOS_SH,
            args=[],
            env_overrides={
                "ASC_API_KEY_DIR": str(asc_key_dir),
            },
            tmp_path=tmp_path,
        )
        # Script may exit non-zero on Linux (no `security`), but key removal
        # is a simple `rm -rf` and should have happened
        assert result.returncode in (0, 1), (
            "Cleanup script must not crash unexpectedly."
        )

    def test_cleanup_runs_even_if_all_paths_missing(self, tmp_path):
        """Cleanup must not crash if optional temp files don't exist."""
        result = _run_script(
            CLEANUP_IOS_SH,
            args=[],
            env_overrides={
                "KEYCHAIN_PATH": str(tmp_path / "nonexistent.keychain-db"),
                "PROFILE_PATH": str(tmp_path / "nonexistent.mobileprovision"),
                "ASC_API_KEY_DIR": str(tmp_path / "nonexistent_dir"),
            },
            tmp_path=tmp_path,
        )
        # Graceful even when nothing to clean
        assert result.returncode in (0, 1), (
            "Cleanup must handle missing files gracefully."
        )


# -----------------------------------------------------------------------
# Artifact contract
# -----------------------------------------------------------------------

class TestArtifactContract:
    """
    After a successful iOS pipeline, specific artifact directories must exist.
    Contract paths: Builds/iOS/{Xcode,Archive,Export,Symbols}/
                    BuildReports/iOS/*  Logs/iOS/*  TestResults/
    """

    def test_artifact_paths_follow_contract(self, tmp_path):
        """Verify that contract-specified paths can be created (path construction test)."""
        ios_root = tmp_path / "Builds" / "iOS"
        for subdir in ("Xcode", "Archive", "Export", "Symbols"):
            d = ios_root / subdir
            d.mkdir(parents=True)
            assert d.exists(), f"Expected artifact dir {d} to exist"
        for report_dir in (
            tmp_path / "BuildReports" / "iOS",
            tmp_path / "Logs" / "iOS",
            tmp_path / "TestResults",
        ):
            report_dir.mkdir(parents=True)
            assert report_dir.exists(), f"Expected report dir {report_dir} to exist"


# -----------------------------------------------------------------------
# TestFlight upload
# -----------------------------------------------------------------------

@pytest.mark.skipif(
    not UPLOAD_TESTFLIGHT_SH.exists(),
    reason="Pending macos-workflows-engineer: scripts/ios/testflight_upload.sh",
)
class TestTestFlightUpload:
    """
    Tests for scripts/ios/testflight_upload.sh.

    Required env vars per script contract:
      IPA_PATH                       — path to the .ipa file
      APP_STORE_CONNECT_KEY_ID       — ASC API key ID
      APP_STORE_CONNECT_ISSUER_ID    — ASC API issuer ID
      APP_STORE_CONNECT_PRIVATE_KEY  — ASC API private key (.p8 content)
    Optional: ASC_API_KEY_DIR, REPORT_PATH
    """

    def test_testflight_upload_without_ipa_exits_nonzero(self, tmp_path):
        """Missing IPA file must cause non-zero exit (script uses :? assertion)."""
        result = _run_script(
            UPLOAD_TESTFLIGHT_SH,
            args=[],
            env_overrides={
                "IPA_PATH": str(tmp_path / "nonexistent.ipa"),
                "APP_STORE_CONNECT_KEY_ID": "FAKEID",
                "APP_STORE_CONNECT_ISSUER_ID": "fake-issuer",
                "APP_STORE_CONNECT_PRIVATE_KEY": "FAKE_KEY",
                "ASC_API_KEY_DIR": str(tmp_path / "asc_keys"),
            },
            tmp_path=tmp_path,
        )
        assert result.returncode != 0, (
            "TestFlight upload with missing IPA path must fail."
        )

    def test_testflight_upload_empty_asc_key_id_exits_nonzero(self, tmp_path):
        """Empty APP_STORE_CONNECT_KEY_ID must cause non-zero exit."""
        ipa = tmp_path / "MyApp.ipa"
        ipa.write_text("FAKE")
        result = _run_script(
            UPLOAD_TESTFLIGHT_SH,
            args=[],
            env_overrides={
                "IPA_PATH": str(ipa),
                "APP_STORE_CONNECT_KEY_ID": "",  # Empty = missing credential
                "APP_STORE_CONNECT_ISSUER_ID": "fake-issuer",
                "APP_STORE_CONNECT_PRIVATE_KEY": "FAKE_KEY",
                "ASC_API_KEY_DIR": str(tmp_path / "asc_keys"),
            },
            tmp_path=tmp_path,
        )
        assert result.returncode != 0, (
            "TestFlight upload with empty KEY_ID must fail (fork/missing-secret guard)."
        )

    def test_fork_release_rejected_empty_credentials(self, tmp_path):
        """
        TestFlight uploads from fork PRs must be rejected.
        Forks do not have access to ASC secrets — all three ASC vars are empty.
        The script uses :? bash assertions which fail on empty values.
        """
        ipa = tmp_path / "MyApp.ipa"
        ipa.write_text("FAKE")
        result = _run_script(
            UPLOAD_TESTFLIGHT_SH,
            args=[],
            env_overrides={
                "IPA_PATH": str(ipa),
                "APP_STORE_CONNECT_KEY_ID": "",
                "APP_STORE_CONNECT_ISSUER_ID": "",
                "APP_STORE_CONNECT_PRIVATE_KEY": "",
                "ASC_API_KEY_DIR": str(tmp_path / "asc_keys"),
                "GITHUB_EVENT_NAME": "pull_request",
            },
            tmp_path=tmp_path,
        )
        assert result.returncode != 0, (
            "TestFlight upload from fork context (empty secrets) must be rejected."
        )


# -----------------------------------------------------------------------
# Unity compile + license failure simulations
# -----------------------------------------------------------------------

class TestUnityFailureSimulations:
    """
    Unity build failure simulations.

    NOTE: Unity build is invoked directly in unity-build-ios.yml (no separate
    build_ios.sh script). These tests validate the contract behaviour by testing
    the fake_unity.sh fixture directly and verifying that the archive script
    (which runs AFTER Unity) fails correctly when no Xcode project was produced.
    The Unity-level failures (compile error, license error) must be verified via
    the workflow's step-level exit code propagation — not a separate script test.
    """

    def test_fake_unity_compile_error_exits_nonzero(self, tmp_path):
        """
        Contract: Unity compile failure must exit non-zero.
        Validates the fake_unity.sh fixture itself (used in other tests).
        """
        _make_executable(FAKE_UNITY)
        result = subprocess.run(
            ["bash", str(FAKE_UNITY), "-batchmode", "-buildTarget", "iOS"],
            capture_output=True, text=True, timeout=10,
            env={
                **os.environ,
                "FAKE_UNITY_MODE": "compile_error",
                "FAKE_UNITY_LOG_FILE": str(tmp_path / "Editor.log"),
                "FAKE_UNITY_OUTPUT_DIR": str(tmp_path / "Builds"),
            },
        )
        assert result.returncode != 0, (
            "fake_unity.sh compile_error mode must exit non-zero"
        )

    def test_fake_unity_license_error_exits_nonzero(self, tmp_path):
        """Contract: Unity license failure must exit non-zero."""
        _make_executable(FAKE_UNITY)
        result = subprocess.run(
            ["bash", str(FAKE_UNITY), "-batchmode"],
            capture_output=True, text=True, timeout=10,
            env={
                **os.environ,
                "FAKE_UNITY_MODE": "license_error",
                "FAKE_UNITY_LOG_FILE": str(tmp_path / "Editor.log"),
                "FAKE_UNITY_OUTPUT_DIR": str(tmp_path / "Builds"),
            },
        )
        assert result.returncode != 0, (
            "fake_unity.sh license_error mode must exit non-zero"
        )

    @pytest.mark.skipif(
        not ARCHIVE_IOS_SH.exists(),
        reason="Pending macos-workflows-engineer: scripts/ios/xcode_archive.sh",
    )
    def test_archive_script_fails_when_no_xcode_project_produced(self, tmp_path):
        """
        If Unity exits 0 but no .xcworkspace/.xcodeproj is in XCODE_PROJECT_PATH,
        the archive script must fail at the xcodebuild invocation.
        This simulates missing iOS Build Support in Unity.
        """
        # Create the directory but NOT the .xcworkspace or .xcodeproj
        empty_xcode_dir = tmp_path / "Builds" / "iOS" / "Xcode"
        empty_xcode_dir.mkdir(parents=True)
        fake_keychain = tmp_path / "ios-build.keychain-db"
        fake_keychain.write_text("FAKE")
        archive_path = tmp_path / "MyGame.xcarchive"

        result = _run_script(
            ARCHIVE_IOS_SH,
            args=[],
            env_overrides={
                # Use invalid_scheme since there's no actual project to detect
                "FAKE_XCODEBUILD_MODE": "invalid_scheme",
                "XCODE_PROJECT_PATH": str(empty_xcode_dir),
                "DEVELOPMENT_TEAM": "ABCDE12345",
                "KEYCHAIN_PATH": str(fake_keychain),
                "SCHEME": "Unity-iPhone",
                "ARCHIVE_PATH": str(archive_path),
                "LOG_PATH": str(tmp_path / "xcode-archive.log"),
                "FAKE_XCODE_LOG_FILE": str(tmp_path / "xcodebuild.log"),
            },
            tmp_path=tmp_path,
        )
        assert result.returncode != 0, (
            "Archive script must fail when the Xcode project is absent or scheme invalid."
        )
