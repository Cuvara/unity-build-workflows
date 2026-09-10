"""
Tests for GitHub Actions workflow YAML contract.

Covers:
- Parsing all workflow YAMLs in .github/workflows/
- unity-build.yml has all required inputs with correct types and defaults
- Required secrets are documented (not hard-coded in env blocks)
- No @main references in reusable workflow calls
- if: always() on report/log upload steps

Note: PyYAML parses the bare key `on:` as boolean True, not the string "on".
All trigger access uses the get_triggers() helper to handle both variants.
"""
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_workflow(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f)


def get_triggers(doc: dict) -> dict:
    """
    Return the workflow triggers dict.
    PyYAML parses bare `on:` as True (boolean), not the string "on".
    Try both keys so tests work regardless of how the loader behaves.
    """
    return doc.get("on") or doc.get(True) or {}


def iter_steps(workflow: dict):
    """Yield every step dict across all jobs."""
    for job in (workflow.get("jobs") or {}).values():
        for step in (job.get("steps") or []):
            yield step


def iter_uses(workflow: dict):
    """Yield every 'uses' value (reusable workflow / action calls)."""
    for step in iter_steps(workflow):
        if "uses" in step:
            yield step["uses"]
    for job in (workflow.get("jobs") or {}).values():
        if "uses" in job:
            yield job["uses"]


def load_unity_build_workflow(workflows_dir: Path):
    """Load unity-build.yml, or return None if not yet created."""
    path = workflows_dir / "unity-build.yml"
    if not path.exists():
        return None
    return load_workflow(path)


# ---------------------------------------------------------------------------
# Workflow discovery
# ---------------------------------------------------------------------------

class TestWorkflowDiscovery:

    def test_workflows_dir_exists(self, workflows_dir):
        assert workflows_dir.exists(), f"Expected .github/workflows/ at {workflows_dir}"

    def test_at_least_one_workflow_exists(self, workflows_dir):
        ymls = list(workflows_dir.glob("*.yml")) + list(workflows_dir.glob("*.yaml"))
        assert ymls, "No workflow YAML files found in .github/workflows/"

    def test_all_workflow_files_are_valid_yaml(self, workflows_dir):
        for yml_path in sorted(workflows_dir.glob("*.yml")):
            try:
                doc = load_workflow(yml_path)
                assert isinstance(doc, dict), f"{yml_path.name} is not a YAML mapping"
            except yaml.YAMLError as exc:
                pytest.fail(f"YAML parse error in {yml_path.name}: {exc}")

    def test_workflow_files_have_name_field(self, workflows_dir):
        for yml_path in sorted(workflows_dir.glob("*.yml")):
            doc = load_workflow(yml_path)
            assert "name" in doc, f"{yml_path.name} is missing 'name' field"


# ---------------------------------------------------------------------------
# unity-build.yml required inputs
# ---------------------------------------------------------------------------

UNITY_BUILD_REQUIRED_INPUTS = {
    "project-path": {"type": "string"},
    "unity-version": {"type": "string", "required": True},
    "target-platform": {"type": "string", "required": True},
    "build-config-path": {"type": "string"},
    "environment": {"type": "string"},
    "cache-mode": {"type": "string"},
}


class TestUnityBuildWorkflowInputs:

    @pytest.fixture(autouse=True)
    def load_workflow(self, workflows_dir):
        doc = load_unity_build_workflow(workflows_dir)
        if doc is None:
            pytest.skip("unity-build.yml not yet implemented")
        self.doc = doc
        self.triggers = get_triggers(doc)
        self.inputs = self.triggers.get("workflow_call", {}).get("inputs", {}) or {}
        self.secrets = self.triggers.get("workflow_call", {}).get("secrets", {}) or {}

    def test_unity_build_workflow_is_reusable(self):
        assert "workflow_call" in self.triggers, \
            "unity-build.yml must use workflow_call trigger"

    def test_required_inputs_present(self):
        for input_name in UNITY_BUILD_REQUIRED_INPUTS:
            assert input_name in self.inputs, \
                f"Missing required input: '{input_name}'"

    def test_unity_version_is_required(self):
        unity_version = self.inputs.get("unity-version", {})
        assert unity_version.get("required") is True, \
            "unity-version input must be required: true"

    def test_build_target_is_required(self):
        build_target = self.inputs.get("target-platform", {})
        assert build_target.get("required") is True, \
            "target-platform input must be required: true"

    def test_input_types_are_string(self):
        for input_name, expected in UNITY_BUILD_REQUIRED_INPUTS.items():
            if input_name in self.inputs:
                actual_type = self.inputs[input_name].get("type")
                assert actual_type == expected["type"], \
                    f"Input '{input_name}' type should be '{expected['type']}', got '{actual_type}'"

    def test_project_path_has_default(self):
        project_path = self.inputs.get("project-path", {})
        assert "default" in project_path, "project-path should have a default value"

    def test_cache_mode_has_default(self):
        cache_mode = self.inputs.get("cache-mode", {})
        assert "default" in cache_mode, "cache-mode should have a default value"

    def test_all_inputs_have_descriptions(self):
        for input_name, definition in self.inputs.items():
            assert "description" in definition and definition["description"], \
                f"Input '{input_name}' is missing a description"


