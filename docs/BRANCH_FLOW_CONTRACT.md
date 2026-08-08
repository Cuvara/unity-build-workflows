# Branch-Based Build Flow — Contract

> Extends the explicit-platform-jobs workflow with automatic push/PR flows for
> `develop`, `staging`, `release-*`, while keeping `workflow_dispatch`.
> Platform lists, tests, and addressables flags are configurable via GitHub
> Repository Variables (see [REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md)).

## Flow resolver script (single source of flow logic — testable)

**File (toolkit):** `scripts/common/resolve_build_flow.sh`
Pure bash. Reads env, writes `KEY=value` lines to stdout AND, if `GITHUB_OUTPUT`
is set, appends there too. Never prints secrets.

### Inputs (env)
| Env | Meaning |
|---|---|
| `EVENT_NAME` | `push` \| `pull_request` \| `workflow_dispatch` |
| `REF_NAME` | branch name (push: the branch; PR: head branch; dispatch: current ref) |
| `BASE_REF` | PR target branch (empty for push/dispatch) |
| `IN_PLATFORM` | dispatch input `platform` (All/Android/WebGL/Linux64/LinuxServer/Windows64/iOS) |
| `IN_ENVIRONMENT` | dispatch input `environment` |
| `IN_RUN_TESTS` | dispatch input `run-tests` (true/false) |
| `IN_TEST_MODE` | dispatch input `test-mode` |
| `IN_BUILD_ADDRESSABLES` | dispatch input `build-addressables` (true/false) |
| `IN_ANDROID_EXPORT` | dispatch input `android-export` (apk \| aab; default: apk) |

### Repository Variable inputs (env, optional)

Grouped (new) variables and their legacy equivalents. The resolver reads both
`NEW_*` and `LEG_*` env for each setting, per the priority order below; the
pipeline maps `vars.<NEW_NAME>` → `NEW_*` and `vars.<LEGACY_NAME>` → `LEG_*`.

