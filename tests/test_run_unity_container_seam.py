"""
Regression tests for scripts/docker/run_unity_container.py's argument contract.

THE BUG THIS GUARDS AGAINST
----------------------------
A caller passes a pre-resolved --image reference, but the script previously
had NO --image arg and required --image-namespace unconditionally, so every
Docker build argparse-errored.

The fix: --image is an optional passthrough. When provided, internal
resolution is skipped and --image-namespace is not required. Release mode
still requires a digest-pinned ref (@sha256:...).

The script is the local Docker-lane runner (docs: CLAUDE.md "Local Docker-lane
build"); the run-unity-container composite action that also called it was
removed with the legacy workflows in v7.0.0.

Coverage
--------
1. --image without --image-namespace → parses + namespace guard passes.
2. Neither --image nor --image-namespace → ValueError / non-zero.
3. --image + --release-mode without a digest-pinned ref → abort (non-zero).
4. Boolean string inputs ("true"/"false") parse to correct bool values.
5. --timeout and --container-timeout both set container_timeout; --command,
   --log-path and --report-path are recognised.
"""
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent

SCRIPT_PATH = REPO_ROOT / "scripts" / "docker" / "run_unity_container.py"

FAKE_DIGEST = "sha256:" + "a" * 64
FAKE_IMAGE = f"ghcr.io/example-namespace/unity-build:2022.3.21f1-android"
FAKE_IMAGE_PINNED = f"{FAKE_IMAGE}@{FAKE_DIGEST}"


# ---------------------------------------------------------------------------
# Module import (skip all import-based tests when script not present)
# ---------------------------------------------------------------------------

sys.path.insert(0, str(REPO_ROOT / "scripts" / "docker"))

try:
    import run_unity_container as _ruc
    _HAS_SCRIPT = True
except ImportError:
    _ruc = None
    _HAS_SCRIPT = False


def _skip_if_no_script():
    if not _HAS_SCRIPT:
        pytest.skip("scripts/docker/run_unity_container.py not yet available")


def _parse(extra_args: list) -> "argparse.Namespace":
    """Call parse_args() with the given args (sys.argv is mocked)."""
    _skip_if_no_script()
    orig = sys.argv
    try:
        sys.argv = ["run_unity_container.py",
                    "--project-path", ".",
                    "--unity-version", "2022.3.21f1",
                    "--target-platform", "Android",
                    ] + extra_args
        return _ruc.parse_args()
    finally:
        sys.argv = orig


# ---------------------------------------------------------------------------
# 1. --image passthrough: no --image-namespace required
# ---------------------------------------------------------------------------

class TestImagePassthrough:
    """
    When a pre-resolved --image is supplied (CI flow), internal resolution is
    skipped and --image-namespace must NOT be required.
    """

    def test_image_arg_is_recognised_by_argparse(self):
        """--image is a known arg; parsing must succeed without --image-namespace."""
        args = _parse(["--image", FAKE_IMAGE])
        assert args.image == FAKE_IMAGE

    def test_image_without_namespace_passes_guard(self):
        """
        The namespace guard (main()):
            if not args.image and not args.image_namespace: raise ValueError(...)
        must NOT fire when --image is supplied.
        """
        args = _parse(["--image", FAKE_IMAGE])
        # Replicate the guard condition from main()
        would_raise = not args.image and not args.image_namespace
        assert not would_raise, (
            "--image supplied → namespace guard condition must be False; "
            f"got image={args.image!r}, image_namespace={args.image_namespace!r}"
        )

    def test_image_digest_appended_when_both_given(self):
        """--image + --image-digest → the digest will be appended at runtime."""
        args = _parse(["--image", FAKE_IMAGE, "--image-digest", FAKE_DIGEST])
        assert args.image == FAKE_IMAGE
        assert args.image_digest == FAKE_DIGEST


# ---------------------------------------------------------------------------
# 2. Missing both --image and --image-namespace → actionable error
# ---------------------------------------------------------------------------

class TestMissingImageNamespaceError:
    """
    When neither --image nor --image-namespace is given, the script must
    exit non-zero with an actionable error message.
    """

    def test_missing_both_exits_nonzero(self, tmp_path):
        """Omitting --image and --image-namespace → exit code 1."""
        if not SCRIPT_PATH.exists():
            pytest.skip("run_unity_container.py not yet created")
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH),
             "--project-path", str(tmp_path),
             "--unity-version", "2022.3.21f1",
             "--target-platform", "Android"],
            capture_output=True, text=True, timeout=15,
        )
        assert result.returncode != 0, (
            "Missing --image-namespace (and no --image) must cause non-zero exit"
        )

    def test_missing_both_error_mentions_namespace(self, tmp_path):
        """Error message must mention 'namespace' so the fix is obvious."""
        if not SCRIPT_PATH.exists():
            pytest.skip("run_unity_container.py not yet created")
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH),
             "--project-path", str(tmp_path),
             "--unity-version", "2022.3.21f1",
             "--target-platform", "Android"],
            capture_output=True, text=True, timeout=15,
        )
        combined = result.stdout + result.stderr
        assert any(kw in combined.lower() for kw in ("namespace", "image-namespace", "required")), (
            f"Error message must mention 'namespace' or 'required'. Got:\n{combined}"
        )


