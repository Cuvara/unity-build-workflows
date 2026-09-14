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


def test_the_reusable_workflow_parses():
    assert yaml.safe_load(TEXT)


def test_every_lane_tells_the_builder_which_android_artifact_to_make():
    """APK or AAB is not derivable inside the Editor.

    Only the docker lane exported `ANDROID_APP_BUNDLE`. On a self-hosted runner a
    release build therefore produced an APK while the artifact was named
    `release-android-aab` — the wrong thing wearing the right label, and silent,
    because PlayerBuilder defaults to APK when the variable is absent.
    """
    assert TEXT.count("ANDROID_APP_BUNDLE") >= 3, (
        "expected the docker lane plus both local lanes to export it; "
        f"found {TEXT.count('ANDROID_APP_BUNDLE')} occurrences"
    )
    # The bash lane and the batch lane each have their own syntax for it.
    assert "export ANDROID_APP_BUNDLE=1" in TEXT, "the macOS/Linux lane must export it"
    assert 'set "ANDROID_APP_BUNDLE=1"' in TEXT, "the Windows lane must set it"


@pytest.mark.parametrize("platform,target", [
    ("iOS", "iOS"),
    ("Android", "Android"),
    ("WebGL", "WebGL"),
    ("Windows64", "StandaloneWindows64"),
    ("Linux64", "StandaloneLinux64"),
    ("LinuxServer", "StandaloneLinux64"),
])
def test_the_macos_lane_maps_every_platform_the_pipeline_can_select(platform, target):
    """A Mac with the modules installed builds the standalone targets too.

    The lane used to map iOS, Android and WebGL and hard-error on anything else,
    so a project with `Windows64` in `RELEASE_BUILD_PLATFORMS` broke the moment
    its runner became a Mac.
    """
    macos_lane = TEXT[TEXT.index("Unsupported platform for macOS lane") - 2000:
                      TEXT.index("Unsupported platform for macOS lane")]
    assert re.search(rf"^\s*{re.escape(platform)}\)\s*BUILD_TARGET=\"{re.escape(target)}\"",
                     macos_lane, re.M), f"{platform} is not mapped on the macOS lane"


def test_the_linux_server_subtarget_survives_on_the_macos_lane():
    """`LinuxServer` is StandaloneLinux64 plus a subtarget flag; dropping the flag
    builds a desktop player under a server artifact's name."""
    assert "-standaloneBuildSubtarget Server" in TEXT
    assert TEXT.count("-standaloneBuildSubtarget Server") >= 2, (
        "the docker resolver and the macOS lane must both carry it"
    )


def test_the_output_directory_is_the_one_the_upload_takes():
    """PlayerBuilder writes under BUILD_OUTPUT_DIR; the artifact upload takes
    `build/`. They have to be the same word."""
    for marker in ('export BUILD_OUTPUT_DIR="build"', 'set "BUILD_OUTPUT_DIR=build"'):
        assert marker in TEXT, marker
