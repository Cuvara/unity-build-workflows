"""
test_sign_ios_platform_isolation.py
====================================
Regression tests for platform isolation of the iOS signing stage.

The invariant: the sign-ios job is matrix-driven and its matrix is populated
ONLY when iOS is selected AND the build type is release. For every other
platform combination the sign matrix is empty, making it structurally
impossible for the pipeline to schedule iOS signing operations for a non-iOS
build.

These tests run the real matrix-generation script (from unity-pipeline.yml's
resolve-config step) and verify the sign-matrix output, not just YAML labels.
"""


# ---------------------------------------------------------------------------
# CASE 1: Android Development — no iOS operations
# ---------------------------------------------------------------------------

class TestAndroidDevelopment:
    def test_sign_matrix_empty(self, resolve_matrix):
        out = resolve_matrix(["Android"], build_type="development")
        assert out["sign"] == [], (
            "Android development build must not generate any sign operations"
        )

    def test_sign_ios_false(self, resolve_matrix):
        out = resolve_matrix(["Android"], build_type="development")
        assert out["sign-ios"] == "false"

    def test_has_sign_ops_false(self, resolve_matrix):
        out = resolve_matrix(["Android"], build_type="development")
        assert out["has-sign-ops"] == "false"

    def test_build_matrix_contains_android_only(self, resolve_matrix):
        out = resolve_matrix(["Android"], build_type="development")
        assert out["build_platforms"] == ["Android"]
        assert "iOS" not in out["sign_platforms"]


# ---------------------------------------------------------------------------
# CASE 2: Android Release — no iOS operations
# ---------------------------------------------------------------------------

class TestAndroidRelease:
    def test_sign_matrix_empty(self, resolve_matrix):
        out = resolve_matrix(["Android"], build_type="release")
        assert out["sign"] == [], (
            "Android release build must not generate any sign operations"
        )

    def test_sign_ios_false(self, resolve_matrix):
        out = resolve_matrix(["Android"], build_type="release")
        assert out["sign-ios"] == "false"

    def test_has_sign_ops_false(self, resolve_matrix):
        out = resolve_matrix(["Android"], build_type="release")
        assert out["has-sign-ops"] == "false"


# ---------------------------------------------------------------------------
# CASE 3: iOS Release — sign operations present
# ---------------------------------------------------------------------------

class TestiOSRelease:
    def test_sign_matrix_contains_ios(self, resolve_matrix):
        out = resolve_matrix(["iOS"], build_type="release")
        assert len(out["sign"]) == 1
        assert out["sign"][0]["platform"] == "iOS"

    def test_sign_ios_true(self, resolve_matrix):
        out = resolve_matrix(["iOS"], build_type="release")
        assert out["sign-ios"] == "true"

    def test_has_sign_ops_true(self, resolve_matrix):
        out = resolve_matrix(["iOS"], build_type="release")
        assert out["has-sign-ops"] == "true"

    def test_sign_matrix_has_artifact_names(self, resolve_matrix):
        out = resolve_matrix(["iOS"], build_type="release")
        row = out["sign"][0]
        assert row["xcodeproj-artifact"] == "release-ios-xcodeproj"
        assert row["ipa-artifact"] == "release-ios-ipa"

    def test_sign_matrix_has_runner_labels(self, resolve_matrix):
        out = resolve_matrix(["iOS"], build_type="release")
        row = out["sign"][0]
        assert row["runner-labels"], "sign matrix row must specify runner labels"


# ---------------------------------------------------------------------------
# CASE 4: iOS Development — no sign operations (signing is release-only)
# ---------------------------------------------------------------------------

class TestiOSDevelopment:
    def test_sign_matrix_empty(self, resolve_matrix):
        out = resolve_matrix(["iOS"], build_type="development")
        assert out["sign"] == [], (
            "iOS development build must not generate sign operations"
        )

    def test_sign_ios_false(self, resolve_matrix):
        out = resolve_matrix(["iOS"], build_type="development")
        assert out["sign-ios"] == "false"


# ---------------------------------------------------------------------------
# CASE 5: WebGL — no iOS operations
# ---------------------------------------------------------------------------

class TestWebGL:
    def test_sign_matrix_empty(self, resolve_matrix):
        out = resolve_matrix(["WebGL"], build_type="development")
        assert out["sign"] == []
        assert out["has-sign-ops"] == "false"

    def test_release_also_empty(self, resolve_matrix):
        out = resolve_matrix(["WebGL"], build_type="release")
        assert out["sign"] == []


