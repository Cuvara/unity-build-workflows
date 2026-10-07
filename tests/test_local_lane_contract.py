"""What the self-hosted lanes actually hand to PlayerBuilder.

`PlayerBuilder.Build` is the default `-executeMethod` on both local lanes, and it
reads its instructions from the environment. Anything the lane does not export,
the builder cannot know — and PlayerBuilder's defaults are silent, so a missing
variable produces a plausible wrong artifact rather than an error.

Audited against a consumer's real `PlayerBuilder.cs` before its Mac runner was
registered, on the principle that the first run of a 337-line script that has
never executed should not also be the first time anyone checked the contract.
"""
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
REUSABLE = REPO_ROOT / ".github" / "workflows" / "reusable-build-platform.yml"
TEXT = REUSABLE.read_text(encoding="utf-8")
LANE = (REPO_ROOT / "scripts" / "build" / "run_unity_player.sh").read_text(encoding="utf-8")


def test_the_reusable_workflow_parses():
    assert yaml.safe_load(TEXT)


def _native_steps():
    steps = yaml.safe_load(TEXT)["jobs"]["build"]["steps"]
    return {s.get("id"): s for s in steps if s.get("id") in ("build-windows", "build-macos")}


def test_every_lane_tells_the_builder_which_android_artifact_to_make():
    """APK or AAB is not derivable inside the Editor.

    Only the docker lane exported `ANDROID_APP_BUNDLE`. On a self-hosted runner a
    release build therefore produced an APK while the artifact was named
    `release-android-aab` — the wrong thing wearing the right label, and silent,
    because PlayerBuilder defaults to APK when the variable is absent.
    The native lanes now share run_unity_player.sh, which owns the rule.
    """
    assert "export ANDROID_APP_BUNDLE=1" in LANE
    assert 'if [ "${EXPORT_TYPE}" = "aab" ]' in LANE
    for step_id, step in _native_steps().items():
        assert "run_unity_player.sh" in step["run"], step_id
        assert '--android-export-type "${{ inputs.android-export-type }}"' in step["run"], step_id
    docker = next(st for st in yaml.safe_load(TEXT)["jobs"]["build"]["steps"]
                  if st.get("id") == "build-docker-windows")["run"]
    # The Windows docker lane runs the same script, inside its container.
    assert "bash /workspace/.toolkit/scripts/build/docker_windows_container.sh" in docker
    container = (REPO_ROOT / "scripts" / "build" / "docker_windows_container.sh").read_text(encoding="utf-8")
    assert "/workspace/.toolkit/scripts/build/run_unity_player.sh" in container
    assert '--android-export-type "$ANDROID_EXPORT_TYPE"' in container
    assert '-e "ANDROID_EXPORT_TYPE=${{ inputs.android-export-type }}"' in docker


def test_the_windows_docker_container_prints_the_log_when_the_build_fails():
    """Under `set -e` the old inline script exited at the failed build, so the
    "Log file candidates" tail meant for exactly that case never printed."""
    container = (REPO_ROOT / "scripts" / "build" / "docker_windows_container.sh").read_text(encoding="utf-8")
    assert "|| BUILD_RC=$?" in container
    assert container.rstrip().endswith("exit $BUILD_RC")


@pytest.mark.parametrize("platform,target", [
    ("iOS", "iOS"),
    ("Android", "Android"),
    ("WebGL", "WebGL"),
    ("Windows64", "StandaloneWindows64"),
    ("Linux64", "StandaloneLinux64"),
    ("LinuxServer", "StandaloneLinux64"),
])
def test_the_native_lanes_map_every_platform_the_pipeline_can_select(platform, target):
    """A Mac with the modules installed builds the standalone targets too.

    The lane used to map iOS, Android and WebGL and hard-error on anything else,
    so a project with `Windows64` in `RELEASE_BUILD_PLATFORMS` broke the moment
    its runner became a Mac. One table now serves every native lane.
    """
    assert re.search(rf"^\s*{re.escape(platform)}\)(?:(?!;;).)*?TARGET={re.escape(target)}(?![A-Za-z0-9])", LANE, re.M | re.S), (
        f"{platform} is not mapped in run_unity_player.sh")


