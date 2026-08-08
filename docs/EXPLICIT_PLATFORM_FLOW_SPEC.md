# Explicit Platform Flow — Interface Contract & Architecture Spec

> **Status:** DRAFT — published by workflow-architect for implementation engineers  
> **Branch:** `feature/explicit-platform-jobs`  
> **Unity version SSOT:** `ProjectSettings/ProjectVersion.txt` → `6000.0.26f1`  
> **Date:** 2026-06-29

---

## 0 — Architecture Decision: Reusable Workflows (Option A)

**Decision: Option A — reusable workflows (`workflow_call`).**

Each explicit platform job in the consumer workflow calls `uses: toolkit/.github/workflows/reusable-build-platform.yml`. A `workflow_call` reusable workflow runs as its **own named job** in the GitHub Actions UI job graph, giving:

- Each platform appears as a **distinct, independently-coloured status node** (Android ✓, WebGL ✗, …).
- Per-platform **retry** in the UI without re-running the whole matrix.
- Clean **needs:** / **if:** fan-out per job, which a composite action inside one job cannot give.
- Separate runner selection (`runs-on`) per call — critical for the iOS / self-hosted-macOS lane.

A composite action (Option B) executes inside the *caller's* job — all platforms would collapse into one job node. That directly contradicts the UI graph requirement.

---

## 1 — Files Created / Changed

### Toolkit repo (`unity-build-workflows`)

| File | Status | Owner |
|---|---|---|
| `.github/workflows/reusable-build-platform.yml` | **CREATE** | platform-build-engineer |
| `.github/workflows/reusable-unity-tests.yml` | **CREATE** | unity-test-runner-engineer |
| `docs/EXPLICIT_PLATFORM_FLOW_SPEC.md` | **CREATE** | workflow-architect (this file) |

### Consumer repo (`NDC-Unity-Template`)

| File | Status | Owner |
|---|---|---|
| `.github/workflows/unity-build.yml` | **REWRITE** | lead (after skeleton approved) |
| `.github/workflows/unity-build.yml.NEW` | **CREATE (skeleton)** | workflow-architect |

### Existing files — **do not modify**

| File | Notes |
|---|---|
| `.github/workflows/unity-build-gameci.yml` | Kept as fallback until new flow is proven |
| `scripts/common/resolve_activation_strategy.sh` | Consumed unchanged |
| `docs/UNITY_PERSONAL_DOCKER_LICENSE.md` | Reference for activation logic |

---

## 2 — Reusable Workflow Interface Contracts

### 2.1 `reusable-build-platform.yml`

**Purpose:** Build a single Unity platform target. Supports both the Docker lane (game-ci/unity-builder, Ubuntu) and the self-hosted-windows lane (local Unity installation, no Docker, no secrets required).

