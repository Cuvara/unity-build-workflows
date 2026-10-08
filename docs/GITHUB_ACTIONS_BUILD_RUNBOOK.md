# GitHub Actions Build Runbook

Operational runbook for the Unity CI system in NDC-Unity-Template. Covers
triggering builds, reading logs, downloading artifacts, and common fixes.

Related docs:
- [UNITY\_PERSONAL\_DOCKER\_LICENSE.md](UNITY_PERSONAL_DOCKER_LICENSE.md) — license setup and troubleshooting
- [UNITY\_VERSION\_UPGRADE.md](UNITY_VERSION_UPGRADE.md) — upgrading Unity version
- [EXPLICIT\_PLATFORM\_FLOW.md](EXPLICIT_PLATFORM_FLOW.md) — the superseded explicit-platform-jobs design (historical record)
- [GITHUB\_ENVIRONMENTS.md](GITHUB_ENVIRONMENTS.md) — GitHub Environments, deployment protection rules, branch-flow mapping, stale deployment cleanup

---

## 1. Required Secrets

Set these secrets in the consumer repo (`Settings → Secrets and variables → Actions`)
before any build can succeed.

### Core (all platforms)

| Secret | Purpose |
|---|---|
| `UNITY_LICENSE` | Raw `.ulf` file contents — **required** for Personal/free license activation |
| `UNITY_EMAIL` | Unity account email — **required** alongside `UNITY_LICENSE` |
| `UNITY_PASSWORD` | Unity account password — **required** alongside `UNITY_LICENSE` |

All three must be set together. See [UNITY\_PERSONAL\_DOCKER\_LICENSE.md](UNITY_PERSONAL_DOCKER_LICENSE.md)
for setup instructions and the reason all three are required.

### Android signing (optional — unsigned builds work without these)

| Secret | Purpose |
|---|---|
| `ANDROID_KEYSTORE_BASE64` | Base64-encoded `.jks` / `.keystore` file |
| `ANDROID_KEYSTORE_PASS` | Keystore password |
| `ANDROID_KEY_ALIAS` | Key alias |
| `ANDROID_KEY_PASS` | Key password |
| `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` | Google Play deployment (optional) |

### WebGL deployment (optional)

| Secret | Purpose |
|---|---|
| `CLOUDFLARE_API_TOKEN` | Cloudflare Pages deployment (optional) |
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account ID (optional) |

### iOS (deferred — see [Section 10](#10-iosmacos-runner-limitations))

| Secret | Purpose |
|---|---|
| `IOS_DISTRIBUTION_CERTIFICATE_BASE64` | Base64-encoded `.p12` |
| `IOS_DISTRIBUTION_CERTIFICATE_PASSWORD` | `.p12` export password |
| `IOS_PROVISIONING_PROFILE_BASE64` | Base64-encoded `.mobileprovision` |
| `APP_STORE_CONNECT_KEY_ID` | ASC API key ID |
| `APP_STORE_CONNECT_ISSUER_ID` | ASC issuer UUID |
| `APP_STORE_CONNECT_PRIVATE_KEY` | `.p8` key contents |

### Notifications (optional)

| Secret | Purpose |
|---|---|
| `DISCORD_WEBHOOK_URL` | Discord channel webhook; omit to disable notifications |

Verify secrets are set:
```bash
gh secret list --repo Cuvara/NDCUnityTemplate \
  | grep -E 'UNITY_LICENSE|UNITY_EMAIL|UNITY_PASSWORD'
```

---

## 2. Triggering Builds Manually

Builds are dispatched through the consumer's entry workflows (copied from
`templates/`): **Build / Development** (`10-build-development.yml`) and
**Build / Release** (`11-build-release.yml`), both calling `unity-pipeline.yml`.
Each platform is a separate, independently retryable job in the run.

### Trigger a single platform build

```bash
gh workflow run "Build / Development" \
  --repo <ORG>/<CONSUMER_REPO> \
  --ref develop \
  -f platform=Android        # Android | iOS | WebGL | Windows | Linux | "Linux Server"
```

### Trigger the environment's platform set

```bash
gh workflow run "Build / Development" \
  --repo <ORG>/<CONSUMER_REPO> \
  --ref develop \
  -f platform=All            # the environment's BUILD_PLATFORMS; Desktop = Windows64 + Linux64
```

### Dispatch inputs

