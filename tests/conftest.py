"""
Shared pytest fixtures for unity-build-workflows test suite.
"""
import json
import os
import sys
from pathlib import Path

import pytest

# Repository root (one level up from tests/)
REPO_ROOT = Path(__file__).parent.parent
FIXTURES_DIR = Path(__file__).parent / "fixtures"
SCHEMA_PATH = REPO_ROOT / "schemas" / "unity-build-config.schema.json"
WORKFLOWS_DIR = REPO_ROOT / ".github" / "workflows"
SCRIPTS_COMMON = REPO_ROOT / "scripts" / "common"

# Make scripts importable
sys.path.insert(0, str(REPO_ROOT / "scripts" / "common"))


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def schema():
    """Load the Unity build config JSON schema once per session."""
    assert SCHEMA_PATH.exists(), f"Schema not found: {SCHEMA_PATH}"
    with SCHEMA_PATH.open() as f:
        return json.load(f)


@pytest.fixture(scope="session")
def schema_validator(schema):
    """Return a jsonschema Draft7Validator bound to the schema."""
    import jsonschema
    try:
        # jsonschema >= 4.18: use referencing library
        from jsonschema import Draft7Validator
        from referencing import Registry, Resource
        resource = Resource.from_contents(schema)
        registry = Registry().with_resource(SCHEMA_PATH.as_uri(), resource)
        return Draft7Validator(schema, registry=registry)
    except (ImportError, TypeError):
        # Fallback for older jsonschema
        resolver = jsonschema.RefResolver(
            base_uri=SCHEMA_PATH.as_uri(),
            referrer=schema,
        )
        return jsonschema.Draft7Validator(schema, resolver=resolver)


# ---------------------------------------------------------------------------
# Config fixtures
# ---------------------------------------------------------------------------

def _load_fixture(name: str) -> dict:
    path = FIXTURES_DIR / name
    assert path.exists(), f"Fixture not found: {path}"
    with path.open() as f:
        return json.load(f)


@pytest.fixture(scope="session")
def valid_base_config():
    return _load_fixture("valid_base_config.json")


@pytest.fixture(scope="session")
def valid_production_config():
    return _load_fixture("valid_production_config.json")


@pytest.fixture(scope="session")
def invalid_production_dev_build():
    return _load_fixture("invalid_production_dev_build.json")


@pytest.fixture(scope="session")
def invalid_empty_scenes():
    return _load_fixture("invalid_empty_scenes.json")


@pytest.fixture(scope="session")
def invalid_bundle_id():
    return _load_fixture("invalid_bundle_id.json")


@pytest.fixture(scope="session")
def minimal_config():
    return _load_fixture("minimal_config.json")


@pytest.fixture(scope="session")
def build_metadata_sample():
    return _load_fixture("build_metadata_sample.json")


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def schema_path():
    return SCHEMA_PATH


@pytest.fixture(scope="session")
def workflows_dir():
    return WORKFLOWS_DIR


@pytest.fixture(scope="session")
def repo_root():
    return REPO_ROOT


# ---------------------------------------------------------------------------
# Build matrix (stage 01 → stage 03/04)
# ---------------------------------------------------------------------------
# Stage 03 and 04 are matrix jobs whose matrix is computed by the
# `Resolve build matrix` step in unity-pipeline.yml. "Which platforms build?"
# is therefore answered by that bash, not by per-job `if:` expressions — so the
# tests run the real step rather than re-implementing it.

PLATFORM_ENV = {
    "Android": "SEL_ANDROID",
    "WebGL": "SEL_WEBGL",
    "Linux64": "SEL_LINUX64",
    "LinuxServer": "SEL_LINUXSERVER",
    "Windows64": "SEL_WINDOWS64",
    "iOS": "SEL_IOS",
}


def _matrix_step_script():
    import yaml
    with (WORKFLOWS_DIR / "unity-pipeline.yml").open() as fh:
        workflow = yaml.safe_load(fh)
    steps = workflow["jobs"]["resolve-config"]["steps"]
    for step in steps:
        if step.get("id") == "matrix":
            return step["run"]
    raise AssertionError("unity-pipeline.yml has no `matrix` step in resolve-config")


@pytest.fixture(scope="session")
def resolve_matrix():
    """Run the real matrix step; return its parsed $GITHUB_OUTPUT.

    Usage:
        out = resolve_matrix(["Android"])                 # single platform
        out = resolve_matrix(["Android", "WebGL"], environment="staging")
        out["build"]      -> [{'platform': 'Android', ...}, ...]
        out["validate"]   -> rows for platforms that have a stage-04 validator
    """
    import json
    import subprocess
    import tempfile

    script = _matrix_step_script()

    def run(platforms, environment="production", android_export="aab",
            build_type="", platform_input="All", run_number=42,
            build_number_offset=0):
        env = dict(os.environ)
        env["ENVIRONMENT"] = environment
        env["ANDROID_TYPE"] = android_export
        # build-type is its own axis; empty derives it from the environment.
        env["IN_BUILD_TYPE"] = build_type
        # `None` is the CI lane: validate and test, build nothing.
        env["IN_PLATFORM"] = platform_input
        # The store-facing build number, resolved once in stage 01.
        env["RUN_NUMBER"] = str(run_number)
        env["BUILD_NUMBER_OFFSET"] = str(build_number_offset)
        for platform, key in PLATFORM_ENV.items():
            env[key] = "true" if platform in platforms else "false"

        with tempfile.NamedTemporaryFile("w+", delete=False) as fh:
            output_path = fh.name
        env["GITHUB_OUTPUT"] = output_path
        try:
            proc = subprocess.run(
                ["bash", "-c", script], env=env, capture_output=True, text=True
            )
            assert proc.returncode == 0, (
                f"matrix step failed ({proc.returncode}):\n{proc.stderr}"
            )
            raw = {}
            for line in open(output_path).read().strip().split("\n"):
                if "=" in line:
                    key, _, value = line.partition("=")
                    raw[key] = value
        finally:
            os.unlink(output_path)

        raw["build"] = json.loads(raw["build-matrix"])
        raw["validate"] = json.loads(raw["validate-matrix"])
        raw["build_platforms"] = [r["platform"] for r in raw["build"]]
        raw["validate_platforms"] = [r["platform"] for r in raw["validate"]]
        raw["artifact_names"] = [r["artifact-name"] for r in raw["build"]]
        return raw

    return run
