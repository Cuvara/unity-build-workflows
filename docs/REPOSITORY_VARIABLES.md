# Repository Variables — Configuration Guide

GitHub Repository Variables let you customize Unity version/build settings,
which platforms are built, whether tests run, addressables pre-building,
runner selection, caching, and artifact retention — per branch flow — without
editing workflow files. Variables are grouped by concern (`UNITY_*`,
`BUILD_*`, `TEST_*`, `ADDRESSABLES_*`, `RUNNER_*`, `CACHE_*`, `ARTIFACT_*`).

> **Variables vs Secrets:** Variables are for non-sensitive configuration
> (platform lists, versions, boolean flags). Secrets are for sensitive values
> like `UNITY_LICENSE`, `UNITY_EMAIL`, `UNITY_PASSWORD`, keystore passwords,
> and Apple signing credentials. Config is always resolved server-side inside
> the workflow — never put secrets in variables. See
> [BRANCH_FLOW_CONTRACT.md](BRANCH_FLOW_CONTRACT.md) for how the resolver
> consumes these variables and what it outputs to downstream jobs.

## How to configure

1. Go to your repository on GitHub
2. Navigate to **Settings → Secrets and variables → Actions → Variables**
3. Click **New repository variable**
4. Enter the variable name and value
5. Click **Add variable**

Variables take effect on the next workflow run — no code changes needed.

## Configuration priority

Every setting is resolved by `scripts/common/resolve_build_flow.sh` using the
same layered priority, highest first:

1. **`workflow_dispatch` input** — an explicit value chosen when manually
   running the workflow (e.g. the `clean-build` dispatch input). Only present
   for manual runs; skipped entirely for `push`/`pull_request` events.
2. **New repo variable** — the grouped variable documented below (e.g.
   `BUILD_DEVELOP_PLATFORMS`), if set to a non-empty value.
3. **Legacy repo variable** — the old, ungrouped variable name (e.g.
   `DEVELOP_BUILD_PLATFORMS`), if set to a non-empty value. Used only when the
   new variable is unset, so existing repos keep working unchanged. The
   resolver logs a deprecation note naming the new variable to migrate to.
4. **`ProjectVersion.txt`** — Unity editor version only, and its single source
   of truth. The version is read directly from
   `ProjectSettings/ProjectVersion.txt`. A `UNITY_VERSION` variable (or the
   `unity-version` dispatch input) may pin it, but is validated against this
   file and cannot override it — a mismatch fails the build.
5. **Toolkit default** — the hardcoded fallback baked into the resolver,
   documented in the "Default" column of each table below.

For `push`/`pull_request` events (no dispatch layer), priority is simply
**New variable → Legacy variable → (ProjectVersion.txt for Unity version) →
Toolkit default**. The resolver never *requires* a new variable to be set —
an unconfigured repo behaves exactly as it did before this refactor.

## UNITY