```yaml
on:
  workflow_call:
    inputs:
      # ── Required ────────────────────────────────────────────────────────────
      platform:
        description: |
          Target platform. One of:
            Android | WebGL | Linux64 | LinuxServer | iOS | Addressables
          "Addressables" is a pseudo-platform: runs the Addressable build pipeline
          only (no binary output). Use for the build-addressables job.
          "iOS" requires runner-mode=self-hosted-macos or it will fail cleanly.
        required: true
        type: string

      # ── Version & paths ─────────────────────────────────────────────────────
      unity-version:
        description: Unity version string (e.g. 6000.0.26f1). Must be resolved by caller.
        required: true
        type: string

      project-path:
        description: Path to the Unity project root relative to the workspace root.
        required: false
        default: '.'
        type: string

      toolkit-ref:
        description: |
          Git ref of the toolkit repo to use for toolkit scripts
          (e.g. main, feature/explicit-platform-jobs).
        required: false
        default: 'main'
        type: string

      # ── Runtime behaviour ───────────────────────────────────────────────────
      runner-mode:
        description: |
          Execution lane:
            docker            — game-ci/unity-builder on ubuntu-latest (default)
            self-hosted-windows — local Unity on [self-hosted, Windows, unity] runner;
                                 no Docker, no UNITY_LICENSE/SERIAL required.
            self-hosted-macos — local Unity on [self-hosted, macOS, unity] runner;
                               required for iOS builds.
            auto              — prefer docker; fall back to self-hosted-windows if
                               docker label unavailable (not yet implemented; use explicit).
        required: false
        default: 'docker'
        type: string

      activation-strategy:
        description: |
          Unity license activation strategy (docker lane only; ignored for
          self-hosted-windows/macos lanes where Unity is pre-activated).
          Values: auto | manual-license | account | serial | preactivated | none
          Default "auto" delegates to resolve_activation_strategy.sh.
        required: false
        default: 'auto'
        type: string

      environment:
        description: Build environment profile passed to Unity (production | staging | development).
        required: false
        default: 'production'
        type: string

      clean-build:
        description: Delete the Library cache before building (full clean build).
        required: false
        default: false
        type: boolean

      build-addressables:
        description: |
          Run Addressable build step before the platform binary build.
          Set true when the platform build depends on pre-built Addressables.
          (Distinct from the build-addressables job, which uses platform=Addressables.)
        required: false
        default: false
        type: boolean

      artifact-retention-days:
        description: Days to retain uploaded build artifacts.
        required: false
        default: 14
        type: number

    secrets:
      UNITY_LICENSE:
        description: |
          Raw .ulf file contents (personal-combined strategy).
          Optional — required only for docker lane with activation-strategy != none/preactivated.
        required: false
      UNITY_EMAIL:
        description: Unity account email. Required for docker lane activation.
        required: false
      UNITY_PASSWORD:
        description: Unity account password. Required for docker lane activation.
        required: false

    outputs:
      result:
        description: |
          Job result string: success | failure | skipped | blocked
          "blocked" is used for iOS when no macOS runner is available.
        value: ${{ jobs.build.outputs.result }}

      artifact-name:
        description: |
          Name of the uploaded build artifact, or empty string if nothing was uploaded.
          Convention: unity-build-<Platform> (e.g. unity-build-Android).
        value: ${{ jobs.build.outputs.artifact-name }}

      unity-version-used:
        description: Exact Unity version string used for this build.
        value: ${{ jobs.build.outputs.unity-version-used }}

      build-duration-seconds:
        description: Elapsed wall-clock seconds for the Unity build step.
        value: ${{ jobs.build.outputs.build-duration-seconds }}
```

#### Internal job structure (`reusable-build-platform.yml`)

```
jobs:
  build:
    name: Build ${{ inputs.platform }}
    runs-on: <selected by runner-mode + platform>
    outputs:
      result / artifact-name / unity-version-used / build-duration-seconds

    steps:
      1. Guard — iOS on non-macOS runner → set result=blocked, exit 0 (clean skip)
      2. Free disk space (docker lane, ubuntu-latest only)
      3. Checkout (lfs: true, submodules: recursive)
      4. Resolve activation strategy (docker lane only)
         → run: scripts/common/resolve_activation_strategy.sh
      5. Cache Library
         key: Library-<platform>-<hash(ProjectVersion.txt, Packages/manifest.json)>
         restore-keys: Library-<platform>-
         (skip if clean-build=true)
      6. Build Addressables (if build-addressables=true AND platform != Addressables)
      7. Build step:
         docker lane:   game-ci/unity-builder@v4
         windows lane:  shell: cmd, Unity.exe -batchmode -buildTarget ...
         macos lane:    shell: bash, Unity -batchmode -buildTarget Ios ...
         platform=Addressables: Unity -batchmode -executeMethod AddressableBuilder.Build
      8. Upload artifact:
         name: unity-build-<Platform>        ← binary output
         name: unity-build-<Platform>-logs   ← Editor.log + BuildReport (always)
      9. Set outputs
```

#### Platform → GameCI `targetPlatform` mapping

| `platform` input | `targetPlatform` | Extra `customParameters` | Required lane |
|---|---|---|---|
| `Android` | `Android` | — | docker or self-hosted-windows |
| `WebGL` | `WebGL` | — | docker or self-hosted-windows |
| `Linux64` | `StandaloneLinux64` | — | docker |
| `LinuxServer` | `StandaloneLinux64` | `-standaloneBuildSubtarget Server` | docker |
| `iOS` | `iOS` | — | **self-hosted-macos only** |
| `Addressables` | *(no binary build)* | — | docker or self-hosted-windows |