| Input | Default | Options | Description |
|---|---|---|---|
| `platform` | `All` | `All`, `Desktop`, `Android`, `iOS`, `WebGL`, `Windows`, `Linux`, `Linux Server` | Platform(s) to build |
| `environment` | `development` | `development`, `staging` | Build / Development only |
| `run-tests` | `true` | `true` / `false` | Unity tests as the quality gate |
| `test-mode` | `All` | `EditMode`, `PlayMode`, `All` | Test suite |
| `build-addressables` | `false` | `true` / `false` | Addressables before the player build |
| `unity-version` | *(blank)* | e.g. `6000.0.26f1` | Override; blank = `ProjectVersion.txt` |
| `clean-build` | `auto` | `auto`, `true`, `false` | `auto` = clean for release, incremental otherwise |
| `define-symbols` | *(blank)* | `;` or `,` separated | Extra scripting define symbols |
| `runner-type` | `auto` | `auto`, `github-hosted`, `self-hosted` | WHERE the job runs |
| `build-engine` | `auto` | `auto`, `docker`, `local` | HOW Unity builds |
| `runner-labels` | *(blank)* | JSON array | Pins every Unity job to these labels |

Everything else is Repository Variables ([REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md)).
On `push` to `develop` / `staging` / `release-*`, the branch decides
([BRANCH_FLOW_CONTRACT.md](BRANCH_FLOW_CONTRACT.md)).

### Example with extra inputs

```bash
gh workflow run "Build / Development" \
  --repo <ORG>/<CONSUMER_REPO> \
  --ref develop \
  -f platform=Android \
  -f environment=staging \
  -f run-tests=true \
  -f test-mode=EditMode \
  -f build-addressables=true \
  -f clean-build=false

# A signed Android App Bundle for Play Store submission
gh workflow run "Build / Release" \
  --repo <ORG>/<CONSUMER_REPO> \
  --ref main \
  -f platform=Android
```

There is no output-format flag: `Build / Release` produces the signed App
Bundle because that is what the release lifecycle means. `Build / Development`
produces an APK for the same reason. See `docs/PIPELINE_ARCHITECTURE.md`
§"Four questions, four owners".

The `unity-build.yml` / `build.yml` dispatch this section used to describe was
removed in 7.0.0 ([MIGRATION_V7.md](MIGRATION_V7.md)).

---

## 3. Platform Build Order

The pipeline workflow (`unity-pipeline.yml`) runs a single Resolve Config →
Validate step, then fans out to explicit per-platform build jobs. Docker
platforms run in parallel. (The legacy `unity-build-multi.yml` matrix workflow
has been removed — use `unity-pipeline.yml`.)

**Supported Docker platforms** (all run on `ubuntu-latest` + Docker):

| # | Platform | Image variant | Status |
|---|---|---|---|
| 1 | Android | `android` | Supported |
| 2 | WebGL | `webgl` | Supported |
| 3 | Linux64 | `linux` | Supported |
| 4 | LinuxServer | `linux` | Supported |

