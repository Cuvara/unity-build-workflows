# Explicit-Platform-Jobs Flow — User Guide

> **Branch:** `feature/explicit-platform-jobs`  
> **Unity version SSOT:** `ProjectSettings/ProjectVersion.txt` → `6000.0.26f1`  
> **Workflow file:** `.github/workflows/unity-build.yml` (consumer repo)

This guide covers the **explicit-platform-jobs** build flow — the successor to the
matrix-based approach. Each platform is a separate, named job in the GitHub Actions
UI, independently retryable and independently colour-coded.

Related docs:
- [UNITY\_PERSONAL\_DOCKER\_LICENSE.md](UNITY_PERSONAL_DOCKER_LICENSE.md) — Personal/free license setup and troubleshooting
- [SELF\_HOSTED\_RUNNER.md](SELF_HOSTED_WINDOWS_RUNNER.md) — Registering and configuring self-hosted Windows / macOS runners

---

## 1. Job Graph

The workflow runs jobs in the following order. All platform build jobs are
independent of each other and run in parallel after the shared gate jobs complete.

```
resolve-config
      │
      ▼
validate-project
      │                         │
      ▼                         ▼
unity-tests               build-addressables
(if run-tests=true)       (if build-addressables=true)
                                │
          ┌──────────┬──────────┬──────────────┬──────────────┬──────────────┐
          ▼          ▼          ▼              ▼              ▼              ▼
     build-android build-webgl build-linux64 build-linuxserver build-windows64 build-ios
                                                                         (blocked without macOS runner)
          └──────────┴──────────┴──────────────┴──────────────┴──────────────┘
                                           │
                                           ▼
                                     final-report  (always runs)
```

### Job descriptions

| Job | Purpose | Runner |
|---|---|---|
| `resolve-config` | Read `ProjectVersion.txt`, emit `unity-version` + `project-path` + `toolkit-ref` | `ubuntu-latest` |
| `validate-project` | Verify `ProjectSettings/`, `Packages/manifest.json`, and `Assets/` exist | `ubuntu-latest` |
| `unity-tests` | Run EditMode / PlayMode / All tests via `reusable-unity-tests.yml` | Selected by `runner-mode` |
| `build-addressables` | Build the Addressables catalog + bundles (pseudo-platform) | Selected by `runner-mode` |
| `build-android` | Build Android APK / AAB | Selected by `runner-mode` |
| `build-webgl` | Build WebGL bundle | Selected by `runner-mode` |
| `build-linux64` | Build Linux Standalone | `ubuntu-latest` (docker lane) |
| `build-linuxserver` | Build Linux Dedicated Server | `ubuntu-latest` (docker lane) |
| `build-windows64` | Build Windows Standalone (Mono scripting backend) | Selected by `runner-mode` |
| `build-ios` | Build iOS Xcode project | **`macos-unity-xcode` runner only** (`ios-runner-label` input) |
| `final-report` | Collect all job results, emit summary annotation | `ubuntu-latest` |

---

## 2. Workflow Dispatch Inputs

Trigger manually from the GitHub UI (`Actions → unity-build → Run workflow`) or
with the `gh` CLI.

### Input reference

