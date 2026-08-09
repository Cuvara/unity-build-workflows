"""
CRITICAL regression test — prevents native Unity invocations from sneaking back
into the repository after the Docker-mandatory migration.

Scans the ENTIRE repository for patterns that indicate direct (non-Docker) Unity
Editor invocations. The ONLY permitted location is docker/unity/entrypoint.sh.

If ANY native invocation is found outside that file, this test FAILS.

Patterns detected:
  - Unity -batchmode  /  unity-editor -batchmode
  - Unity.exe -batchmode
  - Unity.app/Contents/MacOS/Unity
  - -executeMethod  (outside the approved entrypoint)
  - game-ci/unity-builder
  - game-ci/unity-test-runner

This test is the last line of defence against regression.
"""
import re
from pathlib import Path
from typing import Iterator

import pytest

REPO_ROOT = Path(__file__).parent.parent

# Files approved to contain direct (non-Docker) Unity invocations.
# docker/unity/entrypoint.sh                — Docker container entrypoint (Docker lane)
# .github/workflows/unity-build-ios.yml     — iOS pipeline (native Unity on macOS)
# .github/workflows/unity-test-ios.yml      — iOS test runner (native Unity on macOS)
# .github/workflows/unity-release-ios.yml   — iOS release pipeline (tag-triggered)
# scripts/ios/run_unity_ios.sh              — Unity batch-mode caller (macOS only, called by workflows)
# .github/actions/build-ios/action.yml      — iOS composite action (native Unity on macOS, added T7)
# The iOS files are an approved exception: Xcode requires native Unity on macOS.
# All other files must use the Docker executor.
ALLOWED_PATH = "docker/unity/entrypoint.sh"  # kept for test backward-compat
ALLOWED_PATHS = frozenset({
    "docker/unity/entrypoint.sh",
    "docker/unity/activate-license.sh",                # License activation (runs inside Docker)
    "scripts/common/resolve_activation_strategy.sh",   # Strategy resolver (references Unity paths for detection)
    ".github/workflows/unity-build-ios.yml",
    ".github/workflows/unity-test-ios.yml",
    ".github/workflows/unity-release-ios.yml",  # iOS release pipeline (tag-triggered)
    "scripts/ios/run_unity_ios.sh",             # iOS Unity batch-mode invocation (macOS only)
    ".github/actions/build-ios/action.yml",     # iOS composite action — native Unity on macOS (T7)
    # GameCI-delegation production path: Unity Personal/free Docker activation
    # is performed by game-ci/unity-builder (the supported, working path). This
    # workflow intentionally uses the game-ci action.
    ".github/workflows/unity-build-gameci.yml",
    # Image build smoke-tests the editor inside the freshly built image
    # (unity-editor -batchmode -buildTarget X -version). This verifies the
    # image, it is not a project build invocation.
    ".github/workflows/build-unity-image.yml",
    # License generation runs unity-editor -batchmode inside Docker to
    # produce a .ulf activation file. Not a project build invocation.
    ".github/workflows/unity-generate-license.yml",
    # Explicit-platform-jobs reusable workflows: docker lane uses game-ci
    # (approved Personal/free path); self-hosted lanes invoke the local Unity
    # editor in batchmode by design.
    ".github/workflows/reusable-build-platform.yml",
    ".github/workflows/reusable-unity-tests.yml",
})

# ---------------------------------------------------------------------------
# Patterns that indicate a direct (non-Docker) Unity invocation
# ---------------------------------------------------------------------------

# Each entry: (human_name, compiled_regex)
NATIVE_PATTERNS = [
    (
        "Unity -batchmode (generic)",
        re.compile(r"\bUnity\b.*-batchmode", re.IGNORECASE),
    ),
    (
        "unity-editor -batchmode",
        re.compile(r"unity-editor\s+-batchmode", re.IGNORECASE),
    ),
    (
        "Unity.exe -batchmode",
        re.compile(r"Unity\.exe\s+-batchmode", re.IGNORECASE),
    ),
    (
        "Unity.app/Contents/MacOS/Unity",
        re.compile(r"Unity\.app/Contents/MacOS/Unity", re.IGNORECASE),
    ),
    (
        "-executeMethod (direct invocation outside entrypoint)",
        re.compile(r"-executeMethod\b"),
    ),
    (
        "game-ci/unity-builder action",
        re.compile(r"game-ci/unity-builder"),
    ),
    (
        "game-ci/unity-test-runner action",
        re.compile(r"game-ci/unity-test-runner"),
    ),
]

# ---------------------------------------------------------------------------
# File glob patterns to scan
# ---------------------------------------------------------------------------

SCAN_GLOBS = [
    ".github/workflows/*.yml",
    ".github/workflows/*.yaml",
    ".github/actions/**/*.yml",
    ".github/actions/**/*.yaml",
    "scripts/**/*.py",
    "scripts/**/*.sh",
    "templates/**/*.yml",
    "templates/**/*.yaml",
]

