# Architecture

> **Scope.** This describes the toolkit's internals — layers, resolvers,
> executors, the Docker lane. It predates the release layer and says nothing
> about Release Sets, the immutable artifact boundary or promotion; for those
> read [PIPELINE_ARCHITECTURE.md](PIPELINE_ARCHITECTURE.md).

`unity-build-workflows` is a Docker-mandatory CI/CD platform for Unity games. All Unity operations run inside pinned, versioned Docker containers. The CI runner is an orchestrator only.

---

## Layer Overview

```
┌──────────────────────────────────────────────────────────────────┐
│  Consumer Repository (.github/workflows/, from templates/)        │
│  • 01-ci / 10-build-development / 11-build-release               │
│      uses: …/unity-pipeline.yml@v7                               │
│  • 20…24-release-<platform>                                      │
│      uses: …/pipeline-<platform>-release.yml@v7                  │
│  • configuration in Repository Variables; secrets               │
└───────────────────────────┬──────────────────────────────────────┘
                            │ workflow_call
┌───────────────────────────▼──────────────────────────────────────┐
│  Workflow Layer  (.github/workflows/)                             │
│  • unity-pipeline.yml — stages 01–08: resolve, validate, tests,  │
│    build matrix, artifact validation, report, Discord            │
│  • reusable-build-platform.yml — the one executor (per platform) │
│  • reusable-unity-tests.yml — EditMode / PlayMode                │
│  • pipeline-<platform>-release.yml — promote a Build / Release   │
│    artifact (Windows / Linux share pipeline-desktop-release.yml) │
│  • build-unity-image.yml, scan-unity-image.yml, license, CI      │
└───────────────────────────┬──────────────────────────────────────┘
                            │ uses: ./.toolkit/.github/actions/<name>
┌───────────────────────────▼──────────────────────────────────────┐
│  Composite Action Layer  (.github/actions/)                      │
│  • deliver-build — Firebase / R2 / stores from the build runner  │
│  • verify-release-artifact, steam-publish, release-report        │
│  • ios-setup-signing, ios-archive-export, ios-testflight, …      │
│  • discord-upload-build, pipeline-progress, setup-fastlane       │
└───────────────────────────┬──────────────────────────────────────┘
                            │ runs
┌───────────────────────────▼──────────────────────────────────────┐
│  Script Layer  (scripts/)                                         │
│  • resolvers: resolve_build_flow.sh, resolve_build_matrix.sh,    │
│    runner_scheduler.py, resolve_platform_executor.py, …          │
│  • build: run_unity_player.sh (every native lane, tests too),    │
│    docker_windows_container.sh, write_build_result.py            │
│  • reports: pipeline_results.py, release_manifest.py             │
└───────────────────────────┬──────────────────────────────────────┘
                            │ invokes
┌───────────────────────────▼──────────────────────────────────────┐
│  Unity Layer                                                      │
│  • Docker lane: game-ci/unity-builder (its default builder, or   │
│    build-method / UNITY_BUILD_METHOD)                            │
│  • native lanes: Unity -batchmode -executeMethod                 │
│    Company.BuildPipeline.Editor.PlayerBuilder.Build (toolkit     │
│    package, copied in per build) or the project's PlayerBuilder  │
└──────────────────────────────────────────────────────────────────┘
```

---

## Execution Flow

```
unity-pipeline.yml
  │
  ├── 01 Prepare     resolve_build_flow.sh → runner_scheduler.py →
  │                  resolve_build_matrix.sh (one row per platform job)
  ├── 02 Quality     validate project / license, Unity tests → gate
  ├── 03 Build       reusable-build-platform.yml, one job per matrix row:
  │                    checkout, toolkit, (SSH submodules), preflight,
  │                    build on the row's lane (Docker / Windows / macOS),
  │                    manifest, upload, (iOS sign + export),
  │                    deliver-build, log summary, write_build_result.py
  ├── 04 Validate    artifact validation per platform
  ├── 05 Release Set release manifest (Build / Release only)
  └── 07 Report      pipeline_results.py → step summary + Discord
```

Releases are a separate run: `pipeline-<platform>-release.yml` downloads the
Build / Release artifact by `source-run-id`, verifies it against the release
manifest in every job that holds it, and publishes it phase by phase. See
[PIPELINE_ARCHITECTURE.md](PIPELINE_ARCHITECTURE.md).

`scripts/docker/run_unity_container.py` + `docker/unity/entrypoint.sh` remain
for local Docker builds (`README.md` § Local Build Commands).

---

## Docker Image Strategy

Images extend pinned GameCI base images with an organizational tooling layer:

```
unityci/editor:6000.0.26f1-android-3  (GameCI base, pinned)
  └─ ghcr.io/<IMAGE_NAMESPACE>/unity-builder:6000.0.26f1-android-v2.0.0
       └─ entrypoint.sh, license scripts, healthcheck, python3, jq
```

See [IMAGE_LIFECYCLE.md](IMAGE_LIFECYCLE.md) for full image strategy.

---

## Executor Lanes

The platform has two executor lanes selected automatically by `scripts/common/resolve_platform_executor.py`:

### docker-unity Lane (Linux runners)

```
CI Runner (ubuntu-latest)
  └── Docker Engine
        └── ghcr.io/<IMAGE_NAMESPACE>/unity-builder@sha256:<digest>
              └── entrypoint.sh → Unity -batchmode -executeMethod
```

### macos-unity-xcode Lane (macOS runners)

```
CI Runner (macos-13)
  ├── Unity (native, pre-installed)
  │     └── Unity -batchmode -buildTarget iOS → Builds/iOS/Xcode/
  └── Xcode (native, selected via xcode-select)
        ├── xcodebuild archive → Builds/iOS/Archive/
        ├── xcodebuild -exportArchive → Builds/iOS/Export/
        └── xcrun altool → TestFlight (optional)
```

