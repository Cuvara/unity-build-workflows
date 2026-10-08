# iOS Pipeline Verification Runbook

How to verify the iOS build/sign/release pipeline on real Apple infrastructure.

> **Why this exists.** Everything in the iOS pipeline is validated statically on
> Linux CI (YAML parse, `bash -n`, fake-`Unity`/fake-`xcodebuild` failure-path
> tests, schema/validator unit tests — full pytest suite green). What **cannot**
> run on Linux is the real toolchain: Unity iOS compilation, `xcodebuild`
> archive/export, Apple code signing, and TestFlight upload. This runbook covers
> that gap. **Do not treat the pipeline as production-ready until the stages
> below pass on a macOS runner.**

---

## Prerequisites

### Runner

A macOS host registered to the repository as a self-hosted runner with the
label **`macos-unity-xcode`**, providing:

| Requirement | Notes |
|---|---|
| macOS | 13 (Ventura) or newer |
| Xcode | Matching the `xcodeVersion` in your BuildConfig (e.g. 16.x); `xcode-select -p` set; license accepted (`sudo xcodebuild -license accept`) |
| Command-line tools | `xcodebuild`, `security`, `codesign`, `xcrun`, `curl` |
| Unity Hub + Editor | The project's Unity version (e.g. `6000.0.26f1`) |
| Unity **iOS Build Support** | Module installed for that exact Editor version — `…/PlaybackEngines/iOSSupport` must exist |

Register the runner:

```bash
# On the macOS host, from the repo's Settings → Actions → Runners → New self-hosted runner
./config.sh --url https://github.com/<owner>/<repo> --token <RUNNER_TOKEN> --labels macos-unity-xcode
./run.sh
```

### Secrets

Set in the repository (and in the protected `ios-production` environment for
release):

| Secret | Used by |
|---|---|
| `UNITY_LICENSE` (or `UNITY_EMAIL` + `UNITY_PASSWORD`) | Unity activation |
| `IOS_DISTRIBUTION_CERTIFICATE_BASE64` | signing (base64 of `.p12`) |
| `IOS_DISTRIBUTION_CERTIFICATE_PASSWORD` | signing (`.p12` passphrase) |
| `IOS_PROVISIONING_PROFILE_BASE64` | signing (base64 of `.mobileprovision`) |
| `APP_STORE_CONNECT_KEY_ID` | TestFlight upload |
| `APP_STORE_CONNECT_ISSUER_ID` | TestFlight upload |
| `APP_STORE_CONNECT_PRIVATE_KEY` | TestFlight upload (`.p8` content) |
| `DISCORD_WEBHOOK_URL` *(optional)* | completion notification |

Encode files for the secrets:

```bash
base64 -i dist_cert.p12             | pbcopy   # → IOS_DISTRIBUTION_CERTIFICATE_BASE64
base64 -i profile.mobileprovision   | pbcopy   # → IOS_PROVISIONING_PROFILE_BASE64
```

### BuildConfig

A `BuildConfig/` with an `iOS` block (canonical key; `ios` still accepted). See
[BUILD_CONFIG.md](BUILD_CONFIG.md). Validate first (runs on any host):

```bash
python3 scripts/common/validate_build_config.py \
  --config-path BuildConfig --platform iOS --environment production --strict
```

---

## Level 0 — Static checks (any host, no Apple toolchain)

Run these first; they gate everything else.

```bash
# Executor resolution
python3 scripts/common/resolve_platform_executor.py --target-platform iOS --runner-os macOS   # → macos-unity-xcode
python3 scripts/common/resolve_platform_executor.py --target-platform iOS --runner-os Linux    # → non-zero, contract error

# Full test suite (fakes stand in for Unity/xcodebuild)
python3 -m pytest -q

# Syntax
bash -n scripts/ios/*.sh
python3 -c "import yaml,glob; [yaml.safe_load(open(f)) for f in glob.glob('.github/workflows/*.yml')]"
```