class TestUnityBuildWorkflowSecrets:

    @pytest.fixture(autouse=True)
    def load_workflow(self, workflows_dir):
        doc = load_unity_build_workflow(workflows_dir)
        if doc is None:
            pytest.skip("unity-build.yml not yet implemented")
        self.doc = doc
        self.triggers = get_triggers(doc)
        self.secrets = self.triggers.get("workflow_call", {}).get("secrets", {}) or {}

    def test_unity_license_secret_documented(self):
        assert "UNITY_LICENSE" in self.secrets, \
            "UNITY_LICENSE must be declared in workflow_call.secrets"

    def test_unity_license_is_required(self):
        unity_license = self.secrets.get("UNITY_LICENSE", {})
        assert unity_license.get("required") is True, \
            "UNITY_LICENSE secret must be required: true"

    def test_no_hardcoded_secrets_in_env_blocks(self):
        """Secrets must come from ${{ secrets.NAME }}, not literal values.
        Checks env: blocks in steps for secret-looking keys with bare literal values.
        """
        for step in iter_steps(self.doc):
            env_block = step.get("env") or {}
            for key, value in env_block.items():
                if not isinstance(value, str):
                    continue
                is_secret_key = any(
                    kw in key.upper()
                    for kw in ("LICENSE", "PASSWORD", "SECRET", "TOKEN",
                               "KEYSTORE", "CERTIFICATE", "KEY_PASS")
                )
                if is_secret_key:
                    assert value.startswith("${{"), (
                        f"Step '{step.get('name')}' env var '{key}' looks like "
                        f"a secret but is not a ${{{{ secrets.* }}}} expression: {value!r}"
                    )


# ---------------------------------------------------------------------------
# No @main references
# ---------------------------------------------------------------------------

class TestNoMainReferences:

    def test_no_at_main_in_unity_test(self, workflows_dir):
        path = workflows_dir / "unity-test.yml"
        if not path.exists():
            pytest.skip("unity-test.yml not present")
        doc = load_workflow(path)
        for ref in iter_uses(doc):
            assert "@main" not in ref, \
                f"@main reference found in unity-test.yml: {ref}"

    def test_no_at_main_in_unity_validate(self, workflows_dir):
        path = workflows_dir / "unity-validate.yml"
        if not path.exists():
            pytest.skip("unity-validate.yml not present")
        doc = load_workflow(path)
        for ref in iter_uses(doc):
            assert "@main" not in ref, \
                f"@main reference found in unity-validate.yml: {ref}"

    def test_no_at_main_in_unity_build(self, workflows_dir):
        doc = load_unity_build_workflow(workflows_dir)
        if doc is None:
            pytest.skip("unity-build.yml not yet implemented")
        for ref in iter_uses(doc):
            assert "@main" not in ref, \
                f"@main reference found in unity-build.yml: {ref}"

    def test_all_workflows_have_no_at_main(self, workflows_dir):
        for yml_path in sorted(workflows_dir.glob("*.yml")):
            doc = load_workflow(yml_path)
            for ref in iter_uses(doc):
                assert "@main" not in ref, \
                    f"@main reference found in {yml_path.name}: {ref}"


# ---------------------------------------------------------------------------
# if: always() on upload/report steps
# ---------------------------------------------------------------------------