---

### 2.2 `reusable-unity-tests.yml`

**Purpose:** Run Unity EditMode / PlayMode / All tests. Produces XML test results and an Editor.log artifact.

```yaml
on:
  workflow_call:
    inputs:
      unity-version:
        description: Unity version string. Must be resolved by caller.
        required: true
        type: string

      test-mode:
        description: Unity test runner mode — EditMode | PlayMode | All
        required: false
        default: 'All'
        type: string

      project-path:
        description: Path to the Unity project root.
        required: false
        default: '.'
        type: string

      runner-mode:
        description: docker | self-hosted-windows | self-hosted-macos (same semantics as reusable-build-platform)
        required: false
        default: 'docker'
        type: string

      activation-strategy:
        description: auto | manual-license | account | serial | preactivated | none
        required: false
        default: 'auto'
        type: string

      toolkit-ref:
        description: Git ref of the toolkit repo.
        required: false
        default: 'main'
        type: string

      artifact-retention-days:
        description: Days to retain test result artifacts.
        required: false
        default: 14
        type: number

    secrets:
      UNITY_LICENSE:
        required: false
      UNITY_EMAIL:
        required: false
      UNITY_PASSWORD:
        required: false

    outputs:
      result:
        description: success | failure | skipped
        value: ${{ jobs.test.outputs.result }}

      tests-passed:
        description: Count of passing tests (integer string).
        value: ${{ jobs.test.outputs.tests-passed }}

      tests-failed:
        description: Count of failing tests (integer string).
        value: ${{ jobs.test.outputs.tests-failed }}

      artifact-name:
        description: Name of the uploaded test-results artifact.
        value: ${{ jobs.test.outputs.artifact-name }}
```

#### Internal job structure (`reusable-unity-tests.yml`)

```
jobs:
  test:
    name: Unity Tests (${{ inputs.test-mode }})
    runs-on: <selected by runner-mode>
    outputs:
      result / tests-passed / tests-failed / artifact-name

    steps:
      1. Checkout (lfs: true, submodules: recursive)
      2. Resolve activation strategy (docker lane only)
      3. Cache Library (key: Library-test-<test-mode>-<hash>)
      4. Run tests:
         docker lane:   game-ci/unity-test-runner@v4
         windows lane:  Unity.exe -batchmode -runTests -testPlatform <mode>
      5. Upload test results:
         name: unity-tests-<test-mode>   ← NUnit XML + Editor.log
      6. Parse results → set tests-passed / tests-failed outputs
      7. Set result output
```

---

## 3 — Consumer Main Workflow Job Graph

### 3.1 Workflow inputs (`unity-build.yml`)

```yaml
on:
  workflow_dispatch:
    inputs:
      platform:
        description: Target platform
        required: false
        default: All
        type: choice
        options: [All, Android, WebGL, Linux64, LinuxServer, iOS]

      environment:
        description: Build environment
        required: false
        default: production
        type: choice
        options: [production, staging, development]

      run-tests:
        description: Run EditMode/PlayMode tests before building
        required: false
        default: false
        type: boolean

      test-mode:
        description: Test suite to run (only if run-tests=true)
        required: false
        default: All
        type: choice
        options: [EditMode, PlayMode, All]

      build-addressables:
        description: Run Addressable build pipeline before platform builds
        required: false
        default: false
        type: boolean

      activation-strategy:
        description: Unity license activation strategy
        required: false
        default: auto
        type: choice
        options: [auto, manual-license, account, serial, preactivated, none]

      runner-mode:
        description: Execution lane
        required: false
        default: docker
        type: choice
        options: [docker, self-hosted-windows, auto]

      unity-version:
        description: Override Unity version (leave blank to use ProjectVersion.txt)
        required: false
        default: ''
        type: string

      clean-build:
        description: Force clean Library cache (slow, use sparingly)
        required: false
        default: false
        type: boolean
```

### 3.2 Job graph