| New env (from new variable) | Legacy env (from legacy variable) | New variable | Legacy variable | Default |
|---|---|---|---|---|
| `NEW_UNITY_VERSION` | *(none)* | `UNITY_VERSION` | *(none)* | `ProjectVersion.txt` → toolkit default |
| `NEW_UNITY_PROJECT_PATH` | *(none)* | `UNITY_PROJECT_PATH` | *(none)* | `.` |
| `NEW_UNITY_BUILD_METHOD` | *(none)* | `UNITY_BUILD_METHOD` | *(none)* | *(empty = game-ci default)* |
| `NEW_UNITY_DEVELOP_DEFINE_SYMBOLS` | `LEG_DEVELOP_DEFINE_SYMBOLS` | `UNITY_DEVELOP_DEFINE_SYMBOLS` | `DEVELOP_DEFINE_SYMBOLS` | *(empty)* |
| `NEW_UNITY_STAGING_DEFINE_SYMBOLS` | `LEG_STAGING_DEFINE_SYMBOLS` | `UNITY_STAGING_DEFINE_SYMBOLS` | `STAGING_DEFINE_SYMBOLS` | *(empty)* |
| `NEW_UNITY_RELEASE_DEFINE_SYMBOLS` | `LEG_RELEASE_DEFINE_SYMBOLS` | `UNITY_RELEASE_DEFINE_SYMBOLS` | `RELEASE_DEFINE_SYMBOLS` | *(empty)* |
| `NEW_BUILD_DEVELOP_PLATFORMS` | `LEG_DEVELOP_BUILD_PLATFORMS` | `BUILD_DEVELOP_PLATFORMS` | `DEVELOP_BUILD_PLATFORMS` | `Android,WebGL` |
| `NEW_BUILD_STAGING_PLATFORMS` | `LEG_STAGING_BUILD_PLATFORMS` | `BUILD_STAGING_PLATFORMS` | `STAGING_BUILD_PLATFORMS` | `Android,WebGL,Linux64,LinuxServer,Windows64` |
| `NEW_BUILD_RELEASE_PLATFORMS` | `LEG_RELEASE_BUILD_PLATFORMS` | `BUILD_RELEASE_PLATFORMS` | `RELEASE_BUILD_PLATFORMS` | `Android,WebGL,Linux64,LinuxServer,Windows64` |
| `NEW_BUILD_TIMEOUT_MINUTES` | *(none)* | `BUILD_TIMEOUT_MINUTES` | *(none)* | `120` |
| `NEW_BUILD_CLEAN` | *(none)* | `BUILD_CLEAN` | *(none)* | `false` |
| `NEW_TEST_DEVELOP_ENABLED` | `LEG_DEVELOP_RUN_TESTS` | `TEST_DEVELOP_ENABLED` | `DEVELOP_RUN_TESTS` | `true` |
| `NEW_TEST_STAGING_ENABLED` | `LEG_STAGING_RUN_TESTS` | `TEST_STAGING_ENABLED` | `STAGING_RUN_TESTS` | `true` |
| `NEW_TEST_RELEASE_ENABLED` | `LEG_RELEASE_RUN_TESTS` | `TEST_RELEASE_ENABLED` | `RELEASE_RUN_TESTS` | `true` |
| `NEW_TEST_EDITMODE_ENABLED` | *(none)* | `TEST_EDITMODE_ENABLED` | *(none)* | `true` |
| `NEW_TEST_PLAYMODE_ENABLED` | *(none)* | `TEST_PLAYMODE_ENABLED` | *(none)* | `true` |
| `NEW_TEST_FAIL_FAST` | *(none)* | `TEST_FAIL_FAST` | *(none)* | `false` |
| `NEW_ADDRESSABLES_DEVELOP_ENABLED` | `LEG_DEVELOP_BUILD_ADDRESSABLES` | `ADDRESSABLES_DEVELOP_ENABLED` | `DEVELOP_BUILD_ADDRESSABLES` | `false` |
| `NEW_ADDRESSABLES_STAGING_ENABLED` | `LEG_STAGING_BUILD_ADDRESSABLES` | `ADDRESSABLES_STAGING_ENABLED` | `STAGING_BUILD_ADDRESSABLES` | `false` |
| `NEW_ADDRESSABLES_RELEASE_ENABLED` | `LEG_RELEASE_BUILD_ADDRESSABLES` | `ADDRESSABLES_RELEASE_ENABLED` | `RELEASE_BUILD_ADDRESSABLES` | `true` |
| `NEW_RUNNER_DEFAULT_MODE` | `LEG_DEFAULT_RUNNER_MODE` | `RUNNER_DEFAULT_MODE` | `DEFAULT_RUNNER_MODE` | `docker` |
| `NEW_RUNNER_WINDOWS_LABEL` | *(none)* | `RUNNER_WINDOWS_LABEL` | *(none)* | `self-hosted-windows` |
| `NEW_RUNNER_MACOS_LABEL` | *(none)* | `RUNNER_MACOS_LABEL` | *(none)* | `self-hosted-macos` |
| `NEW_RUNNER_LINUX_LABEL` | *(none)* | `RUNNER_LINUX_LABEL` | *(none)* | `ubuntu-latest` |
| `NEW_CACHE_LIBRARY_ENABLED` | *(none)* | `CACHE_LIBRARY_ENABLED` | *(none)* | `true` |
| `NEW_CACHE_GRADLE_ENABLED` | *(none)* | `CACHE_GRADLE_ENABLED` | *(none)* | `true` |
| `NEW_CACHE_ADDRESSABLES_ENABLED` | *(none)* | `CACHE_ADDRESSABLES_ENABLED` | *(none)* | `true` |
| `NEW_CACHE_NUGET_ENABLED` | *(none)* | `CACHE_NUGET_ENABLED` | *(none)* | `true` |
| `NEW_ARTIFACT_RETENTION_DAYS` | *(none)* | `ARTIFACT_RETENTION_DAYS` | *(none)* | `30` |
| `NEW_ARTIFACT_COMPRESSION` | *(none)* | `ARTIFACT_COMPRESSION` | *(none)* | `zip` |

