# Architecture

`unity-build-workflows` is a Docker-mandatory CI/CD platform for Unity games. All Unity operations run inside pinned, versioned Docker containers. The CI runner is an orchestrator only.

---

## Layer Overview

```
┌──────────────────────────────────────────────────────────────────┐
│  Consumer Repository (.github/workflows/build.yml)               │
│  • calls reusable workflows via uses: <WORKFLOW_OWNER>/unity-build-  │
│    workflows/.github/workflows/<workflow>.yml@<ref>              │
│  • provides BuildConfig JSON + GitHub secrets                    │
└───────────────────────────┬──────────────────────────────────────┘
                            │ workflow_call
┌───────────────────────────▼──────────────────────────────────────┐
│  Workflow Layer  (.github/workflows/)                             │
│  • unity-build.yml (main orchestrator)                           │
│  • unity-build-android.yml, unity-build-webgl.yml,               │
│    unity-build-linux.yml                                         │
│  • unity-build-ios.yml  ← NEW (macOS runner, Xcode)             │
│  • unity-test.yml, unity-validate.yml                            │
│  • unity-release.yml, unity-nightly.yml                          │
│  • build-unity-image.yml, scan-unity-image.yml                   │
└───────────────────────────┬──────────────────────────────────────┘
                            │ uses: ./.github/actions/<name>
┌───────────────────────────▼──────────────────────────────────────┐
│  Composite Action Layer  (.github/actions/)                      │
│  • resolve-unity-image/  — target → image resolution            │
│  • run-unity-container/  — Docker container execution            │
│  • restore-docker-cache/ — Library cache volumes                 │
│  • collect-container-output/ — logs, reports, artifacts          │
│  • upload-build-report/  — build report upload                  │
│  • discord-notify/       — Discord build-completion notification │
└───────────────────────────┬──────────────────────────────────────┘
                            │ executes
┌───────────────────────────▼──────────────────────────────────────┐
│  Docker Layer                                                     │
│  • scripts/docker/run_unity_container.py  — Docker wrapper       │
│  • scripts/docker/resolve_image_reference.py — image resolution  │
│  • docker/unity/entrypoint.sh  — container entrypoint            │
│  • docker/unity/activate-license.sh — license activation         │
└───────────────────────────┬──────────────────────────────────────┘
                            │ invokes
┌───────────────────────────▼──────────────────────────────────────┐
│  Unity Layer (inside container)                                   │
│  • Unity Editor -batchmode -executeMethod                        │
│    PlayerBuilder.Build  (consumer-provided; the default)          │
│  • BuildConfigurationLoader → BuildValidator → PlatformBuilder   │
│  • Reports and artifacts written to bind-mounted directories     │
└───────────────────────────┬──────────────────────────────────────┘
                            │ reads
┌───────────────────────────▼──────────────────────────────────────┐
│  Config Layer                                                     │
│  • BuildConfig.*.json   — per-environment build configuration    │
│  • schema validation    — JSON Schema v7 enforced at CI entry    │
└──────────────────────────────────────────────────────────────────┘
```

---

## Execution Flow

```
CI Runner (ubuntu-latest)
  │
  ├── 1. Checkout project
  ├── 2. Resolve Unity image (resolve-unity-image action)
  │       └── Maps target-platform → image variant → digest-pinned reference
  ├── 3. Restore Docker cache volumes (restore-docker-cache action)
  ├── 4. Run Unity in container (run-unity-container action)
  │       └── docker run --rm --init --user $(id -u):$(id -g)
  │             └── entrypoint.sh build --target-platform Android ...
  │                   └── activate-license.sh
  │                   └── Unity -batchmode -executeMethod PlayerBuilder.Build
  │                   └── copy Editor.log to Logs/
  │                   └── return-license.sh
  │                   └── cleanup trap
  ├── 5. Collect container output (collect-container-output action)
  │       └── Gather logs, reports, test results from bind mounts
  ├── 6. Upload artifacts and reports
  ├── 7. Post-build steps (signing, deployment) on host
  └── 8. Discord notification (discord-notify action — no-op if DISCORD_WEBHOOK_URL unset)
```

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

## Build Entry Points — two lanes that do not agree

Which C# method Unity executes depends on which lane runs, and the toolkit is not currently
consistent across them:

| Lane | Entry point | Where it comes from |
|---|---|---|
| Docker / game-ci and self-hosted — Android, WebGL, Windows, Linux, and the pipeline's `Build iOS` job | `PlayerBuilder.Build`, **implemented by the consuming project** | `reusable-build-platform.yml` `BUILD_METHOD` default (`:872`; `:812` for the Windows variant), overridable via the `build-method` input / `UNITY_BUILD_METHOD` repo variable |
| iOS native — `unity-build-ios.yml`, `unity-release-ios.yml` | `Company.BuildPipeline.Editor.BuildCommand.Execute`, from this package | `scripts/ios/run_unity_ios.sh:46`, `readonly`, **not overridable** |

`reusable-build-platform.yml` never invokes `scripts/ios/run_unity_ios.sh`, so the two iOS routes do
not share an entry point and `UNITY_BUILD_METHOD` has no effect on the native one. Resolving this
means teaching `run_unity_ios.sh` to accept `build-method`; that is a code change and has not been
made. Documented here so the disagreement is visible rather than discovered.

See [ADD_NEW_PROJECT.md](ADD_NEW_PROJECT.md) Step 1b for what a consumer must provide.

---

## Unity Package Layer

The `unity-package/` directory contains a Unity Editor package (`com.company.build-pipeline`) providing:

- **BuildCommand.Execute** — C# entry point invoked via `-executeMethod` by the **iOS-native**
  workflows only. The Docker and self-hosted lanes default to `PlayerBuilder.Build`, which the
  consuming project provides — see below
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
