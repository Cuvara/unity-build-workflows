# Environment-Scoped Variables

One variable name per setting, with a different value per GitHub Environment,
instead of a separate repository variable per branch.

| Before (one repository variable per branch) | After (one name, set per environment) |
|---|---|
| `BUILD_DEVELOP_PLATFORMS`, `BUILD_STAGING_PLATFORMS`, `BUILD_RELEASE_PLATFORMS` | `BUILD_PLATFORMS` |
| `TEST_DEVELOP_ENABLED`, `TEST_STAGING_ENABLED`, `TEST_RELEASE_ENABLED` | `TEST_ENABLED` |
| `ADDRESSABLES_DEVELOP_ENABLED`, `ADDRESSABLES_STAGING_ENABLED`, `ADDRESSABLES_RELEASE_ENABLED` | `ADDRESSABLES_ENABLED` |
| `UNITY_DEVELOP_DEFINE_SYMBOLS`, `UNITY_STAGING_DEFINE_SYMBOLS`, `UNITY_RELEASE_DEFINE_SYMBOLS` | `UNITY_DEFINE_SYMBOLS` |
| `BUILD_NUMBER_OFFSET_DEVELOPMENT`, `BUILD_NUMBER_OFFSET_RELEASE` | `BUILD_NUMBER_OFFSET` (see [below](#build-number-offset)) |

The per-branch names keep working. Nothing changes for a project until it sets
one of the generic names.

---

## 1. How it works

GitHub hands a job an environment's variables only when the job declares that
environment. Inside such a job, `${{ vars.NAME }}` is the **environment value
if the environment defines `NAME`, otherwise the repository value**.

`unity-pipeline.yml` uses that:

```
select-environment  (ubuntu-latest, ~10 s)
   runs resolve_build_flow.sh with the event + dispatch inputs only
   → name = development | staging | production | <empty>
        │
        ▼
resolve-config      environment: ${{ needs.select-environment.outputs.name }}
   every vars.* here is the environment value first
   → platforms, tests, addressables, define symbols, build number, runners ...
        │
        ▼
build (per platform) environment: same name (secrets-environment)
   environment secrets (signing) and variables (Firebase app ids, testers ...)
```

Both jobs take the name from the same resolver output, `secrets-environment`,
so the values a build was configured with and the secrets it signs with always
belong to the same environment.

### Which environment a run uses

| Event | Environment |
|---|---|
| push → `develop` | `development` |
| push → `staging` | `staging` |
| push → `release-*` | `production` |
| `workflow_dispatch` | the `environment` input |
| pull request | *none*: repository values only |
| `BUILD_ENVIRONMENT_SECRETS=false` | *none*: repository values only |

The full mapping is in [BRANCH_FLOW_CONTRACT.md](BRANCH_FLOW_CONTRACT.md) and
[GITHUB_ENVIRONMENTS.md](GITHUB_ENVIRONMENTS.md#2-branch-flow--github-environment-mapping).

---

## 2. The generic variables

| Variable | Replaces | Values | Example (`development` / `production`) |
|---|---|---|---|
| `BUILD_PLATFORMS` | `BUILD_{DEVELOP,STAGING,RELEASE}_PLATFORMS` | comma list: `Android`, `WebGL`, `Linux64`, `LinuxServer`, `Windows64`, `iOS` | `Android` / `Android,WebGL` |
| `TEST_ENABLED` | `TEST_{DEVELOP,STAGING,RELEASE}_ENABLED` | `true` \| `false` | `false` / `true` |
| `ADDRESSABLES_ENABLED` | `ADDRESSABLES_{DEVELOP,STAGING,RELEASE}_ENABLED` | `true` \| `false` | `false` / `true` |
| `UNITY_DEFINE_SYMBOLS` | `UNITY_{DEVELOP,STAGING,RELEASE}_DEFINE_SYMBOLS` | `;`-separated symbols, added to the project's own | `DEV_CHEATS;LOG_VERBOSE` / *(unset)* |

Values are validated exactly as the per-branch names are: an unknown platform
or a boolean other than `true`/`false` fails Resolve Build Config, and the
error names the per-branch variable it stands for (for example
`Invalid TEST_DEVELOP_ENABLED='maybe'`).

iOS rules do not change: a push never builds iOS, and dispatch `All` never
includes it. Ask for iOS by name in the dispatch form.

### `BUILD_PLATFORMS` is not `BUILD_PLATFORMS_ENABLED`

Two different questions:

| Variable | Question | Scope |
|---|---|---|
| `BUILD_PLATFORMS_ENABLED` (legacy `PLATFORMS`) | Which platforms can this **project** build at all? (capability) | repository |
| `BUILD_PLATFORMS` | Which platforms does **this environment** build? (selection) | environment |

The capability filters the selection. With `BUILD_PLATFORMS_ENABLED=Android,iOS`
and `BUILD_PLATFORMS=Android,WebGL`, only Android is built and WebGL is
reported as skipped.

---

## 3. Priority

### Push and pull request

Highest first. The first non-empty value wins.

1. **Generic variable**: environment value, else repository value.
2. Per-branch variable (`BUILD_DEVELOP_PLATFORMS`, ...).
3. Legacy per-branch variable (`DEVELOP_BUILD_PLATFORMS`, ...).
4. Toolkit default ([REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md)).

### Manual dispatch

The form decides; the generic variables fill only what the form leaves open.

| Setting | Source on dispatch |
|---|---|
| platforms | the `platform` field. `All` = the priority list above, for the branch type matching the chosen `environment` (`development` → develop, `staging` → staging, anything else → release) |
| tests, addressables | the `run-tests` / `build-addressables` checkboxes. They always carry a value, so `TEST_ENABLED` / `ADDRESSABLES_ENABLED` are not used |
| define symbols | the `define-symbols` field; **empty** → the environment's `UNITY_DEFINE_SYMBOLS`. Per-branch `UNITY_*_DEFINE_SYMBOLS` never apply to a dispatch |

The log of Resolve Build Config names the winning tier, for example:

```
Platforms (variable-new) resolved to 'Android' for BUILD_PLATFORMS or BUILD_DEVELOP_PLATFORMS (or legacy equivalent)
```

> A generic name set at **repository** level applies to every environment
> **and** to pull requests, and it beats every per-branch variable. Set the
> generic names in environments, or delete the per-branch ones when you set a
> repository-wide value on purpose.

---

## 4. Other variables that become per-environment

`resolve-config` declares the environment, so **every** variable it reads can
now be overridden per environment by setting the same name there. Useful ones:

| Variable | Typical use |
|---|---|
| `BUILD_NUMBER_OFFSET` | separate build-number ranges for development and production |
| `BUILD_IOS_SIGN_DEVELOPMENT` | sign iOS only in `development` |
| `RUNNER_TYPE`, `BUILD_ENGINE`, `RUNNER_LABELS`, `RUNNER_*_LABEL` | build release on a different machine |
| `RUNNER_POLICY` | a different runner policy per environment |
| `BUILD_CLEAN`, `BUILD_TIMEOUT_MINUTES` | clean builds for production only |
| `TEST_EDITMODE_ENABLED`, `TEST_PLAYMODE_ENABLED`, `TEST_FAIL_FAST` | test depth per environment |
| `CACHE_*_ENABLED`, `ARTIFACT_RETENTION_DAYS`, `ARTIFACT_COMPRESSION` | keep production artifacts longer |
| `UNITY_BUILD_METHOD` | a different build method per environment |

The platform build job already declared the environment before this change,
so the variables it reads were already per-environment:
`FIREBASE_APP_ID_ANDROID`, `FIREBASE_APP_ID_IOS`, `FIREBASE_TESTER_GROUPS`,
`IOS_APP_ID`, `ANDROID_PACKAGE_NAME`, `R2_*`, `BUILD_PUBLISH_*`.

### Build number offset

The matrix step resolves the offset as
`BUILD_NUMBER_OFFSET_<TYPE>` → `BUILD_NUMBER_OFFSET` → `0`, where `<TYPE>` is the
build type (`DEVELOPMENT` or `RELEASE`), not the environment
([VERSIONING.md](VERSIONING.md)). To key the offset by environment instead,
set `BUILD_NUMBER_OFFSET` in each environment and **delete** the
`BUILD_NUMBER_OFFSET_DEVELOPMENT` / `BUILD_NUMBER_OFFSET_RELEASE` repository
variables, which would otherwise win.

### Stays at repository level

These are read before an environment is known, or by jobs that declare none:

| Variable | Why |
|---|---|
| `BUILD_ENVIRONMENT_SECRETS` | decides whether there is an environment at all (`select-environment` reads it) |
| `UNITY_VERSION` | validated in `resolve-config` too, but `ProjectVersion.txt` is the source of truth anyway |
| `BUILD_DELIVERY`, `ARTIFACT_STORAGE` | passed by the pipeline to the reusable build workflow; a job that calls a reusable workflow cannot declare an environment |
| `DISCORD_THREAD_ID*` | read by `notify-discord`, which declares no environment. Per-platform routing lives in `.github/discord.json` ([DISCORD_NOTIFICATIONS.md](DISCORD_NOTIFICATIONS.md)) |
| `UNITY_LICENSE_VERSION`, `IDENTITY_DRIFT`, `POST_TEST_CHECK_RUN` | read by jobs without an environment |

A value set for these in an environment is ignored. Keep them in
**Settings → Secrets and variables → Actions → Variables**.

---

## 5. Side effects of declaring the environment earlier

- **Protection rules apply at the start of the run.** Required reviewers,
  wait timers and deployment-branch policies of the environment now gate
  `resolve-config`, the first real job, instead of the first build job. A
  production run waits for approval before anything is resolved. Nothing that
  used to build without approval needs one now: the build jobs already
  declared the same environment.
- **One more deployment record per run.** `resolve-config` records a deployment
  to the environment, like each build job and `final-report` already do.
  Cleanup: [GITHUB_ENVIRONMENTS.md §6](GITHUB_ENVIRONMENTS.md#6-cleaning-up-stale-deployments).
- **One more short job.** `select-environment` runs on `ubuntu-latest` for
  about ten seconds. It reads only event data, dispatch inputs,
  `BUILD_ENVIRONMENT_SECRETS` and the platform capability
  (`BUILD_PLATFORMS_ENABLED` / `PLATFORMS`), all at repository level.
- **The environment must exist** or GitHub creates it on first use, without
  protection rules. Create `development`, `staging` and `production` before
  setting variables in them.

---

## 6. Setting it up

### Web UI

**Settings → Environments → `development` → Environment variables → Add
variable**. Repeat for `staging` and `production`.

### GitHub CLI

```bash
REPO=<owner>/<repo>

gh variable set BUILD_PLATFORMS      --env development --body "Android"      -R "$REPO"
gh variable set TEST_ENABLED         --env development --body "false"        -R "$REPO"
gh variable set UNITY_DEFINE_SYMBOLS --env development --body "DEV_CHEATS"   -R "$REPO"

gh variable set BUILD_PLATFORMS      --env staging     --body "Android"      -R "$REPO"

gh variable set BUILD_PLATFORMS      --env production  --body "Android,iOS"  -R "$REPO"
gh variable set ADDRESSABLES_ENABLED --env production  --body "true"         -R "$REPO"

# Check
gh variable list --env development -R "$REPO"
```

### Migrating from per-branch variables

1. For each environment, copy the per-branch value to the generic name:

   | Per-branch variable | Environment |
   |---|---|
   | `BUILD_DEVELOP_PLATFORMS`, `TEST_DEVELOP_ENABLED`, ... | `development` |
   | `BUILD_STAGING_PLATFORMS`, `TEST_STAGING_ENABLED`, ... | `staging` |
   | `BUILD_RELEASE_PLATFORMS`, `TEST_RELEASE_ENABLED`, ... | `production` |

   ```bash
   copy() {  # copy <repo-variable> <environment> <generic-name>
     v="$(gh variable get "$1" -R "$REPO" 2>/dev/null)" || return 0
     gh variable set "$3" --env "$2" --body "$v" -R "$REPO"
   }
   copy BUILD_DEVELOP_PLATFORMS development BUILD_PLATFORMS
   copy BUILD_STAGING_PLATFORMS staging     BUILD_PLATFORMS
   copy BUILD_RELEASE_PLATFORMS production  BUILD_PLATFORMS
   copy TEST_DEVELOP_ENABLED    development TEST_ENABLED
   # ... same for TEST_*, ADDRESSABLES_*, UNITY_*_DEFINE_SYMBOLS
   ```

2. Run a build on each branch (or dispatch with `platform: All` per
   environment) and check the Resolve Build Config log: the source should read
   `variable-new` with the expected value.
3. Delete the per-branch repository variables:

   ```bash
   gh variable delete BUILD_DEVELOP_PLATFORMS -R "$REPO"
   ```

   Keep them if pull requests should still see per-branch values: pull
   requests have no environment, so the generic names reach them only at
   repository level (see the next section).

### Pull requests

A pull request has no environment, so `resolve-config` reads repository
values only. A PR into `develop` still resolves tests and define symbols from
`TEST_DEVELOP_ENABLED` / `UNITY_DEVELOP_DEFINE_SYMBOLS` and their legacy names.
Keep those per-branch variables if PR validation depends on them, or set the
generic name at repository level to apply one value to every PR.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| The environment value is ignored, the repository value is used | the run had no environment: a pull request, or `BUILD_ENVIRONMENT_SECRETS=false` | expected; see the table in §1 |
| Every environment builds the same platforms | `BUILD_PLATFORMS` is set at repository level and the environments don't define it | set it in each environment, or remove the repository value |
| A per-branch variable no longer has any effect | a generic name is set (at either level) and wins | delete one of them |
| `resolve-config` is waiting | the environment has required reviewers or a wait timer | approve the deployment, or relax the rule for `development` |
| `Branch "x" is not allowed to deploy to production` on Resolve Build Config | the production deployment-branch policy excludes the ref | dispatch from an allowed branch, or update the policy |
| Select Environment fails | the dispatch inputs are invalid (for example an unknown platform) | read its log; the same check runs in Resolve Build Config |

---

## Related

- [REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md): every variable and its default
- [GITHUB_ENVIRONMENTS.md](GITHUB_ENVIRONMENTS.md): environments, protection rules, environment secrets
- [MIGRATING_TO_ENVIRONMENT_SECRETS.md](MIGRATING_TO_ENVIRONMENT_SECRETS.md): the same move for signing secrets
- [BRANCH_FLOW_CONTRACT.md](BRANCH_FLOW_CONTRACT.md): the resolver's outputs
- [VERSIONING.md](VERSIONING.md): build number and offsets
