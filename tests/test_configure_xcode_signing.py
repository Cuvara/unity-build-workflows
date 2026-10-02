"""
scripts/ios/configure_xcode_signing.py and the pipeline's iOS signing steps.

The pipeline signed iOS with inputs ios-setup-signing never declared
(`certificate-base64` for `ios-distribution-certificate-base64`, ...) and with
no team or bundle ID at all, so every release IPA failed to sign. These tests
pin the step wiring and the script that now supplies team, bundle ID and
profile from the provisioning profile.
"""

import plistlib
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "ios" / "configure_xcode_signing.py"
BUILD_PLATFORM = REPO_ROOT / ".github" / "workflows" / "reusable-build-platform.yml"
SETUP_ACTION = REPO_ROOT / ".github" / "actions" / "ios-setup-signing" / "action.yml"
ARCHIVE_ACTION = REPO_ROOT / ".github" / "actions" / "ios-archive-export" / "action.yml"
CLEANUP_ACTION = REPO_ROOT / ".github" / "actions" / "ios-cleanup-signing" / "action.yml"

TEAM = "AB12CD34EF"
BUNDLE = "com.example.game"


def profile(app_id=f"{TEAM}.{BUNDLE}", devices=None, task_allow=False, name="Game App Store"):
    data = {
        "Name": name,
        "UUID": "11111111-2222-3333-4444-555555555555",
        "TeamIdentifier": [TEAM],
        "Entitlements": {"application-identifier": app_id, "get-task-allow": task_allow},
    }
    if devices:
        data["ProvisionedDevices"] = devices
    return data


def pbxproj(bundle=BUNDLE):
    """Minimal Unity-shaped project: app target + UnityFramework, two configs each."""
    def configs(prefix, settings):
        return {f"{prefix}-{c}": {"isa": "XCBuildConfiguration", "name": c,
                                  "buildSettings": dict(settings)}
                for c in ("Debug", "Release")}
    app_configs = configs("APPCFG", {"PRODUCT_BUNDLE_IDENTIFIER": bundle,
                                     "CODE_SIGN_STYLE": "Automatic"})
    fw_configs = configs("FWCFG", {"PROVISIONING_PROFILE_SPECIFIER": "stale"})
    objects = {
        "APP": {"isa": "PBXNativeTarget", "name": "Unity-iPhone", "buildConfigurationList": "APPLIST"},
        "FW": {"isa": "PBXNativeTarget", "name": "UnityFramework", "buildConfigurationList": "FWLIST"},
        "APPLIST": {"isa": "XCConfigurationList", "buildConfigurations": sorted(app_configs)},
        "FWLIST": {"isa": "XCConfigurationList", "buildConfigurations": sorted(fw_configs)},
        **app_configs, **fw_configs,
    }
    return {"archiveVersion": "1", "objectVersion": "54", "objects": objects, "rootObject": "APP"}


def run(tmp_path, prof, project, export_method=""):
    proj_dir = tmp_path / "xcode"
    (proj_dir / "Unity-iPhone.xcodeproj").mkdir(parents=True)
    pbx = proj_dir / "Unity-iPhone.xcodeproj" / "project.pbxproj"
    pbx.write_bytes(plistlib.dumps(project))
    prof_path = tmp_path / "profile.plist"
    prof_path.write_bytes(plistlib.dumps(prof))
    cmd = [sys.executable, str(SCRIPT), "--project-dir", str(proj_dir),
           "--profile-plist", str(prof_path)]
    if export_method:
        cmd += ["--export-method", export_method]
    result = subprocess.run(cmd, capture_output=True, text=True)
    outputs = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    written = plistlib.loads(pbx.read_bytes()) if result.returncode == 0 else None
    return result, outputs, written


def settings_of(project, list_key):
    objects = project["objects"]
    return [objects[c]["buildSettings"] for c in objects[list_key]["buildConfigurations"]]