```
resolve-config
      │
      ▼
validate-project
      │                    │
      ▼                    ▼
unity-tests          build-addressables
(conditional)        (conditional)
                           │
           ┌───────────────┼───────────────────┐──────────────────┐
           ▼               ▼                   ▼                  ▼
      build-android   build-webgl        build-linux64   build-linuxserver
                                                                    │
                                                               build-ios
                                                               (conditional/blocked)
           └───────────────┴───────────────────┴──────────────────┘
                                      │
                                      ▼
                                final-report  (if: always())
```

### 3.3 Job definitions table

| Job | `needs` | `if` condition | Calls |
|---|---|---|---|
| `resolve-config` | *(none)* | *(always)* | inline |
| `validate-project` | `[resolve-config]` | `needs.resolve-config.result == 'success'` | inline |
| `unity-tests` | `[validate-project]` | `inputs.run-tests == true && needs.validate-project.result == 'success'` | `reusable-unity-tests.yml` |
| `build-addressables` | `[validate-project]` | `inputs.build-addressables == true && needs.validate-project.result == 'success'` | `reusable-build-platform.yml` (platform=Addressables) |
| `build-android` | `[validate-project, build-addressables]` | `needs.validate-project.result == 'success' && (needs.build-addressables.result == 'success' \|\| needs.build-addressables.result == 'skipped') && (inputs.platform == 'All' \|\| inputs.platform == 'Android')` | `reusable-build-platform.yml` |
| `build-webgl` | `[validate-project, build-addressables]` | same pattern, platform==WebGL | `reusable-build-platform.yml` |
| `build-linux64` | `[validate-project, build-addressables]` | same pattern, platform==Linux64 | `reusable-build-platform.yml` |
| `build-linuxserver` | `[validate-project, build-addressables]` | same pattern, platform==LinuxServer | `reusable-build-platform.yml` |
| `build-ios` | `[validate-project, build-addressables]` | same pattern, platform==iOS | `reusable-build-platform.yml` (runner-mode=self-hosted-macos) |
| `final-report` | `[resolve-config, validate-project, unity-tests, build-addressables, build-android, build-webgl, build-linux64, build-linuxserver, build-ios]` | `always()` | inline |

> **iOS note:** `build-ios` passes `runner-mode: self-hosted-macos` to the reusable. If no macOS runner is registered, the job will fail to start (GitHub queues it indefinitely or shows a runner-not-found error). The reusable's first step guards against non-macOS runners and outputs `result: blocked` + a clear error annotation, then exits 0 so final-report can collect the status without a workflow-level failure. The consumer may choose to omit iOS from `platform: All` until a macOS runner is provisioned — see Section 5.

### 3.4 `resolve-config` inline job outputs

```yaml
outputs:
  unity-version:       # Resolved from ProjectVersion.txt (or override)
  project-path:        # Normalised project-path (trimmed trailing slash)
  toolkit-ref:         # Ref used for all toolkit workflow calls (default: main)
```

Steps:
1. Checkout (shallow, no LFS — only needs `ProjectVersion.txt`)
2. Read `ProjectVersion.txt`, extract `m_EditorVersion`
3. If `inputs.unity-version` is set and differs → fail unless `allow-version-mismatch` (not exposed as input; always fail to enforce SSOT)
4. Emit outputs to `GITHUB_OUTPUT`

### 3.5 `validate-project` inline job

Verifies the project is structurally sound before any build or test job starts.

Steps:
1. Checkout
2. Verify `ProjectSettings/ProjectVersion.txt` exists and is parseable
3. Verify `Packages/manifest.json` exists
4. Verify `Assets/` directory is non-empty
5. (Optional) Run `scripts/validate/validate-project.sh` if it exists in toolkit

---

## 4 — Artifact Naming Convention