def test_the_linux_server_subtarget_survives_on_every_lane():
    """`LinuxServer` is StandaloneLinux64 plus a subtarget flag; dropping the flag
    builds a desktop player under a server artifact's name."""
    assert "-standaloneBuildSubtarget Server" in TEXT, "the docker resolver"
    assert "EXTRA=(-standaloneBuildSubtarget Server)" in LANE, "the native lanes"


def test_the_output_directory_is_the_one_the_upload_takes():
    """PlayerBuilder writes under BUILD_OUTPUT_DIR; the artifact upload takes
    `build/`. They have to be the same word."""
    assert "export BUILD_OUTPUT_DIR=build" in LANE, "native lanes"
    assert "path: build/" in TEXT, "the upload"


# ── Custom Android keystore on the native lanes ─────────────────────────────
# Found on a real self-hosted Mac: a project with Player Settings > Custom
# Keystore enabled fails in batchmode with "Can not sign the application ...
# please provide passwords!", because Unity never stores keystore passwords in
# the project. The passwords come in as optional secrets, reach ONLY the native
# build steps, and PlayerBuilder applies them in memory.

KEYSTORE_SECRETS = ("ANDROID_KEYSTORE_PASS", "ANDROID_KEY_PASS")
PIPELINE = REPO_ROOT / ".github" / "workflows" / "unity-pipeline.yml"
TEMPLATE = (REPO_ROOT / "unity-package/Packages/com.company.build-pipeline/Editor/Builders/PlayerBuilder.cs").read_text(encoding="utf-8")


def _on(doc):
    return doc.get("on", doc.get(True))


@pytest.mark.parametrize("secret", KEYSTORE_SECRETS)
def test_keystore_secrets_are_optional_inputs_of_both_workflows(secret):
    for path in (PIPELINE, REUSABLE):
        declared = _on(yaml.safe_load(path.read_text(encoding="utf-8")))["workflow_call"]["secrets"]
        assert secret in declared, f"{path.name} does not declare {secret}"
        assert declared[secret]["required"] is False, "optional: most projects have no custom keystore"


@pytest.mark.parametrize("secret", KEYSTORE_SECRETS)
def test_the_build_matrix_passes_the_keystore_secrets(secret):
    jobs = yaml.safe_load(PIPELINE.read_text(encoding="utf-8"))["jobs"]
    matrix_jobs = [j for j in jobs.values()
                   if str(j.get("uses", "")).endswith("reusable-build-platform.yml") and "strategy" in j]
    assert matrix_jobs, "expected the per-platform build matrix job"
    for job in matrix_jobs:
        assert job["secrets"][secret] == "${{ secrets.%s }}" % secret


def test_only_the_native_build_steps_see_the_keystore_passwords():
    steps = yaml.safe_load(TEXT)["jobs"]["build"]["steps"]
    holders = sorted(s.get("id", s.get("name")) for s in steps
                     if any(k in (s.get("env") or {}) for k in KEYSTORE_SECRETS))
    # redact-log-secrets needs the values to scrub them from Editor.log, into
    # which Unity dumps the environment when Gradle fails; it never expands them.
    assert holders == ["build-docker-windows", "build-macos", "build-windows", "redact-log-secrets"]
    for s in steps:
        for secret in KEYSTORE_SECRETS:
            assert "$" + secret not in str(s.get("run", "")) \
                and "%" + secret + "%" not in str(s.get("run", "")), \
                f"{s.get('name')} must not echo or expand {secret}; PlayerBuilder reads it"


def test_player_builder_applies_the_passwords_only_for_a_custom_keystore():
    assert "PlayerSettings.Android.useCustomKeystore" in TEMPLATE
    for secret in KEYSTORE_SECRETS:
        assert f'GetEnvironmentVariable("{secret}")' in TEMPLATE
    assert "PlayerSettings.Android.keystorePass = storePass" in TEMPLATE
    assert "PlayerSettings.Android.keyaliasPass" in TEMPLATE
    # Fails with the fix instead of Unity's "Can not sign the application".
    assert "ANDROID_KEYSTORE_PASS" in TEMPLATE[TEMPLATE.index("private static bool ApplyAndroidKeystorePasswords"):]
    # Passwords are never logged.
    assert "storePass}" not in TEMPLATE and "keyPass}" not in TEMPLATE
