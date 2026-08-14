# iOS Builds

This document describes the full iOS build pipeline: Unity → Xcode project generation → archive → signing → IPA export → (optional) TestFlight upload.

iOS builds run on the `macos-unity-xcode` executor — a macOS GitHub Actions runner with Unity and Xcode pre-installed. **They do not use Docker.** The `docker-unity` executor (used for Android, WebGL, Linux) is incompatible with iOS because Xcode is macOS-only.

---

## Prerequisites

| Requirement | Minimum Version | Notes |
|---|---|---|
| macOS runner | macOS 13 (Ventura) | `macos-13` (approved; `macos-latest` is not claimed — it floats) |
| Xcode | 15.0 | Set via `xcodeVersion` in BuildConfig |
| Unity Editor | 2022.3 LTS or 6000.x | Must include iOS Build Support module |
| Apple Developer account | Active membership | Required for signing and TestFlight |

See [IOS_SIGNING.md](IOS_SIGNING.md) for certificate and provisioning profile setup.

---

## Pipeline Overview

```
Consumer Workflow
  │ uses: unity-build-workflows/.github/workflows/unity-build-ios.yml@<ref>
  │ with: target-platform: iOS
  │
  ▼
macOS Runner (macos-unity-xcode executor)
  │
  ├── 1. Checkout project
  ├── 2. Validate BuildConfig (scripts/common/validate_build_config.py)
  ├── 3. Resolve platform executor (scripts/common/resolve_platform_executor.py)
  │       → iOS + macos = macos-unity-xcode ✓
  ├── 4. Setup signing (scripts/ios/setup_signing.sh)
  │       ├── Import distribution certificate → temp keychain
  │       ├── Install provisioning profile
  │       └── Write ASC private key to temp file
  ├── 5. Unity build — Xcode project generation (scripts/ios/build_ios.sh)
  │       └── Unity -batchmode -buildTarget iOS -executeMethod \
  │             Company.BuildPipeline.Editor.BuildCommand.Execute
  │             └── Output: Builds/iOS/Xcode/*.xcworkspace
  ├── 6. Archive (scripts/ios/archive_ios.sh)
  │       └── xcodebuild archive -workspace ... -scheme ... -archivePath ...
  │             └── Output: Builds/iOS/Archive/*.xcarchive
  ├── 7. Export IPA (scripts/ios/export_ios.sh)
  │       ├── Generate ExportOptions.plist from BuildConfig
  │       └── xcodebuild -exportArchive -archivePath ... -exportPath ...
  │             └── Output: Builds/iOS/Export/*.ipa
  ├── 8. (Optional) Upload to TestFlight (scripts/ios/upload_testflight.sh)
  │       └── xcrun altool --upload-app or notarytool
  ├── 9. Upload artifacts
  │       ├── Builds/iOS/Archive/  → ios-archive artifact
  │       ├── Builds/iOS/Export/   → ios-ipa artifact
  │       ├── Builds/iOS/Symbols/  → ios-symbols artifact (if generateSymbols=true)
  │       ├── BuildReports/iOS/    → ios-build-report artifact
  │       └── Logs/iOS/            → ios-logs artifact
  └── 10. Cleanup (scripts/ios/cleanup_ios.sh — runs always)
        ├── Delete temp keychain
        ├── Remove installed provisioning profile
        └── Remove ASC private key file
```

---