class TestAlwaysConditionOnUploadSteps:
    """
    Upload and report steps must run even when earlier steps fail.
    This can be satisfied either at step level (if: always()) or at
    job level (if: always() on the containing job).
    """

    UPLOAD_STEP_KEYWORDS = (
        "upload-artifact", "upload_artifact", "upload-build-report",
        "test-reporter", "dorny/test-reporter",
    )

    def _is_upload_step(self, step: dict) -> bool:
        uses = step.get("uses", "") or ""
        name = (step.get("name", "") or "").lower()
        # "report" alone is too broad — "Report toolkit version" is a logging step,
        # not an artifact upload. Require "report" to co-occur with upload/test/build/artifact
        # to avoid false positives on diagnostic steps added by toolkit setup.
        is_report_upload = "report" in name and any(
            k in name for k in ("upload", "test", "build", "artifact", "result")
        )
        return (
            any(kw in uses for kw in self.UPLOAD_STEP_KEYWORDS)
            or "upload" in name
            or is_report_upload
        )

    def _job_has_always(self, job_def: dict) -> bool:
        return "always()" in str(job_def.get("if", ""))

    def _check_workflow_upload_steps(self, doc: dict, workflow_name: str):
        """Assert every upload/report step is covered by always() at step or job level."""
        for job_name, job_def in (doc.get("jobs") or {}).items():
            job_always = self._job_has_always(job_def)
            for step in (job_def.get("steps") or []):
                if self._is_upload_step(step):
                    step_always = "always()" in str(step.get("if", ""))
                    assert job_always or step_always, (
                        f"{workflow_name}: upload/report step "
                        f"'{step.get('name', step.get('uses'))}' in job '{job_name}' "
                        f"is not protected by if: always() at step or job level"
                    )

    def test_upload_steps_in_test_workflow_have_always(self, workflows_dir):
        path = workflows_dir / "unity-test.yml"
        if not path.exists():
            pytest.skip("unity-test.yml not present")
        self._check_workflow_upload_steps(load_workflow(path), "unity-test.yml")

    def test_upload_steps_in_validate_workflow_have_always(self, workflows_dir):
        path = workflows_dir / "unity-validate.yml"
        if not path.exists():
            pytest.skip("unity-validate.yml not present")
        self._check_workflow_upload_steps(load_workflow(path), "unity-validate.yml")

    def test_upload_steps_in_build_workflow_have_always(self, workflows_dir):
        doc = load_unity_build_workflow(workflows_dir)
        if doc is None:
            pytest.skip("unity-build.yml not yet implemented")
        self._check_workflow_upload_steps(doc, "unity-build.yml")


# ---------------------------------------------------------------------------
# Existing workflow specific checks
# ---------------------------------------------------------------------------

class TestExistingWorkflowContracts:

    def test_unity_test_has_workflow_call_trigger(self, workflows_dir):
        path = workflows_dir / "unity-test.yml"
        if not path.exists():
            pytest.skip("unity-test.yml not present")
        doc = load_workflow(path)
        triggers = get_triggers(doc)
        assert "workflow_call" in triggers

    def test_unity_test_unity_version_input_required(self, workflows_dir):
        path = workflows_dir / "unity-test.yml"
        if not path.exists():
            pytest.skip("unity-test.yml not present")
        doc = load_workflow(path)
        triggers = get_triggers(doc)
        inputs = triggers.get("workflow_call", {}).get("inputs", {})
        assert "unity-version" in inputs
        assert inputs["unity-version"].get("required") is True

    def test_unity_test_has_unity_license_secret(self, workflows_dir):
        path = workflows_dir / "unity-test.yml"
        if not path.exists():
            pytest.skip("unity-test.yml not present")
        doc = load_workflow(path)
        triggers = get_triggers(doc)
        secrets = triggers.get("workflow_call", {}).get("secrets", {})
        assert "UNITY_LICENSE" in secrets

    def test_unity_validate_has_workflow_call_trigger(self, workflows_dir):
        path = workflows_dir / "unity-validate.yml"
        if not path.exists():
            pytest.skip("unity-validate.yml not present")
        doc = load_workflow(path)
        triggers = get_triggers(doc)
        assert "workflow_call" in triggers

    def test_unity_validate_target_platform_required(self, workflows_dir):
        path = workflows_dir / "unity-validate.yml"
        if not path.exists():
            pytest.skip("unity-validate.yml not present")
        doc = load_workflow(path)
        triggers = get_triggers(doc)
        inputs = triggers.get("workflow_call", {}).get("inputs", {})
        assert "target-platform" in inputs
        assert inputs["target-platform"].get("required") is True

    def test_unity_test_outputs_tests_passed(self, workflows_dir):
        path = workflows_dir / "unity-test.yml"
        if not path.exists():
            pytest.skip("unity-test.yml not present")
        doc = load_workflow(path)
        triggers = get_triggers(doc)
        outputs = triggers.get("workflow_call", {}).get("outputs", {})
        assert "tests-passed" in outputs, \
            "unity-test.yml must expose 'tests-passed' output"


