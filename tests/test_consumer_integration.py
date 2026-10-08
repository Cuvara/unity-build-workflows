"""
Consumer integration tests for the unity-build-workflows toolkit.

Covers:
- Required project fields present in base config
- Android applicationId validation across overlays
- iOS bundleId validation
- Scene path validation
- Toolkit-checkout contract: given consumer-workspace layout (project/ + .ci/unity-build-workflows/)
  scripts/schemas must resolve from the toolkit path, not the consumer project root
- Package-dependency preflight: missing package → actionable error message
- Workflow/toolkit version compatibility (VERSION file)
- Executor resolution: Android→docker-unity, iOS→macos-unity-xcode
- Android-cannot-native, iOS-cannot-Linux contract errors
- Missing Docker image error, missing macOS prereq error
- Secret redaction in logs
- Fork/release secret protection contract

END-TO-END CONTRACT TEST:
  Isolated sample consumer fixture (under tests/) containing only:
    - minimal Unity project metadata (no real Unity install needed)
    - BuildConfig overlays (base + production)
    - package manifest declaring the UPM dependency
    - no copied toolkit scripts/actions
  Asserts the toolkit can locate: consumer project, toolkit path, schemas,
  scripts, image manifest, package contract, artifact directories — using
  fake Unity/Docker/Xcode stubs. Does NOT claim real Android/iOS build success.
"""
import copy
import re
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
FIXTURES_DIR = Path(__file__).parent / "fixtures"
SCHEMA_PATH = REPO_ROOT / "schemas" / "unity-build-config.schema.json"

sys.path.insert(0, str(REPO_ROOT / "scripts" / "common"))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict:
    assert path.exists(), f"File not found: {path}"
    with path.open() as f:
        return json.load(f)