> **This lane's entry point is fixed, not a default.** `scripts/ios/run_unity_ios.sh:46` declares
> `readonly BUILD_METHOD="Company.BuildPipeline.Editor.BuildCommand.Execute"` and uses it at `:268`,
> `:300` and `:352`; there is no input, and the `build-method` input / `UNITY_BUILD_METHOD` repo
> variable do not reach it. `com.company.build-pipeline` is therefore a hard requirement here, which
> is why the script preflights for it.
>
> The other lanes work the other way round: `reusable-build-platform.yml` defaults to
> `PlayerBuilder.Build`, supplied by the consuming project, and never calls this script — including
> for the pipeline's own `Build iOS` job. The two iOS routes do not share an entry point. See
> [ARCHITECTURE.md](ARCHITECTURE.md#build-entry-points--two-lanes-that-do-not-agree).
>
> **`BuildCommand.Execute` did not compile until the Unity 6 fix** (four `BuildPipeline.BuildPlayer`
> call sites resolved against the enclosing `Company.BuildPipeline` namespace instead of
> `UnityEditor.BuildPipeline`), so this lane could not have succeeded before it. It went unnoticed
> because the lane requires manual dispatch and a self-hosted macOS runner, and had never run.

## BuildConfig iOS Section

Add an `iOS` block to your `BuildConfig/base.json`.

> **Canonical key is `iOS` (capital S).** The lowercase alias `ios` is still accepted for
> backward compatibility but is deprecated and will produce a warning in v3.0.0. All new
> configs and templates must use `iOS`.

```json
{
  "projectName": "my-game",
  "companyName": "My Studio",
  "productName": "My Game",
  "bundleVersion": "1.0.0",
  "outputDirectory": "Builds/iOS",
  "scenes": ["Assets/Scenes/Bootstrap.unity"],
  "scriptingBackend": "IL2CPP",
  "iOS": {
    "bundleIdentifier": "com.mystudio.mygame",
    "marketingVersion": "1.0.0",
    "buildNumber": "42",
    "sdkVersion": "iphoneos",
    "targetOSVersion": "14.0",
    "architecture": "ARM64",
    "xcodeVersion": "15.2",
    "developmentTeamId": "YOURTEAMID1",
    "signingStyle": "manual",
    "provisioningProfileSpecifier": "My Game App Store",
    "codeSignIdentity": "iPhone Distribution",
    "exportMethod": "app-store",
    "enableBitcode": false,
    "generateSymbols": true,
    "uploadSymbols": true,
    "uploadToTestFlight": false
  }
}
```

### Field Reference

| Field | Type | Required | Default | Description |
|---|---|---|---|---|
| `bundleIdentifier` | string | **Yes** | — | iOS bundle ID (reverse-DNS) |
| `marketingVersion` | string | No | `bundleVersion` | User-facing version string (CFBundleShortVersionString) |
| `buildNumber` | string | No | derived | CFBundleVersion. Derived from `buildNumberStrategy` if omitted |
| `sdkVersion` | enum | No | `iphoneos` | `iphoneos` or `iphonesimulator` |
| `targetOSVersion` | string | No | `14.0` | Minimum iOS deployment target (`MAJOR.MINOR`) |
| `architecture` | enum | No | `ARM64` | `ARM64` (device), `x86_64` (simulator) |
| `xcodeVersion` | string | No | runner default | Xcode version to select via `xcode-select` |
| `developmentTeamId` | string | No | — | 10-char Apple Developer Team ID |
| `signingStyle` | enum | No | `manual` | `manual` or `automatic` |
| `provisioningProfileSpecifier` | string | No | — | Profile name for manual signing |
| `codeSignIdentity` | string | No | `iPhone Distribution` | Code signing identity |
| `exportMethod` | enum | No | `app-store` | `app-store`, `ad-hoc`, `enterprise`, `development` |
| `enableBitcode` | boolean | No | `false` | Enable Bitcode (deprecated since Xcode 14) |
| `generateSymbols` | boolean | No | `true` | Generate dSYM symbol files |
| `uploadSymbols` | boolean | No | `false` | Upload dSYMs to App Store Connect |
| `uploadToTestFlight` | boolean | No | `false` | Submit IPA to TestFlight after export |

**No secrets in BuildConfig.** All signing credentials (certificate, profile, ASC keys) are GitHub Secrets. See [IOS_SIGNING.md](IOS_SIGNING.md).

---

## Caller Workflow Example

```yaml
# .github/workflows/build-ios.yml  (in your game repository)
name: iOS Build

on:
  push:
    branches: [main, release/*]
  workflow_dispatch:

jobs:
  build-ios:
    uses: <WORKFLOW_OWNER>/unity-build-workflows/.github/workflows/unity-build-ios.yml@<ref>
    with:
      project-path: .
      unity-version: '6000.0.26f1'
      target-platform: iOS
      environment: staging
      build-config-path: BuildConfig
      upload-artifact: true
    secrets: inherit
```

For production releases with TestFlight upload, see [IOS_RELEASE.md](IOS_RELEASE.md).

---

## Artifact Paths

After a successful pipeline run, the following artifacts are uploaded:

| Artifact Name | Path | Contents |
|---|---|---|
| `ios-xcode` | `Builds/iOS/Xcode/` | Xcode project/workspace |
| `ios-archive` | `Builds/iOS/Archive/` | `.xcarchive` bundle |
| `ios-ipa` | `Builds/iOS/Export/` | `.ipa` file |
| `ios-symbols` | `Builds/iOS/Symbols/` | `.dSYM` symbol files |
| `ios-build-report` | `BuildReports/iOS/` | Build report JSON/Markdown |
| `ios-logs` | `Logs/iOS/` | `Editor.log`, `xcodebuild.log` |
| `test-results` | `TestResults/` | NUnit XML results |

---

## Workspace vs. Project Resolution

Unity generates either:
- `MyGame.xcworkspace` (when CocoaPods are used — preferred)
- `MyGame.xcodeproj` (standard project)

The archive script (`scripts/ios/archive_ios.sh`) detects which is present:

```bash
if [ -d "${XCODE_OUT}/${SCHEME}.xcworkspace" ]; then
  BUILD_FLAG="-workspace ${SCHEME}.xcworkspace"
else
  BUILD_FLAG="-project ${SCHEME}.xcodeproj"
fi
```

The scheme defaults to the project name. Override via the `SCHEME` environment variable if your scheme differs.

---

## ExportOptions.plist Generation

The export script generates `ExportOptions.plist` from BuildConfig values:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "...">
<plist version="1.0">
<dict>
  <key>method</key>
  <string>app-store</string>
  <key>teamID</key>
  <string>YOURTEAMID1</string>
  <key>signingStyle</key>
  <string>manual</string>
  <key>provisioningProfiles</key>
  <dict>
    <key>com.mystudio.mygame</key>
    <string>My Game App Store</string>
  </dict>
  <key>uploadBitcode</key>
  <false/>
  <key>generateAppStoreInformation</key>
  <true/>
</dict>
</plist>
```

---

## Security

- Certificates, profiles, and ASC keys are **never** written to artifact directories.
- The temp keychain is created with a random password and deleted after the build.
- All cleanup steps run via `trap` on EXIT — even when the build fails.
- No secrets appear in `xcodebuild` command-line arguments (passed via environment or file).

See [IOS_SIGNING.md](IOS_SIGNING.md) and [SECURITY.md](SECURITY.md).

---

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md#ios-issues) for common errors:
- Certificate not found in keychain
- Provisioning profile expired or mismatched
- "No applicable devices" export error
- TestFlight processing delays

---

## Platform Limitations

- iOS builds **cannot run on Docker** (`docker-unity` executor).
- iOS builds **cannot run on Linux runners**.
- iOS builds **cannot run on Windows runners**.
- The `macos-unity-xcode` executor is macOS-only.

See [PLATFORM_LIMITATIONS.md](PLATFORM_LIMITATIONS.md).