| Artifact name | Contents | Uploaded by |
|---|---|---|
| `unity-build-Android` | `build/` directory (APK/AAB) | `build-android` |
| `unity-build-WebGL` | `build/` directory | `build-webgl` |
| `unity-build-Linux64` | `build/` directory | `build-linux64` |
| `unity-build-LinuxServer` | `build/` directory | `build-linuxserver` |
| `unity-build-iOS` | `build/` directory (Xcode project) | `build-ios` |
| `unity-build-Android-logs` | `Editor.log`, `BuildReport/` | `build-android` (always) |
| `unity-build-WebGL-logs` | `Editor.log`, `BuildReport/` | `build-webgl` (always) |
| `unity-build-Linux64-logs` | `Editor.log`, `BuildReport/` | `build-linux64` (always) |
| `unity-build-LinuxServer-logs` | `Editor.log`, `BuildReport/` | `build-linuxserver` (always) |
| `unity-build-iOS-logs` | `Editor.log`, `BuildReport/` | `build-ios` (always) |
| `unity-tests-EditMode` | NUnit XML, Editor.log | `unity-tests` |
| `unity-tests-PlayMode` | NUnit XML, Editor.log | `unity-tests` |
| `unity-tests-All` | NUnit XML, Editor.log | `unity-tests` |
| `unity-build-Addressables` | Addressables catalog + bundles | `build-addressables` |
| `unity-build-Addressables-logs` | Editor.log | `build-addressables` (always) |

**Rules:**
- Binary artifact: `unity-build-<Platform>` (exact platform name from the `platform` input, PascalCase)
- Log artifact: `unity-build-<Platform>-logs` — uploaded with `if: always()` so failures are diagnosable
- Retention: 14 days (configurable via `artifact-retention-days` input, default 14)
- `if-no-files-found: warn` for binary artifacts (a missing binary is a build failure, already caught by exit code)

---

## 5 — Job → Reusable Workflow Mapping & Inputs

### `build-android`
```yaml
uses: Cuvara/unity-build-workflows/.github/workflows/reusable-build-platform.yml@main
with:
  platform: Android
  unity-version: ${{ needs.resolve-config.outputs.unity-version }}
  project-path: ${{ needs.resolve-config.outputs.project-path }}
  environment: ${{ inputs.environment }}
  runner-mode: ${{ inputs.runner-mode }}
  activation-strategy: ${{ inputs.activation-strategy }}
  clean-build: ${{ inputs.clean-build }}
  build-addressables: false   # addressables pre-built by build-addressables job
  toolkit-ref: ${{ needs.resolve-config.outputs.toolkit-ref }}
secrets:
  UNITY_LICENSE:  ${{ secrets.UNITY_LICENSE }}
  UNITY_EMAIL:    ${{ secrets.UNITY_EMAIL }}
  UNITY_PASSWORD: ${{ secrets.UNITY_PASSWORD }}
```

### `build-webgl`
Same as `build-android` with `platform: WebGL`.

### `build-linux64`
Same with `platform: Linux64`.

### `build-linuxserver`
Same with `platform: LinuxServer`.

### `build-ios`
```yaml
uses: Cuvara/unity-build-workflows/.github/workflows/reusable-build-platform.yml@main
with:
  platform: iOS
  unity-version: ${{ needs.resolve-config.outputs.unity-version }}
  project-path: ${{ needs.resolve-config.outputs.project-path }}
  environment: ${{ inputs.environment }}
  runner-mode: self-hosted-macos      # HARDCODED — iOS cannot run on docker/windows
  activation-strategy: preactivated   # macOS runner has local Unity activated
  clean-build: ${{ inputs.clean-build }}
  build-addressables: false
  toolkit-ref: ${{ needs.resolve-config.outputs.toolkit-ref }}
secrets: inherit
```

> **iOS runner-mode is hardcoded to `self-hosted-macos`** — the `inputs.runner-mode` dispatch input is irrelevant here. The reusable first step asserts `runner.os == 'macOS'` and sets `result: blocked` + exits 0 if the runner is wrong.