**Expect:** resolver returns/rejects correctly; pytest green; no syntax errors.

---

## Level 1 — Unity Xcode-project generation (macOS runner, Unity, no Apple signing)

Generates the Xcode project. No certificate needed.

```bash
export UNITY_EDITOR="/Applications/Unity/Hub/Editor/6000.0.26f1/Unity.app/Contents/MacOS/Unity"
export PROJECT_PATH="."
export ENVIRONMENT="development"
export BUILD_CONFIG_PATH="BuildConfig"
export OUTPUT_PATH="Builds/iOS/Xcode"
export LOG_PATH="Logs/iOS"
export UNITY_LICENSE="$(cat your.ulf | base64)"   # or UNITY_EMAIL/UNITY_PASSWORD

bash scripts/ios/run_unity_ios.sh activate
bash scripts/ios/run_unity_ios.sh build
bash scripts/ios/run_unity_ios.sh return-license
```

**Verify:**
- `Builds/iOS/Xcode/` contains `Unity-iPhone.xcodeproj` (or `.xcworkspace`) and is non-empty.
- `BuildReports/iOS/build-report.json` and `build-metadata.json` exist.
- `Logs/iOS/Editor.log` preserved.
- Post-process applied: in `project.pbxproj`, `ENABLE_BITCODE = NO`; `Info.plist` has the expected usage descriptions; entitlements present.
- Non-zero exit on failure (test it: point `BUILD_CONFIG_PATH` at a bad config → build fails, report still written).

---

## Level 2 — Development archive + ad-hoc export (macOS runner, signing)

```bash
# Signing environment
export IOS_DISTRIBUTION_CERTIFICATE_BASE64="…"
export IOS_DISTRIBUTION_CERTIFICATE_PASSWORD="…"
export IOS_PROVISIONING_PROFILE_BASE64="…"
source scripts/ios/create_keychain.sh        # exports KEYCHAIN_PATH / KEYCHAIN_PASSWORD
bash   scripts/ios/import_certificate.sh
source scripts/ios/install_profile.sh         # exports PROFILE_UUID

# Export options (ad-hoc for device testing)
export EXPORT_METHOD="ad-hoc"
export BUNDLE_IDENTIFIER="com.yourco.yourgame"
export DEVELOPMENT_TEAM="ABCDE12345"
export PROFILE_UUID="${PROFILE_UUID}"
source scripts/ios/generate_export_options.sh # exports EXPORT_OPTIONS_PATH

# Archive → export → validate
export XCODE_PROJECT_PATH="Builds/iOS/Xcode"
bash scripts/ios/xcode_archive.sh             # → Builds/iOS/Archive/Game.xcarchive
export ARCHIVE_PATH="Builds/iOS/Archive/Game.xcarchive"
bash scripts/ios/xcode_export.sh              # → Builds/iOS/Export/Game.ipa
export IPA_PATH="Builds/iOS/Export/Game.ipa"
bash scripts/ios/validate_ipa.sh "${IPA_PATH}"

# ALWAYS clean up signing material
bash scripts/ios/cleanup_signing.sh
```

**Verify:**
- `Game.xcarchive` and `Game.ipa` produced.
- `codesign --verify --deep --strict "${IPA_PATH}"` passes; `Logs/iOS/xcode-archive.log` + `xcode-export.log` preserved; dSYMs under `Builds/iOS/Symbols/`.
- Archive/export failures propagate a non-zero exit (test with a bad scheme).
- After `cleanup_signing.sh`: `security list-keychains` no longer shows the temp keychain; the profile and any ASC key dir are gone.
- No secret value appears in any log (`grep -ri "$DEVELOPMENT_TEAM" Logs/` is fine; cert/key contents must be absent).

---

## Level 3 — App Store distribution archive + TestFlight (protected, gated)