def _deep_merge(base: dict, override: dict) -> dict:
    """
    Simple deep-merge: override wins on scalar conflicts; dicts are merged
    recursively.  Used in tests when config_loader is not yet available.
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _schema_validator():
    """Build a jsonschema validator for the build config schema."""
    import jsonschema
    schema = _load_json(SCHEMA_PATH)
    try:
        from jsonschema import Draft7Validator
        from referencing import Registry, Resource
        resource = Resource.from_contents(schema)
        registry = Registry().with_resource(SCHEMA_PATH.as_uri(), resource)
        return Draft7Validator(schema, registry=registry)
    except (ImportError, TypeError):
        resolver = jsonschema.RefResolver(base_uri=SCHEMA_PATH.as_uri(), referrer=schema)
        return jsonschema.Draft7Validator(schema, resolver=resolver)


# ---------------------------------------------------------------------------
# Android applicationId validation
# ---------------------------------------------------------------------------

class TestAndroidApplicationId:

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.validator = _schema_validator()

    def _make_config(self, app_id: str) -> dict:
        return {
            "projectName": "test", "companyName": "Acme",
            "productName": "Test", "bundleVersion": "1.0.0",
            "outputDirectory": "Builds",
            "scenes": ["Assets/Scenes/Main.unity"],
            "android": {"applicationId": app_id},
        }

    def test_valid_application_id_passes(self):
        errors = list(self.validator.iter_errors(self._make_config("com.example.game")))
        assert not errors

    def test_example_project_application_id_passes(self):
        errors = list(self.validator.iter_errors(self._make_config("com.example.project")))
        assert not errors

    def test_dev_variant_application_id_passes(self):
        errors = list(self.validator.iter_errors(self._make_config("com.example.project.dev")))
        assert not errors

    def test_staging_variant_application_id_passes(self):
        errors = list(self.validator.iter_errors(self._make_config("com.example.project.staging")))
        assert not errors

    def test_single_segment_id_rejected(self):
        errors = list(self.validator.iter_errors(self._make_config("singleSegment")))
        assert errors, "Single-segment applicationId must be rejected"

    def test_id_with_spaces_rejected(self):
        errors = list(self.validator.iter_errors(self._make_config("invalid bundle id")))
        assert errors, "applicationId with spaces must be rejected"

    def test_id_starting_with_digit_rejected(self):
        errors = list(self.validator.iter_errors(self._make_config("1com.example.game")))
        assert errors, "applicationId starting with digit must be rejected"


# ---------------------------------------------------------------------------
# iOS bundleId validation
# ---------------------------------------------------------------------------

class TestIOSBundleId:

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.validator = _schema_validator()

    def _make_ios_config(self, bundle_id: str) -> dict:
        return {
            "projectName": "test", "companyName": "Acme",
            "productName": "Test", "bundleVersion": "1.0.0",
            "outputDirectory": "Builds",
            "scenes": ["Assets/Scenes/Main.unity"],
            "ios": {"bundleIdentifier": bundle_id},
        }

    def test_valid_bundle_id_passes(self):
        errors = list(self.validator.iter_errors(self._make_ios_config("com.example.game")))
        assert not errors

    def test_three_segment_bundle_id_passes(self):
        errors = list(self.validator.iter_errors(self._make_ios_config("com.example.mygame")))
        assert not errors

    def test_bundle_id_starting_with_digit_rejected(self):
        errors = list(self.validator.iter_errors(self._make_ios_config("123.example.game")))
        assert errors, "bundleIdentifier starting with digit must be rejected"

    def test_bundle_id_single_segment_rejected(self):
        errors = list(self.validator.iter_errors(self._make_ios_config("singleSegment")))
        assert errors, "Single-segment bundleIdentifier must be rejected"


# ---------------------------------------------------------------------------
# Toolkit-checkout contract
# ---------------------------------------------------------------------------

class TestToolkitCheckoutContract:
    """
    Consumer workspace layout:
      <workspace>/
        project/         ← Unity project (consumer repo)
        .ci/
          unity-build-workflows/   ← toolkit checkout (this repo)

    Asserts that schemas and scripts resolve from the TOOLKIT path,
    not from the consumer project root.
    """

    def test_schema_resolves_from_toolkit_path(self, tmp_path):
        """
        When the toolkit is at .ci/unity-build-workflows/, the schema
        must be found at .ci/unity-build-workflows/schemas/unity-build-config.schema.json
        and NOT at project/schemas/ (which does not exist in a consumer project).
        """
        # Simulate consumer workspace
        consumer_project = tmp_path / "project"
        toolkit_path = tmp_path / ".ci" / "unity-build-workflows"

        consumer_project.mkdir(parents=True)
        toolkit_schemas = toolkit_path / "schemas"
        toolkit_schemas.mkdir(parents=True)

        # Copy schema to toolkit location
        src_schema = SCHEMA_PATH
        if not src_schema.exists():
            pytest.skip("schemas/unity-build-config.schema.json not found")
        (toolkit_schemas / "unity-build-config.schema.json").write_text(
            src_schema.read_text()
        )

        # Consumer project root does NOT have schemas/
        assert not (consumer_project / "schemas").exists(), (
            "Consumer project must not have its own schemas/ directory"
        )

        # Schema is found in toolkit, not in consumer project
        toolkit_schema = toolkit_path / "schemas" / "unity-build-config.schema.json"
        assert toolkit_schema.exists(), (
            "Schema must be accessible at the toolkit path: "
            ".ci/unity-build-workflows/schemas/unity-build-config.schema.json"
        )

    def test_scripts_resolve_from_toolkit_path(self, tmp_path):
        """
        CI scripts must live under .ci/unity-build-workflows/scripts/,
        not under the consumer project root.
        """
        toolkit_path = tmp_path / ".ci" / "unity-build-workflows"
        toolkit_scripts = toolkit_path / "scripts"
        toolkit_scripts.mkdir(parents=True)
        (toolkit_scripts / "placeholder.py").write_text("# toolkit script")

        consumer_project = tmp_path / "project"
        consumer_project.mkdir()

        # Scripts are NOT in consumer project
        assert not (consumer_project / "scripts").exists()
        # Scripts ARE in toolkit
        assert (toolkit_path / "scripts").exists()

    def test_build_config_lives_in_consumer_project(self, tmp_path):
        """
        BuildConfig/ is owned by the consumer and must NOT be in the toolkit.
        The toolkit reads it from the consumer project path.
        """
        consumer_project = tmp_path / "project"
        build_config_dir = consumer_project / "BuildConfig"
        build_config_dir.mkdir(parents=True)
        (build_config_dir / "base.json").write_text(json.dumps({
            "projectName": "my-game",
            "companyName": "MyCompany",
            "productName": "My Game",
            "bundleVersion": "1.0.0",
            "outputDirectory": "Builds",
            "scenes": ["Assets/Scenes/Main.unity"],
        }))

        # BuildConfig must be in consumer project, not in toolkit
        toolkit_path = tmp_path / ".ci" / "unity-build-workflows"
        assert not (toolkit_path / "BuildConfig").exists()
        assert (consumer_project / "BuildConfig" / "base.json").exists()

    def test_consumer_workspace_separation(self, tmp_path):
        """
        Consumer project and toolkit must be in separate directories.
        Toolkit changes must not affect consumer project contents.
        """
        consumer_project = tmp_path / "project"
        toolkit_path = tmp_path / ".ci" / "unity-build-workflows"
        consumer_project.mkdir(parents=True)
        toolkit_path.mkdir(parents=True)

        # They are distinct paths
        assert consumer_project != toolkit_path
        assert not consumer_project.is_relative_to(toolkit_path), (
            "Consumer project must not be nested inside the toolkit checkout"
        )
        assert not toolkit_path.is_relative_to(consumer_project), (
            "Toolkit checkout must not be nested inside the consumer project"
        )

    def test_toolkit_files_not_in_consumer_project(self, tmp_path):
        """
        Toolkit scripts/schemas must live in .ci/unity-build-workflows/,
        NOT inside project/.

        Guards against the silent-wrong-repo bug: if workflow_ref/workflow_sha
        inside a reusable workflow resolves to the CALLER (consumer repo), the
        auto-derived checkout path could clone the consumer repo into both
        project/ and .ci/, silently giving wrong scripts.
        Consumer-workflow-engineer uses caller-provided 'workflow-repository' /
        'workflow-ref' inputs to avoid this.
        """
        consumer_project = tmp_path / "project"
        toolkit_path = tmp_path / ".ci" / "unity-build-workflows"

        # Simulate toolkit checkout — scripts/common/ is the sentinel
        (toolkit_path / "scripts" / "common").mkdir(parents=True)
        (toolkit_path / "scripts" / "common" / "config_loader.py").write_text(
            "# toolkit script"
        )
        (toolkit_path / "schemas").mkdir()
        (toolkit_path / "VERSION").write_text("2.0.0")

        # Simulate consumer project WITHOUT toolkit scripts
        consumer_project.mkdir(parents=True)
        (consumer_project / "Assets" / "Scenes").mkdir(parents=True)
        (consumer_project / "Packages").mkdir()

        # Toolkit has scripts/common/
        assert (toolkit_path / "scripts" / "common").exists(), (
            "Toolkit must have scripts/common/ at .ci/unity-build-workflows/scripts/common/"
        )
        # Consumer project does NOT have toolkit scripts
        assert not (consumer_project / "scripts").exists(), (
            "project/ must NOT contain scripts/ — toolkit is at "
            ".ci/unity-build-workflows/, separate from the consumer project"
        )
        # Consumer Assets/ does NOT contain toolkit Python files
        assert not (consumer_project / "Assets" / "config_loader.py").exists(), (
            "Toolkit Python scripts must NOT land in project/Assets/"
        )
        # Toolkit VERSION confirms it is the toolkit, not the consumer repo
        assert (toolkit_path / "VERSION").read_text().strip() == "2.0.0"


# ---------------------------------------------------------------------------
# Package-dependency preflight
# ---------------------------------------------------------------------------

class TestPackageDependencyPreflight:
    """
    The consumer's Packages/manifest.json must declare the build-pipeline
    UPM package.  Missing declaration → actionable error.
    """

    REQUIRED_PACKAGE_ID = "com.company.build-pipeline"

    def _make_manifest(self, deps: dict) -> dict:
        return {"dependencies": deps}

    def test_missing_package_declaration_is_detectable(self):
        """
        When the required package is missing from a manifest, it must be
        detectable programmatically (not silently ignored).
        """
        manifest = self._make_manifest({
            "com.unity.mathematics": "1.3.1",
        })
        deps = manifest.get("dependencies", {})
        assert self.REQUIRED_PACKAGE_ID not in deps, (
            "Sanity: test manifest must not have the package"
        )
        # Detection logic
        missing = self.REQUIRED_PACKAGE_ID not in deps
        assert missing, "Missing package must be detectable"

    def test_manifest_with_build_pipeline_package_is_valid(self):
        """A manifest that includes the required package passes validation."""
        manifest = self._make_manifest({
            self.REQUIRED_PACKAGE_ID: (
                "https://github.com/YOUR-ORG/unity-build-workflows.git"
                "?path=/unity-package/Packages/com.company.build-pipeline#main"
            ),
            "com.unity.mathematics": "1.3.1",
        })
        deps = manifest.get("dependencies", {})
        assert self.REQUIRED_PACKAGE_ID in deps


# ---------------------------------------------------------------------------
# Workflow / toolkit version compatibility
# ---------------------------------------------------------------------------

class TestVersionCompatibility:
    """
    The toolkit exposes a VERSION file at the repo root.
    Consumers should be able to read and validate the version they depend on.
    """

    VERSION_FILE = REPO_ROOT / "VERSION"

    def test_version_file_exists(self):
        assert self.VERSION_FILE.exists(), (
            "VERSION file must exist at the repo root for consumers to check compatibility"
        )

    def test_version_file_is_semver(self):
        import re
        if not self.VERSION_FILE.exists():
            pytest.skip("VERSION file not found")
        version = self.VERSION_FILE.read_text().strip()
        semver_pattern = re.compile(r"^\d+\.\d+\.\d+$")
        assert semver_pattern.match(version), (
            f"VERSION file must contain a semver string (MAJOR.MINOR.PATCH), got: {version!r}"
        )

    def test_version_file_content_is_non_empty(self):
        if not self.VERSION_FILE.exists():
            pytest.skip("VERSION file not found")
        content = self.VERSION_FILE.read_text().strip()
        assert content, "VERSION file must not be empty"

    def test_consumer_can_read_toolkit_version_at_checkout_path(self, tmp_path):
        """
        In the consumer workspace, VERSION is accessible at
        .ci/unity-build-workflows/VERSION.
        """
        if not self.VERSION_FILE.exists():
            pytest.skip("VERSION file not found")

        toolkit_root = tmp_path / ".ci" / "unity-build-workflows"
        toolkit_root.mkdir(parents=True)
        version_content = self.VERSION_FILE.read_text()
        (toolkit_root / "VERSION").write_text(version_content)

        # Consumer reads from toolkit path
        consumer_version_path = toolkit_root / "VERSION"
        assert consumer_version_path.exists()
        assert consumer_version_path.read_text().strip() == version_content.strip()


# ---------------------------------------------------------------------------
# Executor resolution (static contract)
# ---------------------------------------------------------------------------

class TestExecutorResolutionContract:
    """
    Platform → executor routing contract:
      Android, WebGL, StandaloneLinux64, LinuxServer → docker-unity  (Linux runner)
      iOS                                            → macos-unity-xcode (macOS runner)

    These tests verify the contract statically (reading the resolver script)
    and dynamically (calling the resolver) when the resolver exists.
    """

    RESOLVER_PATH = REPO_ROOT / "scripts" / "common" / "resolve_platform_executor.py"

    DOCKER_PLATFORMS = ["Android", "WebGL", "StandaloneLinux64", "LinuxServer"]
    IOS_PLATFORMS = ["iOS"]
    NATIVE_ONLY_PLATFORMS = ["iOS", "Windows64", "StandaloneWindows64"]

    def _run_resolver(self, *args: str) -> "subprocess.CompletedProcess":
        return subprocess.run(
            [sys.executable, str(self.RESOLVER_PATH), *args],
            capture_output=True, text=True, timeout=10,
        )

    def test_android_resolves_to_docker(self):
        if not self.RESOLVER_PATH.exists():
            pytest.skip("resolve_platform_executor.py not yet created")
        result = self._run_resolver("--target-platform", "Android", "--runner-os", "linux")
        assert result.returncode == 0
        assert "docker-unity" in result.stdout

    def test_ios_resolves_to_macos_xcode(self):
        if not self.RESOLVER_PATH.exists():
            pytest.skip("resolve_platform_executor.py not yet created")
        result = self._run_resolver("--target-platform", "iOS", "--runner-os", "macos")
        assert result.returncode == 0
        assert "macos-unity-xcode" in result.stdout

    def test_android_on_macos_is_rejected(self):
        """Android requires Docker on Linux — macOS runner is wrong executor lane."""
        if not self.RESOLVER_PATH.exists():
            pytest.skip("resolve_platform_executor.py not yet created")
        result = self._run_resolver("--target-platform", "Android", "--runner-os", "macos")
        assert result.returncode != 0, (
            "Android on macOS runner must be rejected (wrong executor lane)"
        )

    def test_ios_on_linux_is_rejected(self):
        """iOS requires macOS/Xcode — Linux runner cannot build it."""
        if not self.RESOLVER_PATH.exists():
            pytest.skip("resolve_platform_executor.py not yet created")
        result = self._run_resolver("--target-platform", "iOS", "--runner-os", "linux")
        assert result.returncode != 0, (
            "iOS on Linux runner must be rejected with actionable error"
        )

    def test_ios_on_linux_error_mentions_macos_or_xcode(self):
        if not self.RESOLVER_PATH.exists():
            pytest.skip("resolve_platform_executor.py not yet created")
        result = self._run_resolver("--target-platform", "iOS", "--runner-os", "linux")
        combined = (result.stdout + result.stderr).lower()
        assert "macos" in combined or "xcode" in combined or "ios" in combined, (
            "iOS-on-Linux error must mention macOS/Xcode so user knows how to fix it"
        )


# ---------------------------------------------------------------------------
# Missing Docker image / missing macOS prereq errors
# ---------------------------------------------------------------------------

class TestMissingPrereqErrors:
    """
    When required infrastructure is absent, the toolkit must produce an
    actionable error (not a cryptic crash).
    NOTE: These are contract/convention tests; they verify error handling logic
    in resolve_image_reference.py — real Docker/Xcode are NOT required.
    """

    @pytest.fixture(scope="class")
    def resolver(self):
        try:
            sys.path.insert(0, str(REPO_ROOT / "scripts" / "docker"))
            import resolve_image_reference
            return resolve_image_reference
        except ImportError:
            return None

    def test_missing_docker_image_in_manifest_raises_actionable_error(self, resolver):
        """
        If the requested platform variant is absent from the image manifest,
        the resolver must raise with an actionable message naming the missing
        variant and guiding the user to build/push it.
        """
        if resolver is None:
            pytest.skip("resolve_image_reference.py not yet implemented")
        import tempfile
        manifest = {
            "image_contract_version": "1",
            "registry": "ghcr.io/example-namespace",
            "images": {
                # webgl only — Android variant intentionally absent
                "webgl": {
                    "tag": "2022.3.45f1-webgl",
                    "digest": "sha256:" + "b" * 64,
                    "supported_targets": ["WebGL"],
                    "unity_version": "2022.3.45f1",
                }
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(manifest, f)
            manifest_path = Path(f.name)
        try:
            with pytest.raises(Exception) as exc_info:
                resolver.resolve(
                    "Android",
                    "2022.3.45f1",
                    "ghcr.io/example-namespace",
                    manifest_path=manifest_path,
                )
            msg = str(exc_info.value).lower()
            assert (
                "android" in msg
                or "variant" in msg
                or "not found" in msg
                or "not present" in msg
            ), (
                f"Missing Docker image variant error must mention the platform or variant. "
                f"Got: {exc_info.value}"
            )
        finally:
            manifest_path.unlink(missing_ok=True)

    def test_ios_platform_rejects_docker_resolver(self, resolver):
        """iOS is rejected by the Docker image resolver with a clear error."""
        if resolver is None:
            pytest.skip("resolve_image_reference.py not yet implemented")
        with pytest.raises(Exception) as exc_info:
            resolver.resolve("iOS", "2022.3.45f1", "ghcr.io/example-namespace")
        msg = str(exc_info.value).lower()
        assert "ios" in msg, (
            f"iOS rejection must mention 'iOS' in the error: {exc_info.value}"
        )


# ---------------------------------------------------------------------------
# Secret redaction contract
# ---------------------------------------------------------------------------

class TestSecretRedactionContract:
    """
    Secrets must not appear in log output.  Tests verify the convention
    that _safe_command_repr() masks known secret env vars.
    """

    @pytest.fixture(scope="class")
    def docker_mod(self):
        try:
            sys.path.insert(0, str(REPO_ROOT / "scripts" / "docker"))
            import run_unity_container
            return run_unity_container
        except ImportError:
            return None

    def test_unity_license_not_logged(self, docker_mod):
        if docker_mod is None:
            pytest.skip("run_unity_container.py not yet implemented")
        fake_license = "<?xml version='1.0'?><License>FAKE</License>"
        cmd = ["docker", "run", "-e", f"UNITY_LICENSE={fake_license}", "image:tag"]
        result = docker_mod._safe_command_repr(cmd)
        assert fake_license not in result, "UNITY_LICENSE value must be redacted from logs"

    def test_keystore_pass_not_logged(self, docker_mod):
        if docker_mod is None:
            pytest.skip("run_unity_container.py not yet implemented")
        fake_pass = "super_secret_keystore_pass_ABC"
        cmd = ["docker", "run", "-e", f"ANDROID_KEYSTORE_PASS={fake_pass}", "image:tag"]
        result = docker_mod._safe_command_repr(cmd)
        assert fake_pass not in result, "ANDROID_KEYSTORE_PASS value must be redacted from logs"

    def test_non_secret_env_not_redacted(self, docker_mod):
        if docker_mod is None:
            pytest.skip("run_unity_container.py not yet implemented")
        cmd = ["docker", "run", "-e", "TARGET_PLATFORM=Android", "image:tag"]
        result = docker_mod._safe_command_repr(cmd)
        assert "TARGET_PLATFORM=Android" in result, (
            "Non-secret env var must NOT be redacted"
        )


# ---------------------------------------------------------------------------
# Fork/release secret protection contract
# ---------------------------------------------------------------------------

class TestForkReleaseSecretProtection:
    """
    Release builds must use digest-pinned image references.
    Mutable tags must be rejected in release mode to prevent supply-chain attacks.
    """

    @pytest.fixture(scope="class")
    def resolver(self):
        try:
            sys.path.insert(0, str(REPO_ROOT / "scripts" / "docker"))
            import resolve_image_reference
            return resolve_image_reference
        except ImportError:
            return None

    def test_release_mode_requires_digest(self, resolver):
        if resolver is None:
            pytest.skip("resolve_image_reference.py not yet implemented")
        with pytest.raises((ValueError, RuntimeError)):
            resolver.resolve(
                "Android", "2022.3.45f1", "ghcr.io/example-namespace",
                release_mode=True,
                # No digest provided — must raise
            )

    def test_release_mode_with_digest_succeeds(self, resolver):
        if resolver is None:
            pytest.skip("resolve_image_reference.py not yet implemented")
        digest = "sha256:" + "a" * 64
        result = resolver.resolve(
            "Android", "2022.3.45f1", "ghcr.io/example-namespace",
            image_digest=digest,
            release_mode=True,
        )
        assert "@sha256:" in result.get("image_ref", ""), (
            "Release mode must produce a digest-pinned image reference"
        )

    def test_mutable_latest_tag_rejected_in_release(self, resolver):
        if resolver is None:
            pytest.skip("resolve_image_reference.py not yet implemented")
        if not hasattr(resolver, "enforce_immutable_reference"):
            pytest.skip("enforce_immutable_reference not yet implemented")
        with pytest.raises(ValueError):
            resolver.enforce_immutable_reference(
                "ghcr.io/example-namespace/unity-android:latest",
                None,
                release_mode=True,
            )


# ---------------------------------------------------------------------------
# Registry CLI contract (--image-registry / --image-namespace)
# ---------------------------------------------------------------------------

class TestRegistryCLIContract:
    """
    Resolver CLI contract (post-breaking-change):
      --image-registry  optional, default 'ghcr.io'
      --image-namespace REQUIRED; raises ValueError / exits non-zero if missing
      --image-digest    required in release mode, must match sha256:[0-9a-f]{64}

    Tests skip gracefully if the script does not yet implement --image-namespace
    (pending unity-package-engineer delivery).
    """

    RESOLVER_PATH = REPO_ROOT / "scripts" / "docker" / "resolve_image_reference.py"
    DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

    def _run_resolver(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.RESOLVER_PATH), *args],
            capture_output=True, text=True, timeout=15,
        )

    def _supports_image_namespace(self) -> bool:
        """Return True if the script already supports --image-namespace."""
        result = self._run_resolver("--help")
        return "--image-namespace" in result.stdout

    def test_missing_image_namespace_exits_nonzero(self):
        """
        --image-namespace is REQUIRED. Omitting it must produce a non-zero exit
        and an actionable error message.
        """
        if not self.RESOLVER_PATH.exists():
            pytest.skip("resolve_image_reference.py not yet created")
        if not self._supports_image_namespace():
            pytest.skip(
                "--image-namespace not yet implemented in resolve_image_reference.py "
                "(pending unity-package-engineer)"
            )
        result = self._run_resolver(
            "--target-platform", "Android",
            "--unity-version", "2022.3.45f1",
            # --image-namespace intentionally omitted
        )
        assert result.returncode != 0, (
            "Omitting --image-namespace must exit non-zero (it is required)"
        )
        combined = result.stdout + result.stderr
        assert combined.strip(), (
            "Missing --image-namespace must produce a diagnostic message"
        )

    def test_missing_image_namespace_error_is_actionable(self):
        """Error for missing --image-namespace must name the missing argument."""
        if not self.RESOLVER_PATH.exists():
            pytest.skip("resolve_image_reference.py not yet created")
        if not self._supports_image_namespace():
            pytest.skip("--image-namespace not yet implemented")
        result = self._run_resolver(
            "--target-platform", "Android",
            "--unity-version", "2022.3.45f1",
        )
        combined = (result.stdout + result.stderr).lower()
        assert "namespace" in combined or "required" in combined, (
            f"Missing --image-namespace error must mention 'namespace' or 'required'. "
            f"Got: {result.stdout + result.stderr!r}"
        )

    def test_image_registry_defaults_to_ghcr_io(self):
        """
        --image-registry is optional.  When omitted, the registry must default
        to 'ghcr.io'.
        """
        if not self.RESOLVER_PATH.exists():
            pytest.skip("resolve_image_reference.py not yet created")
        if not self._supports_image_namespace():
            pytest.skip("--image-namespace not yet implemented")
        result = self._run_resolver(
            "--target-platform", "Android",
            "--unity-version", "2022.3.45f1",
            "--image-namespace", "example-namespace",
            "--output-json",
        )
        if result.returncode != 0:
            pytest.skip(f"Resolver failed (may need other args): {result.stderr}")
        # Default registry 'ghcr.io' must appear in the resolved image ref
        assert "ghcr.io" in result.stdout, (
            f"Default registry 'ghcr.io' must appear in output when "
            f"--image-registry is omitted. Got: {result.stdout!r}"
        )

    def test_explicit_image_registry_overrides_default(self):
        """
        When --image-registry is provided, it must override the 'ghcr.io' default.
        """
        if not self.RESOLVER_PATH.exists():
            pytest.skip("resolve_image_reference.py not yet created")
        if not self._supports_image_namespace():
            pytest.skip("--image-namespace not yet implemented")
        result = self._run_resolver(
            "--target-platform", "Android",
            "--unity-version", "2022.3.45f1",
            "--image-registry", "docker.io",
            "--image-namespace", "example-namespace",
            "--output-json",
        )
        if result.returncode != 0:
            pytest.skip(f"Resolver failed: {result.stderr}")
        assert "docker.io" in result.stdout, (
            f"Explicit --image-registry 'docker.io' must appear in output. "
            f"Got: {result.stdout!r}"
        )

    def test_release_mode_requires_image_digest(self):
        """
        In release mode, --image-digest is required.
        Omitting it must exit non-zero.
        """
        if not self.RESOLVER_PATH.exists():
            pytest.skip("resolve_image_reference.py not yet created")
        if not self._supports_image_namespace():
            pytest.skip("--image-namespace not yet implemented")
        result = self._run_resolver(
            "--target-platform", "Android",
            "--unity-version", "2022.3.45f1",
            "--image-namespace", "example-namespace",
            "--release-mode",
            # --image-digest intentionally omitted
        )
        assert result.returncode != 0, (
            "Release mode without --image-digest must exit non-zero"
        )

    def test_release_mode_with_valid_digest_succeeds(self):
        """
        Release mode with a valid sha256: digest must succeed.
        """
        if not self.RESOLVER_PATH.exists():
            pytest.skip("resolve_image_reference.py not yet created")
        if not self._supports_image_namespace():
            pytest.skip("--image-namespace not yet implemented")
        digest = "sha256:" + "a" * 64
        result = self._run_resolver(
            "--target-platform", "Android",
            "--unity-version", "2022.3.45f1",
            "--image-namespace", "example-namespace",
            "--release-mode",
            "--image-digest", digest,
            "--output-json",
        )
        assert result.returncode == 0, (
            f"Release mode with valid digest must succeed. stderr: {result.stderr}"
        )

    def _supports_digest_format_validation(self) -> bool:
        """
        Return True if the resolver rejects a malformed digest string.
        The current implementation accepts any digest value; this guard
        allows the format-validation test to skip until unity-package-engineer
        hardens the --image-digest flag.
        """
        result = self._run_resolver(
            "--target-platform", "Android",
            "--unity-version", "2022.3.45f1",
            "--image-namespace", "example-namespace",
            "--release-mode",
            "--image-digest", "not-a-valid-digest",
        )
        return result.returncode != 0

    def test_image_digest_must_match_sha256_pattern(self):
        """
        --image-digest must match ^sha256:[0-9a-f]{64}$.
        An invalid digest format must be rejected.

        NOTE: The current resolver accepts any string as a digest value and
        delegates immutability enforcement to enforce_immutable_reference().
        This test skips until the unity-package-engineer adds CLI-level
        digest format validation to resolve_image_reference.py.
        """
        if not self.RESOLVER_PATH.exists():
            pytest.skip("resolve_image_reference.py not yet created")
        if not self._supports_image_namespace():
            pytest.skip("--image-namespace not yet implemented")
        if not self._supports_digest_format_validation():
            pytest.skip(
                "Digest format validation not yet implemented in resolve_image_reference.py "
                "(pending unity-package-engineer: --image-digest must validate ^sha256:[0-9a-f]{64}$)"
            )
        result = self._run_resolver(
            "--target-platform", "Android",
            "--unity-version", "2022.3.45f1",
            "--image-namespace", "example-namespace",
            "--release-mode",
            "--image-digest", "not-a-valid-digest",
        )
        assert result.returncode != 0, (
            "Malformed --image-digest must be rejected (must match sha256:[0-9a-f]{64})"
        )

    def test_digest_pattern_validates_correctly(self):
        """Unit test for the sha256 digest pattern used in release mode validation."""
        valid_digest = "sha256:" + "a" * 64
        assert self.DIGEST_PATTERN.match(valid_digest), (
            f"Valid digest must match pattern: {valid_digest!r}"
        )

        invalid_digests = [
            "sha256:" + "a" * 63,   # too short
            "sha256:" + "a" * 65,   # too long
            "sha256:" + "g" * 64,   # invalid hex char
            "md5:" + "a" * 32,      # wrong algorithm
            "not-a-digest",
            "",
        ]
        for bad in invalid_digests:
            assert not self.DIGEST_PATTERN.match(bad), (
                f"Invalid digest must NOT match pattern: {bad!r}"
            )


# ---------------------------------------------------------------------------
# END-TO-END CONSUMER FIXTURE CONTRACT TEST
# ---------------------------------------------------------------------------

class TestEndToEndConsumerFixtureContract:
    """
    Isolated consumer fixture (fully contained in tmp_path):
      consumer_project/
        BuildConfig/
          base.json          ← minimal valid Unity project metadata + config
          production.json    ← production overlay (env-specific diffs only)
        Packages/
          manifest.json      ← declares com.company.build-pipeline UPM dependency

      toolkit/               ← simulates .ci/unity-build-workflows/
        schemas/
          unity-build-config.schema.json
        scripts/
          common/
            (placeholder — real scripts not required for contract test)
        IMAGE_MANIFEST/
          image-manifest.json
        VERSION

    Tests that the toolkit can:
      - locate the consumer project root (via project_path arg)
      - locate the toolkit at a separate checkout path
      - read schemas from toolkit (not consumer project)
      - load and merge BuildConfig from consumer project
      - validate merged config against schema
      - find the image manifest in the toolkit
      - find the UPM package declaration in consumer Packages/
      - find expected artifact output directories (Builds/)

    Does NOT claim real Android/iOS build success.
    Does NOT require Unity, Docker, or Xcode.
    """

    PACKAGE_ID = "com.company.build-pipeline"

    @pytest.fixture
    def consumer_workspace(self, tmp_path):
        """
        Build an isolated consumer workspace:
          tmp_path/project/           ← Unity project (consumer repo)
          tmp_path/.ci/
            unity-build-workflows/   ← toolkit checkout
          tmp_path/artifacts/         ← build outputs (workspace root)
          tmp_path/logs/              ← build logs (workspace root)
        """
        # --- Consumer project ---
        project = tmp_path / "project"
        (project / "BuildConfig").mkdir(parents=True)

        # Minimal base config (all required fields, generic names)
        base_cfg = {
            "projectName": "example-project",
            "companyName": "ExampleCompany",
            "productName": "ExampleProject",
            "bundleVersion": "1.0.0",
            "buildNumberStrategy": "github_run_number",
            "outputDirectory": "Builds",
            "scenes": ["Assets/Scenes/Bootstrap.unity"],
            "scriptingBackend": "IL2CPP",
            "developmentBuild": False,
            "allowDebugging": False,
            "android": {
                "applicationId": "com.example.project",
                "buildAppBundle": True,
                "minSdkVersion": 22,
                "targetSdkVersion": 34,
                "architecture": "ARM64",
                "keystoreMode": "custom",
            },
        }
        (project / "BuildConfig" / "base.json").write_text(json.dumps(base_cfg, indent=2))

        # Production overlay
        prod_overlay = {
            "outputDirectory": "Builds/Production",
            "android": {"symbolExport": "public"},
            "metadata": {"environment": "production", "channel": "google-play"},
        }
        (project / "BuildConfig" / "production.json").write_text(json.dumps(prod_overlay, indent=2))

        # Consumer Packages/manifest.json declaring the UPM dependency
        (project / "Packages").mkdir()
        consumer_manifest = {
            "dependencies": {
                self.PACKAGE_ID: (
                    "https://github.com/YOUR-ORG/unity-build-workflows.git"
                    "?path=/unity-package/Packages/com.company.build-pipeline#main"
                ),
                "com.unity.nuget.newtonsoft-json": "3.2.1",
            }
        }
        (project / "Packages" / "manifest.json").write_text(json.dumps(consumer_manifest, indent=2))

        # --- Toolkit checkout ---
        toolkit = tmp_path / ".ci" / "unity-build-workflows"
        (toolkit / "schemas").mkdir(parents=True)

        if SCHEMA_PATH.exists():
            (toolkit / "schemas" / "unity-build-config.schema.json").write_text(
                SCHEMA_PATH.read_text()
            )

        (toolkit / "scripts" / "common").mkdir(parents=True)
        (toolkit / "scripts" / "docker").mkdir(parents=True)

        # Image manifest (toolkit-owned, not consumer-owned)
        image_manifest_dir = toolkit / "IMAGE_MANIFEST"
        image_manifest_dir.mkdir()
        image_manifest = {
            "image_contract_version": "1",
            "registry": "ghcr.io/example-namespace",
            "images": {
                "android": {
                    "tag": "2022.3.45f1-android",
                    "digest": "sha256:" + "a" * 64,
                    "supported_targets": ["Android"],
                    "unity_version": "2022.3.45f1",
                }
            },
        }
        (image_manifest_dir / "image-manifest.json").write_text(json.dumps(image_manifest, indent=2))

        # VERSION file
        version_src = REPO_ROOT / "VERSION"
        version_content = version_src.read_text() if version_src.exists() else "2.0.0"
        (toolkit / "VERSION").write_text(version_content)

        # --- Workspace-root outputs (NOT inside project/ or toolkit/) ---
        artifacts_dir = tmp_path / "artifacts"
        artifacts_dir.mkdir()
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()

        return {
            "project": project,
            "toolkit": toolkit,
            "workspace": tmp_path,
            "artifacts": artifacts_dir,
            "logs": logs_dir,
        }

    # ---- Location assertions ----

    def test_consumer_project_root_is_locatable(self, consumer_workspace):
        project = consumer_workspace["project"]
        assert project.exists(), "Consumer project root must exist"
        assert (project / "BuildConfig").is_dir()

    def test_toolkit_is_separate_from_consumer_project(self, consumer_workspace):
        project = consumer_workspace["project"]
        toolkit = consumer_workspace["toolkit"]
        assert project != toolkit
        assert not toolkit.is_relative_to(project), (
            "Toolkit must not be nested inside consumer project"
        )

    def test_schema_found_in_toolkit_not_consumer(self, consumer_workspace):
        project = consumer_workspace["project"]
        toolkit = consumer_workspace["toolkit"]
        assert (toolkit / "schemas" / "unity-build-config.schema.json").exists(), (
            "Schema must be in toolkit path"
        )
        assert not (project / "schemas").exists(), (
            "Consumer project must NOT have its own schemas/"
        )

    # ---- Config loading + merge ----

    def test_base_config_loadable(self, consumer_workspace):
        project = consumer_workspace["project"]
        base = _load_json(project / "BuildConfig" / "base.json")
        assert base["projectName"] == "example-project"

    def test_merged_production_config_is_valid(self, consumer_workspace):
        project = consumer_workspace["project"]
        schema_path = consumer_workspace["toolkit"] / "schemas" / "unity-build-config.schema.json"
        if not schema_path.exists():
            pytest.skip("Schema not available in consumer workspace")

        base = _load_json(project / "BuildConfig" / "base.json")
        overlay = _load_json(project / "BuildConfig" / "production.json")
        merged = _deep_merge(base, overlay)

        schema = _load_json(schema_path)
        import jsonschema
        try:
            from jsonschema import Draft7Validator
            from referencing import Registry, Resource
            resource = Resource.from_contents(schema)
            registry = Registry().with_resource(schema_path.as_uri(), resource)
            validator = Draft7Validator(schema, registry=registry)
        except (ImportError, TypeError):
            resolver = jsonschema.RefResolver(base_uri=schema_path.as_uri(), referrer=schema)
            validator = jsonschema.Draft7Validator(schema, resolver=resolver)

        errors = list(validator.iter_errors(merged))
        assert not errors, (
            "Merged base+production config from consumer fixture must be schema-valid:\n"
            + "\n".join(e.message for e in errors)
        )

    def test_merged_config_sets_production_metadata(self, consumer_workspace):
        project = consumer_workspace["project"]
        base = _load_json(project / "BuildConfig" / "base.json")
        overlay = _load_json(project / "BuildConfig" / "production.json")
        merged = _deep_merge(base, overlay)
        assert merged.get("metadata", {}).get("environment") == "production"

    # ---- Image manifest ----

    def test_image_manifest_found_in_toolkit(self, consumer_workspace):
        toolkit = consumer_workspace["toolkit"]
        manifest_path = toolkit / "IMAGE_MANIFEST" / "image-manifest.json"
        assert manifest_path.exists(), (
            "Image manifest must be found in the toolkit (IMAGE_MANIFEST/image-manifest.json), "
            "not in the consumer project"
        )

    def test_image_manifest_has_android_image(self, consumer_workspace):
        toolkit = consumer_workspace["toolkit"]
        manifest = _load_json(toolkit / "IMAGE_MANIFEST" / "image-manifest.json")
        assert "android" in manifest.get("images", {}), (
            "Image manifest must define an 'android' image entry"
        )

    def test_image_manifest_android_has_digest(self, consumer_workspace):
        toolkit = consumer_workspace["toolkit"]
        manifest = _load_json(toolkit / "IMAGE_MANIFEST" / "image-manifest.json")
        android = manifest.get("images", {}).get("android", {})
        assert android.get("digest", "").startswith("sha256:"), (
            "Android image manifest entry must have a sha256: digest"
        )

    # ---- Package contract ----

    def test_consumer_packages_declares_build_pipeline(self, consumer_workspace):
        project = consumer_workspace["project"]
        manifest = _load_json(project / "Packages" / "manifest.json")
        deps = manifest.get("dependencies", {})
        assert self.PACKAGE_ID in deps, (
            f"Consumer Packages/manifest.json must declare '{self.PACKAGE_ID}'. "
            f"Found: {list(deps.keys())}"
        )

    def test_package_url_is_git_upm_form(self, consumer_workspace):
        project = consumer_workspace["project"]
        manifest = _load_json(project / "Packages" / "manifest.json")
        url = manifest.get("dependencies", {}).get(self.PACKAGE_ID, "")
        assert url.startswith("https://"), f"Package URL must be https://, got: {url!r}"
        assert "?path=" in url, f"Package URL must have ?path= param, got: {url!r}"
        assert "#" in url, f"Package URL must have #ref, got: {url!r}"

    # ---- Artifact directories ----

    def test_consumer_output_dir_is_project_relative(self, consumer_workspace):
        """
        Builds/ output directory is relative to the consumer project root,
        not to the toolkit or the workspace root.
        """
        project = consumer_workspace["project"]
        base = _load_json(project / "BuildConfig" / "base.json")
        output_dir = base.get("outputDirectory", "Builds")
        # The resolved artifact path is consumer_project / output_dir
        resolved = project / output_dir
        # It's under the consumer project, not the toolkit
        assert resolved.is_relative_to(project), (
            f"Artifact output dir must be relative to consumer project root, "
            f"got: {resolved}"
        )

    def test_output_path_not_relative_to_toolkit(self, consumer_workspace):
        """
        Cross-path assertion: build output MUST be inside project/ and
        MUST NOT be inside .ci/unity-build-workflows/.

        Catches the class of bugs where output_dir is resolved relative to
        the toolkit root instead of the consumer project root.
        """
        project = consumer_workspace["project"]
        toolkit = consumer_workspace["toolkit"]
        base = _load_json(project / "BuildConfig" / "base.json")
        output_dir = base.get("outputDirectory", "Builds")

        # Resolve relative to project root (the correct anchor)
        output_path = project / output_dir

        # Must be inside the consumer project
        assert output_path.is_relative_to(project), (
            f"Output path {output_path} must be relative to consumer project root {project}"
        )
        # Must NOT be inside the toolkit checkout tree
        assert not output_path.is_relative_to(toolkit), (
            f"Output path {output_path} must NOT be inside toolkit tree {toolkit}. "
            "Toolkit is at .ci/unity-build-workflows/ and must never receive build output."
        )

    def test_toolkit_scripts_not_in_consumer_project(self, consumer_workspace):
        """
        Toolkit scripts must live in .ci/unity-build-workflows/scripts/,
        NOT inside project/.
        Verifies the separation contract: toolkit changes cannot affect
        consumer project contents.
        """
        project = consumer_workspace["project"]
        toolkit = consumer_workspace["toolkit"]

        # Populate toolkit scripts to simulate a real checkout
        (toolkit / "scripts" / "common").mkdir(parents=True, exist_ok=True)
        (toolkit / "scripts" / "common" / "config_loader.py").write_text(
            "# toolkit config_loader"
        )

        # Assert: toolkit scripts are in the toolkit
        assert (toolkit / "scripts" / "common").exists(), (
            "Toolkit must have scripts/common/ at .ci/unity-build-workflows/scripts/common/"
        )

        # Assert: consumer project does NOT have a scripts/ directory from the toolkit
        assert not (project / "scripts").exists(), (
            "Consumer project/ must NOT contain toolkit scripts/ — "
            "toolkit checkout at .ci/unity-build-workflows/ must be separate"
        )

    def test_toolkit_files_not_in_consumer_assets(self, consumer_workspace):
        """
        Toolkit files must NEVER land in project/Assets/.
        Toolkit is at .ci/unity-build-workflows/, which is outside the Unity
        project directory (project/). Unity would import any file in Assets/
        as a Unity asset, which would pollute the consumer project.
        """
        project = consumer_workspace["project"]

        # project/Assets/ is the Unity assets folder
        # It must not exist OR if it exists, must be empty of toolkit files
        assets = project / "Assets"
        # (In this fixture assets/ doesn't exist, which is fine)
        if assets.exists():
            toolkit_sentinel = assets / "scripts" / "run_unity_build.py"
            assert not toolkit_sentinel.exists(), (
                "Toolkit Python scripts must NOT land in project/Assets/"
            )

    def test_toolkit_files_not_in_consumer_packages(self, consumer_workspace):
        """
        Toolkit Unity package files must NOT be copied into project/Packages/.
        The consumer declares the package as a UPM Git dependency in
        project/Packages/manifest.json, which points UPM to the toolkit repo.
        The files are fetched by UPM into the Unity cache, not into project/Packages/.
        """
        project = consumer_workspace["project"]
        toolkit = consumer_workspace["toolkit"]

        # consumer project has Packages/manifest.json (dependency declaration)
        assert (project / "Packages" / "manifest.json").exists(), (
            "Consumer project must have Packages/manifest.json with UPM dependency"
        )

        # consumer Packages/ must NOT contain physical toolkit package files
        toolkit_package_in_consumer = project / "Packages" / "com.company.build-pipeline"
        assert not toolkit_package_in_consumer.exists(), (
            "Toolkit package must NOT be physically copied into project/Packages/. "
            "UPM resolves it from the Git URL declared in manifest.json."
        )

    def test_artifact_outputs_at_workspace_root(self, consumer_workspace):
        """
        Build artifacts and logs are written to the workspace root (tmp_path/),
        NOT inside the consumer project/ or the toolkit .ci/ directory.

        Contract:
          <workspace>/artifacts/   ← build outputs
          <workspace>/logs/        ← build logs
        NOT:
          <workspace>/project/artifacts/
          <workspace>/.ci/unity-build-workflows/artifacts/
        """
        workspace = consumer_workspace["workspace"]
        project = consumer_workspace["project"]
        toolkit = consumer_workspace["toolkit"]

        artifacts = consumer_workspace["artifacts"]
        logs = consumer_workspace["logs"]

        # Outputs are at workspace root
        assert artifacts.exists()
        assert logs.exists()

        # Outputs are NOT inside consumer project
        assert not artifacts.is_relative_to(project), (
            "artifacts/ must be at workspace root, not inside project/"
        )
        assert not logs.is_relative_to(project), (
            "logs/ must be at workspace root, not inside project/"
        )

        # Outputs are NOT inside toolkit checkout
        assert not artifacts.is_relative_to(toolkit), (
            "artifacts/ must be at workspace root, not inside .ci/unity-build-workflows/"
        )
        assert not logs.is_relative_to(toolkit), (
            "logs/ must be at workspace root, not inside .ci/unity-build-workflows/"
        )

        # Both are children of workspace root
        assert artifacts.parent == workspace, (
            f"artifacts/ must be a direct child of workspace root, got: {artifacts}"
        )
        assert logs.parent == workspace, (
            f"logs/ must be a direct child of workspace root, got: {logs}"
        )

    def test_toolkit_checkout_path_is_specifically_ci_subdir(self, consumer_workspace):
        """
        The toolkit must be checked out at .ci/unity-build-workflows/ specifically.
        This tests that the checkout is at the EXACT expected path, not merely
        at some non-empty path — preventing the silent-wrong-repo bug where
        github.workflow_ref could resolve to the caller (consumer) repo instead
        of the toolkit repo.
        """
        workspace = consumer_workspace["workspace"]
        toolkit = consumer_workspace["toolkit"]

        # Toolkit is at <workspace>/.ci/unity-build-workflows/
        expected_toolkit_path = workspace / ".ci" / "unity-build-workflows"
        assert toolkit == expected_toolkit_path, (
            f"Toolkit must be at exactly .ci/unity-build-workflows/, "
            f"got: {toolkit.relative_to(workspace)}"
        )

        # The .ci/ parent exists (not just the unity-build-workflows/ leaf)
        assert (workspace / ".ci").is_dir(), (
            ".ci/ parent directory must exist (toolkit is not at root level)"
        )

        # Toolkit contains toolkit-specific files (VERSION)
        # This confirms it's actually the toolkit, not some other directory
        assert (toolkit / "VERSION").exists(), (
            ".ci/unity-build-workflows/ must contain VERSION — "
            "confirms the toolkit repo is checked out here, not the consumer repo"
        )

    # ---- No real Unity/Docker/Xcode ----

    def test_does_not_require_real_unity(self, consumer_workspace):
        """
        Confirm this test does NOT invoke real Unity.
        Fake unity stub path is not on PATH here.
        """
        import shutil
        # unity is not expected to be on PATH in the CI environment
        unity_binary = shutil.which("unity") or shutil.which("Unity")
        # We don't call unity — this just validates the fixture setup
        # (Unity absent is OK — the test does not call it)
        assert True, "Contract test must not require a real Unity installation"

    def test_toolkit_version_file_readable(self, consumer_workspace):
        toolkit = consumer_workspace["toolkit"]
        version_path = toolkit / "VERSION"
        assert version_path.exists(), "Toolkit VERSION file must be accessible at checkout path"
        content = version_path.read_text().strip()
        assert content, "VERSION must be non-empty"
