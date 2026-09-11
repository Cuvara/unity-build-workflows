"""
Stage 03/04 artifact tooling:

  * scripts/common/artifact_manifest.py    — artifact identity + metadata
  * scripts/android/validate_android_artifact.py
  * scripts/android/axml.py                — compiled AndroidManifest reader
  * scripts/webgl/validate_webgl_artifact.py
  * scripts/ios/validate_xcode_project.py

Every fixture is synthesised here — no Unity, no Android SDK, no network — so
the checks that guard a release are themselves covered by the fast suite.
"""
import importlib.util
import json
import plistlib
import struct
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS = REPO_ROOT / "scripts"


def _load(module_name, relative_path):
    """Import a script by path — scripts/ is not a package."""
    path = SCRIPTS / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


artifact_manifest = _load("artifact_manifest", "common/artifact_manifest.py")
axml = _load("axml", "android/axml.py")
validate_android = _load("validate_android_artifact", "android/validate_android_artifact.py")
validate_webgl = _load("validate_webgl_artifact", "webgl/validate_webgl_artifact.py")
validate_xcode = _load("validate_xcode_project", "ios/validate_xcode_project.py")


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def make_apk(path, *, signed=True, with_dex=True, with_manifest=True, padding=2_000_000):
    """A zip shaped like an APK — enough for the structural checks."""
    with zipfile.ZipFile(path, "w") as zf:
        if with_manifest:
            zf.writestr("AndroidManifest.xml", b"\x00" * 64)
        if with_dex:
            zf.writestr("classes.dex", b"dex\n035\x00")
        zf.writestr("resources.arsc", b"\x00" * 32)
        if signed:
            zf.writestr("META-INF/CERT.RSA", b"\x30\x82fake-signature")
            zf.writestr("META-INF/MANIFEST.MF", b"Manifest-Version: 1.0\n")
        zf.writestr("assets/padding.bin", b"\x00" * padding)
    return path


def make_aab(path, *, signed=True, with_dex=True, padding=2_000_000):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("BundleConfig.pb", b"\x08\x01")
        zf.writestr("base/manifest/AndroidManifest.xml", b"\x00" * 64)
        if with_dex:
            zf.writestr("base/dex/classes.dex", b"dex\n035\x00")
        if signed:
            zf.writestr("META-INF/CERT.RSA", b"\x30\x82fake-signature")
        zf.writestr("base/assets/padding.bin", b"\x00" * padding)
    return path


def make_webgl(root, *, compression="none", with_index=True, streaming_assets=False,
               padding=2_000_000, mixed=False):
    """A directory shaped like a Unity WebGL player."""
    root.mkdir(parents=True, exist_ok=True)
    suffix = {"none": "", "gzip": ".gz", "brotli": ".br"}[compression]
    build = root / "Build"
    build.mkdir(exist_ok=True)
    roles = {
        "app.loader.js": b"createUnityInstance",
        "app.framework.js": b"framework",
        "app.data": b"\x00" * padding,
        "app.wasm": b"\x00asm",
    }
    for i, (name, content) in enumerate(roles.items()):
        # `mixed` leaves the loader uncompressed while the rest are compressed —
        # the shape that renders as a blank canvas in a browser.
        this_suffix = "" if (mixed and i == 0) else suffix
        (build / (name + this_suffix)).write_bytes(content)
    if with_index:
        (root / "index.html").write_text(
            "<html><script src='Build/app.loader.js'></script></html>"
        )
    if streaming_assets:
        (root / "StreamingAssets").mkdir(exist_ok=True)
        (root / "StreamingAssets" / "data.bin").write_bytes(b"\x00" * 16)
    return root