### `build-addressables`
```yaml
uses: Cuvara/unity-build-workflows/.github/workflows/reusable-build-platform.yml@main
with:
  platform: Addressables
  unity-version: ${{ needs.resolve-config.outputs.unity-version }}
  project-path: ${{ needs.resolve-config.outputs.project-path }}
  environment: ${{ inputs.environment }}
  runner-mode: ${{ inputs.runner-mode }}
  activation-strategy: ${{ inputs.activation-strategy }}
  clean-build: ${{ inputs.clean-build }}
  build-addressables: false   # this IS the addressables job
  toolkit-ref: ${{ needs.resolve-config.outputs.toolkit-ref }}
secrets:
  UNITY_LICENSE:  ${{ secrets.UNITY_LICENSE }}
  UNITY_EMAIL:    ${{ secrets.UNITY_EMAIL }}
  UNITY_PASSWORD: ${{ secrets.UNITY_PASSWORD }}
```

### `unity-tests`
```yaml
uses: Cuvara/unity-build-workflows/.github/workflows/reusable-unity-tests.yml@main
with:
  unity-version: ${{ needs.resolve-config.outputs.unity-version }}
  test-mode: ${{ inputs.test-mode }}
  project-path: ${{ needs.resolve-config.outputs.project-path }}
  runner-mode: ${{ inputs.runner-mode }}
  activation-strategy: ${{ inputs.activation-strategy }}
  toolkit-ref: ${{ needs.resolve-config.outputs.toolkit-ref }}
secrets:
  UNITY_LICENSE:  ${{ secrets.UNITY_LICENSE }}
  UNITY_EMAIL:    ${{ secrets.UNITY_EMAIL }}
  UNITY_PASSWORD: ${{ secrets.UNITY_PASSWORD }}
```

---

## 6 — Platform Selection Testing Notes

*(For unity-test-runner-engineer to write test cases)*

The platform selection logic lives entirely in the consumer workflow's job-level `if:` conditions (Section 3.3). Tests should assert:

### What to test

1. **Single platform dispatch** — trigger with `platform: Android` → only `build-android` runs; `build-webgl`, `build-linux64`, `build-linuxserver`, `build-ios` are **skipped** (result=`skipped`, not `failure`).

2. **All platforms dispatch** — trigger with `platform: All` → all five build jobs run (iOS is skipped/blocked if no macOS runner is registered, but its `if:` condition evaluates to `true`).

3. **Conditional gates:**
   - `run-tests: false` → `unity-tests` job must be **skipped**, not failed.
   - `run-tests: true` → `unity-tests` runs; `final-report` receives its result.
   - `build-addressables: false` → `build-addressables` job **skipped**; all platform builds still run (because their `if:` checks `result == 'success' || result == 'skipped'`).
   - `build-addressables: true` → `build-addressables` runs first; if it fails, all platform builds are **skipped** (their `if:` will be false for `result == 'failure'`).

4. **Validation gate** — if `validate-project` fails, all downstream jobs (`unity-tests`, `build-addressables`, all `build-*`) must be skipped.

5. **iOS blocking** — trigger with `platform: iOS` on a non-macOS runner → `build-ios` starts (macOS runner may queue), reusable's guard step sets `result: blocked` and job succeeds; `final-report` records `blocked`.

6. **final-report always runs** — even if all build jobs fail, `final-report` must execute and summarize.

### Test mechanism

- Use `workflow_dispatch` with specific `platform` input values.
- Check job conclusions via `gh run view --json jobs`.
- For unit-level tests (without running the full workflow), assert the `if:` expression evaluates correctly by parsing the YAML and evaluating conditions with mock inputs. A simple bash/Python script that substitutes input values and checks the boolean expression is sufficient.

---

## 7 — Key Constraints Reminder

| Constraint | Implication |
|---|---|
| Unity Personal / free (no UNITY_SERIAL) | `UNITY_LICENSE` optional but recommended; use `personal-combined` strategy |
| Docker lane = game-ci/unity-builder | The only proven Personal path in ephemeral containers |
| `self-hosted-windows` lane | Local Unity (no Docker, no license secrets), `runs-on: [self-hosted, Windows, unity]` |
| iOS = macOS only | `build-ios` hardcodes `runner-mode: self-hosted-macos`; blocks cleanly otherwise |
| ProjectVersion.txt = SSOT | Consumer never passes `unity-version` override in production; `resolve-config` reads it |
| Activation strategy auto | `resolve_activation_strategy.sh` selects `personal-combined` when all three secrets present |