# ---------------------------------------------------------------------------
# Docker-only contract
# ---------------------------------------------------------------------------

# Inputs that were valid before Docker migration but must NOT exist after it.
FORBIDDEN_EXECUTOR_INPUTS = ("executor-mode", "use-docker", "native-runner")

# Actions that belong to the native (pre-Docker) runner — forbidden post-migration.
FORBIDDEN_ACTIONS = ("setup-unity",)

# Workflow files that must NOT exist after Docker migration (native-only platforms).
FORBIDDEN_WORKFLOW_FILES = ("unity-build-ios.yml", "unity-build-windows.yml")

# Workflow that MUST exist after migration (image build pipeline).
REQUIRED_WORKFLOW_FILES = ("build-unity-image.yml",)

# Keyword that indicates a step is a Docker container run (post-migration contract).
DOCKER_RUN_INDICATORS = (
    "run-unity-container",
    "run_unity_container",
    "docker run",
    "scripts/docker/",
)


def _all_workflow_inputs(workflows_dir: Path) -> dict[str, dict]:
    """Return {workflow_filename: {input_name: definition}} for all workflows."""
    result: dict[str, dict] = {}
    for yml_path in sorted(workflows_dir.glob("*.yml")):
        doc = load_workflow(yml_path)
        triggers = get_triggers(doc)
        inputs = triggers.get("workflow_call", {}).get("inputs", {}) or {}
        if inputs:
            result[yml_path.name] = inputs
    return result