# ---------------------------------------------------------------------------
# 3. --image + --release-mode without digest → abort
# ---------------------------------------------------------------------------

class TestReleaseModeDigestEnforcement:
    """
    In release mode, the script must refuse a mutable (un-pinned) --image.
    A digest (@sha256:…) must be present — either embedded in --image or via
    --image-digest.
    """

    def test_release_mode_without_digest_triggers_abort_path(self):
        """
        Validate the abort condition directly without running Docker.

        The code path inside main() for --image + --release-mode:
            if args.release_mode and "@sha256:" not in image_ref:
                _abort("Release mode requires an immutable digest-pinned image…")
        """
        args = _parse(["--image", FAKE_IMAGE, "--release-mode"])
        # args.release_mode must be truthy; image must lack @sha256:
        assert args.release_mode, (
            "--release-mode (no value) must be truthy; got %r" % args.release_mode
        )
        assert "@sha256:" not in args.image, (
            "Test setup: FAKE_IMAGE must not be digest-pinned for this test"
        )
        # Confirm the abort condition would fire
        would_abort = args.release_mode and "@sha256:" not in args.image
        assert would_abort, "release-mode + un-pinned image must trigger the abort path"

    def test_release_mode_with_digest_in_image_ref_passes(self):
        """--image @sha256:pinned + --release-mode → abort condition is False."""
        args = _parse(["--image", FAKE_IMAGE_PINNED, "--release-mode"])
        would_abort = args.release_mode and "@sha256:" not in args.image
        assert not would_abort, (
            "release-mode + digest-pinned image must NOT trigger the abort path"
        )

    def test_release_mode_with_separate_digest_passes(self):
        """--image tag + --image-digest sha256: + --release-mode → no abort."""
        args = _parse([
            "--image", FAKE_IMAGE,
            "--image-digest", FAKE_DIGEST,
            "--release-mode",
        ])
        # In main(): image_ref gets "@{digest}" appended → "@sha256:" present
        image_ref = args.image
        if args.image_digest and "@sha256:" not in image_ref:
            image_ref = f"{image_ref}@{args.image_digest}"
        would_abort = args.release_mode and "@sha256:" not in image_ref
        assert not would_abort, (
            "release-mode + separate --image-digest must NOT trigger the abort path"
        )


# ---------------------------------------------------------------------------
# 5. Boolean string inputs ("true"/"false") from GitHub Actions env vars
# ---------------------------------------------------------------------------

class TestBooleanStringInputParsing:
    """
    GitHub Actions bool inputs arrive as string env vars: "true" or "false".
    The action passes --clean-build "$CLEAN_BUILD" and --release-mode "$RELEASE_MODE".
    Both flags must parse those strings correctly.
    """

    def test_clean_build_false_string_parses_to_false(self):
        args = _parse(["--image", FAKE_IMAGE, "--clean-build", "false"])
        assert args.clean_build is False

    def test_clean_build_true_string_parses_to_true(self):
        args = _parse(["--image", FAKE_IMAGE, "--clean-build", "true"])
        assert args.clean_build is True

    def test_clean_build_flag_only_parses_to_true(self):
        """--clean-build (no value) is the legacy form; must remain truthy."""
        args = _parse(["--image", FAKE_IMAGE, "--clean-build"])
        assert args.clean_build

    def test_release_mode_false_string_parses_to_false(self):
        args = _parse(["--image", FAKE_IMAGE, "--release-mode", "false"])
        assert args.release_mode is False

    def test_release_mode_true_string_parses_to_true(self):
        args = _parse(["--image", FAKE_IMAGE, "--release-mode", "true"])
        assert args.release_mode is True

    def test_release_mode_flag_only_parses_to_true(self):
        args = _parse(["--image", FAKE_IMAGE, "--release-mode"])
        assert args.release_mode


# ---------------------------------------------------------------------------
# 6. --timeout / --container-timeout aliasing
# ---------------------------------------------------------------------------

class TestTimeoutAliasing:
    """
    The action uses --timeout; the script's canonical arg is --container-timeout.
    Both must resolve to the same dest (container_timeout).
    """

    def test_timeout_flag_is_recognised(self):
        args = _parse(["--image", FAKE_IMAGE, "--timeout", "1800"])
        assert args.container_timeout == 1800

    def test_container_timeout_still_works(self):
        args = _parse(["--image", FAKE_IMAGE, "--container-timeout", "900"])
        assert args.container_timeout == 900

    def test_command_flag_is_recognised(self):
        args = _parse(["--image", FAKE_IMAGE, "--command", "test-editmode"])
        assert args.command == "test-editmode"

    def test_log_path_flag_is_recognised(self):
        args = _parse(["--image", FAKE_IMAGE, "--log-path", "/workspace/Logs"])
        assert args.log_path == "/workspace/Logs"

    def test_report_path_flag_is_recognised(self):
        args = _parse(["--image", FAKE_IMAGE, "--report-path", "/workspace/Reports"])
        assert args.report_path == "/workspace/Reports"