Run via the release workflows only — never from a fork or PR. A signed App Store IPA comes
from **Build / Release** (`templates/consumer-11-build-release.yml`, iOS selected); TestFlight
and the App Store are **Release / iOS** (`templates/consumer-21-release-ios.yml` →
`pipeline-ios-release.yml`), started with that run's `source-run-id` and the IPA's
`artifact-name`. (The `unity-release-ios.yml` dispatch this level used was removed in 7.0.0 —
[MIGRATION_V7.md](MIGRATION_V7.md).)

**Gates that must hold:**
- Every Release / iOS job that holds the IPA verifies it against the Release Set first
  (`verify-release-artifact`): same run, name, version, build number and SHA-256.
- TestFlight internal, external and App Store phases run behind the `internal-testing`,
  `external-testing` and `production` environments.
- `dry-run` publishes nothing.
- "iOS — Cleanup signing" runs on `if: always()` in the build job.

```bash
# TestFlight upload (if running stages manually)
export IPA_PATH="Builds/iOS/Export/Game.ipa"
export APP_STORE_CONNECT_KEY_ID="…"
export APP_STORE_CONNECT_ISSUER_ID="…"
export APP_STORE_CONNECT_PRIVATE_KEY="$(cat AuthKey_XXXX.p8)"
export BUILD_VERSION="1.0.0"
bash scripts/ios/testflight_upload.sh
bash scripts/ios/release_metadata.sh
bash scripts/ios/cleanup_signing.sh
```

---

## Artifact contract (what a successful run leaves behind)

```
Builds/iOS/Xcode/                      Unity-generated Xcode project
Builds/iOS/Archive/Game.xcarchive      xcodebuild archive
Builds/iOS/Export/Game.ipa             exported IPA
Builds/iOS/Symbols/                    dSYMs
BuildReports/iOS/build-report.json     Unity build result
BuildReports/iOS/build-report.md
BuildReports/iOS/build-metadata.json
BuildReports/iOS/archive-metadata.json
BuildReports/iOS/export-metadata.json
Logs/iOS/Editor.log
Logs/iOS/xcode-archive.log
Logs/iOS/xcode-export.log
TestResults/                            EditMode test results
```

Reports and logs upload on `if: always()`. Keychains, certificates,
provisioning profiles, and ASC private keys are **never** uploaded.

---

## Verification checklist

- [ ] Level 0 static checks green (resolver, pytest, syntax)
- [ ] Correct Unity version + iOS Build Support detected on runner
- [ ] Correct Xcode version
- [ ] Bundle identifier and build number match BuildConfig
- [ ] Xcode project generated and non-empty
- [ ] `.xcarchive` generated
- [ ] `.ipa` generated and `codesign --verify` passes
- [ ] dSYMs / symbols preserved
- [ ] `Editor.log` + archive/export logs preserved (even on failure)
- [ ] Non-zero exit codes propagate on Unity / archive / export / missing-IPA failure
- [ ] Temp keychain, profile, and ASC key removed after run (success **and** failure)
- [ ] No secret values in logs or uploaded artifacts
- [ ] Fork / PR cannot reach release signing
- [ ] TestFlight upload only with explicit authorization; status reported honestly
- [ ] SHA-256 checksums emitted for release artifacts
- [ ] (Optional) Discord notification received with correct status

---

## Troubleshooting

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) (iOS section): certificate/profile
errors, Xcode version migration, profile expiry, Unity license recovery,
TestFlight processing limits. Signing setup detail: [IOS_SIGNING.md](IOS_SIGNING.md).
Release specifics: [IOS_RELEASE.md](IOS_RELEASE.md).

---

## Honesty note

This runbook describes the verification procedure. As of writing, Levels 1–3
have **not** been executed end-to-end in this repository — no macOS runner,
Unity iOS toolchain, or Apple credentials were available during development.
Record the date, runner, Unity version, and Xcode version when each level first
passes, and link the successful run here.