# Glob patterns to EXCLUDE from scanning (compiled for speed)
EXCLUDE_PATTERNS = [
    re.compile(r"__pycache__"),
    re.compile(r"\.pyc$"),
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_excluded(path: Path) -> bool:
    path_str = str(path)
    return any(p.search(path_str) for p in EXCLUDE_PATTERNS)


def _is_allowed(path: Path) -> bool:
    """
    Return True if this file is an approved location for Unity invocations.

    Approved locations:
    - docker/unity/entrypoint.sh              — Docker container entrypoint
    - .github/workflows/unity-build-ios.yml   — iOS pipeline (native macOS/Xcode)
    - .github/workflows/unity-test-ios.yml    — iOS test runner (native macOS/Xcode)
    - .github/workflows/unity-release-ios.yml — iOS release pipeline
    - scripts/ios/run_unity_ios.sh            — Unity batch-mode caller (macOS only)

    iOS files are approved exceptions to the Docker-mandatory rule.
    iOS builds require native Unity on macOS because Xcode only runs on macOS.
    """
    try:
        rel = path.relative_to(REPO_ROOT)
    except ValueError:
        return False
    return str(rel).replace("\\", "/") in ALLOWED_PATHS


def _scan_file(path: Path) -> list[tuple[int, str, str]]:
    """
    Scan a file for native Unity invocation patterns.

    Returns a list of (line_number, pattern_name, line_content) for each hit.
    Skips the allowed entrypoint file.
    """
    if _is_excluded(path) or _is_allowed(path):
        return []

    hits = []
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError):
        return []

    for lineno, line in enumerate(content.splitlines(), start=1):
        for pattern_name, pattern in NATIVE_PATTERNS:
            if pattern.search(line):
                hits.append((lineno, pattern_name, line.strip()))
    return hits


def _iter_scan_files() -> Iterator[Path]:
    """Yield all files that should be scanned."""
    for glob_pattern in SCAN_GLOBS:
        yield from REPO_ROOT.glob(glob_pattern)


# ---------------------------------------------------------------------------
# Parameterized per-file test
# ---------------------------------------------------------------------------

def _collect_violations() -> list[tuple[Path, int, str, str]]:
    """Collect all violations across the repo: (path, lineno, pattern, line)."""
    violations = []
    for path in _iter_scan_files():
        for lineno, pattern_name, line in _scan_file(path):
            violations.append((path, lineno, pattern_name, line))
    return violations