# ---------------------------------------------------------------------------
# CASE 6: Linux — no iOS operations
# ---------------------------------------------------------------------------

class TestLinux:
    def test_sign_matrix_empty(self, resolve_matrix):
        out = resolve_matrix(["Linux64"], build_type="development")
        assert out["sign"] == []
        assert out["has-sign-ops"] == "false"

    def test_release_also_empty(self, resolve_matrix):
        out = resolve_matrix(["Linux64"], build_type="release")
        assert out["sign"] == []


# ---------------------------------------------------------------------------
# CASE 7: Multi-platform Android + WebGL — no iOS operations
# ---------------------------------------------------------------------------

class TestMultiPlatformNoiOS:
    def test_sign_matrix_empty(self, resolve_matrix):
        out = resolve_matrix(["Android", "WebGL"], build_type="development")
        assert out["sign"] == [], (
            "Multi-platform build without iOS must not generate sign operations"
        )
        assert "iOS" not in out["sign_platforms"]

    def test_release_also_empty(self, resolve_matrix):
        out = resolve_matrix(["Android", "WebGL"], build_type="release")
        assert out["sign"] == []


# ---------------------------------------------------------------------------
# CASE 8: Multi-platform including iOS release — sign ops for iOS only
# ---------------------------------------------------------------------------

class TestMultiPlatformWithiOS:
    def test_sign_matrix_contains_only_ios(self, resolve_matrix):
        out = resolve_matrix(["Android", "iOS", "WebGL"], build_type="release")
        assert len(out["sign"]) == 1
        assert out["sign"][0]["platform"] == "iOS"
        assert out["has-sign-ops"] == "true"

    def test_build_matrix_has_all_platforms(self, resolve_matrix):
        out = resolve_matrix(["Android", "iOS", "WebGL"], build_type="release")
        assert set(out["build_platforms"]) == {"Android", "iOS", "WebGL"}

    def test_sign_matrix_does_not_contain_android(self, resolve_matrix):
        """The core regression: Android must never appear in the sign matrix."""
        out = resolve_matrix(["Android", "iOS", "WebGL"], build_type="release")
        sign_platforms = [r["platform"] for r in out["sign"]]
        assert "Android" not in sign_platforms
        assert "WebGL" not in sign_platforms


# ---------------------------------------------------------------------------
# CASE 9: Windows — no iOS operations
# ---------------------------------------------------------------------------

class TestWindows:
    def test_sign_matrix_empty(self, resolve_matrix):
        out = resolve_matrix(["Windows64"], build_type="release")
        assert out["sign"] == []
        assert out["has-sign-ops"] == "false"


# ---------------------------------------------------------------------------
# Structural: sign-ios job in pipeline YAML is matrix-driven
# ---------------------------------------------------------------------------

def test_sign_ios_job_uses_matrix_strategy():
    """The sign-ios job must use a matrix strategy, not a static boolean gate."""
    from pathlib import Path
    import yaml
    pipeline = yaml.safe_load(
        (Path(__file__).parent.parent / ".github" / "workflows" / "unity-pipeline.yml")
        .read_text()
    )
    job = pipeline["jobs"]["sign-ios"]
    assert "strategy" in job, "sign-ios must have a strategy block"
    assert "matrix" in job["strategy"], "sign-ios must use matrix strategy"
    assert "include" in job["strategy"]["matrix"], "sign-ios matrix must use include"
    # The matrix must reference the dynamically generated sign-matrix output.
    include_expr = str(job["strategy"]["matrix"]["include"])
    assert "sign-matrix" in include_expr, (
        "sign-ios matrix must be driven by the sign-matrix output from resolve-config"
    )


def test_sign_ios_gate_uses_has_sign_ops():
    """The sign-ios job must gate on has-sign-ops, not the old sign-ios boolean."""
    from pathlib import Path
    import yaml
    pipeline = yaml.safe_load(
        (Path(__file__).parent.parent / ".github" / "workflows" / "unity-pipeline.yml")
        .read_text()
    )
    job = pipeline["jobs"]["sign-ios"]
    condition = str(job.get("if", ""))
    assert "has-sign-ops" in condition, (
        "sign-ios job condition must reference has-sign-ops output"
    )