class TestDockerOnlyWorkflowContract:
    """
    Enforces the Docker-mandatory contract:
    - No legacy executor-mode / use-docker / native-runner inputs
    - No setup-unity action usage
    - Build/test workflows use run-unity-container
    - upload/report steps still have if: always()
    - Platform-specific impossible workflows (iOS, Windows) do not exist
    - Image build pipeline (build-unity-image.yml) exists
    """

    def test_no_executor_mode_input_in_any_workflow(self, workflows_dir):
        """executor-mode input was for hybrid mode — must be gone post-migration."""
        violations = []
        for wf_name, inputs in _all_workflow_inputs(workflows_dir).items():
            for forbidden in FORBIDDEN_EXECUTOR_INPUTS:
                if forbidden in inputs:
                    violations.append(f"{wf_name}: forbidden input '{forbidden}'")
        assert not violations, \
            "Forbidden executor-mode inputs found (Docker is now mandatory):\n" + \
            "\n".join(f"  {v}" for v in violations)

    def test_no_use_docker_input_in_any_workflow(self, workflows_dir):
        for wf_name, inputs in _all_workflow_inputs(workflows_dir).items():
            assert "use-docker" not in inputs, \
                f"{wf_name}: 'use-docker' input must not exist — Docker is mandatory"

    def test_no_native_runner_input_in_any_workflow(self, workflows_dir):
        for wf_name, inputs in _all_workflow_inputs(workflows_dir).items():
            assert "native-runner" not in inputs, \
                f"{wf_name}: 'native-runner' input must not exist — Docker is mandatory"

    def test_no_setup_unity_action_used(self, workflows_dir):
        """setup-unity is a native-runner action — must not appear post-migration."""
        violations = []
        for yml_path in sorted(workflows_dir.glob("*.yml")):
            doc = load_workflow(yml_path)
            for ref in iter_uses(doc):
                if "setup-unity" in ref:
                    violations.append(f"{yml_path.name}: uses '{ref}'")
        # Also check composite actions
        actions_dir = workflows_dir.parent / "actions"
        if actions_dir.exists():
            for action_yml in actions_dir.rglob("action.yml"):
                doc = load_workflow(action_yml)
                for ref in iter_uses(doc):
                    if "setup-unity" in ref:
                        rel = action_yml.relative_to(workflows_dir.parent.parent)
                        violations.append(f"{rel}: uses '{ref}'")
        assert not violations, \
            "setup-unity action found — it must be removed after Docker migration:\n" + \
            "\n".join(f"  {v}" for v in violations)

    def test_unity_build_ios_workflow_exists(self, workflows_dir):
        """
        iOS builds are supported via the macos-unity-xcode executor (v2.1.0+).
        unity-build-ios.yml must exist after the iOS pipeline was added.

        NOTE: This replaces the v2.0.0-era test that asserted iOS workflow must NOT
        exist (when iOS was unsupported). iOS is now supported via native macOS runner,
        not Docker. The workflow MUST exist for iOS builds to work.
        """
        ios_workflow = workflows_dir / "unity-build-ios.yml"
        assert ios_workflow.exists(), \
            "unity-build-ios.yml must exist (added in v2.1.0 for macOS/Xcode iOS pipeline). " \
            "iOS is no longer unsupported — it uses the macos-unity-xcode executor."

    def test_unity_build_windows_workflow_does_not_exist(self, workflows_dir):
        """Windows builds are not Docker-supported on Linux runners — workflow must be removed."""
        windows_workflow = workflows_dir / "unity-build-windows.yml"
        assert not windows_workflow.exists(), \
            "unity-build-windows.yml must NOT exist after Docker migration " \
            "(Windows target not supported in Linux Docker containers)"

    def test_build_unity_image_workflow_exists(self, workflows_dir):
        """The Docker image build pipeline must exist post-migration."""
        image_build_workflow = workflows_dir / "build-unity-image.yml"
        assert image_build_workflow.exists(), \
            "build-unity-image.yml must exist — it builds the Unity Docker images"

    def test_build_unity_image_workflow_is_valid_yaml(self, workflows_dir):
        path = workflows_dir / "build-unity-image.yml"
        if not path.exists():
            pytest.skip("build-unity-image.yml not yet created")
        doc = load_workflow(path)
        assert isinstance(doc, dict), "build-unity-image.yml must be a valid YAML mapping"
        assert "name" in doc, "build-unity-image.yml must have a 'name' field"

    def test_all_build_workflows_reference_docker_runner(self, workflows_dir):
        """
        All build/test workflows must reference run-unity-container or scripts/docker/
        somewhere in their steps, confirming Docker-only execution.
        """
        build_workflows = [
            "unity-build-android.yml",
            "unity-build-webgl.yml",
            "unity-test.yml",
        ]
        missing_docker = []
        for wf_name in build_workflows:
            path = workflows_dir / wf_name
            if not path.exists():
                continue  # Will be created by Task #5
            doc = load_workflow(path)
            # Gather all step 'run' and 'uses' text
            all_step_text = ""
            for step in iter_steps(doc):
                all_step_text += str(step.get("uses", "")) + " " + str(step.get("run", ""))
            has_docker = any(
                indicator in all_step_text
                for indicator in DOCKER_RUN_INDICATORS
            )
            if not has_docker:
                missing_docker.append(wf_name)

        assert not missing_docker, \
            "These build/test workflows do not reference Docker container execution:\n" + \
            "\n".join(f"  {wf}" for wf in missing_docker)

    def test_upload_steps_always_condition_in_build_android(self, workflows_dir):
        """Upload/report steps in unity-build-android.yml must have if: always()."""
        path = workflows_dir / "unity-build-android.yml"
        if not path.exists():
            pytest.skip("unity-build-android.yml not present")
        checker = TestAlwaysConditionOnUploadSteps()
        checker._check_workflow_upload_steps(load_workflow(path), "unity-build-android.yml")

    def test_upload_steps_always_condition_in_build_webgl(self, workflows_dir):
        """Upload/report steps in unity-build-webgl.yml must have if: always()."""
        path = workflows_dir / "unity-build-webgl.yml"
        if not path.exists():
            pytest.skip("unity-build-webgl.yml not present")
        checker = TestAlwaysConditionOnUploadSteps()
        checker._check_workflow_upload_steps(load_workflow(path), "unity-build-webgl.yml")


