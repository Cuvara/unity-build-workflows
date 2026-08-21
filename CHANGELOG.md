# Changelog

All notable changes to `unity-build-workflows` are documented here.

This project adheres to [Semantic Versioning](https://semver.org/) and [Conventional Commits](https://www.conventionalcommits.org/).

The public API is the set of reusable workflow inputs/outputs documented in [docs/BUILD_CONFIG.md](docs/BUILD_CONFIG.md). Changes to that interface that require consumer updates are marked as **BREAKING**.

---

## [Unreleased]

### Fixed
- **A build lane that has never completed can now start from another lane's warm `Library`.**
  `restore-keys` gained a bare `Library-` fallback behind the platform-scoped one.

  The cache key is `Library-<platform>-<hashFiles(ProjectVersion.txt, manifest.json)>`. Neither
  hashed file is platform-specific, so **the hash is identical for every lane** — only the label
  in front of it differs. Measured on `Cuvara/IndieRPGMMOAdventure`, 2026-08-21:

  ```
  Library-Android-ba667c16…    3666 MB
  Library-WebGL-ba667c16…      1649 MB
  Library-test-All-ba667c16…   1334 MB
  ```

  The Addressables lane asked for `Library-Addressables-ba667c16…`, missed, fell back to
  `Library-Addressables-`, missed again, and did a **fully cold import of 6000+ package assets on
  every run** — with 3.6 GB of a warm `Library` at the *identical content hash* sitting unused.

  It could not recover on its own: `actions/cache` writes only in the post step of a job that
  finishes, and that lane was being cancelled by the 120-minute timeout, so it never saved a cache
  to hit next time. A lane that cannot finish can never warm itself; only a cross-lane fallback
  breaks that.

  A `Library` from another build target is not wrong, only incomplete — Unity reimports the
  platform-specific artefacts and keeps the rest, which is far cheaper than importing everything
  from nothing.

### Changed
- **The `Addressables` lane names its build target instead of leaving it empty.** An empty target
  let GameCI apply its own default, which *is* `StandaloneLinux64` — confirmed from a build log
  (`-buildTarget StandaloneLinux64`), not from documentation. Behaviour is unchanged; the lane's
  actual target is now readable from the workflow instead of recoverable only from a log.

### Fixed

- **`ARCHITECTURE.md` drew the Docker lane as running `BuildCommand.Execute`; it runs
  `PlayerBuilder.Build`.** Both diagrams and the package-layer bullet corrected, and a *Build Entry
  Points* section added stating the two lanes side by side. `IOS.md` was right for the iOS-native
  lane and is left saying `BuildCommand.Execute`, now with the part that was missing: that entry
  point is `readonly` in `scripts/ios/run_unity_ios.sh:46`, unaffected by `build-method` or
  `UNITY_BUILD_METHOD`, and the package is a hard requirement there. Both files record that
  `reusable-build-platform.yml` never calls that script, so the two iOS routes disagree.
- **`docs/ADD_NEW_PROJECT.md` claimed "Your project does not need to implement build logic", which is
  false on the default lanes.** `reusable-build-platform.yml` runs `PlayerBuilder.Build` unless
  `build-method` says otherwise, and `PlayerBuilder` is implemented **by the consuming project** — so
  a project that installs the package and stops there has no entry point on Android, WebGL, Windows,
  Linux or the pipeline's own `Build iOS` job. New *Step 1b* states which lane runs which entry point,
  shows the signature `PlayerBuilder.Build` must have (public static, parameterless, global namespace,
  Editor assembly) and the environment variables CI supplies, and documents `UNITY_BUILD_METHOD` as
  the supported override. It also records the inconsistency rather than smoothing it: the iOS-native
  scripts hardcode `BuildCommand.Execute` as `readonly`, `reusable-build-platform.yml` never calls
  them, and `UNITY_BUILD_METHOD` does not reach that route.

- **`com.company.build-pipeline` did not compile against Unity 6, so `BuildCommand` did not exist at
  all.** Two unrelated causes, both fatal:
  - **Namespace shadowing, not a missing reference.** All four platform builders call
    `BuildPipeline.BuildPlayer(...)` from inside `namespace Company.BuildPipeline.Editor`, where the
    identifier `BuildPipeline` binds to the enclosing `Company.BuildPipeline` namespace rather than
    to `UnityEditor.BuildPipeline` — the nearer scope wins, and `using UnityEditor;` cannot override
    it. Hence `CS0234: 'BuildPlayer' does not exist in the namespace 'Company.BuildPipeline'`. Call
    sites are now fully qualified as `UnityEditor.BuildPipeline.BuildPlayer(...)`. This was never
    version-specific: the code could not have compiled on any Unity version.
  - **Unity 6 API removal.** `PlayerSettings.iOS.architecture` and the `iOSArchitecture` enum are
    gone (verified absent from the 6000.3.9f1 managed assemblies); the iOS player is ARM64-only, so
    there is no setting left to write. ARM64 was already the only App Store-valid value, so no
    capability is lost, but a config requesting anything else now logs a warning instead of being
    silently ignored.

- `unity-package/Packages/com.company.build-pipeline/package.json` — version was `1.0.0` while the
  consumer-pin tags had run to `v1.1.0`, so a package resolved by tag reported a manifest version
  that disagreed with the tag it came from — misleading to anyone resolving a version conflict.
  Set to `1.1.2` to match the tag that carries it. The repo-level `VERSION` (2.2.0) and the release
  history below are a separate series and are untouched.

### Added

#### Generic Consumer Integration (`feature/generic-consumer-integration`)

- `templates/BuildConfig.*.json` — De-game-ified all four BuildConfig templates: replaced `Acme Studios` / `com.acmestudios.myunitygame` / multi-scene game setup with generic `ExampleCompany` / `ExampleProject` / `com.example.project` / `Assets/Scenes/Main.unity`. Overlay templates (`development`, `staging`, `production`) now carry only environment-specific diffs; `base.json` is the complete, schema-valid source of truth.
- `templates/build-secrets.example.md` — Rewritten as a full secret matrix with required-for columns: **Development / Staging / Production / Artifact-only / Store-deploy**. Covers all secret groups: `UNITY_*`, `ANDROID_*`, `GOOGLE_PLAY_*`, `IOS_*`, `APP_STORE_CONNECT_*`, `DISCORD_WEBHOOK_URL`. Production secrets (`APP_STORE_CONNECT_*`, `GOOGLE_PLAY_*`) marked for GitHub Environment scoping.
- `docs/ADD_NEW_PROJECT.md` — Rewritten as a consumer-centric onboarding guide. Documents the consumer contract: Unity project + BuildConfig + UPM package dependency + small caller workflow + secrets. Includes the canonical caller YAML pattern (`uses: <WORKFLOW_OWNER>/unity-build-workflows/...@<ref>`), UPM manifest example, toolkit-checkout note, and `<ref>` guidance (dev=`@main`/SHA, stable=exact tag). Fixes iOS/Windows platform status: **iOS is supported** via the macOS lane; Windows is unsupported.
- `docs/ARCHITECTURE.md` — Updated consumer diagram to use `<WORKFLOW_OWNER>` placeholder and correct `<ref>` guidance.
- `docs/IMAGE_LIFECYCLE.md` — Added "Bootstrap" section: explains that a compatible image must be published before consumers can use it; documents the dev bootstrap path (manual `build-unity-image.yml` dispatch), the production digest-pinned path, and the actionable error when no image is found in the registry.
- `docs/ANDROID.md` — Added image bootstrap reference and aligned `<WORKFLOW_OWNER>` placeholders.
- `docs/SECURITY.md` — Standardized iOS secret names (`IOS_DISTRIBUTION_CERTIFICATE_BASE64`, `APP_STORE_CONNECT_PRIVATE_KEY`) throughout; aligned with secret matrix; added `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` to rotation policy.

### Changed

- `README.md` — Consumer-centric rewrite: UPM package step added as Step 1; `<WORKFLOW_OWNER>` placeholder used everywhere `BuzzelStudio` appeared; platform table references `docs/PLATFORM_MATRIX.md` as canonical source; versioning policy clarified: `@vMAJOR` tags do not exist yet, dev→`@main`/SHA, stable→exact tag; image disclaimer added (images must be published before use).
- `CHANGELOG.md` — All `BuzzelStudio` GitHub URL references replaced with `<WORKFLOW_OWNER>` placeholder.
- `CONTRIBUTING.md` — Clone URL and `@BuzzelStudio/mobile` team reference replaced with `<WORKFLOW_OWNER>` placeholder.
- All docs — `ghcr.io/buzzelstudio/unity-builder` replaced with `ghcr.io/<WORKFLOW_OWNER>/unity-builder` throughout owned files.

### Fixed

- **iOS supported/unsupported contradiction** — `docs/ADD_NEW_PROJECT.md` previously stated "iOS and Windows are **not supported**" — corrected to reflect that iOS is supported via the `macos-unity-xcode` executor (added in v2.1.0). Windows remains unsupported. Platform status is now consistent across `README.md`, `docs/ARCHITECTURE.md`, `docs/PLATFORM_LIMITATIONS.md`, and `docs/ADD_NEW_PROJECT.md`.
- **`macos-latest` claim removed** — `docs/ARCHITECTURE.md` and `docs/PLATFORM_LIMITATIONS.md` previously listed `macos-latest` as a valid runner for iOS. Corrected to `macos-13` only (approved, validated runner).
- **`@v2` / `@v2.0.0` tag claims** — All references claiming a specific version tag (e.g. `@v2`, `@v2.0.0`, `@v2.1.0`) is currently valid have been corrected. No version tags have been published yet. Documentation now distinguishes `@main` (dev), exact SHA (pinned), and `@vX.Y.Z` (stable — use once a release is published).
- **Package ID / UPM path** — `com.example.build-pipeline` was incorrectly used; corrected to `com.company.build-pipeline` (the canonical, intentionally neutral identifier). UPM path corrected to `/unity-package/Packages/com.company.build-pipeline#<WORKFLOW_REF>` (verified against on-disk structure).
- **Registry namespace** — Image references now use `ghcr.io/<IMAGE_NAMESPACE>/unity-builder` (configurable via `--image-namespace`) rather than a hardcoded org name.

---

## [2.2.0] — 2026-06-18

### Added

#### Discord Build Notifications

- `.github/actions/discord-notify/action.yml` — New composite action that posts a Discord embed on build completion via `curl` (no third-party action, no supply-chain exposure).
  - Inputs: `status`, `platform`, `environment`, `build-version`, `run-url`, `artifact-name` (optional), `extra-text` (optional).
  - Webhook URL read from `DISCORD_WEBHOOK_URL` environment variable — never an action input.
  - No-ops gracefully when `DISCORD_WEBHOOK_URL` is unset or empty (exit 0, notice log).
  - Uses `set +x` and `::add-mask::` to prevent the webhook URL from appearing in logs.
  - `curl` failures are non-fatal (`::warning::` only); a Discord outage cannot block a release.
  - Payload JSON constructed via `python3` for safe quoting; validated before sending.
  - Embed includes: title with status emoji (✅/❌/⚠️), color (green/red/grey), repository, platform, environment, version, short commit SHA, triggered-by, run URL, and (when available) artifact name.

#### Workflow wiring (`if: always()` — notifies on success AND failure)

- `unity-build.yml` — `DISCORD_WEBHOOK_URL` added to `secrets:` block; `discord-notify` step added to `report` job; reports the platform-resolved build result.
- `unity-build-ios.yml` — `DISCORD_WEBHOOK_URL` added to `secrets:` block; `discord-notify` step added as final step of `build` job.
- `unity-release-ios.yml` — `DISCORD_WEBHOOK_URL` passed via job-level `env:`; `discord-notify` step added as final step of `release-build` job (after signing cleanup).
- `unity-release.yml` — New dedicated `notify` job added with `if: always()`, depending on `pre-release-checks`, `release-test`, `release-build`, and `create-github-release`. Reports `release-build` result.

#### Documentation

- `docs/DISCORD_NOTIFICATIONS.md` — Full guide: what it does, creating a Discord webhook, which workflows notify, success/failure/cancelled behaviour, no-op-when-unset, security notes, example embed, and troubleshooting table.

### Changed

- `README.md` — Added `DISCORD_WEBHOOK_URL` to secrets section, added Step 5 for Discord setup, added `docs/DISCORD_NOTIFICATIONS.md` to the documentation index, bumped current version to 2.2.0.
- `docs/SECURITY.md` — Added "Discord Webhook Secret Handling" section documenting `set +x`, `::add-mask::`, env-var-not-input pattern, payload content policy, and rotation instructions. Added `DISCORD_WEBHOOK_URL` to the rotation policy table.
- `docs/ADD_NEW_PROJECT.md` — Replaced placeholder Step 7 with concrete Discord notification setup instructions.
- `docs/ARCHITECTURE.md` — Added `discord-notify/` to the Composite Action Layer diagram.

---

## [2.1.0] — 2026-06-18

This release adds production iOS support via a dedicated macOS executor lane.
iOS was previously unsupported (Docker-only platform). It is now a first-class
build target via the `macos-unity-xcode` executor.

**Semver guidance:** This is a **minor** (feature) release. The workflow input
interface gains a new reusable workflow (`unity-build-ios.yml`) and new
BuildConfig `ios` fields, but the existing Docker-lane interface is unchanged.
Consumer repositories targeting Android/WebGL/Linux do not need to update.

### Added

#### iOS Pipeline
- `unity-build-ios.yml` — Reusable workflow for the full iOS pipeline:
  Unity → Xcode project generation → archive → IPA export → (optional) TestFlight upload.
- `scripts/ios/build_ios.sh` — Unity batch-mode iOS build (Xcode project generation).
- `scripts/ios/setup_signing.sh` — Temp keychain creation, certificate import, provisioning profile install, ASC key write.
- `scripts/ios/archive_ios.sh` — `xcodebuild archive` with workspace/project auto-detection and scheme resolution.
- `scripts/ios/export_ios.sh` — `xcodebuild -exportArchive` with auto-generated `ExportOptions.plist`.
- `scripts/ios/upload_testflight.sh` — TestFlight upload via `xcrun altool` / `notarytool`.
- `scripts/ios/cleanup_ios.sh` — Unconditional cleanup: temp keychain, provisioning profile, ASC key file.
- `scripts/common/resolve_platform_executor.py` — Platform→executor resolver. iOS+macOS → `macos-unity-xcode`; Docker platforms+linux → `docker-unity`. Exits non-zero for cross-lane mismatches.

#### iOS BuildConfig Fields (new in `iOS` section)

> **Canonical key is `iOS`.** The lowercase alias `ios` is deprecated-but-accepted
> (schema `$refs` both to one definition). Use `iOS` in all new configs and templates.
- `marketingVersion` — CFBundleShortVersionString
- `sdkVersion` — `iphoneos` or `iphonesimulator`
- `architecture` — `ARM64` or `x86_64`
- `xcodeVersion` — pinned Xcode version
- `developmentTeamId` — 10-char Apple Team ID
- `signingStyle` — `manual` or `automatic`
- `provisioningProfileSpecifier` — profile name for manual signing
- `codeSignIdentity` — signing identity string
- `enableBitcode` — boolean (default false)
- `generateSymbols` — boolean (default true)
- `uploadSymbols` — boolean (default false)
- `uploadToTestFlight` — boolean (default false)

#### Tests
- `tests/test_ios_build_config.py` — iOS BuildConfig schema validation (valid, invalid bundle IDs, enums, contract fields).
- `tests/test_ios_executor_resolution.py` — Platform→executor resolution, iOS-on-Linux rejection, Docker-on-macOS rejection.
- `tests/test_ios_shell_scripts.py` — Shell script tests: archive/export success/failure, secret redaction, cleanup, artifact contract, missing IPA/archive, TestFlight rejection, fork rejection, Unity failure simulations.
- `tests/fixtures/valid_ios_config.json` — Full iOS config fixture.
- `tests/fixtures/valid_ios_minimal.json` — Minimal iOS config fixture.
- `tests/fixtures/invalid_ios_bundle_id.json` — Invalid bundle ID fixture.
- `tests/fixtures/fake_xcodebuild.sh` — Fake xcodebuild for macOS-free testing.

#### Documentation
- `docs/IOS.md` — Full iOS pipeline guide.
- `docs/IOS_SIGNING.md` — Certificate, provisioning profile, and ASC key setup.
- `docs/IOS_RELEASE.md` — Release workflow, GitHub Environment, versioning, TestFlight.

### Changed
- `docs/PLATFORM_LIMITATIONS.md` — iOS section rewritten from "unsupported" to "supported via macOS". Windows remains unsupported.
- `docs/ARCHITECTURE.md` — Added `macos-unity-xcode` executor lane alongside `docker-unity`. Added iOS to supported platforms table.
- `docs/BUILD_CONFIG.md` — `ios` object section fully documented with all contract fields.
- `docs/SECURITY.md` — Added iOS secret inventory, temp credential lifecycle, macOS-specific guidance, and updated rotation policy table.
- `docs/TROUBLESHOOTING.md` — Added iOS section: cert errors, Xcode migration, TestFlight issues, profile expiry, cert rotation.
- `README.md` — Added iOS integration example, iOS secrets reference, updated platform table and documentation index.

### Migration from v2.0.0

Existing Android/WebGL/Linux workflows are **unaffected**. iOS migration:

1. Add the `ios` BuildConfig block to your `base.json` (see [docs/IOS.md](docs/IOS.md))
2. Add iOS GitHub Secrets (see [docs/IOS_SIGNING.md](docs/IOS_SIGNING.md))
3. Create a caller workflow using `unity-build-ios.yml@v2`
4. For releases: create a `production` GitHub Environment (see [docs/IOS_RELEASE.md](docs/IOS_RELEASE.md))
5. If you previously redirected users away from iOS using `docs/PLATFORM_LIMITATIONS.md`, update any internal documentation pointing to that "unsupported" section.

---

## [2.0.0] — 2026-06-12

### BREAKING CHANGES

This release migrates the entire build platform from native Unity Editor execution to Docker-mandatory containers. **All consuming repositories must update their workflow references from `@v1` to `@v2`.**

#### Removed Workflows
- **`unity-build-ios.yml`** — iOS is unsupported by the Docker-only platform. Use a dedicated macOS pipeline. See [docs/PLATFORM_LIMITATIONS.md](docs/PLATFORM_LIMITATIONS.md).
- **`unity-build-windows.yml`** — Windows is unsupported by the Docker-only platform. Use a dedicated Windows pipeline.

#### Removed Workflow Inputs
- `executor-mode` — Docker is now mandatory and implicit.
- `use-docker` — Removed; Docker is the only executor.
- `native-runner` — Removed; no native execution path exists.

#### Removed Actions
- `actions/setup-unity/` — Docker images have Unity pre-installed. No host-side Unity installation needed.

#### Removed Scripts
- `scripts/run_unity_build.py` — Replaced by `scripts/docker/run_unity_container.py`.
- `scripts/ios/` — iOS pipeline removed.
- `scripts/windows/` — Windows pipeline removed.

#### Changed Workflow API
- All build/test workflows now require Docker Engine on the runner.
- `runs-on` changed to `ubuntu-latest` for all supported builds (was platform-specific).
- Image resolution is automatic from the approved image manifest.
- Nightly build matrix reduced to `[Android, WebGL, Linux64]`.

### Added

#### Docker Platform
- `docker/unity/Dockerfile` — Base Unity image extending pinned GameCI base.
- `docker/variants/android.Dockerfile` — Android build image with SDK/NDK/JDK.
- `docker/variants/webgl.Dockerfile` — WebGL build image.
- `docker/variants/linux.Dockerfile` — Linux standalone and dedicated server image.
- `docker/unity/entrypoint.sh` — Strict container entrypoint supporting build, test, validate, and inspect commands.
- `docker/unity/healthcheck.sh` — Image health verification.
- `docker/unity/activate-license.sh` — Ephemeral license activation.
- `docker/unity/return-license.sh` — License cleanup.
- `docker/metadata/image-manifest.schema.json` — Image manifest JSON Schema.

#### Docker Scripts
- `scripts/docker/run_unity_container.py` — Single entry point for running Unity in Docker (CI and local).
- `scripts/docker/build_unity_image.py` — Image build automation.
- `scripts/docker/validate_unity_image.py` — Image validation and compliance.
- `scripts/docker/resolve_image_reference.py` — Target platform to image resolution.
- `scripts/docker/generate_sbom.sh` — SBOM generation.
- `scripts/docker/scan_image.sh` — Vulnerability scanning.

#### New Workflows
- `build-unity-image.yml` — Dedicated image build, scan, and publish workflow.
- `scan-unity-image.yml` — Scheduled image vulnerability scanning.
- `unity-build-linux.yml` — Linux standalone and dedicated server builds.

#### New Actions
- `actions/resolve-unity-image/` — Resolve target platform to approved image.
- `actions/run-unity-container/` — Standardized Docker container execution.
- `actions/restore-docker-cache/` — Docker volume-based Library cache.

#### New Documentation
- `docs/DOCKER_BUILD.md` — Container execution flow documentation.
- `docs/IMAGE_LIFECYCLE.md` — Image strategy, scanning, SBOM, tagging.
- `docs/LINUX.md` — Linux platform documentation.
- `docs/PLATFORM_LIMITATIONS.md` — iOS/Windows exclusion rationale and alternatives.
- `docs/adr/001-docker-mandatory-architecture.md` — Architecture decision record.

#### Schemas
- `schemas/unity-image-manifest.schema.json` — Image manifest validation schema.

#### Tests
- `tests/test_docker_command.py` — Docker command construction tests.
- `tests/test_image_resolution.py` — Image reference resolution tests.
- `tests/test_image_manifest.py` — Image manifest schema tests.
- `tests/test_secret_redaction.py` — Secret leak prevention tests.
- `tests/test_no_native_unity_invocation.py` — Regression test preventing native Unity execution.
- `tests/test_entrypoint.py` — Entrypoint integration tests with fake Unity.

#### Platform Support
- Linux Standalone (Linux64) builds via Docker.
- Linux Dedicated Server (LinuxServer) builds via Docker.

### Changed
- All Unity compilation, tests, and builds now execute inside Docker containers.
- Cache strategy updated to use Docker volumes instead of host filesystem.
- License handling redesigned for ephemeral containers.
- All workflows use `ubuntu-latest` runners.
- Security model updated for container isolation.

### Migration Guide

1. Update workflow references: `@v1` → `@v2`
2. Remove `executor-mode`, `use-docker`, `native-runner` inputs from caller workflows.
3. Remove iOS and Windows build jobs (use dedicated non-Docker pipelines).
4. Ensure runners have Docker Engine available.
5. Update `UNITY_LICENSE` secret to contain the `.ulf` file content.
6. See [docs/ADD_NEW_PROJECT.md](docs/ADD_NEW_PROJECT.md) for the updated integration guide.

---

## [1.0.0] — 2024-06-12

### Added

#### Core Platform
- Reusable workflow `android.yml` — Android APK and AAB builds with debug and custom keystore signing, IL2CPP and Mono backends, configurable SDK versions and architectures.
- Reusable workflow `ios.yml` — Unity → Xcode project generation → IPA export pipeline with manual and automatic signing, App Store Connect upload via API key.
- Reusable workflow `windows.yml` — Windows Standalone x86_64 and x86 builds with IL2CPP (MSVC) and Mono support, optional output compression.
- Reusable workflow `webgl.yml` — WebGL builds with Brotli, Gzip, and Disabled compression formats; configurable memory size and HTML template.
- Reusable workflow `test.yml` — Unity Test Runner (EditMode and PlayMode) as a standalone workflow step.
- Reusable workflow `release.yml` — Tag-triggered production build and promotion pipeline with environment gating.

#### Configuration
- `schemas/unity-build-config.schema.json` — JSON Schema (Draft-07) validating the full BuildConfig object.
- Template BuildConfig files for base, development, staging, and production environments.

#### Unity Package
- `unity-package/com.company.build-pipeline/` — Unity Editor package with BuildCommand, validation rules, platform builders, and build hooks.

#### Documentation
- Complete documentation suite covering architecture, onboarding, platform guides, security, and troubleshooting.

---

[Unreleased]: https://github.com/<WORKFLOW_OWNER>/unity-build-workflows/compare/v2.2.0...HEAD
[2.2.0]: https://github.com/<WORKFLOW_OWNER>/unity-build-workflows/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/<WORKFLOW_OWNER>/unity-build-workflows/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/<WORKFLOW_OWNER>/unity-build-workflows/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/<WORKFLOW_OWNER>/unity-build-workflows/releases/tag/v1.0.0