| Input | Type | Default | Allowed values | Description |
|---|---|---|---|---|
| `platform` | choice | `All` | `All`, `Android`, `WebGL`, `Linux64`, `LinuxServer`, `Windows64`, `iOS` | Platform(s) to build. `All` runs every platform whose runner is available. |
| `run-tests` | boolean | `false` | `true` / `false` | Run Unity tests before building. When `false` the `unity-tests` job is skipped. |
| `test-mode` | choice | `All` | `EditMode`, `PlayMode`, `All` | Test suite to run. Only used when `run-tests=true`. |
| `build-addressables` | boolean | `false` | `true` / `false` | Run the Addressables build pipeline before any platform build. When `false` the `build-addressables` job is skipped and platform builds proceed immediately. |
| `clean-build` | boolean | `false` | `true` / `false` | Delete the `Library/` cache before building (full reimport). Use sparingly — significantly increases build time. |
| `environment` | choice | `production` | `production`, `staging`, `development` | Build environment profile passed to Unity. |
| `activation-strategy` | choice | `auto` | `auto`, `manual-license`, `account`, `preactivated`, `none` | Unity license strategy for the Docker lane. See [Section 4](#4-unity-personal--free-activation). Ignored for self-hosted lanes (Unity is pre-activated on the runner). |
| `runner-mode` | choice | `docker` | `docker`, `self-hosted-windows`, `auto` | Execution lane. Controls which runner type and Docker usage. See [Section 3](#3-runner-modes). |
| `android-export` | string | `apk` | `apk`, `aab` | Android output format. `apk` = installable debug/release APK. `aab` = Play Store App Bundle. On `push → release-*` (branch flow) the pipeline automatically overrides this to `aab` regardless of the dispatch input — APKs are never uploaded to the Play Store. |
| `unity-version` | string | *(empty)* | Any version string, e.g. `6000.0.26f1` | Override the Unity version. **Leave blank in production** — the version is read from `ProjectSettings/ProjectVersion.txt` (SSOT). Providing a value that mismatches the file causes `resolve-config` to fail. |

### Quick CLI examples

```bash
# Build Android only (docker lane, production)
gh workflow run unity-build.yml \
  --repo Cuvara/NDCUnityTemplate \
  --ref main \
  -f platform=Android

# Build all platforms with tests
gh workflow run unity-build.yml \
  --repo Cuvara/NDCUnityTemplate \
  --ref main \
  -f platform=All \
  -f run-tests=true \
  -f test-mode=EditMode

# Build WebGL with Addressables, staging environment, clean
gh workflow run unity-build.yml \
  --repo Cuvara/NDCUnityTemplate \
  --ref main \
  -f platform=WebGL \
  -f build-addressables=true \
  -f environment=staging \
  -f clean-build=true

# Build Android on self-hosted Windows runner
gh workflow run unity-build.yml \
  --repo Cuvara/NDCUnityTemplate \
  --ref main \
  -f platform=Android \
  -f runner-mode=self-hosted-windows
```

---

## 3. Runner Modes

The `runner-mode` input selects the execution lane for every job in the workflow
(except `build-ios`, which always uses `self-hosted-macos`).

| `runner-mode` | Runner label | Docker | Unity license secrets |
|---|---|---|---|
| `docker` | `ubuntu-latest` | Yes — `game-ci/unity-builder@v4` | Required (see [Section 4](#4-unity-personal--free-activation)) |
| `self-hosted-windows` | `[self-hosted, Windows, unity]` | No — local Unity installation | Not required (Unity pre-activated on runner) |
| `auto` | *(not yet implemented — use explicit values)* | — | — |

> **iOS exception:** `build-ios` ignores `runner-mode` entirely. It routes to the
> runner named by the `ios-runner-label` input (default `macos-unity-xcode`) via
> `runs-on: ${{ inputs.ios-runner-label }}`. The `runner-mode` dispatch input
> controls all other platform jobs.

For self-hosted Windows runner setup, see
[SELF\_HOSTED\_WINDOWS\_RUNNER.md](SELF_HOSTED_WINDOWS_RUNNER.md); for the macOS
(iOS) runner, see [SELF\_HOSTED\_MACOS\_RUNNER.md](SELF_HOSTED_MACOS_RUNNER.md).

---

## 4. Unity Personal / Free Activation

This project uses Unity **Personal / free** licensing. There is **no `UNITY_SERIAL`**.

### Activation priority (`activation-strategy: auto`)

When `activation-strategy` is `auto` (default), `resolve_activation_strategy.sh`
selects a strategy in this priority order:

| Priority | Strategy | Condition |
|---|---|---|
| 1 | `manual-license` | `UNITY_LICENSE` secret is set (raw `.ulf` contents) |
| 2 | `account` | `UNITY_EMAIL` + `UNITY_PASSWORD` are set (no `.ulf`) |
| 3 | `preactivated` | No license secrets at all (assumes runner has a cached license) |

**Recommended:** Set all three secrets (`UNITY_LICENSE`, `UNITY_EMAIL`,
`UNITY_PASSWORD`) so the `manual-license` (personal-combined) path is selected.
See [UNITY\_PERSONAL\_DOCKER\_LICENSE.md](UNITY_PERSONAL_DOCKER_LICENSE.md) for
`.ulf` generation and secret setup.

### Strategy reference

| `activation-strategy` value | Behaviour |
|---|---|
| `auto` | Delegate to `resolve_activation_strategy.sh` (priority order above) |
| `manual-license` | Use `UNITY_LICENSE` (`.ulf`) + `UNITY_EMAIL` + `UNITY_PASSWORD` |
| `account` | Activate via Unity account (no `.ulf`) using `UNITY_EMAIL` + `UNITY_PASSWORD` |
| `preactivated` | Assume Unity is already activated; skip activation step |
| `none` | Skip activation entirely (build may fail if Unity requires a license) |

> **`UNITY_LICENSE` is optional** but strongly recommended for the docker lane.  
> **`serial` is not a valid value** — this project does not use serial (Pro/Plus) licensing.

### Self-hosted lanes

The `activation-strategy` input is **ignored** for `runner-mode: self-hosted-windows`
and `runner-mode: self-hosted-macos`. Unity on self-hosted runners is expected to be
permanently activated via Unity Hub on the machine.

---

## 5. Platform Selection Rules

The `platform` dispatch input controls which `build-*` jobs run. Each job's `if:`
condition checks both the `platform` value and the upstream gate jobs.

### What runs per `platform` value

| `platform` input | `build-android` | `build-webgl` | `build-linux64` | `build-linuxserver` | `build-windows64` | `build-ios` |
|---|---|---|---|---|---|---|
| `All` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ (blocked if no macOS runner) |
| `Android` | ✓ | ✗ | ✗ | ✗ | ✗ | ✗ |
| `WebGL` | ✗ | ✓ | ✗ | ✗ | ✗ | ✗ |
| `Linux64` | ✗ | ✗ | ✓ | ✗ | ✗ | ✗ |
| `LinuxServer` | ✗ | ✗ | ✗ | ✓ | ✗ | ✗ |
| `Windows64` | ✗ | ✗ | ✗ | ✗ | ✓ | ✗ |
| `iOS` | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ (blocked if no macOS runner) |

> **Windows64 scripting backend:** The docker lane cross-compiles `StandaloneWindows64`
> using the **Mono scripting backend** only. IL2CPP Windows builds cannot be
> cross-compiled on Linux — use `runner-mode=self-hosted-windows` with a local Unity
> installation for IL2CPP. See [Section 3](#3-runner-modes) for runner mode details.

> **Skipped ≠ failed.** Jobs that do not match the selected platform have
> `result: skipped` in the GitHub Actions UI. `final-report` distinguishes
> between `skipped`, `success`, `failure`, and `blocked`.

### Conditional gate rules

| Condition | Effect on downstream jobs |
|---|---|
| `validate-project` fails | **All** `unity-tests`, `build-addressables`, and `build-*` jobs are skipped |
| `run-tests: false` | `unity-tests` is skipped (not failed); does not block builds |
| `build-addressables: false` | `build-addressables` is skipped; platform builds proceed immediately |
| `build-addressables: true` AND fails | All `build-*` platform jobs are skipped (they require addressables `result == success OR skipped`) |
| `build-addressables: true` AND succeeds | Platform builds wait for it, then run in parallel |

---

## 6. iOS Build — Special Requirements

> **iOS builds are BLOCKED without a registered macOS runner.**

`build-ios` always runs on the `ios-runner-label` runner (default `macos-unity-xcode`)
via `runs-on: ${{ inputs.ios-runner-label }}`, regardless of the `runner-mode` dispatch
input. `activation-strategy` is hardcoded to `preactivated` for the macOS lane.

### What happens when no macOS runner is available

The reusable workflow's first step asserts `runner.os == 'macOS'`. If the job is
dispatched to a non-macOS runner (or queues indefinitely due to no matching runner):

- The step sets `result: blocked` as a job output.
- The step emits a GitHub Actions error annotation with a clear message.
- The step exits `0` (clean skip) so `final-report` can collect the status without
  a workflow-level failure.

`final-report` records `build-ios: blocked`.

### Unblocking iOS

To enable iOS builds:

1. Provision a macOS machine (physical or cloud-hosted).
2. Install Unity **6000.0.26f1** with **iOS Build Support** module.
3. Install Xcode and select the correct version with `xcode-select`.
4. Register the machine as a GitHub Actions self-hosted runner with the
   `macos-unity-xcode` label (`./config.sh --labels macos-unity-xcode`).
5. Activate Unity on the runner via Unity Hub (the macOS lane uses `preactivated`).
6. Set iOS signing secrets (`IOS_DISTRIBUTION_CERTIFICATE_BASE64`, etc.) — see
   [Section 1 of GITHUB\_ACTIONS\_BUILD\_RUNBOOK.md](GITHUB_ACTIONS_BUILD_RUNBOOK.md#1-required-secrets).

Full step-by-step provisioning: [SELF\_HOSTED\_MACOS\_RUNNER.md](SELF_HOSTED_MACOS_RUNNER.md).

---

## 7. Build Artifacts

All artifacts are retained for **14 days** (configurable via `artifact-retention-days`).

| Artifact name | Contents | Produced by |
|---|---|---|
| `unity-build-Android` | APK (`.apk`) or AAB (`.aab`) depending on `android-export` input; `push → release-*` always produces AAB | `build-android` |
| `unity-build-WebGL` | WebGL bundle (`build/` directory) | `build-webgl` |
| `unity-build-Linux64` | Linux binary (`build/` directory) | `build-linux64` |
| `unity-build-LinuxServer` | Server binary (`build/` directory) | `build-linuxserver` |
| `unity-build-Windows64` | Windows `.exe` + `_Data/` (`build/` directory) | `build-windows64` |
| `unity-build-iOS` | Xcode project (`build/` directory) | `build-ios` |
| `unity-build-Android-logs` | `Editor.log` + `BuildReport/` | `build-android` (always) |
| `unity-build-WebGL-logs` | `Editor.log` + `BuildReport/` | `build-webgl` (always) |
| `unity-build-Linux64-logs` | `Editor.log` + `BuildReport/` | `build-linux64` (always) |
| `unity-build-LinuxServer-logs` | `Editor.log` + `BuildReport/` | `build-linuxserver` (always) |
| `unity-build-Windows64-logs` | `Editor.log` + `BuildReport/` | `build-windows64` (always) |
| `unity-build-iOS-logs` | `Editor.log` + `BuildReport/` | `build-ios` (always) |
| `unity-build-Addressables` | Addressables catalog + bundles | `build-addressables` |
| `unity-build-Addressables-logs` | `Editor.log` | `build-addressables` (always) |
| `unity-tests-EditMode` | NUnit XML + `Editor.log` | `unity-tests` |
| `unity-tests-PlayMode` | NUnit XML + `Editor.log` | `unity-tests` |
| `unity-tests-All` | NUnit XML + `Editor.log` | `unity-tests` |

Log artifacts (`*-logs`) are always uploaded — even on build failure — so failures
are diagnosable. Binary artifacts are uploaded only on success.

### Downloading artifacts

```bash
# List artifact names for a run
gh run view <RUN_ID> \
  --repo Cuvara/NDCUnityTemplate \
  --json artifacts --jq '.artifacts[].name'

# Download a specific artifact
gh run download <RUN_ID> \
  --repo Cuvara/NDCUnityTemplate \
  --name unity-build-Android

# Download all artifacts
gh run download <RUN_ID> \
  --repo Cuvara/NDCUnityTemplate
```

---

## 8. Job Names in the GitHub Actions UI

In the **Actions → Run** detail view, each platform appears as a distinct job node:

| UI job name | YAML job key |
|---|---|
| `resolve-config` | `resolve-config` |
| `validate-project` | `validate-project` |
| `Unity Tests (EditMode)` / `(PlayMode)` / `(All)` | `unity-tests` |
| `Build Addressables` | `build-addressables` |
| `Build Android` | `build-android` |
| `Build WebGL` | `build-webgl` |
| `Build Linux64` | `build-linux64` |
| `Build LinuxServer` | `build-linuxserver` |
| `Build Windows64` | `build-windows64` |
| `Build iOS` | `build-ios` |
| `final-report` | `final-report` |

Each platform job can be **re-run individually** from the UI without re-running
the whole workflow.

---

## 9. Common Issues

| Symptom | Likely cause | Fix |
|---|---|---|
| `build-ios` queues indefinitely | No `macos-unity-xcode` runner registered (label mismatch) | See [Section 6](#6-ios-build--special-requirements) |
| `build-ios` shows `blocked` | Runner is not macOS (guard step caught it) | Register a macOS runner |
| All `build-*` jobs skipped after `build-addressables` | `build-addressables` failed | Check `unity-build-Addressables-logs` artifact; fix the Addressables configuration |
| All downstream jobs skipped after `validate-project` | Project structure validation failed | Check `Assets/`, `Packages/manifest.json`, `ProjectSettings/ProjectVersion.txt` |
| `TimeStamp validation failed` in build logs | `.ulf` present but `UNITY_EMAIL`/`UNITY_PASSWORD` missing | Set all three secrets together — see [UNITY\_PERSONAL\_DOCKER\_LICENSE.md](UNITY_PERSONAL_DOCKER_LICENSE.md) |
| `0 entitlements` in build logs | Credentials present but `UNITY_LICENSE` missing | Add `UNITY_LICENSE` secret (raw `.ulf` contents) |
| `resolve-config` fails with version mismatch | `unity-version` override does not match `ProjectVersion.txt` | Leave `unity-version` blank or correct the override to match `6000.0.26f1` |

For licensing-specific issues, see [UNITY\_PERSONAL\_DOCKER\_LICENSE.md](UNITY_PERSONAL_DOCKER_LICENSE.md).  
For self-hosted runner issues, see [SELF\_HOSTED\_RUNNER.md](SELF_HOSTED_WINDOWS_RUNNER.md).
