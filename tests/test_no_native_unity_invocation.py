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
# scripts/ios/run_unity_ios.sh              — Unity batch-mode caller (macOS only)
# The native lanes (reusable-build-platform.yml, reusable-unity-tests.yml and
# the scripts they call) are approved by design; each entry says why.
ALLOWED_PATH = "docker/unity/entrypoint.sh"  # kept for test backward-compat
ALLOWED_PATHS = frozenset({
    "docker/unity/entrypoint.sh",
    "docker/unity/activate-license.sh",                # License activation (runs inside Docker)
    "scripts/common/resolve_activation_strategy.sh",   # Strategy resolver (references Unity paths for detection)
    # Maps UNITY_EDITOR_ROOT_{WINDOWS,MACOS} onto preflight's inputs. It tests
    # whether <folder>/Unity.app/Contents/MacOS/Unity exists to tell one editor
    # from an editors root; it never runs the editor.
    "scripts/common/unity_editor_root.sh",
    "scripts/ios/run_unity_ios.sh",             # iOS Unity batch-mode invocation (macOS only)
    # Image build smoke-tests the editor inside the freshly built image
    # (unity-editor -batchmode -buildTarget X -version). This verifies the
    # image, it is not a project build invocation.
    ".github/workflows/build-unity-image.yml",
    # License generation runs unity-editor -batchmode inside Docker to
    # produce a .ulf activation file. Not a project build invocation.
    ".github/workflows/unity-generate-license.yml",
    # The invariant checker DENIES these operations in promotion workflows, so
    # it necessarily contains their names as a denylist. Matching on the string
    # here is a false positive: this file forbids Unity invocations, it does
    # not perform one.
    "scripts/common/validate_pipeline_invariants.py",
    # Explicit-platform-jobs reusable workflows: docker lane uses game-ci
    # (approved Personal/free path); self-hosted lanes invoke the local Unity
    # editor in batchmode by design.
    ".github/workflows/reusable-build-platform.yml",
    ".github/workflows/reusable-unity-tests.yml",
    # The native build lanes of reusable-build-platform.yml (self-hosted
    # macOS / Windows, the Addressables pre-step) all call this one script
    # instead of each spelling the Unity command line in its own shell. It
    # is the same approved native invocation, moved to a single place.
    "scripts/build/run_unity_player.sh",
    # The Windows-runner docker lane's in-container script: game-ci's serial
    # activation, then run_unity_player.sh. It was an inline PowerShell array
    # inside reusable-build-platform.yml (already allowed), moved verbatim.
    "scripts/build/docker_windows_container.sh",
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

    Approved locations are ALLOWED_PATHS; each entry there says why.
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
        game-ci/unity-builder in the approved reusable-build-platform.yml (and
        game-ci/unity-test-runner in reusable-unity-tests.yml); any other use
        of a game-ci action is still a violation.
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
                f"Each approved path says why in ALLOWED_PATHS — any other file "
                f"must use the Docker executor or run_unity_player.sh.\n\n"
                f"{msg}"
            )

    def test_allowed_path_constant_is_correct(self):
        """Sanity check: ALLOWED_PATHS contains the expected approved files."""
        assert "docker/unity/entrypoint.sh" in ALLOWED_PATHS, \
            "docker/unity/entrypoint.sh must be in ALLOWED_PATHS"
        assert ".github/workflows/reusable-build-platform.yml" in ALLOWED_PATHS, \
            "reusable-build-platform.yml must be in ALLOWED_PATHS (the native and game-ci lanes)"
        for path in ALLOWED_PATHS:
            assert "/" in path, f"All paths must use forward slashes: {path}"

    def test_entrypoint_is_in_allowed_paths(self):
        """Document the contract: entrypoint.sh must remain in the approved allowlist."""
        assert "docker/unity/entrypoint.sh" in ALLOWED_PATHS

    def test_every_approved_path_exists(self):
        """An allowlist entry for a file that is gone is an exception nobody
        needs: it would quietly approve whatever is added under that name."""
        missing = sorted(p for p in ALLOWED_PATHS if not (REPO_ROOT / p).exists())
        assert not missing, f"ALLOWED_PATHS names files that no longer exist: {missing}"


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
