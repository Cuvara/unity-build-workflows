# Platform Limitations

> **Canonical platform matrix:** See [docs/PLATFORM_MATRIX.md](PLATFORM_MATRIX.md) for the authoritative, up-to-date platform support table (maintained by the platform architect). This document provides the rationale and limitations detail for each platform.

This repository operates two execution lanes:

- **`docker-unity`** — Docker-mandatory lane for Android, WebGL, and Linux builds. Runs on `ubuntu-latest`.
- **`macos-unity-xcode`** — Native macOS lane for iOS builds. Runs on `macos-13` (approved runner; `macos-latest` is not used — it floats and is not validated).

The platform executor resolver (`scripts/common/resolve_platform_executor.py`) selects the correct lane automatically based on `target-platform`. Docker and macOS builds are **mutually exclusive** — each platform runs in exactly one lane.

## Supported Platforms

### iOS

**Status:** Supported via the `macos-unity-xcode` executor (macOS runner, native Xcode).

**Executor:** `macos-unity-xcode` — a macOS GitHub Actions runner with Unity and Xcode pre-installed.

**Why macOS is required:** Unity iOS builds produce an Xcode project that requires the macOS-native Xcode toolchain for:
- Code signing with Apple certificates (`security` + keychain)
- Provisioning profile installation
- IPA archive export (`xcodebuild archive`, `xcodebuild -exportArchive`)
- App Store Connect upload (`xcrun altool` / `notarytool`)

**Docker is not used for iOS.** Running Xcode inside a Linux container is not possible.

**How to use:** Add `target-platform: iOS` to your caller workflow. The resolver automatically selects the `macos-unity-xcode` executor.

**Attempting iOS on a Linux runner** produces:
```
ERROR: Platform 'iOS' requires executor 'macos-unity-xcode' (macOS runner).
Current runner-os is 'linux', which is incompatible.
Use an approved macOS runner: runs-on: macos-13
```

**Full documentation:** [docs/IOS.md](IOS.md), [docs/IOS_SIGNING.md](IOS_SIGNING.md), [docs/IOS_RELEASE.md](IOS_RELEASE.md)

## Partially supported platforms

### Windows — supported, with one real limit

**Status:** Supported. `Windows64` builds on the Docker lane and ships through
`Release / Windows` to Steam. This page previously said "unsupported by this
repository", which contradicted
[PLATFORM_MATRIX.md](PLATFORM_MATRIX.md) — the authoritative matrix — and was
disproved by a real release build.

**The limit is the scripting backend, not the platform.** The Docker lane
cross-compiles to `StandaloneWindows64` using the `windows-mono` editor image,
so it produces **Mono** binaries. A Linux container cannot produce IL2CPP
Windows binaries: IL2CPP emits C++ that MSVC has to compile, and MSVC does not
run there.

| You need | Lane |
|---|---|
| Mono Windows build | Docker on `ubuntu-latest` — the default, nothing to configure |
| IL2CPP Windows build | Self-hosted Windows runner with Unity installed: `runner-type=self-hosted`, `build-engine=local` |

See [SELF_HOSTED_WINDOWS_RUNNER.md](SELF_HOSTED_WINDOWS_RUNNER.md) for the
second lane, and [PLATFORM_MATRIX.md](PLATFORM_MATRIX.md) for every
platform/executor combination.

## Unsupported platforms

None of the five first-class platforms is unsupported. What is *not* built
automatically is iOS: it needs a macOS runner, so it is dispatch-only and never
joins an `All` build — a selection whose result depends on infrastructure is
not a selection.

## Platform-Specific Limitations

### Graphical PlayMode Tests

PlayMode tests that require a GPU or display server may not work in headless Linux containers. EditMode tests run without issues.

**Workaround:** Use `Xvfb` (X Virtual Framebuffer) if PlayMode tests require a display. The entrypoint supports this when the container includes Xvfb.

### GPU-Dependent Features

Unity features requiring GPU access (e.g., GPU skinning, compute shaders in editor) are unavailable in standard Docker containers.

**Impact:** Build-time shader compilation works (cross-compilation). Runtime GPU testing does not.

### Native Plugins

Native plugins (.so, .dll, .dylib) must be compatible with the container's Linux environment during the build phase. Platform-specific native plugins for the target platform (e.g., Android .so files) are included in the build output but not executed during the build.

### Cross-Compilation

| Build Target | Executor | Runner OS | Compilation |
|---|---|---|---|
| Android | `docker-unity` | Linux | Cross-compilation via Android SDK/NDK |
| WebGL | `docker-unity` | Linux | Cross-compilation via Emscripten |
| Linux64 | `docker-unity` | Linux | Native compilation |
| LinuxServer | `docker-unity` | Linux | Native compilation |
| iOS | `macos-unity-xcode` | macOS | Native — Xcode on macOS |
| Windows64 | — | — | **Unsupported** — requires Windows containers |

### Container Resource Limits

Unity builds are memory-intensive. Recommended minimums:

| Build Type | Memory | CPU |
|---|---|---|
| Android (IL2CPP) | 8 GB | 4 cores |
| WebGL | 8 GB | 4 cores |
| Linux (IL2CPP) | 6 GB | 4 cores |
| EditMode Tests | 4 GB | 2 cores |

Set limits via `--container-memory` and `--container-cpus` flags in `run_unity_container.py`.

### Android SDK/NDK Version Constraints

The Android image variant pins specific SDK and NDK versions. If your project requires different versions:

1. Update `docker/variants/android.Dockerfile`
2. Rebuild the image
3. Update the image manifest

Do not attempt to install SDK components at build time — this breaks reproducibility.