# ---------------------------------------------------------------------------
# Sparse checkout must respect project-path
# ---------------------------------------------------------------------------

class TestSparseCheckoutHonoursProjectPath:
    """
    A sparse-checkout pattern that contains a slash is anchored to the repository
    root in non-cone mode. Any workflow that checks out a consumer project file
    sparsely and then reads it under `inputs.project-path` must therefore either
    lead with '**/' or interpolate the project path — otherwise a project living
    in a subdirectory checks out nothing and the step fails.
    """

    def _sparse_patterns(self, workflow: dict):
        for step in iter_steps(workflow):
            patterns = (step.get("with") or {}).get("sparse-checkout")
            if patterns:
                for line in str(patterns).splitlines():
                    line = line.strip()
                    if line:
                        yield step.get("name", "<unnamed step>"), line

    def test_project_file_patterns_match_at_any_depth(self, workflows_dir):
        offenders = []
        for path in sorted(workflows_dir.glob("*.yml")):
            workflow = load_workflow(path)
            if not isinstance(workflow, dict):
                continue
            for step_name, pattern in self._sparse_patterns(workflow):
                if "/" not in pattern:
                    continue  # unanchored already: matches at any depth
                if pattern.startswith("**/") or "project-path" in pattern:
                    continue
                offenders.append(f"  {path.name} :: {step_name} :: {pattern}")

        assert not offenders, (
            "Root-anchored sparse-checkout patterns break a project-path in a "
            "subdirectory. Lead with '**/' or interpolate inputs.project-path:\n"
            + "\n".join(offenders)
        )

    def test_pipeline_checks_out_the_project_version_it_reads(self, workflows_dir):
        workflow = load_workflow(workflows_dir / "unity-pipeline.yml")
        patterns = [p for _, p in self._sparse_patterns(workflow)]
        assert "**/ProjectSettings/ProjectVersion.txt" in patterns, (
            "resolve-config reads ${PROJECT_PATH}/ProjectSettings/ProjectVersion.txt, "
            f"so it must sparsely check it out at any depth. Patterns found: {patterns}"
        )


# ---------------------------------------------------------------------------
# ProjectVersion.txt parsing must survive CRLF
# ---------------------------------------------------------------------------

class TestProjectVersionParsingIsCRLFSafe:
    """
    Unity writes `ProjectSettings/ProjectVersion.txt` with CRLF on Windows, so a
    consumer repository routinely commits it that way. Any shell reader that
    pulls the version out with `awk`/`cut` keeps the trailing CR unless it is
    stripped, and the result compares unequal to a clean string while printing
    identically — the failure is invisible in the log:

        ::error::Unity version mismatch:
        ::error::  ProjectVersion.txt: 6000.3.9f1
        ::error::  Pin (input/UNITY_VERSION var): 6000.3.9f1
    """

    SEARCH_DIRS = ("scripts", ".github/workflows")
    STRIPPERS = ("tr -d '\\r'", 'tr -d "\\r"', "tr -d '[:space:]'", "sed 's/\\r//'", "${_v//$'\\r'/}")

    def _reader_lines(self, repo_root: Path):
        for rel in self.SEARCH_DIRS:
            base = repo_root / rel
            if not base.exists():
                continue
            for path in sorted(base.rglob("*")):
                if path.suffix not in (".sh", ".yml", ".yaml") or not path.is_file():
                    continue
                lines = path.read_text(encoding="utf-8").splitlines()
                for i, line in enumerate(lines, start=1):
                    if "m_EditorVersion" not in line:
                        continue
                    # the extraction may wrap onto the following line
                    window = " ".join(lines[i - 1:i + 1])
                    yield path.relative_to(repo_root), i, window

    def test_every_shell_reader_strips_carriage_returns(self, repo_root):
        offenders = []
        for rel, lineno, window in self._reader_lines(repo_root):
            if not any(op in window for op in ("awk", "cut", "sed")):
                continue  # not an extraction
            if any(s in window for s in self.STRIPPERS):
                continue
            offenders.append(f"  {rel}:{lineno}")

        assert not offenders, (
            "These read the Unity version out of ProjectVersion.txt without "
            "stripping CR, so a CRLF file yields a version with a trailing \\r:\n"
            + "\n".join(offenders)
        )
