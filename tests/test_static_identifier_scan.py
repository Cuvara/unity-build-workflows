"""
Static prohibited-identifier scan — KEYSTONE test.

Fails the suite if any file in the repo (outside explicit allowlist) contains
real game-specific identifiers that should have been genericised.

Prohibited identifiers
----------------------
- BackpackAdventures / backpack-adventures / backpack adventures (the game name)
- BuzzelStudio / buzzellstudio / buzzel-studio (the original studio, all spellings)
- ghcr.io/buzzelstudio / ghcr.io/buzzellstudio (fixed Docker registry namespace)

Scan targets
------------
  scripts/          Python + shell scripts distributed with the toolkit
  templates/        Template config and workflow files
  examples/         Consumer-facing integration examples
  unity-package/    The UPM package distributed to consumers
  schemas/          JSON schemas (example strings must use generic namespaces)
  docker/metadata/  Image-manifest schema (example strings must be generic)
  tests/            All test modules AND fixture data — must be fully generic

Allowlisted paths (minimal — keep as small as possible)
--------------------------------------------------------
  docs/                          Historical illustration; ADR 003-* quotes old
                                 values for context — intentional.
  .git/                          VCS internals.
  README.md                      Top-level meta file with migration history.
  CHANGELOG.md                   Release-note history legitimately mentions BuzzelStudio.
  CONTRIBUTING.md                Contributor guide; may reference original project.
  tests/test_static_identifier_scan.py  — contains prohibited patterns AS
                                 regex literals for the scan itself; the only
                                 legitimate source of these strings in tests/.

  NOTE: tests/ as a WHOLE is NOT allowlisted.  Fixture data, helper modules,
  and all other test files must be fully generic.  The blanket exemption was
  the root cause of the fixtures/build_metadata_sample.json leak going
  undetected.  Only the scan file itself is exempted (file-specific, not prefix).
"""
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Prohibited patterns
# ---------------------------------------------------------------------------

PROHIBITED_PATTERNS = [
    (
        re.compile(r"backpack[-\s]?adventures", re.IGNORECASE),
        "game name 'Backpack Adventures'",
    ),
    (
        re.compile(r"backpackadventures", re.IGNORECASE),
        "game name 'BackpackAdventures' (concatenated)",
    ),
    (
        # Catches: BuzzelStudio, buzzellstudio, buzzel-studio (all spellings)
        re.compile(r"buzzell?-?studio", re.IGNORECASE),
        "fixed org identifier 'BuzzelStudio' (also: buzzellstudio, buzzel-studio)",
    ),
    (
        re.compile(r"ghcr\.io/buzzell?studio", re.IGNORECASE),
        "fixed Docker registry namespace 'ghcr.io/buzzel(l)studio'",
    ),
]

# ---------------------------------------------------------------------------
# Scan targets and allowlists
# ---------------------------------------------------------------------------

SCAN_DIRS = [
    "scripts",
    "templates",
    "examples",
    "unity-package",
    "schemas",
    "docker/metadata",
    "tests",
]

# Path prefixes that are allowlisted (relative to repo root, using forward slashes).
# Keep this list MINIMAL — allowlisting a fixable file defeats the guard.
ALLOWLISTED_PREFIXES = [
    "docs/",    # historical illustration; ADR 003-* quotes old values
    ".git/",    # VCS internals
]