The two lanes are **mutually exclusive** — each platform resolves to exactly one executor.

### Runner scheduling (which machine runs a lane)

The lanes say *what kind* of machine a platform needs. Which concrete machine
runs it is decided in stage 01 by `scripts/common/runner_scheduler.py`, one job at
a time:

```
resolve_build_flow.sh  ──legacy answer──▶  runner_scheduler.py  ──runner-selection──▶  matrix rows → runs-on
                                             │  requirements ← resolve_platform_executor.allowed_runner_os()
                                             │  inventory    ← runner_inventory.py (GitHub runner API, optional)
                                             └  capability → availability → priority → fallback
```

With no runner policy, it passes the legacy answer through unchanged. With one,
it picks a self-hosted runner first and uses GitHub-hosted only when the policy
allows. See [MULTI_RUNNER_SCHEDULING.md](MULTI_RUNNER_SCHEDULING.md) and
[ADR 004](adr/004-runner-selection.md).

## Supported Platforms

| Platform | Executor | Runner OS | Support |
|---|---|---|---|
| Android | `docker-unity` | ubuntu-latest | Full — Docker, cross-compilation via Android SDK/NDK |
| WebGL | `docker-unity` | ubuntu-latest | Full — Docker, cross-compilation via Emscripten |
| Linux64 | `docker-unity` | ubuntu-latest | Full — Docker, native compilation |
| LinuxServer | `docker-unity` | ubuntu-latest | Full — Docker, native compilation |
| iOS | `macos-unity-xcode` | macos-13 | Full — native macOS + Xcode pipeline (approved runner) |
| Windows64 | — | — | **Unsupported** — requires Windows containers |

See [PLATFORM_LIMITATIONS.md](PLATFORM_LIMITATIONS.md).

---

## Build Entry Points

Which C# method Unity executes depends on the lane:

| Lane | Entry point | Where it comes from |
|---|---|---|
| **Docker / game-ci** — the default for Android, WebGL, Linux, and the pipeline's `Build iOS` job | **game-ci's own default builder.** No consumer method is required | `reusable-build-platform.yml` — `build-method` defaults to `''` and is passed straight through as `buildMethod`. Empty means game-ci decides. Set the `build-method` input or the `UNITY_BUILD_METHOD` repo variable to override |
| **Self-hosted** (Windows and macOS local-engine lanes) and **Docker on a Windows runner** | `Company.BuildPipeline.Editor.PlayerBuilder.Build`, **from this package**, which the build job copies into the project's `Packages/` for the build | `scripts/build/run_unity_player.sh`: the `build-method` input, else the "Install toolkit build package" step's `player-method` output. A project that still has its own global `PlayerBuilder` class gets `PlayerBuilder.Build` (its own) instead |

No lane needs a build script in the consumer project. The Addressables steps call the package's
`AddressableBuilder` the same way. Details: [TOOLKIT_BUILD_PACKAGE.md](TOOLKIT_BUILD_PACKAGE.md).

7.0.0 removed the native iOS workflows (`unity-build-ios.yml`, `unity-release-ios.yml`), whose
`BuildCommand.Execute` entry point was the one lane that `UNITY_BUILD_METHOD` could not change —
see [MIGRATION_V7.md](MIGRATION_V7.md).

---

## Unity Package Layer

The `unity-package/` directory contains a Unity Editor package (`com.company.build-pipeline`) providing:

- **PlayerBuilder / AddressableBuilder** — the `-executeMethod` targets of the native lanes
  (above)
- **BuildCommand.Execute** — the BuildConfig-driven entry point. No toolkit workflow invokes
  it since 7.0.0 (it was the removed native iOS route's); a project may still call it from its
  own editor tooling
- **BuildConfigurationLoader** — Loads and merges BuildConfig JSON
- **BuildValidator** — Runs validation rules before build
- **PlatformBuilders** — Platform-specific build logic (Android, WebGL, Linux)
- **BuildHookRegistry** — Lifecycle hooks (BeforeValidation, BeforeBuild, AfterBuild)
- **BuildReportExporter** — Exports build reports to JSON/Markdown

Docker changes only the execution environment. All build logic stays in typed C# code.

---

## Config Layer

BuildConfig files follow a layered merge pattern:

```
base.json  ←  environment.json  →  merged config (validated)  →  passed to container
```

The merged config is validated against `schemas/unity-build-config.schema.json` before any build steps run.

---

## Extension Points

### Lifecycle Hooks

Implement `IBuildHook` in the Unity package. Hooks run inside the container at:
- BeforeValidation
- BeforeBuild
- AfterBuild

### Custom Validation Rules

Implement `IBuildValidationRule` and register in the build pipeline.

### Custom Platform Builders

Implement `IPlatformBuilder` for additional build targets.

---

## Error Handling

| Failure Type | Behavior |
|---|---|
| Unsupported platform | Fails before Docker invocation with actionable error |
| Image not found | Fails with registry/version guidance |
| Schema validation failure | Pipeline fails immediately with field path |
| Unity license failure | Container preserves activation log; pipeline fails |
| Unity build failure | Editor.log + reports persisted via bind mounts; pipeline fails |
| Missing expected artifact | Wrapper fails even if Unity exits 0 |
| Container OOM/timeout | Exit code 137 propagated; logs preserved if possible |

All cleanup steps use `if: always()` to ensure license return and temp file deletion.

---

## Security Model

See [SECURITY.md](SECURITY.md) for the full security documentation.

Key principles:
- Containers run non-privileged with `--cap-drop=ALL`
- No Docker socket mount in build containers
- Secrets injected at runtime, never in image layers
- Production builds use digest-pinned images
- Image security scanning before publication