def make_xcode_export(root, *, bundle_id="com.example.game", version="1.2.3",
                      build_number="42", trees=("Classes", "Libraries", "Data"),
                      with_pbxproj=True, padding=2_000_000):
    root.mkdir(parents=True, exist_ok=True)
    proj = root / "Unity-iPhone.xcodeproj"
    proj.mkdir(exist_ok=True)
    if with_pbxproj:
        (proj / "project.pbxproj").write_text(
            "// !$*UTF8*$!\n{ isa = PBXProject; objects = { } }\n"
        )
    for tree in trees:
        (root / tree).mkdir(exist_ok=True)
        (root / tree / "payload.bin").write_bytes(b"\x00" * (padding // max(len(trees), 1)))
    with (root / "Info.plist").open("wb") as fh:
        plistlib.dump(
            {
                "CFBundleIdentifier": bundle_id,
                "CFBundleShortVersionString": version,
                "CFBundleVersion": build_number,
            },
            fh,
        )
    return root


def build_axml(attributes):
    """Assemble a minimal Android binary XML with a <manifest> element.

    Strings are UTF-16 (the flag-less encoding), which is what aapt emits for
    a real AndroidManifest.xml.
    """
    strings = ["manifest"] + list(attributes.keys())
    values = []
    for value in attributes.values():
        if isinstance(value, str):
            values.append(len(strings))
            strings.append(value)
        else:
            values.append(None)

    # ── String pool ──
    encoded, offsets = b"", []
    for s in strings:
        offsets.append(len(encoded))
        raw = s.encode("utf-16-le")
        encoded += struct.pack("<H", len(s)) + raw + b"\x00\x00"
    while len(encoded) % 4:
        encoded += b"\x00"

    pool_header = 28
    strings_start = pool_header + 4 * len(strings)
    pool_size = strings_start + len(encoded)
    pool = struct.pack(
        "<HHIIIIII",
        0x0001, pool_header, pool_size, len(strings), 0, 0, strings_start, 0,
    )
    pool += b"".join(struct.pack("<I", o) for o in offsets)
    pool += encoded

    # ── StartElement chunk ──
    attr_count = len(attributes)
    attrs = b""
    for i, (name, value) in enumerate(attributes.items()):
        name_idx = 1 + i
        if isinstance(value, str):
            raw_value, data_type, data = values[i], 0x03, values[i]
        else:
            raw_value, data_type, data = 0xFFFFFFFF, 0x10, int(value)
        attrs += struct.pack(
            "<IIIHBBI", 0xFFFFFFFF, name_idx, raw_value, 8, 0, data_type, data
        )

    elem_body = struct.pack(
        "<IIIIHHHHHH", 0, 0xFFFFFFFF, 0xFFFFFFFF, 0, 20, 20, attr_count, 0, 0, 0
    )
    elem_size = 8 + len(elem_body) + len(attrs)
    element = struct.pack("<HHI", 0x0102, 16, elem_size) + elem_body + attrs

    total = 8 + len(pool) + len(element)
    return struct.pack("<HHI", 0x0003, 8, total) + pool + element


class Args:
    """Stand-in for argparse.Namespace with the validators' defaults."""

    def __init__(self, **kwargs):
        defaults = dict(
            search_root="", expected_type="", expected_package="",
            expected_version_name="", expected_version_code="",
            min_size_bytes=1_000_000, max_size_mb=0.0,
            require_signed=False, require_identity=False, report="",
            expected_compression="", require_streaming_assets=False,
            expected_bundle_id="", expected_version="", expected_build_number="",
        )
        defaults.update(kwargs)
        for key, value in defaults.items():
            setattr(self, key, value)


def failed_checks(result):
    return {name for status, name, _ in result.checks if status == "fail"}


# ---------------------------------------------------------------------------
# artifact_manifest.py
# ---------------------------------------------------------------------------

class TestArtifactManifest:
    def test_discovers_the_artifact_by_type(self, tmp_path):
        build = tmp_path / "build"
        (build / "nested").mkdir(parents=True)
        (build / "app.aab").write_bytes(b"aab")
        found = artifact_manifest.discover_artifact(build, "AAB")
        assert found is not None and found.name == "app.aab"

    def test_prefers_the_shallowest_match(self, tmp_path):
        build = tmp_path / "build"
        (build / "deep" / "deeper").mkdir(parents=True)
        (build / "deep" / "deeper" / "intermediate.apk").write_bytes(b"x")
        (build / "shipped.apk").write_bytes(b"x")
        found = artifact_manifest.discover_artifact(build, "APK")
        assert found.name == "shipped.apk"

    def test_returns_none_when_the_type_is_absent(self, tmp_path):
        build = tmp_path / "build"
        build.mkdir()
        (build / "app.apk").write_bytes(b"x")
        assert artifact_manifest.discover_artifact(build, "IPA") is None

    def test_manifest_carries_every_required_field(self, tmp_path):
        artifact = tmp_path / "app.aab"
        artifact.write_bytes(b"content" * 100)
        manifest = artifact_manifest.build_manifest(
            platform="Android",
            configuration="Production",
            artifact_type="AAB",
            artifact_path=str(artifact),
            version="1.4.2",
            build_number="77",
            unity_version="6000.0.26f1",
            commit="abc123def456",
            branch="release/1.4",
            env={"GITHUB_RUN_ID": "999", "GITHUB_REPOSITORY": "o/r",
                 "GITHUB_ACTIONS": "true", "GITHUB_SERVER_URL": "https://github.com"},
        )
        assert manifest["platform"] == "Android"
        assert manifest["configuration"] == "Production"
        assert manifest["artifactType"] == "AAB"
        assert manifest["version"] == "1.4.2"
        assert manifest["buildNumber"] == "77"
        assert manifest["gitCommit"] == "abc123def456"
        assert manifest["gitBranch"] == "release/1.4"
        assert manifest["unityVersion"] == "6000.0.26f1"
        assert manifest["artifactSizeBytes"] == artifact.stat().st_size
        assert len(manifest["artifactSha256"]) == 64
        assert manifest["timestamp"].endswith("Z")
        assert manifest["ci"]["runId"] == "999"
        assert manifest["ci"]["runUrl"] == "https://github.com/o/r/actions/runs/999"

    def test_manifest_is_written_for_a_build_with_no_artifact(self, tmp_path):
        """A failed build still gets a manifest, so the report can say what broke."""
        manifest = artifact_manifest.build_manifest(
            platform="iOS", configuration="Production", artifact_type="IPA",
            artifact_path=None, env={},
        )
        assert manifest["artifactPath"] == ""
        assert manifest["artifactSizeBytes"] == 0
        assert manifest["success"] is False

    def test_sha256_is_stable_and_content_derived(self, tmp_path):
        a, b = tmp_path / "a.bin", tmp_path / "b.bin"
        a.write_bytes(b"same"); b.write_bytes(b"same")
        assert artifact_manifest.sha256_of(a) == artifact_manifest.sha256_of(b)
        b.write_bytes(b"different")
        assert artifact_manifest.sha256_of(a) != artifact_manifest.sha256_of(b)

    def test_directory_size_is_recursive(self, tmp_path):
        d = tmp_path / "dir"
        (d / "sub").mkdir(parents=True)
        (d / "one.bin").write_bytes(b"\x00" * 100)
        (d / "sub" / "two.bin").write_bytes(b"\x00" * 50)
        assert artifact_manifest.size_of(d) == 150

    def test_cli_writes_the_manifest_into_the_build_output(self, tmp_path, monkeypatch):
        build = tmp_path / "build"
        build.mkdir()
        (build / "game.apk").write_bytes(b"\x00" * 1024)
        monkeypatch.chdir(tmp_path)
        rc = artifact_manifest.main([
            "--platform", "Android", "--configuration", "Staging",
            "--artifact-type", "APK", "--search-root", str(build),
            "--version", "2.0.0",
        ])
        assert rc == 0
        written = json.loads((build / artifact_manifest.MANIFEST_FILENAME).read_text())
        assert written["artifactType"] == "APK"
        assert written["configuration"] == "Staging"


# ---------------------------------------------------------------------------
# axml.py
# ---------------------------------------------------------------------------

class TestAxml:
    def test_reads_manifest_attributes(self):
        data = build_axml({
            "versionCode": 42,
            "versionName": "1.2.3",
            "package": "com.example.game",
        })
        attrs = axml.parse_manifest_attributes(data)
        assert attrs["package"] == "com.example.game"
        assert attrs["versionName"] == "1.2.3"
        assert attrs["versionCode"] == 42

    def test_rejects_a_non_axml_buffer(self):
        with pytest.raises(axml.AxmlError):
            axml.parse_manifest_attributes(b"<?xml version='1.0'?><manifest/>")

    def test_rejects_a_truncated_buffer(self):
        with pytest.raises(axml.AxmlError):
            axml.parse_manifest_attributes(b"\x03\x00")


# ---------------------------------------------------------------------------
# Android artifact validation
# ---------------------------------------------------------------------------

class TestAndroidValidation:
    def test_accepts_a_well_formed_signed_aab(self, tmp_path):
        make_aab(tmp_path / "game.aab")
        result = validate_android.validate(
            Args(search_root=str(tmp_path), expected_type="AAB", require_signed=True)
        )
        assert not result.failed, result.to_dict()
        assert result.facts["signed"] is True

    def test_rejects_a_missing_artifact(self, tmp_path):
        result = validate_android.validate(Args(search_root=str(tmp_path)))
        assert "artifact-present" in failed_checks(result)

    def test_rejects_an_apk_when_an_aab_was_expected(self, tmp_path):
        make_apk(tmp_path / "game.apk")
        result = validate_android.validate(
            Args(search_root=str(tmp_path), expected_type="AAB")
        )
        assert "artifact-type" in failed_checks(result)

    def test_prefers_the_expected_type_over_a_stray_intermediate(self, tmp_path):
        make_apk(tmp_path / "intermediate.apk")
        make_aab(tmp_path / "shipped.aab")
        result = validate_android.validate(
            Args(search_root=str(tmp_path), expected_type="AAB")
        )
        assert result.facts["artifactType"] == "AAB"
        assert "artifact-type" not in failed_checks(result)

    def test_rejects_a_truncated_artifact(self, tmp_path):
        make_apk(tmp_path / "game.apk", padding=10)
        result = validate_android.validate(Args(search_root=str(tmp_path)))
        assert "artifact-size" in failed_checks(result)

    def test_rejects_an_artifact_over_the_ceiling(self, tmp_path):
        make_apk(tmp_path / "game.apk", padding=3_000_000)
        result = validate_android.validate(
            Args(search_root=str(tmp_path), max_size_mb=1.0)
        )
        assert "artifact-size" in failed_checks(result)

    def test_rejects_an_unsigned_release_artifact(self, tmp_path):
        make_aab(tmp_path / "game.aab", signed=False)
        result = validate_android.validate(
            Args(search_root=str(tmp_path), expected_type="AAB", require_signed=True)
        )
        assert "signing" in failed_checks(result)

    def test_unsigned_is_only_a_warning_when_signing_is_not_required(self, tmp_path):
        make_aab(tmp_path / "game.aab", signed=False)
        result = validate_android.validate(
            Args(search_root=str(tmp_path), expected_type="AAB")
        )
        assert "signing" not in failed_checks(result)
        assert result.facts["signed"] is False

    def test_rejects_an_apk_with_no_dex(self, tmp_path):
        make_apk(tmp_path / "game.apk", with_dex=False)
        result = validate_android.validate(Args(search_root=str(tmp_path)))
        assert "archive-structure" in failed_checks(result)

    def test_rejects_an_aab_missing_bundle_config(self, tmp_path):
        path = tmp_path / "game.aab"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("base/dex/classes.dex", b"dex")
            zf.writestr("pad.bin", b"\x00" * 2_000_000)
        result = validate_android.validate(
            Args(search_root=str(tmp_path), expected_type="AAB")
        )
        assert "archive-structure" in failed_checks(result)

    def test_reads_identity_from_the_compiled_manifest(self, tmp_path):
        path = tmp_path / "game.apk"
        make_apk(path)
        # Replace the placeholder manifest with real binary XML.
        raw = build_axml({
            "versionCode": 77,
            "versionName": "3.1.0",
            "package": "com.example.identity",
        })
        rebuilt = tmp_path / "rebuilt.apk"
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(rebuilt, "w") as dst:
            for item in src.infolist():
                data = raw if item.filename == "AndroidManifest.xml" else src.read(item.filename)
                dst.writestr(item.filename, data)
        path.unlink()
        rebuilt.rename(path)

        result = validate_android.validate(
            Args(search_root=str(tmp_path), expected_type="APK",
                 expected_package="com.example.identity",
                 expected_version_name="3.1.0",
                 expected_version_code="77")
        )
        assert not result.failed, result.to_dict()
        assert result.facts["package"] == "com.example.identity"

    def test_rejects_a_package_id_mismatch(self, tmp_path):
        path = tmp_path / "game.apk"
        make_apk(path)
        raw = build_axml({"versionCode": 1, "versionName": "1.0.0",
                          "package": "com.example.wrong"})
        rebuilt = tmp_path / "rebuilt.apk"
        with zipfile.ZipFile(path) as src, zipfile.ZipFile(rebuilt, "w") as dst:
            for item in src.infolist():
                data = raw if item.filename == "AndroidManifest.xml" else src.read(item.filename)
                dst.writestr(item.filename, data)
        path.unlink()
        rebuilt.rename(path)

        result = validate_android.validate(
            Args(search_root=str(tmp_path), expected_type="APK",
                 expected_package="com.example.right")
        )
        assert "identity-package" in failed_checks(result)

    def test_falls_back_to_the_shipped_manifest_for_identity(self, tmp_path):
        make_aab(tmp_path / "game.aab")
        (tmp_path / "artifact-manifest.json").write_text(json.dumps({
            "bundleId": "com.example.fallback",
            "version": "9.9.9",
            "buildNumber": "123",
        }))
        result = validate_android.validate(
            Args(search_root=str(tmp_path), expected_type="AAB",
                 expected_package="com.example.fallback",
                 expected_version_name="9.9.9")
        )
        assert not result.failed, result.to_dict()
        assert result.facts["identityProvenance"]["package"] == "manifest-json"

    def test_unverifiable_identity_is_a_warning_by_default(self, tmp_path):
        """No aapt2, no readable manifest, no shipped json — not an artifact defect."""
        make_aab(tmp_path / "game.aab")
        result = validate_android.validate(
            Args(search_root=str(tmp_path), expected_type="AAB")
        )
        assert "identity-package" not in failed_checks(result)
        assert result.facts["package"] == validate_android.UNVERIFIED

    def test_unverifiable_identity_fails_when_required(self, tmp_path):
        make_aab(tmp_path / "game.aab")
        result = validate_android.validate(
            Args(search_root=str(tmp_path), expected_type="AAB", require_identity=True)
        )
        assert "identity-package" in failed_checks(result)

    def test_cli_exit_code_reflects_the_verdict(self, tmp_path):
        make_aab(tmp_path / "game.aab")
        assert validate_android.main([
            "--search-root", str(tmp_path), "--expected-type", "AAB"
        ]) == 0
        assert validate_android.main([
            "--search-root", str(tmp_path), "--expected-type", "APK"
        ]) == 1


# ---------------------------------------------------------------------------
# WebGL artifact validation
# ---------------------------------------------------------------------------

class TestWebGLValidation:
    def test_accepts_a_well_formed_player(self, tmp_path):
        make_webgl(tmp_path / "out")
        result = validate_webgl.validate(Args(search_root=str(tmp_path)))
        assert not result.failed, result.to_dict()

    def test_finds_a_player_nested_in_the_artifact(self, tmp_path):
        make_webgl(tmp_path / "build" / "WebGL" / "Product")
        result = validate_webgl.validate(Args(search_root=str(tmp_path)))
        assert not result.failed, result.to_dict()

    def test_rejects_a_missing_index_html(self, tmp_path):
        make_webgl(tmp_path / "out", with_index=False)
        result = validate_webgl.validate(Args(search_root=str(tmp_path)))
        assert "index-html" in failed_checks(result)

    def test_rejects_a_missing_build_directory(self, tmp_path):
        root = tmp_path / "out"
        root.mkdir()
        (root / "index.html").write_text("<html>Build/app.loader.js</html>")
        result = validate_webgl.validate(Args(search_root=str(tmp_path)))
        assert "build-dir" in failed_checks(result)

    def test_rejects_a_missing_player_file(self, tmp_path):
        root = make_webgl(tmp_path / "out")
        (root / "Build" / "app.wasm").unlink()
        result = validate_webgl.validate(Args(search_root=str(tmp_path)))
        assert "build-files" in failed_checks(result)

    @pytest.mark.parametrize("compression", ["gzip", "brotli", "none"])
    def test_detects_each_compression_mode(self, tmp_path, compression):
        make_webgl(tmp_path / "out", compression=compression)
        result = validate_webgl.validate(
            Args(search_root=str(tmp_path), expected_compression=compression)
        )
        assert not result.failed, result.to_dict()
        assert result.facts["compressionMode"] == compression

    def test_rejects_mixed_compression(self, tmp_path):
        """Loader plain + wasm brotli is the blank-canvas trap."""
        make_webgl(tmp_path / "out", compression="brotli", mixed=True)
        result = validate_webgl.validate(Args(search_root=str(tmp_path)))
        assert "compression-consistent" in failed_checks(result)

    def test_rejects_unexpected_compression(self, tmp_path):
        make_webgl(tmp_path / "out", compression="gzip")
        result = validate_webgl.validate(
            Args(search_root=str(tmp_path), expected_compression="brotli")
        )
        assert "compression-expected" in failed_checks(result)

    def test_rejects_a_truncated_player(self, tmp_path):
        make_webgl(tmp_path / "out", padding=100)
        result = validate_webgl.validate(Args(search_root=str(tmp_path)))
        assert "artifact-size" in failed_checks(result)

    def test_streaming_assets_can_be_required(self, tmp_path):
        make_webgl(tmp_path / "out")
        result = validate_webgl.validate(
            Args(search_root=str(tmp_path), require_streaming_assets=True)
        )
        assert "streaming-assets" in failed_checks(result)

        make_webgl(tmp_path / "with", streaming_assets=True)
        ok = validate_webgl.validate(
            Args(search_root=str(tmp_path / "with"), require_streaming_assets=True)
        )
        assert "streaming-assets" not in failed_checks(ok)

    def test_cli_exit_code_reflects_the_verdict(self, tmp_path):
        make_webgl(tmp_path / "out")
        assert validate_webgl.main(["--search-root", str(tmp_path)]) == 0
        assert validate_webgl.main([
            "--search-root", str(tmp_path), "--expected-compression", "brotli"
        ]) == 1


# ---------------------------------------------------------------------------
# iOS Xcode project validation
# ---------------------------------------------------------------------------

class TestXcodeProjectValidation:
    def test_accepts_a_well_formed_export(self, tmp_path):
        make_xcode_export(tmp_path / "export")
        result = validate_xcode.validate(Args(search_root=str(tmp_path)))
        assert not result.failed, result.to_dict()

    def test_rejects_a_missing_xcodeproj(self, tmp_path):
        (tmp_path / "empty").mkdir()
        result = validate_xcode.validate(Args(search_root=str(tmp_path)))
        assert "xcodeproj-present" in failed_checks(result)

    def test_rejects_a_missing_pbxproj(self, tmp_path):
        make_xcode_export(tmp_path / "export", with_pbxproj=False)
        result = validate_xcode.validate(Args(search_root=str(tmp_path)))
        assert "pbxproj-readable" in failed_checks(result)

    def test_rejects_an_export_missing_unity_trees(self, tmp_path):
        make_xcode_export(tmp_path / "export", trees=("Classes",))
        result = validate_xcode.validate(Args(search_root=str(tmp_path)))
        assert "unity-trees" in failed_checks(result)

    def test_reads_identity_from_info_plist(self, tmp_path):
        make_xcode_export(tmp_path / "export", bundle_id="com.example.ios", version="4.5.6")
        result = validate_xcode.validate(
            Args(search_root=str(tmp_path),
                 expected_bundle_id="com.example.ios",
                 expected_version="4.5.6")
        )
        assert not result.failed, result.to_dict()
        assert result.facts["bundleId"] == "com.example.ios"

    def test_rejects_a_bundle_id_mismatch(self, tmp_path):
        make_xcode_export(tmp_path / "export", bundle_id="com.example.wrong")
        result = validate_xcode.validate(
            Args(search_root=str(tmp_path), expected_bundle_id="com.example.right")
        )
        assert "identity-bundleId" in failed_checks(result)

    def test_xcode_build_setting_placeholder_is_not_a_failure(self, tmp_path):
        """Unity writes $(PRODUCT_BUNDLE_IDENTIFIER); Xcode substitutes it later."""
        make_xcode_export(tmp_path / "export", bundle_id="$(PRODUCT_BUNDLE_IDENTIFIER)")
        result = validate_xcode.validate(
            Args(search_root=str(tmp_path), expected_bundle_id="com.example.game")
        )
        assert "identity-bundleId" not in failed_checks(result)

    def test_rejects_a_truncated_export(self, tmp_path):
        make_xcode_export(tmp_path / "export", padding=100)
        result = validate_xcode.validate(Args(search_root=str(tmp_path)))
        assert "artifact-size" in failed_checks(result)

    def test_cli_exit_code_reflects_the_verdict(self, tmp_path):
        make_xcode_export(tmp_path / "export", bundle_id="com.example.ios")
        assert validate_xcode.main(["--search-root", str(tmp_path)]) == 0
        assert validate_xcode.main([
            "--search-root", str(tmp_path), "--expected-bundle-id", "com.other"
        ]) == 1


# ---------------------------------------------------------------------------
# Report shape — the pipeline uploads these as artifacts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "module,builder",
    [(validate_android, make_aab), (validate_webgl, make_webgl), (validate_xcode, make_xcode_export)],
)
def test_report_json_shape(tmp_path, module, builder):
    target = tmp_path / "out"
    builder(target if builder is not make_aab else tmp_path / "game.aab")
    root = tmp_path if builder is make_aab else tmp_path
    report_path = tmp_path / "report.json"
    module.main(["--search-root", str(root), "--report", str(report_path)])
    report = json.loads(report_path.read_text())
    assert report["status"] in {"success", "failure"}
    assert isinstance(report["checks"], list)
    assert all({"status", "name", "detail"} <= set(c) for c in report["checks"])
    assert isinstance(report["facts"], dict)