# Individual allowlisted filenames (repo-root-relative).
# NOTE: the blanket "tests/" prefix exemption was removed — test DATA fixtures
# must be generic and are now scanned. Only this scan file itself is exempt,
# because it must contain the forbidden patterns as detection test-data.
ALLOWLISTED_FILES = {
    "README.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    # This scan file legitimately references forbidden patterns as test-data.
    "tests/test_static_identifier_scan.py",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_allowlisted(rel_path: str) -> bool:
    """Return True if this path is in an allowlisted location."""
    if rel_path in ALLOWLISTED_FILES:
        return True
    for prefix in ALLOWLISTED_PREFIXES:
        if rel_path.startswith(prefix):
            return True
    return False


def _collect_files() -> list:
    """Collect all files under SCAN_DIRS that are not allowlisted."""
    files = []
    for scan_dir in SCAN_DIRS:
        target = REPO_ROOT / scan_dir
        if not target.exists():
            continue
        for path in sorted(target.rglob("*")):
            if not path.is_file():
                continue
            # Skip binary-ish extensions
            if path.suffix in {".pyc", ".pyo", ".png", ".jpg", ".ico",
                                ".gif", ".woff", ".woff2", ".ttf", ".eot",
                                ".apk", ".aab", ".ipa", ".zip", ".tar", ".gz"}:
                continue
            rel = path.relative_to(REPO_ROOT).as_posix()
            if not _is_allowlisted(rel):
                files.append(path)
    return files


def _scan_file(path: Path) -> list:
    """Return list of (lineno, line, pattern_label) for each violation."""
    violations = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return violations
    for lineno, line in enumerate(text.splitlines(), start=1):
        for pattern, label in PROHIBITED_PATTERNS:
            if pattern.search(line):
                violations.append((lineno, line.rstrip(), label))
    return violations


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestProhibitedIdentifiers:
    """
    Keystone: no production/template/example file may contain game-specific
    identifiers.  All such names must be replaced with generic
    ExampleProject / ExampleCompany / com.example.* equivalents.
    """

    def test_no_prohibited_identifiers_in_production_files(self):
        files = _collect_files()
        assert files, (
            f"No files found under {SCAN_DIRS} — check that REPO_ROOT is correct: {REPO_ROOT}"
        )

        all_violations: list[str] = []
        for path in files:
            hits = _scan_file(path)
            rel = path.relative_to(REPO_ROOT).as_posix()
            for lineno, line, label in hits:
                all_violations.append(
                    f"  {rel}:{lineno}: [{label}]\n    {line!r}"
                )

        assert not all_violations, (
            "Prohibited game-specific identifiers found in production files.\n"
            "Replace all occurrences with generic placeholders "
            "(ExampleProject / ExampleCompany / com.example.project / "
            "ghcr.io/example-namespace/...).\n\n"
            "Violations:\n" + "\n".join(all_violations)
        )

    def test_scan_covers_templates_dir(self):
        """Sanity: templates/ directory exists and is in SCAN_DIRS."""
        assert "templates" in SCAN_DIRS, "templates/ must be in SCAN_DIRS"
        assert (REPO_ROOT / "templates").exists(), "templates/ directory must exist"

    def test_scan_covers_examples_dir(self):
        """Sanity: examples/ directory exists and is in SCAN_DIRS."""
        assert "examples" in SCAN_DIRS, "examples/ must be in SCAN_DIRS"
        assert (REPO_ROOT / "examples").exists(), "examples/ directory must exist"

    def test_scan_covers_schemas_dir(self):
        """Sanity: schemas/ is now actively scanned (not allowlisted)."""
        assert "schemas" in SCAN_DIRS, "schemas/ must be in SCAN_DIRS"
        assert "schemas/" not in ALLOWLISTED_PREFIXES, (
            "schemas/ must NOT be allowlisted — example strings in schemas "
            "must use generic namespaces (e.g. ghcr.io/example-namespace/...)"
        )

    def test_scan_covers_docker_metadata_dir(self):
        """Sanity: docker/metadata/ is actively scanned."""
        assert "docker/metadata" in SCAN_DIRS, "docker/metadata/ must be in SCAN_DIRS"

    def test_allowlist_does_not_suppress_examples_root(self):
        """
        examples/ as a whole is NOT allowlisted — violations in consumer-facing
        files must be caught by the scan.
        """
        assert "examples/" not in ALLOWLISTED_PREFIXES, (
            "examples/ must not be globally allowlisted — consumer-facing "
            "BuildConfig and Packages files must be fully generic."
        )
        assert "examples" in SCAN_DIRS, "examples/ must be in SCAN_DIRS"

    def test_allowlist_does_not_suppress_templates(self):
        """
        templates/ is now clean — no exemptions needed.  Confirm templates/
        is not globally allowlisted (it must be actively scanned).
        """
        assert "templates/" not in ALLOWLISTED_PREFIXES, (
            "templates/ must not be allowlisted — it is now fully genericised "
            "and must be actively scanned."
        )

    def test_allowlist_does_not_suppress_github(self):
        """
        .github/ is now clean — no longer allowlisted.  Violations there
        must be caught by the scan.
        """
        assert ".github/" not in ALLOWLISTED_PREFIXES, (
            ".github/ must not be allowlisted — it is now clean and should "
            "be caught by the scan if a future change reintroduces a leak."
        )

    def test_unity_package_metadata_is_generic(self):
        """unity-package/ must not contain game-studio-specific strings."""
        pkg_dir = REPO_ROOT / "unity-package"
        if not pkg_dir.exists():
            pytest.skip("unity-package/ directory not found")
        violations = []
        for path in sorted(pkg_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix in {".meta", ".pyc"}:
                continue
            hits = _scan_file(path)
            rel = path.relative_to(REPO_ROOT).as_posix()
            for lineno, line, label in hits:
                violations.append(f"  {rel}:{lineno}: [{label}] {line!r}")
        assert not violations, (
            "unity-package/ contains game-specific identifiers:\n"
            + "\n".join(violations)
        )


class TestAllowlistRationale:
    """
    Meta-tests that document why certain directories are (or are NOT) allowlisted.
    """

    def test_docs_is_allowlisted_but_not_scripts(self):
        """docs/ is allowlisted; scripts/ is actively scanned."""
        assert "docs/" in ALLOWLISTED_PREFIXES, "docs/ must be in the allowlist"
        for prefix in ALLOWLISTED_PREFIXES:
            assert not "scripts".startswith(prefix.rstrip("/")), (
                "scripts/ must NOT be allowlisted — it is a production scan target"
            )

    def test_schemas_is_now_scanned_not_allowlisted(self):
        """
        schemas/ is actively scanned.  The historic allowlist entry was removed
        once unity-package-engineer genericised the example strings in
        unity-image-manifest.schema.json and docker/metadata/image-manifest.schema.json.
        """
        assert "schemas/" not in ALLOWLISTED_PREFIXES, (
            "schemas/ must NOT be allowlisted — example strings must use "
            "generic namespaces (ghcr.io/example-namespace/...)"
        )
        assert "schemas" in SCAN_DIRS, "schemas/ must be in SCAN_DIRS"

    def test_github_workflows_is_now_scanned_not_allowlisted(self):
        """
        .github/ is now clean and actively scanned.  The sprint allowlist
        entry was removed once reusable-workflow-engineer genericised all
        org-specific references in .github/workflows/.
        """
        assert ".github/" not in ALLOWLISTED_PREFIXES, (
            ".github/ must NOT be allowlisted — it is now clean and must "
            "fail the scan if a future change reintroduces a leak."
        )

    def test_tests_dir_is_scanned_not_blanket_allowlisted(self):
        """
        tests/ is actively SCANNED — fixture JSON and test helpers must be
        fully generic, just like production files.

        The blanket 'tests/' prefix allowlist was the root cause of the
        fixtures/build_metadata_sample.json leak surviving multiple scan
        iterations without detection.  That exemption is now removed.

        Only tests/test_static_identifier_scan.py is file-specifically
        exempted because it must contain the forbidden regex literals.
        """
        assert "tests/" not in ALLOWLISTED_PREFIXES, (
            "tests/ must NOT be blanket-allowlisted — fixture data and test "
            "helpers must be clean. Only the scan file itself is file-specifically "
            "exempted (it must contain the patterns as regex literals)."
        )
        assert "tests" in SCAN_DIRS, "tests/ must be in SCAN_DIRS"

    def test_scan_file_is_file_specifically_allowlisted(self):
        """
        The scan file itself must be in ALLOWLISTED_FILES (not suppressed by
        a blanket prefix) — so that if it moves or is renamed, the exemption
        stops working rather than silently growing.
        """
        assert "tests/test_static_identifier_scan.py" in ALLOWLISTED_FILES, (
            "tests/test_static_identifier_scan.py must be in ALLOWLISTED_FILES "
            "(file-specific, not prefix-based) — it contains patterns as test data."
        )

    def test_scan_covers_tests_fixtures_dir(self):
        """
        Sanity: tests/fixtures/ is reachable via the tests/ scan dir entry.
        Fixture files contain example values that must be generic.
        """
        assert "tests" in SCAN_DIRS
        fixtures_dir = REPO_ROOT / "tests" / "fixtures"
        assert fixtures_dir.exists(), "tests/fixtures/ directory must exist"
