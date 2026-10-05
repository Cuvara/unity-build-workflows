"""The toolkit's Unity package is the project's build script.

A project needs no PlayerBuilder / AddressableBuilder of its own: the build
job copies unity-package/Packages/com.company.build-pipeline into the project's
Packages/ folder for one build (install_build_package.sh), runs the package's
builders, and removes the copy. docs/TOOLKIT_BUILD_PACKAGE.md
"""
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "common" / "install_build_package.sh"
PACKAGE = REPO_ROOT / "unity-package" / "Packages" / "com.company.build-pipeline"
EDITOR = PACKAGE / "Editor"
REUSABLE = REPO_ROOT / ".github" / "workflows" / "reusable-build-platform.yml"

PLAYER = "Company.BuildPipeline.Editor.PlayerBuilder.Build"
ADDRESSABLES = "Company.BuildPipeline.Editor.AddressableBuilder.Build"


# ── install_build_package.sh ────────────────────────────────────────────────

def _run(action, project, tmp_path, package=PACKAGE):
    args = ["bash", str(SCRIPT), action, "--project", str(project)]
    if action == "install":
        args += ["--package", str(package)]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "RUNNER_TEMP": str(tmp_path / "tmp")}
    r = subprocess.run(args, capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, r.stderr
    out = {}
    for line in r.stdout.splitlines():
        key, _, value = line.partition("=")
        out[key] = value
    return out


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "proj"
    (root / "Packages").mkdir(parents=True)
    (root / "Assets" / "Editor").mkdir(parents=True)
    (root / "Packages" / "manifest.json").write_text('{"dependencies": {}}')
    (root / "Packages" / "packages-lock.json").write_text("ORIGINAL")
    return root


def test_install_copies_the_package_without_its_tests(project, tmp_path):
    out = _run("install", project, tmp_path)
    copy = project / "Packages" / "com.company.build-pipeline"
    assert out["installed"] == "true"
    assert (copy / "package.json").is_file()
    assert (copy / "Editor" / "Builders" / "PlayerBuilder.cs").is_file()
    assert not (copy / "Tests").exists()
    # The manifest is never edited: Unity loads Packages/<name> as embedded.
    assert json.loads((project / "Packages" / "manifest.json").read_text()) == {"dependencies": {}}


def test_install_selects_the_package_builders(project, tmp_path):
    out = _run("install", project, tmp_path)
    assert out["player-method"] == PLAYER
    assert out["addressables-method"] == ADDRESSABLES


def test_remove_deletes_the_copy_and_restores_the_lock_file(project, tmp_path):
    _run("install", project, tmp_path)
    (project / "Packages" / "packages-lock.json").write_text("REWRITTEN BY UNITY")
    _run("remove", project, tmp_path)
    assert not (project / "Packages" / "com.company.build-pipeline").exists()
    assert (project / "Packages" / "packages-lock.json").read_text() == "ORIGINAL"


def test_a_project_player_builder_keeps_winning(project, tmp_path):
    (project / "Assets" / "Editor" / "PlayerBuilder.cs").write_text(
        "using UnityEditor;\npublic static class PlayerBuilder { public static void Build() {} }\n")
    out = _run("install", project, tmp_path)
    assert out["player-method"] == "PlayerBuilder.Build"
    assert out["addressables-method"] == ADDRESSABLES


def test_a_namespaced_class_of_the_same_name_does_not_count(project, tmp_path):
    (project / "Assets" / "Editor" / "PlayerBuilder.cs").write_text(
        "namespace Game.Tools {\n  public static class PlayerBuilder {}\n}\n")
    assert _run("install", project, tmp_path)["player-method"] == PLAYER


def test_a_project_that_references_the_package_keeps_its_own(project, tmp_path):
    (project / "Packages" / "manifest.json").write_text(
        '{"dependencies": {"com.company.build-pipeline": "https://example.invalid/x.git"}}')
    out = _run("install", project, tmp_path)
    assert out["installed"] == "false"
    assert not (project / "Packages" / "com.company.build-pipeline").exists()


def test_a_project_that_embeds_the_package_keeps_its_own(project, tmp_path):
    own = project / "Packages" / "com.company.build-pipeline"
    own.mkdir()
    (own / "package.json").write_text('{"name": "com.company.build-pipeline"}')
    assert _run("install", project, tmp_path)["installed"] == "false"
    _run("remove", project, tmp_path)
    assert (own / "package.json").is_file(), "remove must never delete a project's own copy"


def test_a_stale_copy_from_an_interrupted_run_is_replaced(project, tmp_path):
    _run("install", project, tmp_path)
    assert _run("install", project, tmp_path)["installed"] == "true"


# ── Workflow wiring ─────────────────────────────────────────────────────────

def _steps():
    return yaml.safe_load(REUSABLE.read_text(encoding="utf-8"))["jobs"]["build"]["steps"]


def _index(steps, step_id):
    return next(i for i, s in enumerate(steps) if s.get("id") == step_id)


def test_the_package_is_installed_on_every_lane_before_any_unity_step():
    steps = _steps()
    install = steps[_index(steps, "build-package")]
    assert "install_build_package.sh install" in install["run"]
    assert install["if"] == "${{ steps.ios-guard.outputs.blocked != 'true' }}"
    toolkit = steps[_index(steps, "preflight-toolkit")]
    assert toolkit["if"] == "${{ steps.ios-guard.outputs.blocked != 'true' }}", \
        "every lane needs the toolkit checkout the package is copied from"
    first_unity = min(_index(steps, i) for i in (
        "addressables-pre-docker", "addressables-pre-selfhosted", "build-docker-windows",
        "build-windows", "build-macos"))
    assert _index(steps, "preflight-toolkit") < _index(steps, "build-package") < first_unity


def test_the_copy_is_removed_even_when_the_build_fails():
    remove = next(s for s in _steps() if s.get("name") == "Remove toolkit build package")
    assert remove["if"].startswith("${{ always() && ")
    assert "steps.build-package.outputs.installed == 'true'" in remove["if"]
    assert "install_build_package.sh remove" in remove["run"]


def test_no_lane_hardcodes_a_build_method_default():
    text = REUSABLE.read_text(encoding="utf-8")
    player = "steps.build-package.outputs.player-method || 'PlayerBuilder.Build'"
    addressables = "steps.build-package.outputs.addressables-method || 'AddressableBuilder.Build'"
    assert text.count(player) == 3, "Windows docker, Windows native and bash lanes"
    assert text.count(addressables) == 5
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("::"):
            continue
        for bare in ('"PlayerBuilder.Build"', "-executeMethod AddressableBuilder.Build",
                     "buildMethod: AddressableBuilder.Build"):
            assert bare not in stripped, line


def test_the_windows_docker_lane_forwards_version_and_keystore_by_name():
    step = next(s for s in _steps() if s.get("id") == "build-docker-windows")
    for name in ("ANDROID_KEYSTORE_PASS", "ANDROID_KEY_PASS", "BUILD_NUMBER", "APP_VERSION"):
        assert name in step["env"], name
        assert f"-e {name}" in step["run"], f"{name} must be passed by name, never by value"


# ── The package itself ──────────────────────────────────────────────────────

def test_player_builder_is_namespaced_and_reads_the_ci_contract():
    src = (EDITOR / "Builders" / "PlayerBuilder.cs").read_text(encoding="utf-8")
    assert "namespace Company.BuildPipeline.Editor" in src
    assert "public static void Build()" in src
    # Inside Company.BuildPipeline.*, a bare BuildPipeline is the namespace.
    assert "UnityEditor.BuildPipeline.BuildPlayer(" in src
    for name in ("ANDROID_APP_BUNDLE", "BUILD_OUTPUT_DIR", "BUILD_NUMBER", "APP_VERSION",
                 "ANDROID_KEYSTORE_PASS", "ANDROID_KEY_PASS", "GITHUB_WORKSPACE"):
        assert f'"{name}"' in src, name
    assert "EditorApplication.Exit(code)" in src


def test_player_builder_accepts_the_workflows_app_bundle_value():
    """The lanes send ANDROID_APP_BUNDLE=1; the old project script only
    accepted "true", so self-hosted release builds came out as APKs."""
    src = (EDITOR / "Builders" / "PlayerBuilder.cs").read_text(encoding="utf-8")
    body = src[src.index("internal static bool IsTrue"):]
    assert 'v == "1"' in body and '"true"' in body


def test_addressables_code_compiles_only_with_addressables():
    asmdef = json.loads((EDITOR / "Company.BuildPipeline.Editor.asmdef").read_text(encoding="utf-8"))
    assert {"name": "com.unity.addressables", "expression": "1.0.0",
            "define": "BUILD_PIPELINE_ADDRESSABLES"} in asmdef["versionDefines"]
    assert "Unity.Addressables.Editor" in asmdef["references"]
    src = (EDITOR / "Builders" / "AddressableBuilder.cs").read_text(encoding="utf-8")
    assert src.count("#if BUILD_PIPELINE_ADDRESSABLES") == 2
    assert src.index("#if BUILD_PIPELINE_ADDRESSABLES") < src.index("using UnityEditor.AddressableAssets;")


def test_ios_post_processor_leaves_unconfigured_builds_alone():
    """Installed into every project now, so without BuildConfig it must not
    add a push entitlement the provisioning profile may lack."""
    src = (EDITOR / "PostProcess" / "IOSXcodePostProcessor.cs").read_text(encoding="utf-8")
    gate = src.index("if (!IOSBuildParameters.Configured)")
    assert gate < src.index("ModifyEntitlements(buildPath);")
    builder = (EDITOR / "PlatformBuilders" / "IOSBuilder.cs").read_text(encoding="utf-8")
    assert "IOSBuildParameters.Configured          = true;" in builder


@pytest.mark.parametrize("path", [
    "Editor/Builders.meta",
    "Editor/Builders/PlayerBuilder.cs.meta",
    "Editor/Builders/AddressableBuilder.cs.meta",
])
def test_new_files_have_meta_files_with_unique_guids(path):
    guids = {}
    for meta in PACKAGE.rglob("*.meta"):
        for line in meta.read_text(encoding="utf-8").splitlines():
            if line.startswith("guid: "):
                guids.setdefault(line[6:].strip(), []).append(meta)
    assert (PACKAGE / path).is_file()
    assert all(len(v) == 1 for v in guids.values()), "duplicate .meta GUIDs"