Additionally, `IN_CLEAN_BUILD` (dispatch input `clean-build`: `auto | true |
false`) feeds `BUILD_CLEAN` resolution — see priority order below.

All repository variable inputs are optional. When unset, New falls back to
Legacy, then to the hardcoded default. Invalid values cause the script to
exit non-zero with a clear error message. Legacy variables are deprecated but
fully supported — existing consumers using only legacy vars behave
identically to before this refactor. See
[REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md) for setup, examples, and
the full legacy → new migration table.

### Outputs (all lowercase string values)
| Key | Values |
|---|---|
| `flow-type` | pr-develop \| push-develop \| pr-staging \| push-staging \| pr-release \| push-release \| manual \| none |
| `environment` | development \| staging \| production |
| `run-tests` | true \| false |
| `test-mode` | None \| EditMode \| PlayMode \| All |
| `build-addressables` | true \| false |
| `build-android` | true \| false |
| `build-webgl` | true \| false |
| `build-linux64` | true \| false |
| `build-linuxserver` | true \| false |
| `build-windows64` | true \| false  (docker lane: Mono scripting backend only; IL2CPP requires self-hosted-windows) |
| `build-ios` | true \| false  (only manual platform==iOS; never automatic) |
| `android-export-type` | `apk` \| `aab` — `push-release` always emits `aab`; `workflow_dispatch` uses `IN_ANDROID_EXPORT` (default `apk`); all other flows emit `apk` |
| `signing` | none \| android-release |
| `platform-source` | default \| variable \| dispatch |
| `define-symbols` | Extra Scripting Define Symbols (`';'`-joined) from the branch's `UNITY_*_DEFINE_SYMBOLS` (or legacy `*_DEFINE_SYMBOLS`) variable, or `IN_DEFINE_SYMBOLS` for manual dispatch; **empty** when unset. Applied additively to `ProjectSettings.asset` before the build by `apply_define_symbols.sh`. |
| `gh-environment` | GitHub deployment environment: `development` \| `staging` \| `production` (push/manual); **empty** for all PR flows and `none`. PRs never target a GitHub environment, keeping production secrets/approvals off PRs. |
| `unity-version` | Resolved Unity version: `UNITY_VERSION` variable → `ProjectVersion.txt` → toolkit default. |
| `project-path` | Resolved from `UNITY_PROJECT_PATH` (default `.`). |
| `build-method` | Resolved from `UNITY_BUILD_METHOD` (empty = game-ci default). |
| `build-timeout-minutes` | Resolved from `BUILD_TIMEOUT_MINUTES` (default `120`). |
| `clean-build` | `true` \| `false` — resolved `BUILD_CLEAN` (see [BUILD_CLEAN priority](#build_clean-priority) below). |
| `clean-build-source` | `workflow_dispatch` \| `variable` \| `default` — where `clean-build` came from. |
| `test-editmode` | `true` \| `false` — resolved `TEST_EDITMODE_ENABLED`. |
| `test-playmode` | `true` \| `false` — resolved `TEST_PLAYMODE_ENABLED`. |
| `test-fail-fast` | `true` \| `false` — resolved `TEST_FAIL_FAST`. |
| `runner-mode` | Resolved `RUNNER_DEFAULT_MODE` (or legacy `DEFAULT_RUNNER_MODE`); one of `docker`, `self-hosted-windows`, `self-hosted-macos`, `auto`. |
| `runner-windows-label` | Resolved `RUNNER_WINDOWS_LABEL` (default `self-hosted-windows`). |
| `runner-macos-label` | Resolved `RUNNER_MACOS_LABEL` (default `self-hosted-macos`). |
| `runner-linux-label` | Resolved `RUNNER_LINUX_LABEL` (default `ubuntu-latest`). |
| `cache-library` | `true` \| `false` — resolved `CACHE_LIBRARY_ENABLED`. |
| `cache-gradle` | `true` \| `false` — resolved `CACHE_GRADLE_ENABLED`. |
| `cache-addressables` | `true` \| `false` — resolved `CACHE_ADDRESSABLES_ENABLED`. |
| `cache-nuget` | `true` \| `false` — resolved `CACHE_NUGET_ENABLED`. |
| `artifact-retention-days` | Resolved `ARTIFACT_RETENTION_DAYS` (default `30`). |
| `artifact-compression` | Resolved `ARTIFACT_COMPRESSION` (default `zip`; only `zip` supported today). |
| `config-source-summary` | Optional multi-line human-readable summary of which settings came from dispatch/new-variable/legacy-variable/default (mirrors the Resolve Config report). |

Per-setting source tracking: alongside `platform-source` (now including
`variable-legacy` as a possible value), the resolver emits `<x>-source` keys
(`variable-new` \| `variable-legacy` \| `dispatch` \| `default`) for
platforms, tests, addressables, define-symbols, runner, and clean-build where
useful for debugging which layer won.

### Flow rules
| Trigger | flow-type | env | tests | addr | platforms built | signing | platform-source |
|---|---|---|---|---|---|---|---|
| PR → develop | pr-develop | development | true | false | *(none — validation only)* | none | default |
| push → develop | push-develop | development | true | false | Android, WebGL | none | default or variable |
| PR → staging | pr-staging | staging | true | false | *(none)* | none | default |
| push → staging | push-staging | staging | true | false | Android, WebGL, Linux64, LinuxServer, Windows64 | none | default or variable |
| PR → release-* | pr-release | production | true | true | *(none)* | none | default |
| push → release-* | push-release | production | true | true | Android, WebGL, Linux64, LinuxServer, Windows64 | android-release | default or variable |
| workflow_dispatch | manual | `IN_ENVIRONMENT` | `IN_RUN_TESTS` | `IN_BUILD_ADDRESSABLES` | per `IN_PLATFORM` | n/a | dispatch |
| anything else | none | development | false | false | *(none)* | none | default |

**Notes:**
- Branch match: `develop` exact; `staging` exact; `release-*` = ref starts with `release-` or `release/`.
- PR target uses `BASE_REF`; push uses `REF_NAME`.
- iOS is NEVER auto-built (no macOS runner); only manual `platform==iOS`.
- `workflow_dispatch` always uses dispatch inputs for the flow-selection
  settings in the table above (platform, environment, run-tests, test-mode,
  build-addressables) — repository variables are ignored for those. Other
  grouped settings without a dedicated dispatch input (e.g. `RUNNER_*_LABEL`,
  `CACHE_*`, `ARTIFACT_*`) still resolve from repository variables even
  during a manual run; `BUILD_CLEAN` has its own dispatch input (`clean-build`)
  — see [BUILD_CLEAN priority](#build_clean-priority).
- "default or variable" in the platform-source column: the output is `variable-new`
  when the corresponding `NEW_BUILD_*_PLATFORMS` env var is set and valid,
  `variable-legacy` when only the legacy `LEG_*_BUILD_PLATFORMS` env var is
  set, otherwise `default`.
- `run-tests` and `build-addressables` can also be overridden by repo variables per branch
  (new grouped variable first, then legacy, then default). The variable is applied
  after the branch default, so it takes precedence.

### Platform validation
Allowed platform names (case-sensitive): `Android`, `WebGL`, `Linux64`, `LinuxServer`, `Windows64`, `iOS`.
An invalid name in any `NEW_BUILD_*_PLATFORMS` or legacy `LEG_*_BUILD_PLATFORMS`
variable causes the script to exit with a non-zero status and an error message
listing the invalid name and the allowed set.

### Priority order
For push/PR events:
1. New repository variable (if set and valid) — `*-source=variable-new`
2. Legacy repository variable (if set and valid) — `*-source=variable-legacy`
3. Hardcoded default — `*-source=default`

For `workflow_dispatch`:
1. Dispatch input (if provided for that setting) — `*-source=dispatch`
2. New repository variable — `*-source=variable-new`
3. Legacy repository variable — `*-source=variable-legacy`
4. Hardcoded default — `*-source=default`

This full New > Legacy > Default chain (plus a dispatch layer on top) applies
uniformly to every grouped setting (`UNITY_*`, `BUILD_*`, `TEST_*`,
`ADDRESSABLES_*`, `RUNNER_*`, `CACHE_*`, `ARTIFACT_*`) — see
[REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md) for the full configuration
priority explanation.

#### BUILD_CLEAN priority
`clean-build` has its own dispatch input (`clean-build`: `auto | true |
false`) layered on top of the standard chain:
1. `IN_CLEAN_BUILD` (dispatch) — `true`/`false` forces the value for this run;
   `auto` falls through to step 2. — `clean-build-source=workflow_dispatch`
2. `BUILD_CLEAN` variable (if set) — `clean-build-source=variable`
3. Default `false` — `clean-build-source=default`

Note: only `workflow_dispatch` events carry `IN_CLEAN_BUILD`/other `IN_*`
inputs; for `push`/`pull_request` events, only steps 2–3 (or the New/Legacy/
Default chain for other settings) apply. Non-dispatch-related grouped
settings (e.g. `RUNNER_*_LABEL`, `CACHE_*`, `ARTIFACT_*`) have no dispatch
input at all and always resolve via New → Legacy → Default.

## Consumer workflow (`.github/workflows/unity-build.yml`)

### Triggers
```yaml
on:
  workflow_dispatch: { inputs: <existing 9 inputs unchanged> }
  push:
    branches: [develop, staging, 'release-*']
  pull_request:
    branches: [develop, staging, 'release-*']
```

### resolve-config job
- Checkout consumer; also obtain the toolkit (checkout repo Cuvara/unity-build-workflows @ TOOLKIT_REF into a path, OR use the submodule) so `resolve_build_flow.sh` is available.
- Run the resolver with the env mapping above; export ALL resolver outputs as job
  outputs **plus** the existing `unity-version` / `project-path` / `toolkit-ref`.
- Pass `vars.*` as `VAR_*` env vars so the resolver can read repository variables.

### Job gating (replaces the inputs.platform checks)
- `unity-tests`  → `if: !cancelled() && needs.validate-project.result=='success' && needs.resolve-config.outputs.run-tests == 'true'`
  with `test-mode: ${{ needs.resolve-config.outputs.test-mode }}`.
- `build-addressables` → `if: !cancelled() && needs.validate-project.result=='success' && needs.resolve-config.outputs.build-addressables == 'true'`.
- `build-<platform>` → `if: !cancelled() && needs.validate-project.result=='success' && (needs.build-addressables.result=='success' || needs.build-addressables.result=='skipped') && needs.resolve-config.outputs.build-<platform> == 'true'`.
- Pass `environment: ${{ needs.resolve-config.outputs.environment }}` to the reusables.

### final-report (always())
Must print: event (`github.event_name`), branch (`github.ref_name`), target branch
(`github.base_ref` or '-'), flow-type, environment, platform-source, selected platforms,
skipped platforms, blockers (iOS if dispatched without macOS runner), repository variable
values (when platform-source is `variable`), run id, commit.

## Tests
- `tests/test_build_flow.py`: F1–F13 (original scenarios) + V1–V13 (repository variable scenarios).
  - V1: no variables → defaults
  - V2–V4: develop/staging/release variable override
  - V5: invalid platform → exit non-zero
  - V6: dispatch overrides variables
  - V7: run-tests variable override
  - V8: build-addressables variable override
  - V9: platform-source emitted for all flows
  - V10–V11: invalid run-tests/build-addressables → exit non-zero
  - V12: iOS in variable ignored for branch flows
  - V13: whitespace in CSV handled
- `tests/test_platform_selection.py`: gate on `needs.resolve-config.outputs.build-<platform>`.
