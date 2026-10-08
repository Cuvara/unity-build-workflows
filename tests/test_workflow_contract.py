"""
Tests for GitHub Actions workflow YAML contract.

Covers:
- Parsing all workflow YAMLs in .github/workflows/
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
# No @main references
# ---------------------------------------------------------------------------

class TestNoMainReferences:

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

    def _job_has_always(self, job_def: dict) -> bool:
        return "always()" in str(job_def.get("if", ""))

    # The workflows a consumer runs: the pipeline, its reusable callees and
    # the promotion pipelines. (scan/build image workflows are maintenance.)
    PIPELINE_GLOBS = ("unity-pipeline.yml", "reusable-*.yml", "pipeline-*-release.yml")

    def test_log_and_report_uploads_in_the_pipeline_have_always(self, workflows_dir):
        paths = sorted({p for g in self.PIPELINE_GLOBS for p in workflows_dir.glob(g)})
        assert paths, "no pipeline workflows found"
        for path in paths:
            doc = load_workflow(path)
            for job_name, job_def in (doc.get("jobs") or {}).items():
                job_always = self._job_has_always(job_def)
                for step in (job_def.get("steps") or []):
                    name = (step.get("name") or "").lower()
                    if "upload-artifact" not in str(step.get("uses", "")):
                        continue
                    if not any(k in name for k in ("log", "report", "result")):
                        continue
                    assert job_always or "always()" in str(step.get("if", "")), (
                        f"{path.name}: '{step.get('name')}' in job '{job_name}' is not "
                        f"protected by if: always()"
                    )


# ---------------------------------------------------------------------------
# Docker-only contract
# ---------------------------------------------------------------------------

# Inputs that were valid before Docker migration but must NOT exist after it.
FORBIDDEN_EXECUTOR_INPUTS = ("executor-mode", "use-docker", "native-runner")

# Actions that belong to the native (pre-Docker) runner — forbidden post-migration.
FORBIDDEN_ACTIONS = ("setup-unity",)

# Workflow files that must NOT exist after Docker migration (native-only platforms).
FORBIDDEN_WORKFLOW_FILES = ("unity-build-windows.yml",)

# Workflow that MUST exist after migration (image build pipeline).
REQUIRED_WORKFLOW_FILES = ("build-unity-image.yml",)

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

    # Root-anchored on purpose, and not project files: the runner policy and the
    # Discord thread config are repository-level files
    # (`.github/unity-runner-policy.json` / `.github/discord.json`, or
    # RUNNER_POLICY_FILE / DISCORD_CONFIG_FILE), resolved from the repository
    # root by contract.
    REPO_ROOT_FILES = ("RUNNER_POLICY_FILE", "DISCORD_CONFIG_FILE")

    def _sparse_patterns(self, workflow: dict):
        for step in iter_steps(workflow):
            if (step.get("with") or {}).get("repository"):
                # Another repository (the toolkit): its layout is fixed and
                # has nothing to do with the consumer's project-path.
                continue
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
                if any(name in pattern for name in self.REPO_ROOT_FILES):
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


# ---------------------------------------------------------------------------
# submodule-auth must reach every job that checks the project out
# ---------------------------------------------------------------------------

class TestSubmoduleAuthIsForwarded:
    """
    `submodule-auth` only works if every job that checks the consumer repository
    out receives it. A new build job that forgets the passthrough silently falls
    back to the token lane, and a private submodule in another organization
    fails there with a misleading "repository not found".
    """

    def test_pipeline_forwards_submodule_auth_to_every_callee_that_takes_it(self, workflows_dir):
        pipeline = load_workflow(workflows_dir / "unity-pipeline.yml")
        missing = []
        for job_id, job in pipeline["jobs"].items():
            uses = job.get("uses")
            if not uses or not uses.startswith("./"):
                continue
            callee = load_workflow(workflows_dir / Path(uses).name)
            declared = get_triggers(callee)["workflow_call"]["inputs"]
            if "submodule-auth" not in declared:
                continue
            if "submodule-auth" not in (job.get("with") or {}):
                missing.append(f"  {job_id} -> {Path(uses).name}")

        assert not missing, (
            "These jobs call a workflow that accepts submodule-auth but do not pass it:\n"
            + "\n".join(missing)
        )

    def test_ssh_lane_disables_the_checkout_action_submodule_fetch(self, workflows_dir):
        """Both lanes must be mutually exclusive, or checkout re-rewrites the URLs."""
        for name in ("reusable-build-platform.yml", "reusable-unity-tests.yml"):
            workflow = load_workflow(workflows_dir / name)
            declared = get_triggers(workflow)["workflow_call"]["inputs"]
            assert "submodule-auth" in declared, f"{name} does not declare submodule-auth"

            checkouts = [
                s for s in iter_steps(workflow)
                if str(s.get("uses", "")).startswith("actions/checkout")
                and "submodules" in (s.get("with") or {})
            ]
            assert checkouts, f"{name} has no submodule checkout to gate"
            for step in checkouts:
                value = str(step["with"]["submodules"])
                assert "submodule-auth" in value, (
                    f"{name}: checkout still fetches submodules unconditionally ({value!r}); "
                    "the ssh lane would fetch them twice, the second time over HTTPS"
                )

            assert any(
                s.get("name") == "Fetch submodules over SSH" for s in iter_steps(workflow)
            ), f"{name} disables the checkout fetch but never fetches over SSH"


# ---------------------------------------------------------------------------
# The ssh submodule lane must force the checkout
# ---------------------------------------------------------------------------

class TestSshSubmoduleUpdateIsForced:
    """
    actions/checkout runs `git clean -ffdx` on a reused workspace, which empties a
    submodule's working tree while leaving its gitdir at the recorded commit. A
    plain `git submodule update` then sees the right SHA, concludes there is
    nothing to do, and leaves the directory empty — on a self-hosted runner the
    build proceeds against packages that have no package.json, and Unity fails
    with hundreds of unrelated-looking CS0246 errors.
    """

    def test_submodule_update_passes_force(self, workflows_dir):
        offenders = []
        for path in sorted(workflows_dir.glob("*.yml")):
            for step in iter_steps(load_workflow(path)):
                run = str(step.get("run") or "")
                if "submodule update" not in run:
                    continue
                for line in run.splitlines():
                    line = line.strip()
                    if not line.startswith("git submodule update"):
                        continue
                    if "--force" not in line and "-f " not in line:
                        offenders.append(f"  {path.name} :: {step.get('name')} :: {line}")

        assert not offenders, (
            "`git submodule update` without --force silently no-ops on a workspace "
            "that git clean has emptied:\n" + "\n".join(offenders)
        )
