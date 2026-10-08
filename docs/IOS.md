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

iOS builds run in `unity-pipeline.yml` like every other platform: the consumer's entry
workflows (`templates/consumer-10-build-development.yml`, `consumer-11-build-release.yml`)
call it, and stage 03 runs `reusable-build-platform.yml` for the iOS row of the matrix.
iOS needs `build-engine: local` on a macOS runner; anywhere else the job reports `blocked`.

```
reusable-build-platform.yml  (iOS row, self-hosted / GitHub-hosted macOS)
  │
  ├── Unity preflight          the exact editor + iOS module (scripts/unity-preflight.sh)
  ├── Unity build              scripts/build/run_unity_player.sh --platform iOS
  │                              → -executeMethod Company.BuildPipeline.Editor.PlayerBuilder.Build
  │                              → Xcode project under build/
  ├── Upload build artifact    the Xcode project (…_ios_xcodeproj)
  │
  │   when the build signs (Build / Release, or BUILD_IOS_SIGN_DEVELOPMENT=true):
  ├── iOS — Find Xcode project        Unity-iPhone.xcodeproj, never Pods/
  ├── iOS — Configure Xcode signing   team, bundle id and profile read from the
  │                                   provisioning profile (scripts/ios/configure_xcode_signing.py)
  ├── iOS — Setup signing             temporary keychain + certificate + profile (10 min timeout)
  ├── iOS — Archive and export IPA    xcodebuild archive / -exportArchive, every target at
  │                                   the project's minimum iOS version
  ├── iOS — Upload signed IPA         + its artifact manifest (the Release Set input)
  ├── iOS — Cleanup signing           always
  └── Deliver the build               Firebase App Distribution (BUILD_DELIVERY=firebase);
                                      TestFlight for a Production build with ARTIFACT_STORAGE=firebase
```

Promotion to TestFlight / App Store from a Build / Release artifact is
`pipeline-ios-release.yml` (`templates/consumer-21-release-ios.yml`) — see
[IOS_RELEASE.md](IOS_RELEASE.md).

## Configuration

- **Player Settings decide the app**: product name, version, bundle id and the minimum iOS
  version come from the project. Signing does not need to be set up in the project; the
  signing step writes manual signing from the provisioning profile.
- **Secrets**: `IOS_DISTRIBUTION_CERTIFICATE_BASE64`, `IOS_DISTRIBUTION_CERTIFICATE_PASSWORD`,
  `IOS_PROVISIONING_PROFILE_BASE64` (and the App Store Connect keys for TestFlight). See
  [IOS_SIGNING.md](IOS_SIGNING.md).
- **Signed development builds**: set `BUILD_IOS_SIGN_DEVELOPMENT=true`; the export method then
  follows the profile (ad-hoc / development).

## Running a Build

```bash
gh workflow run "Build / Development" --ref develop -f platform=iOS
```

Setup: [CONSUMER_SETUP.md](CONSUMER_SETUP.md). The 7.0.0 removal of the native iOS workflows
(`BuildCommand.Execute` + `BuildConfig/`) is described in [MIGRATION_V7.md](MIGRATION_V7.md).

---

## Security

- Certificates, profiles, and ASC keys are **never** written to artifact directories.
- The temp keychain is created with a random password and deleted after the build.
- "iOS — Cleanup signing" runs on every outcome (`always()`) — even when the build fails.
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