**iOS** — see [Section 10](#10-iosmacos-runner-limitations). Currently blocked /
deferred.

---

## 4. Reading Logs

### List recent runs

```bash
gh run list --repo <ORG>/<CONSUMER_REPO> \
  --workflow "Build / Development" --limit 10
```

### View failed job logs

```bash
# Show only logs from failed steps
gh run view <RUN_ID> \
  --repo Cuvara/NDCUnityTemplate \
  --log-failed
```

### View all logs for a run

```bash
gh run view <RUN_ID> \
  --repo Cuvara/NDCUnityTemplate \
  --log
```

### Stream logs while a run is in progress

```bash
gh run watch <RUN_ID> \
  --repo Cuvara/NDCUnityTemplate
```

### Fetch raw logs for a specific job via API

```bash
# List jobs in a run to get job IDs
gh api repos/Cuvara/NDCUnityTemplate/actions/runs/<RUN_ID>/jobs \
  --jq '.jobs[] | {id: .id, name: .name, status: .status, conclusion: .conclusion}'

# Fetch raw log for a specific job
gh api repos/Cuvara/NDCUnityTemplate/actions/jobs/<JOB_ID>/logs
```

### Key log lines to look for

| Log pattern | Meaning |
|---|---|
| `Selected activation strategy: personal-combined` | License activation path chosen correctly |
| `License activation succeeded (personal-combined)` | Unity licensed OK |
| `TimeStamp validation failed` | Missing `UNITY_EMAIL`/`UNITY_PASSWORD` alongside `.ulf` |
| `0 entitlements` | Missing `UNITY_LICENSE` alongside credentials |
| `Activation successful` | Unity internal confirmation |
| `Build succeeded` | Unity build completed |
| `Error response from daemon: manifest unknown` | Docker image not found — rebuild images |

---

## 5. Downloading Artifacts

Build artifacts are retained per build type — release 90 days, staging 14, development 7 — unless `ARTIFACT_RETENTION_DAYS` overrides it ([REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md)).

### List artifacts for a run

```bash
gh run view <RUN_ID> \
  --repo Cuvara/NDCUnityTemplate \
  --json artifacts --jq '.artifacts[].name'
```

### Download all artifacts from a run

```bash
gh run download <RUN_ID> \
  --repo Cuvara/NDCUnityTemplate
ls -lh
```

### Download a specific artifact by name

```bash
gh run download <RUN_ID> \
  --repo Cuvara/NDCUnityTemplate \
  --name "<artifact-name>"
```

---

## 6. Rebuilding Docker Images

Docker images are hosted at `ghcr.io/cuvara/unity-editor:<version>-<variant>`.
Rebuild whenever the Unity version changes or the `docker/` directory is modified.

Image build workflow: `build-unity-image.yml` in the toolkit repo.

```bash
UNITY_VERSION="6000.0.26f1"

# Rebuild a single variant
gh workflow run build-unity-image.yml \
  --repo Cuvara/unity-build-workflows \
  --ref main \
  -f unity-version="${UNITY_VERSION}" \
  -f image-variant=android \
  -f push-image=true \
  -f run-vulnerability-scan=true

# Trigger all three variants
for VARIANT in android webgl linux; do
  gh workflow run build-unity-image.yml \
    --repo Cuvara/unity-build-workflows \
    --ref main \
    -f unity-version="${UNITY_VERSION}" \
    -f image-variant="${VARIANT}" \
    -f push-image=true \
    -f run-vulnerability-scan=true
done
```

Monitor builds:
```bash
gh run list --repo Cuvara/unity-build-workflows \
  --workflow build-unity-image.yml --limit 10
```

For a full upgrade procedure, see [UNITY\_VERSION\_UPGRADE.md](UNITY_VERSION_UPGRADE.md).

---

## 7. Common Errors and Fixes

| Error | Cause | Fix |
|---|---|---|
| `TimeStamp validation failed` | `.ulf` alone without credentials | Set all three secrets — see [UNITY\_PERSONAL\_DOCKER\_LICENSE.md](UNITY_PERSONAL_DOCKER_LICENSE.md) |
| `0 entitlements` | Credentials alone without `.ulf` | Add `UNITY_LICENSE` secret (raw `.ulf`) |
| `Error response from daemon: manifest unknown` | Docker image not found for this version+variant | Rebuild image — see [Section 6](#6-rebuilding-docker-images) |
| `MFA_OR_2FA_REQUIRED` | CI Unity account has 2FA enabled | Disable 2FA on the CI Unity account |
| `ACTIVATION_LIMIT_REACHED` | License activation seats exhausted | Return a seat in Unity Hub (Manage License → Return License) |
| `AUTH_FAILED` | Wrong email or password | Re-set `UNITY_EMAIL` and `UNITY_PASSWORD` |
| Build passes but artifacts empty | Upload step skipped or artifact path wrong | Check `ARTIFACT_STORAGE` (with `firebase` no binary goes to GitHub); check the Unity build output path (`build/`) |
| `version mismatch` in Editor.log | Image built for different Unity version | `ProjectSettings/ProjectVersion.txt` decides the version; rebuild images or clear a stale `unity-version` dispatch override |

For licensing-specific issues, see the full troubleshooting table in
[UNITY\_PERSONAL\_DOCKER\_LICENSE.md](UNITY_PERSONAL_DOCKER_LICENSE.md).

---

## 8. GameCI Baseline

The Docker lane of `unity-pipeline.yml` already builds through stock
`game-ci/unity-builder`, so a separate baseline workflow is not needed to
isolate toolkit-vs-Unity failures. (`unity-build-gameci.yml` was removed in
7.0.0 — [MIGRATION_V7.md](MIGRATION_V7.md).)

---

## 9. When to Use a Self-Hosted Windows Runner

The toolkit does **not** support Windows build targets (Unity Windows
Standalone). However, a self-hosted Windows runner may be useful for:

- Pre-processing steps that require Windows-native tools.
- Running Unity Editor in Play Mode tests locally before CI.
- Generating `.alf` license request files when Unity Hub is not available on
  another platform.

If a Windows runner is registered, it must have Docker Desktop installed and
running for Docker-lane builds to work. For Windows-only tasks, configure the
job `runs-on:` to match the runner's label, e.g. `windows-unity`.

There is no Windows CI lane in the current workflow configuration.

---

## 10. iOS / macOS Runner Limitations

**iOS builds are currently BLOCKED / deferred.**

| Requirement | Status |
|---|---|
| Self-hosted macOS runner (labels `self-hosted`, `macOS`) | Required |
| Unity iOS Build Support module installed on runner | Not configured |
| Xcode installed and selected on runner | Not configured |
| iOS secrets (`IOS_DISTRIBUTION_CERTIFICATE_BASE64`, etc.) | Not set |

The pipeline routes iOS to `RUNNER_MACOS_LABEL` (or a runner policy). Until a
macOS runner matching it is registered and configured, an iOS build queues, or
reports `blocked` on a non-macOS runner.

**To unblock iOS:** Provision a macOS machine (physical or cloud), install Unity
with iOS Build Support and Xcode, register it as a self-hosted GitHub Actions
runner (see [SELF_HOSTED_MACOS_RUNNER.md](SELF_HOSTED_MACOS_RUNNER.md)), then set the iOS signing secrets
listed in [Section 1](#1-required-secrets).

See [IOS\_VERIFICATION.md](IOS_VERIFICATION.md) for the macOS runner verification
runbook once a runner is available.

---

## 11. Environments & Deployments

The pipeline creates GitHub Deployment records via the `final-report` job's
`environment:` key. Only push and manual-dispatch runs create deployments —
PR runs never do (the `gh-environment` output is empty for all PR flows).

| Branch | GitHub Environment | Protection rules |
|---|---|---|
| push → `develop` | `development` | Branch: `develop` |
| push → `staging` | `staging` | Branch: `staging` |
| push → `release-*` | `production` | Branches: `release-*`, `main` — **add required reviewer** |
| `workflow_dispatch` | Selected `environment` input | Per-environment rules apply |
| PR (any target) | *(none)* | n/a |

**Action required:** The `production` environment currently has **0 required
reviewers**. Add at least one human approver in
`Settings → Environments → production → Required reviewers` before your first
release push.

For the full guide — environment vs repository variables vs secrets, protection
rule configuration steps, branch policy verification, and stale deployment
cleanup — see [GITHUB\_ENVIRONMENTS.md](GITHUB_ENVIRONMENTS.md).

```bash
# Verify environments and branch policies
gh api repos/Cuvara/NDCUnityTemplate/environments \
  --jq '.environments[] | {name: .name, protection_rules: .protection_rules}'

# List recent deployments
gh api 'repos/Cuvara/NDCUnityTemplate/deployments?per_page=20' \
  --jq '.[] | {id: .id, environment: .environment, ref: .ref, created_at: .created_at}'
```

---

## Common `gh` Command Reference

```bash
# List recent workflow runs
gh run list --repo Cuvara/NDCUnityTemplate --workflow "Build / Development" --limit 10

# View a specific run (summary)
gh run view <RUN_ID> --repo Cuvara/NDCUnityTemplate

# Show failed step logs
gh run view <RUN_ID> --repo Cuvara/NDCUnityTemplate --log-failed

# Download artifacts
gh run download <RUN_ID> --repo Cuvara/NDCUnityTemplate

# List secrets (names only)
gh secret list --repo Cuvara/NDCUnityTemplate

# Set a secret from a file
gh secret set UNITY_LICENSE --repo Cuvara/NDCUnityTemplate < Unity_lic.ulf

# Trigger a build
gh workflow run "Build / Development" --repo Cuvara/NDCUnityTemplate --ref develop -f platform=Android

# Trigger image rebuild
gh workflow run build-unity-image.yml --repo Cuvara/unity-build-workflows --ref main \
  -f unity-version=6000.0.26f1 -f image-variant=android -f push-image=true

# List image build runs
gh run list --repo Cuvara/unity-build-workflows --workflow build-unity-image.yml --limit 10
```