class TestNoNativeUnityInvocation:
    """
    Scans .github/workflows, .github/actions, scripts, and templates for
    native Unity invocations. Only docker/unity/entrypoint.sh may contain them.
    """

    def test_no_batchmode_in_workflows(self):
        """No workflow YAML should invoke Unity with -batchmode directly."""
        violations = []
        for path in REPO_ROOT.glob(".github/workflows/*.yml"):
            for lineno, pattern_name, line in _scan_file(path):
                if "-batchmode" in pattern_name.lower() or "unity.app" in pattern_name.lower():
                    violations.append((path, lineno, pattern_name, line))

        if violations:
            msg = _format_violation_message(violations)
            pytest.fail(
                f"Native Unity invocations found in workflows — Docker is mandatory:\n{msg}"
            )

    def test_no_batchmode_in_actions(self):
        """No composite action should invoke Unity with -batchmode directly."""
        violations = []
        for path in REPO_ROOT.glob(".github/actions/**/*.yml"):
            for lineno, pattern_name, line in _scan_file(path):
                if "-batchmode" in pattern_name.lower() or "Unity.app" in line:
                    violations.append((path, lineno, pattern_name, line))

        if violations:
            msg = _format_violation_message(violations)
            pytest.fail(
                f"Native Unity invocations found in composite actions — Docker is mandatory:\n{msg}"
            )

    def test_no_execute_method_in_python_scripts(self):
        """Python scripts must not invoke Unity's -executeMethod directly."""
        violations = []
        pattern_name = "-executeMethod (direct invocation outside entrypoint)"
        pattern = re.compile(r"-executeMethod\b")
        for path in REPO_ROOT.glob("scripts/**/*.py"):
            if _is_excluded(path):
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(content.splitlines(), start=1):
                if pattern.search(line):
                    violations.append((path, lineno, pattern_name, line.strip()))

        if violations:
            msg = _format_violation_message(violations)
            pytest.fail(
                f"-executeMethod found in Python scripts outside entrypoint:\n{msg}"
            )

    def test_no_game_ci_actions_in_workflows(self):
        """No workflow should use game-ci actions EXCEPT approved (ALLOWED_PATHS).

        The toolkit delegates Unity Personal/free Docker activation to
        game-ci/unity-builder in the approved unity-build-gameci.yml workflow;
        any other use of a game-ci action is still a violation.
        """
        violations = []
        builder_pat = re.compile(r"game-ci/unity-builder")
        runner_pat = re.compile(r"game-ci/unity-test-runner")

        for glob_pat in (".github/workflows/*.yml", ".github/actions/**/*.yml"):
            for path in REPO_ROOT.glob(glob_pat):
                if _is_allowed(path):
                    continue
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for lineno, line in enumerate(content.splitlines(), start=1):
                    if builder_pat.search(line):
                        violations.append((path, lineno, "game-ci/unity-builder", line.strip()))
                    if runner_pat.search(line):
                        violations.append((path, lineno, "game-ci/unity-test-runner", line.strip()))

        if violations:
            msg = _format_violation_message(violations)
            pytest.fail(
                f"game-ci actions found — Docker-native runner is mandatory:\n{msg}"
            )

    def test_no_native_invocations_in_shell_scripts(self):
        """Shell scripts (outside entrypoint.sh) must not invoke Unity directly."""
        violations = []
        for path in REPO_ROOT.glob("scripts/**/*.sh"):
            for lineno, pattern_name, line in _scan_file(path):
                violations.append((path, lineno, pattern_name, line))

        if violations:
            msg = _format_violation_message(violations)
            pytest.fail(
                f"Native Unity invocations found in shell scripts:\n{msg}"
            )

    def test_no_native_invocations_in_templates(self):
        """Template YAML files must not invoke Unity directly."""
        violations = []
        for glob_pat in ("templates/**/*.yml", "templates/**/*.yaml"):
            for path in REPO_ROOT.glob(glob_pat):
                for lineno, pattern_name, line in _scan_file(path):
                    violations.append((path, lineno, pattern_name, line))

        if violations:
            msg = _format_violation_message(violations)
            pytest.fail(
                f"Native Unity invocations found in templates:\n{msg}"
            )

    def test_full_repo_scan_no_violations(self):
        """
        Comprehensive scan: no native Unity invocations anywhere except entrypoint.sh.

        This is the definitive regression test. All other tests above are scoped;
        this one covers everything.
        """
        all_violations = _collect_violations()
        if all_violations:
            msg = _format_violation_message(all_violations)
            allowed_list = ", ".join(sorted(ALLOWED_PATHS))
            pytest.fail(
                f"REGRESSION: Native Unity invocations found outside approved paths.\n"
                f"Approved: {allowed_list}\n"
                f"iOS workflows (unity-build-ios.yml, unity-test-ios.yml) are approved "
                f"exceptions — all other files must use the Docker executor.\n\n"
                f"{msg}"
            )

    def test_allowed_path_constant_is_correct(self):
        """Sanity check: ALLOWED_PATHS contains the expected approved files."""
        assert "docker/unity/entrypoint.sh" in ALLOWED_PATHS, \
            "docker/unity/entrypoint.sh must be in ALLOWED_PATHS"
        assert ".github/workflows/unity-build-ios.yml" in ALLOWED_PATHS, \
            "unity-build-ios.yml must be in ALLOWED_PATHS (approved iOS exception)"
        for path in ALLOWED_PATHS:
            assert "/" in path, f"All paths must use forward slashes: {path}"

    def test_entrypoint_is_in_allowed_paths(self):
        """Document the contract: entrypoint.sh must remain in the approved allowlist."""
        assert "docker/unity/entrypoint.sh" in ALLOWED_PATHS

    def test_ios_workflow_is_approved_exception(self):
        """
        iOS workflows and the run_unity_ios.sh shell script are approved exceptions
        to the Docker-mandatory architecture. iOS builds require native Unity on
        macOS because Xcode only runs on macOS.
        All three iOS workflows and the iOS Unity runner script must be in ALLOWED_PATHS.
        """
        ios_approved = [
            ".github/workflows/unity-build-ios.yml",
            ".github/workflows/unity-test-ios.yml",
            ".github/workflows/unity-release-ios.yml",
            "scripts/ios/run_unity_ios.sh",
        ]
        for path in ios_approved:
            assert path in ALLOWED_PATHS, (
                f"{path} must be an approved exception. "
                "iOS native Unity execution on macOS is required for Xcode integration."
            )


# ---------------------------------------------------------------------------
# Formatting helper
# ---------------------------------------------------------------------------

def _format_violation_message(violations: list) -> str:
    lines = []
    for path, lineno, pattern_name, line_content in violations:
        try:
            rel = path.relative_to(REPO_ROOT)
        except ValueError:
            rel = path
        lines.append(f"  {rel}:{lineno}  [{pattern_name}]")
        lines.append(f"    {line_content}")
    return "\n".join(lines)
