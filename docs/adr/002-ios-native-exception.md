# ADR-002: iOS Native Executor Exception to Docker-Mandatory Architecture

> **Amendment (later).** Windows64 is no longer unsupported: the Docker lane
> cross-compiles it with the `windows-mono` image, and a self-hosted Windows
> runner covers IL2CPP. The decision recorded below stands as written — an ADR
> records what was decided when, not what is true now. See
> [PLATFORM_MATRIX.md](../PLATFORM_MATRIX.md).

**Status:** Accepted
**Date:** 2026-06-18
**Decision Makers:** BuzzelStudio Build Platform Team
**Supersedes:** ADR-001 (extends, does not replace)

## Context

ADR-001 established that all Unity compilation must run inside Docker containers on Linux runners.
This eliminated environment drift, reduced runner cost, and produced a versioned, auditable build
environment. At the time, iOS was explicitly excluded with the notation:
_"Unsupported — use dedicated macOS pipeline"._

iOS is now a target the organisation needs to ship. This ADR documents the controlled exception
to the Docker-mandatory invariant and establishes the governing rules under which that exception
operates.

### Why iOS Cannot Use Docker

1. **Xcode toolchain lock-in.** Unity iOS builds generate an Xcode project that must be archived
   and signed by Xcode on macOS. Xcode is macOS-only software with no Linux port.
2. **Apple signing runtime.** Certificate import (`security`), provisioning profile installation,
   and `xcrun altool`/`xcrun notarytool` calls require the Apple-provided macOS keychain.
3. **Simulator and device builds.** iOS SDK headers and frameworks are only distributed as part of
   the macOS Xcode installation; there is no binary-compatible Linux alternative.
4. **No verified Docker path.** No industry-accepted, license-compliant Docker image for full
   Unity iOS → IPA production builds exists as of this writing.

### Invariant Being Preserved

The Docker-mandatory invariant reads:
> _All Unity compilation, testing, Addressables builds, player builds, and validation must run
> inside Docker containers. The CI runner serves only as an orchestrator._

iOS is admitted as a **narrow, bounded exception** only for the Xcode archive/sign/export
phase and the macOS-side Unity IL2CPP compilation. The invariant is preserved for all other
platforms and for the Unity build step that precedes Xcode toolchain invocation.

## Decision

**iOS builds run on the `macos-unity-xcode` executor.** All other platforms continue to use the
`docker-unity` executor. No generic executor-selection input is exposed — the platform resolver
determines the executor automatically and exclusively.

### Executor Resolution Policy

The module `scripts/common/resolve_platform_executor.py` is the single source of truth.

| Platform            | Executor             | Notes                                      |
|---------------------|----------------------|--------------------------------------------|
| Android             | `docker-unity`       | Linux container, Docker-mandatory          |
| WebGL               | `docker-unity`       | Linux container, Docker-mandatory          |
| StandaloneLinux64   | `docker-unity`       | Linux container, Docker-mandatory          |
| LinuxServer         | `docker-unity`       | Linux container, Docker-mandatory          |
| iOS                 | `macos-unity-xcode`  | Approved macOS runner, Xcode required      |
| Windows64           | _(unsupported)_      | Explicitly unsupported, no executor        |

**No workflow input exposes `executor-mode`, `use-docker`, `allow-native`, or similar knobs.**
The platform alone determines the executor. Consumers cannot opt out of Docker for Android/WebGL/
Linux, and cannot opt into Docker for iOS.

### Contract Error Strings

These exact strings are enforced by `resolve_platform_executor.py` and the Docker-path guards:

- **iOS on Linux/Docker:**
  ```
  Target `iOS` requires an approved macOS runner with Xcode and Unity iOS Build Support. Linux Docker execution is not supported.
  ```

- **Docker platform run natively (e.g. Android on macOS):**
  ```
  Target `Android` must use the Docker Unity executor. Native Unity execution is prohibited.
  ```
  _(Substitute the actual platform name for `Android`.)_

### Phase Boundaries

The iOS pipeline is divided into three strictly separated phases to maintain auditability and
minimise macOS runner time:

| Phase | Responsibility | Executor |
|-------|---------------|----------|
| **Unity Build** | IL2CPP compilation → Xcode project generation | `macos-unity-xcode` |
| **Xcode Archive** | `xcodebuild archive` → unsigned/signed `.xcarchive` | `macos-unity-xcode` |
| **Sign & Deploy** | Certificate import, export IPA, upload to App Store Connect / TestFlight | `macos-unity-xcode` |

All three phases run on the same approved macOS runner. The runner is ephemeral — credentials
exist only for the duration of the job and are deleted from the keychain before the runner is
released.

### Shared Report and Artifact Contracts

To maintain consistency with the Docker-based pipeline:

- **Build report:** JSON file at `BuildReports/<platform>-build-report.json` using the same
  schema as Android/WebGL reports (keys: `platform`, `unityVersion`, `buildTime`, `outputSize`,
  `success`, `warnings`, `errors`).
- **Artifacts:** IPA and dSYM uploaded as GitHub Actions artifacts under
  `<projectName>-ios-<environment>-<buildNumber>`.
- **Logs:** Unity Editor log at `Logs/unity-build.log`; Xcode build log at
  `Logs/xcode-archive.log`.
- **Metadata:** `BuildReports/build-metadata.json` with the same fields as other platforms plus
  `xcodeVersion` and `provisioningStyle`.

### Secrets Policy

No signing credentials appear in `BuildConfig` files. All secrets are GitHub Actions secrets:

| Secret | Purpose |
|--------|---------|
| `IOS_DISTRIBUTION_CERTIFICATE_BASE64` | P12 distribution certificate (base64) |
| `IOS_DISTRIBUTION_CERTIFICATE_PASSWORD` | P12 password |
| `IOS_PROVISIONING_PROFILE_BASE64` | Provisioning profile (base64) |
| `APP_STORE_CONNECT_KEY_ID` | App Store Connect API key ID |
| `APP_STORE_CONNECT_ISSUER_ID` | App Store Connect issuer ID |
| `APP_STORE_CONNECT_PRIVATE_KEY` | App Store Connect private key (PEM) |
| `UNITY_LICENSE` / `UNITY_EMAIL` / `UNITY_PASSWORD` | Unity activation (shared with Docker path) |

### BuildConfig `iOS` Block

The `iOS` key in `BuildConfig` files carries non-secret build metadata only. See
`schemas/unity-build-config.schema.json` for the typed schema. Key constraints:

- `bundleIdentifier` — reverse-DNS format, validated at config-read time
- `signingStyle: "manual"` requires both `provisioningProfileSpecifier` and `codeSignIdentity`
- `uploadToTestFlight: true` is a _request flag_ only; the actual upload is gated by the
  workflow's deploy step and the presence of App Store Connect secrets
- No certificate data, no profile data, no API keys

## Consequences

### Positive

- iOS pipeline is enabled without compromising the Docker invariant for other platforms.
- Executor selection is automated and tamper-resistant — consumers cannot misconfigure it.
- Signing credentials never appear in source control.
- Phase separation makes it straightforward to add Windows64 later without disrupting iOS.

### Negative / Risks

- macOS runners are more expensive than Linux runners. iOS builds will incur higher CI cost.
- The macOS runner is a larger attack surface than a rootless Linux container. Mitigated by:
  ephemeral runners, post-job keychain cleanup, and secret rotation policy.
- Unity license activation on macOS uses the same credential set as Docker; a license leak
  affects both paths. Mitigated by using floating seats, not personal seats, for CI.
- Apple signing infra (certificates, profiles, App Store Connect API) has its own renewal
  cadence — pipeline breakage from expired credentials is a real operational risk.

### Out of Scope

- Windows64: remains explicitly unsupported. A future ADR-003 would handle it.
- Catalyst / visionOS / tvOS: not evaluated; require separate ADRs.
- Self-hosted macOS runners vs. GitHub-hosted: runner procurement is an infra decision
  outside this ADR's scope.

## References

- ADR-001: Docker-Mandatory Unity Build Architecture
- `scripts/common/resolve_platform_executor.py` — executor resolver implementation
- `schemas/unity-build-config.schema.json` — typed `iOS` BuildConfig block
- `scripts/common/validate_workflow_contract.py` — iOS config validation
- `docs/PLATFORM_LIMITATIONS.md` — runtime platform support matrix