| Variable | Default | Applies to | Notes |
|---|---|---|---|
| `UNITY_VERSION` | *(read from `ProjectVersion.txt`)* | all flows | **Validated pin**, not an override. If set, it must equal the editor version in `ProjectSettings/ProjectVersion.txt` — a mismatch fails the build. `ProjectVersion.txt` stays the source of truth (a different value would select a Unity image that can't open the project). The detected version is printed in the Resolve Config report. |
| `UNITY_BUILD_METHOD` | *(empty = game-ci default)* | all flows | Override the Unity build method invoked by game-ci (docker lane, non-Addressables). |
| `UNITY_DEVELOP_DEFINE_SYMBOLS` | *(empty)* | `push/PR → develop` | Legacy: `DEVELOP_DEFINE_SYMBOLS`. |
| `UNITY_STAGING_DEFINE_SYMBOLS` | *(empty)* | `push/PR → staging` | Legacy: `STAGING_DEFINE_SYMBOLS`. |
| `UNITY_RELEASE_DEFINE_SYMBOLS` | *(empty)* | `push/PR → release-*` | Legacy: `RELEASE_DEFINE_SYMBOLS`. |

> **To change the Unity editor version:** edit `ProjectSettings/ProjectVersion.txt` (the SSOT). Keep `UNITY_VERSION` in sync (or unset) — it only *confirms* the version, it cannot change it.
>
> **Not a repository variable:** **project path** is set once by the calling workflow (`unity-build.yml`, `project-path: '.'`), not per-run configuration. There is no `UNITY_PROJECT_PATH` variable.

Define-symbols variables are **additive** — the listed symbols are merged
into every platform group at build time; existing project symbols (e.g.
`ODIN_INSPECTOR`, `DOTWEEN`) are preserved and duplicates de-duplicated.
Format: `';'` or `','` separated (whitespace trimmed), e.g.
`STAGING;PROFILER_ENABLED`. Manual `workflow_dispatch` runs ignore these
variables (they are branch-scoped) — pass the `define-symbols` dispatch input
instead.

```
UNITY_DEVELOP_DEFINE_SYMBOLS=DEVELOPMENT_BUILD;VERBOSE_LOGGING
UNITY_STAGING_DEFINE_SYMBOLS=STAGING;PROFILER_ENABLED
UNITY_RELEASE_DEFINE_SYMBOLS=PRODUCTION;LIVE_BACKEND
```

## BUILD

| Variable | Default | Applies to | Notes |
|---|---|---|---|
| `BUILD_DEVELOP_PLATFORMS` | `Android,WebGL` | `push → develop` | Legacy: `DEVELOP_BUILD_PLATFORMS`. |
| `BUILD_STAGING_PLATFORMS` | `Android,WebGL,Linux64,LinuxServer,Windows64` | `push → staging` | Legacy: `STAGING_BUILD_PLATFORMS`. |
| `BUILD_RELEASE_PLATFORMS` | `Android,WebGL,Linux64,LinuxServer,Windows64` | `push → release-*` | Legacy: `RELEASE_BUILD_PLATFORMS`. |
| `BUILD_TIMEOUT_MINUTES` | `120` | all build jobs | Positive integer; per-job timeout in minutes. |
| `BUILD_CLEAN` | `false` | all build jobs | See [BUILD_CLEAN](#build_clean-clean-vs-incremental-builds) below. |

Platform lists are comma-separated and case-sensitive. **Allowed platform
names:** `Android`, `WebGL`, `Linux64`, `LinuxServer`, `Windows64`, `iOS`.

> **iOS note:** iOS in a platform variable is silently ignored for branch
> flows because iOS requires a self-hosted macOS runner. Use
> `workflow_dispatch` with `platform=iOS` for iOS builds — see
> [Known limitations](#known-limitations).

```
BUILD_DEVELOP_PLATFORMS=Android,WebGL
BUILD_STAGING_PLATFORMS=Android,WebGL,Linux64,LinuxServer,Windows64
BUILD_RELEASE_PLATFORMS=Android,WebGL,Linux64,LinuxServer,Windows64
BUILD_TIMEOUT_MINUTES=120
BUILD_CLEAN=false
```

### BUILD_CLEAN: clean vs incremental builds

- **`true` (clean):** forces a clean build — no stale artifacts carried over.
  The Library cache is cleared only when the selected runner mode requires it
  to guarantee a clean state. Use this when you need certainty over speed.
- **`false` (incremental, default):** reuses the Library/Gradle/NuGet caches
  from the previous run for faster builds.

Manual runs can override the variable with the `Clean Build` `workflow_dispatch`
input, which accepts `auto | true | false`:

- `auto` — use the `BUILD_CLEAN` variable (or its default if unset).
- `true` / `false` — force the value for this run only, ignoring the variable.

Priority: **`workflow_dispatch` input → `BUILD_CLEAN` variable → default
(`false`)**. The resolver emits both `clean-build` (`true`/`false`) and
`clean-build-source` (`workflow_dispatch` | `variable` | `default`).

**Recommended defaults:** leave `develop`, `staging`, and `release` all
incremental (`false`). Only force `true` (via the variable or the dispatch
input) when:

- the Library cache is suspected corrupted,
- stale build outputs are suspected,
- you just upgraded the Unity version,
- you changed scripting backend (Mono/IL2CPP) or a build pipeline setting, or
- you're debugging CI caching behavior.

**Performance impact:** clean builds skip the Library/Gradle/Addressables
caches and re-import/re-compile everything, which is significantly slower
(often several times longer) than an incremental build. Reserve `true` for
the cases above rather than as a default.

## TEST

| Variable | Default | Applies to | Notes |
|---|---|---|---|
| `TEST_DEVELOP_ENABLED` | `true` | `push/PR → develop` | Legacy: `DEVELOP_RUN_TESTS`. |
| `TEST_STAGING_ENABLED` | `true` | `push/PR → staging` | Legacy: `STAGING_RUN_TESTS`. |
| `TEST_RELEASE_ENABLED` | `true` | `push/PR → release-*` | Legacy: `RELEASE_RUN_TESTS`. |
| `TEST_EDITMODE_ENABLED` | `true` | all test runs | Toggles EditMode suite. |
| `TEST_PLAYMODE_ENABLED` | `true` | all test runs | Toggles PlayMode suite. |
| `TEST_FAIL_FAST` | `false` | all test runs | Stop on first failure instead of running the full suite. |

Values must be exactly `true` or `false`; anything else fails the workflow.

`test-mode` is derived from the EditMode/PlayMode toggles: both enabled →
`All`, EditMode only → `EditMode`, PlayMode only → `PlayMode`, neither →
`None`. If the branch's `TEST_*_ENABLED` resolves to `false`, `test-mode` is
forced to `None` regardless of the EditMode/PlayMode toggles.

```
TEST_DEVELOP_ENABLED=true
TEST_STAGING_ENABLED=true
TEST_RELEASE_ENABLED=true
TEST_EDITMODE_ENABLED=true
TEST_PLAYMODE_ENABLED=true
TEST_FAIL_FAST=false
```

## ADDRESSABLES

| Variable | Default | Applies to | Notes |
|---|---|---|---|
| `ADDRESSABLES_DEVELOP_ENABLED` | `false` | `push/PR → develop` | Legacy: `DEVELOP_BUILD_ADDRESSABLES`. |
| `ADDRESSABLES_STAGING_ENABLED` | `false` | `push/PR → staging` | Legacy: `STAGING_BUILD_ADDRESSABLES`. |
| `ADDRESSABLES_RELEASE_ENABLED` | `true` | `push/PR → release-*` | Legacy: `RELEASE_BUILD_ADDRESSABLES`. |

Values must be exactly `true` or `false`. Resolver output: `build-addressables`.

```
ADDRESSABLES_DEVELOP_ENABLED=false
ADDRESSABLES_STAGING_ENABLED=false
ADDRESSABLES_RELEASE_ENABLED=true
```

## RUNNER

The toolkit separates **where** a job runs (`RUNNER_TYPE` + `RUNNER_LABELS`)
from **how** Unity builds (`BUILD_ENGINE`). The two are independent settings —
pick a runner type and a build engine separately, and the resolver combines
them into an *execution strategy*. See
[RUNNER_AND_BUILD_ENGINE.md](RUNNER_AND_BUILD_ENGINE.md) for the full
architecture, diagrams, and licensing guidance per mode.

| Variable | Allowed values | Default | Applies to | Notes |
|---|---|---|---|---|
| `RUNNER_TYPE` | `github-hosted`, `self-hosted` | `github-hosted` | all flows | WHERE the job runs. |
| `BUILD_ENGINE` | `docker`, `local` | `docker` | all flows | HOW Unity builds. |
| `RUNNER_LABELS` | comma-separated labels | *(derived, see below)* | all flows | `runs-on` labels for the job. |

`RUNNER_LABELS` default when unset is derived from `RUNNER_TYPE`:

- `RUNNER_TYPE=github-hosted` → `ubuntu-latest`
- `RUNNER_TYPE=self-hosted` → `self-hosted,windows`

```
RUNNER_TYPE=github-hosted
BUILD_ENGINE=docker
RUNNER_LABELS=ubuntu-latest
```

### Resolution priority

Each of the three settings above is resolved independently, in this order:

1. `workflow_dispatch` input (`runner-type` / `build-engine` / `runner-labels`)
2. Repository variable (`RUNNER_TYPE` / `BUILD_ENGINE` / `RUNNER_LABELS`)
3. Legacy `RUNNER_DEFAULT_MODE` mapping (see migration table below) — only
   consulted when neither of the above is set
4. Toolkit default (`github-hosted` / `docker` / derived labels)

### Supported combinations

| RUNNER_TYPE | BUILD_ENGINE | Execution strategy | Build step |
|---|---|---|---|
| `github-hosted` | `docker` | `github-docker` | GameCI Docker image |
| `self-hosted` | `local` | `selfhosted-local` | Local `Unity.exe` / `.app` |
| `self-hosted` | `docker` | `selfhosted-docker` | GameCI Docker image via Docker Desktop |

**Invalid:** `RUNNER_TYPE=github-hosted` + `BUILD_ENGINE=local` fails fast in
Resolve Config — GitHub-hosted runners have no local Unity install. Use
`BUILD_ENGINE=docker` or switch to `RUNNER_TYPE=self-hosted`.

### RUNNER_LABELS normalization

`RUNNER_LABELS` is comma-split, each entry trimmed, empty entries dropped, and
duplicates removed (order preserved). The workflow fails if zero labels remain
after normalization. Examples:

```
RUNNER_LABELS=self-hosted,windows
RUNNER_LABELS=self-hosted,windows,unity
RUNNER_LABELS=self-hosted,windows,gpu
RUNNER_LABELS=self-hosted,macOS
RUNNER_LABELS=ubuntu-latest
```

### Legacy: `RUNNER_DEFAULT_MODE`

`RUNNER_DEFAULT_MODE` (legacy: `DEFAULT_RUNNER_MODE`) still works when
`RUNNER_TYPE`/`BUILD_ENGINE` are unset. It is mapped as follows, with a
deprecation warning logged pointing at the new variables:

| Legacy `RUNNER_DEFAULT_MODE` | Maps to |
|---|---|
| `docker` | `RUNNER_TYPE=github-hosted`, `BUILD_ENGINE=docker` |
| `auto` | `RUNNER_TYPE=github-hosted`, `BUILD_ENGINE=docker` |
| `self-hosted-windows` | `RUNNER_TYPE=self-hosted`, `BUILD_ENGINE=local` (labels default `self-hosted,windows`) |
| `self-hosted-macos` | `RUNNER_TYPE=self-hosted`, `BUILD_ENGINE=local` (labels default `self-hosted,macOS`) |

Explicit `RUNNER_TYPE`/`BUILD_ENGINE`/`RUNNER_LABELS` (repo variable or
`workflow_dispatch`) always win over the legacy mapping. A repo that sets
nothing behaves exactly as before: `github-hosted` + `docker` +
`ubuntu-latest`.

### Superseded label variables

`RUNNER_WINDOWS_LABEL`, `RUNNER_MACOS_LABEL`, and `RUNNER_LINUX_LABEL` are
kept as fallback outputs for anything not yet migrated to `RUNNER_LABELS`, but
new setups should configure `RUNNER_LABELS` directly.

| Variable | Default | Notes |
|---|---|---|
| `RUNNER_WINDOWS_LABEL` | `self-hosted-windows` | Fallback label; prefer `RUNNER_LABELS`. |
| `RUNNER_MACOS_LABEL` | `self-hosted-macos` | Fallback label; prefer `RUNNER_LABELS`. |
| `RUNNER_LINUX_LABEL` | `ubuntu-latest` | Fallback label; prefer `RUNNER_LABELS`. |

## CACHE

All cache toggles default to `true`. Disable a cache only to debug a cache
corruption issue or to force a fully cold run.

| Variable | Default | Notes |
|---|---|---|
| `CACHE_LIBRARY_ENABLED` | `true` | Caches Unity's `Library/` folder between runs. |
| `CACHE_GRADLE_ENABLED` | `true` | Caches Gradle dependencies for Android builds. |
| `CACHE_ADDRESSABLES_ENABLED` | `true` | Caches built Addressables content. |
| `CACHE_NUGET_ENABLED` | `true` | Caches NuGet packages. |

```
CACHE_LIBRARY_ENABLED=true
CACHE_GRADLE_ENABLED=true
CACHE_ADDRESSABLES_ENABLED=true
CACHE_NUGET_ENABLED=true
```

## ARTIFACT

| Variable | Default | Notes |
|---|---|---|
| `ARTIFACT_RETENTION_DAYS` | per build type | Positive integer. **Unset** the pipeline tiers it: release **90** days, staging 14, development 7 — a release artifact that expires can never be promoted, while a development one is disposable (I-002). Logs and reports always keep 7. Setting this is a decision and overrides every tier. |
| `ARTIFACT_COMPRESSION` | `zip` | See [Known limitations](#known-limitations) — only `zip` is supported today. |

```
ARTIFACT_RETENTION_DAYS=30
ARTIFACT_COMPRESSION=zip
```

## Migration guide: legacy → new variables

Legacy (ungrouped) variables **still work** — they are read whenever the
corresponding new variable is unset, so no existing consumer needs to change
anything. New variables take priority when both are set. Migrate at your own
pace; the resolver logs a deprecation note pointing at the new name whenever
it falls back to a legacy variable.

| Legacy variable | New variable |
|---|---|
| `DEVELOP_BUILD_PLATFORMS` | `BUILD_DEVELOP_PLATFORMS` |
| `STAGING_BUILD_PLATFORMS` | `BUILD_STAGING_PLATFORMS` |
| `RELEASE_BUILD_PLATFORMS` | `BUILD_RELEASE_PLATFORMS` |
| `DEVELOP_RUN_TESTS` | `TEST_DEVELOP_ENABLED` |
| `STAGING_RUN_TESTS` | `TEST_STAGING_ENABLED` |
| `RELEASE_RUN_TESTS` | `TEST_RELEASE_ENABLED` |
| `DEVELOP_BUILD_ADDRESSABLES` | `ADDRESSABLES_DEVELOP_ENABLED` |
| `STAGING_BUILD_ADDRESSABLES` | `ADDRESSABLES_STAGING_ENABLED` |
| `RELEASE_BUILD_ADDRESSABLES` | `ADDRESSABLES_RELEASE_ENABLED` |
| `DEFAULT_RUNNER_MODE` | `RUNNER_DEFAULT_MODE` |
| `DEVELOP_DEFINE_SYMBOLS` | `UNITY_DEVELOP_DEFINE_SYMBOLS` |
| `STAGING_DEFINE_SYMBOLS` | `UNITY_STAGING_DEFINE_SYMBOLS` |
| `RELEASE_DEFINE_SYMBOLS` | `UNITY_RELEASE_DEFINE_SYMBOLS` |

Everything in the BUILD_CLEAN and ARTIFACT groups is new — there is no legacy
equivalent, so those simply default until you set them.

### Legacy → new: runner / build engine

| Legacy `RUNNER_DEFAULT_MODE` | New `RUNNER_TYPE` | New `BUILD_ENGINE` | Default `RUNNER_LABELS` |
|---|---|---|---|
| `docker` | `github-hosted` | `docker` | `ubuntu-latest` |
| `auto` | `github-hosted` | `docker` | `ubuntu-latest` |
| `self-hosted-windows` | `self-hosted` | `local` | `self-hosted,windows` |
| `self-hosted-macos` | `self-hosted` | `local` | `self-hosted,macOS` |

`RUNNER_DEFAULT_MODE` still works (deprecation-logged); it is only consulted
when `RUNNER_TYPE`/`BUILD_ENGINE` are unset. See
[RUNNER_AND_BUILD_ENGINE.md](RUNNER_AND_BUILD_ENGINE.md) for the full
runner/build-engine model.

## GitHub configuration examples

**Settings → Secrets and variables → Actions → Variables:**

```
# UNITY

# BUILD
BUILD_DEVELOP_PLATFORMS=Android,WebGL
BUILD_STAGING_PLATFORMS=Android,WebGL,Linux64,LinuxServer,Windows64
BUILD_RELEASE_PLATFORMS=Android,WebGL,Linux64,LinuxServer,Windows64
BUILD_TIMEOUT_MINUTES=120
BUILD_CLEAN=false

# TEST
TEST_DEVELOP_ENABLED=true
TEST_STAGING_ENABLED=true
TEST_RELEASE_ENABLED=true
TEST_EDITMODE_ENABLED=true
TEST_PLAYMODE_ENABLED=true
TEST_FAIL_FAST=false

# ADDRESSABLES
ADDRESSABLES_DEVELOP_ENABLED=false
ADDRESSABLES_STAGING_ENABLED=false
ADDRESSABLES_RELEASE_ENABLED=true

# RUNNER
RUNNER_TYPE=github-hosted
BUILD_ENGINE=docker
RUNNER_LABELS=ubuntu-latest

# CACHE
CACHE_LIBRARY_ENABLED=true
CACHE_GRADLE_ENABLED=true
CACHE_ADDRESSABLES_ENABLED=true
CACHE_NUGET_ENABLED=true

# ARTIFACT
ARTIFACT_RETENTION_DAYS=30
ARTIFACT_COMPRESSION=zip
```

**Settings → Secrets and variables → Actions → Secrets** (never variables):

| Secret | Purpose |
|---|---|
| `UNITY_LICENSE` | Unity license file content (`.ulf`) |
| `UNITY_EMAIL` | Unity account email |
| `UNITY_PASSWORD` | Unity account password |
| Keystore secrets | Android signing keystore and passwords |
| Apple signing | iOS distribution certificates and provisioning profiles |

## Best practices

### Unity Personal license

- `UNITY_SERIAL` is not required and should not be used.
- `UNITY_LICENSE` is optional — the activation strategy system handles this.
- **Recommended for Personal/Free:** `RUNNER_TYPE=self-hosted` +
  `BUILD_ENGINE=local`. It reuses your existing Unity Hub activation, needs no
  license secrets, and sidesteps Docker activation issues entirely. Docker
  remains a fully-supported advanced option. See
  [RUNNER_AND_BUILD_ENGINE.md](RUNNER_AND_BUILD_ENGINE.md) and
  [UNITY_PERSONAL_DOCKER_LICENSE.md](UNITY_PERSONAL_DOCKER_LICENSE.md).

### Docker build engine

- Keep `RUNNER_TYPE=github-hosted` + `BUILD_ENGINE=docker` for the default CI
  path — it's the only combination CI itself verifies end-to-end.
- `CACHE_LIBRARY_ENABLED` / `CACHE_GRADLE_ENABLED` matter most here since
  Docker jobs run on ephemeral GitHub-hosted runners with no persistent disk
  between separate machines.
- Leave `BUILD_CLEAN=false` in Docker unless you're troubleshooting — clean
  builds are considerably more expensive on shared runners.

### Self-hosted runner + local build engine

- Set `RUNNER_TYPE=self-hosted`, `BUILD_ENGINE=local`, and confirm
  `RUNNER_LABELS` matches the label(s) of your provisioned runner(s) (default
  `self-hosted,windows`).
- Required for IL2CPP Windows64 builds (the Docker lane only supports Mono
  scripting backend for Windows64).
- See [RUNNER_AND_BUILD_ENGINE.md](RUNNER_AND_BUILD_ENGINE.md) for the
  architecture and [SELF_HOSTED_WINDOWS_RUNNER.md](SELF_HOSTED_WINDOWS_RUNNER.md)
  for provisioning steps.

## Known limitations

- **`ARTIFACT_COMPRESSION`** only supports `zip` today (maps directly to
  `actions/upload-artifact`'s zip packaging). Other formats are not
  implemented; setting anything else fails validation.
- **`RUNNER_TYPE=self-hosted`** requires you to provision and register the
  runner(s) yourself. CI for this toolkit only verifies `github-hosted` +
  `docker` end-to-end at runtime; `self-hosted` + `docker` is verified at the
  Resolve Config level only (needs Docker Desktop on the host). See
  [RUNNER_AND_BUILD_ENGINE.md](RUNNER_AND_BUILD_ENGINE.md#known-limitations).
- **`RUNNER_TYPE=github-hosted` + `BUILD_ENGINE=local` is invalid** — fails
  fast at Resolve Config since GitHub-hosted runners have no local Unity
  install.
- **iOS is manual-dispatch only** — iOS in any `BUILD_*_PLATFORMS` variable
  is silently ignored for automatic branch flows; there is no automatic path
  to a macOS runner. Trigger iOS via `workflow_dispatch` with `platform=iOS`
  (self-hosted macOS runner, `BUILD_ENGINE=local`, forced regardless of the
  resolved runner/engine settings).
- **`RUNNER_LABELS` / `RUNNER_*_LABEL` customization** is best-effort: the
  toolkit passes the labels through to the job's `runs-on`, but it cannot
  validate that a runner with those labels is actually online or configured
  correctly.

## Validation summary

- Platform names: case-sensitive, one of `Android`, `WebGL`, `Linux64`,
  `LinuxServer`, `Windows64`, `iOS`.
- `RUNNER_TYPE`: one of `github-hosted`, `self-hosted`.
- `BUILD_ENGINE`: one of `docker`, `local`.
- `RUNNER_LABELS`: comma-separated, trimmed, deduplicated; fails if empty
  after normalization.
- Legacy `RUNNER_DEFAULT_MODE`: one of `docker`, `self-hosted-windows`,
  `self-hosted-macos`, `auto` (only consulted when the new variables are
  unset).
- Booleans (`*_ENABLED`, `TEST_FAIL_FAST`, `BUILD_CLEAN` when not `auto`):
  exactly `true` or `false`.
- `BUILD_TIMEOUT_MINUTES` / `ARTIFACT_RETENTION_DAYS`: positive integers.
- `ARTIFACT_COMPRESSION`: `zip` only.
- Invalid values fail the workflow fast with a clear error message; whitespace
  around CSV platform entries is trimmed automatically.

See [BRANCH_FLOW_CONTRACT.md](BRANCH_FLOW_CONTRACT.md) for the full resolver
input/output contract and per-flow behavior.