class TestConfigureXcodeSigning:
    def test_outputs_come_from_the_profile_and_project(self, tmp_path):
        result, out, _ = run(tmp_path, profile(), pbxproj())
        assert result.returncode == 0, result.stderr
        assert out["development-team"] == TEAM
        assert out["bundle-identifier"] == BUNDLE
        assert out["profile-name"] == "Game App Store"
        assert out["export-method"] == "app-store"

    def test_app_target_gets_manual_signing(self, tmp_path):
        _, _, project = run(tmp_path, profile(), pbxproj())
        for s in settings_of(project, "APPLIST"):
            assert s["CODE_SIGN_STYLE"] == "Manual"
            assert s["DEVELOPMENT_TEAM"] == TEAM
            assert s["PROVISIONING_PROFILE_SPECIFIER"] == "Game App Store"
            assert s["CODE_SIGN_IDENTITY"] == "iPhone Distribution"

    def test_framework_gets_team_but_no_profile(self, tmp_path):
        # A framework target with a provisioning profile fails the archive.
        _, _, project = run(tmp_path, profile(), pbxproj())
        for s in settings_of(project, "FWLIST"):
            assert s["DEVELOPMENT_TEAM"] == TEAM
            assert "PROVISIONING_PROFILE_SPECIFIER" not in s

    def test_wildcard_profile_accepts_the_project_bundle(self, tmp_path):
        result, out, _ = run(tmp_path, profile(app_id=f"{TEAM}.com.example.*"), pbxproj())
        assert result.returncode == 0, result.stderr
        assert out["bundle-identifier"] == BUNDLE

    def test_bundle_mismatch_fails_with_a_reason(self, tmp_path):
        result, _, _ = run(tmp_path, profile(app_id=f"{TEAM}.com.other.app"), pbxproj())
        assert result.returncode != 0
        assert "does not match the provisioning profile" in result.stderr

    @pytest.mark.parametrize("devices,task_allow,method", [
        (None, False, "app-store"),
        (["udid"], False, "ad-hoc"),
        (["udid"], True, "development"),
    ])
    def test_export_method_from_profile(self, tmp_path, devices, task_allow, method):
        _, out, _ = run(tmp_path, profile(devices=devices, task_allow=task_allow), pbxproj())
        assert out["export-method"] == method

    def test_export_method_mismatch_fails_early(self, tmp_path):
        result, _, _ = run(tmp_path, profile(devices=["udid"]), pbxproj(), export_method="app-store")
        assert result.returncode != 0
        assert "ad-hoc profile" in result.stderr

    def test_missing_team_fails(self, tmp_path):
        prof = profile()
        prof["TeamIdentifier"] = []
        result, _, _ = run(tmp_path, prof, pbxproj())
        assert result.returncode != 0
        assert "TeamIdentifier" in result.stderr


# ---------------------------------------------------------------------------
# Step wiring: every `with:` key must be an input the action declares.
# ---------------------------------------------------------------------------

def _steps():
    return yaml.safe_load(BUILD_PLATFORM.read_text(encoding="utf-8"))["jobs"]["build"]["steps"]


def _step(name):
    return next(s for s in _steps() if s.get("name") == name)


@pytest.mark.parametrize("step_name,action", [
    ("iOS — Setup signing", SETUP_ACTION),
    ("iOS — Archive and export IPA", ARCHIVE_ACTION),
    ("iOS — Cleanup signing", CLEANUP_ACTION),
])
def test_ios_steps_pass_only_declared_inputs(step_name, action):
    declared = set(yaml.safe_load(action.read_text(encoding="utf-8"))["inputs"])
    passed = set(_step(step_name)["with"])
    assert passed <= declared, f"{step_name} passes undeclared inputs: {sorted(passed - declared)}"


def test_setup_signing_receives_team_and_bundle():
    with_ = _step("iOS — Setup signing")["with"]
    assert with_["development-team"] == "${{ steps.ios-xcode-signing.outputs.development-team }}"
    assert with_["bundle-identifier"] == "${{ steps.ios-xcode-signing.outputs.bundle-identifier }}"
    assert with_["ios-distribution-certificate-base64"] == "${{ secrets.IOS_DISTRIBUTION_CERTIFICATE_BASE64 }}"


def test_xcode_signing_runs_before_setup_and_archive():
    names = [s.get("name") for s in _steps()]
    order = ["iOS — Find Xcode project", "iOS — Configure Xcode signing",
             "iOS — Setup signing", "iOS — Archive and export IPA"]
    assert [names.index(n) for n in order] == sorted(names.index(n) for n in order)
