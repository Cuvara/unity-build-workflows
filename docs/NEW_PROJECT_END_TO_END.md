# New Project, End to End

One pass from an empty Unity repository to a green build, then to building on
your own machine. Every command and every line reference here was exercised
against this toolkit; where something was not verified, it says so.

The other guides go deeper on single topics and are linked at the end. This one
is the order to do things in.

---

## 0 — The one path

Projects consume this toolkit through `unity-pipeline.yml` (builds) and
`pipeline-<platform>-release.yml` (releases), called by the numbered entry
workflows in `templates/` (`consumer-01-ci`, `10-build-development`,
`11-build-release`, `20`–`24` releases). Configuration is Repository Variables;
no `BuildConfig/*.json` is needed.

The pipeline never touches this organization's own images.
`reusable-build-platform.yml` builds either through `game-ci/unity-builder@v5`
or, on a self-hosted Windows runner, through
`docker run unityci/editor:ubuntu-<version>-<variant>-3`, or with a local editor
on a self-hosted machine.

The explicit per-job `unity-build.yml` path (with `BuildConfig/` and the
`ghcr.io/<org>/unity-editor` images) was removed in 7.0.0 —
[MIGRATION_V7.md](MIGRATION_V7.md).

---

## 1 — What your repository must contain

`validate-project` fails the run when any of these is missing
(`unity-pipeline.yml:411-445`):

```
ProjectSettings/ProjectVersion.txt     # also the single source of the Unity version
Packages/manifest.json
Assets/
```

That is the whole hard requirement. No `BuildConfig/`, no build script, no
submodule — the toolkit is referenced remotely by `uses:`.

The Unity version is read from `ProjectVersion.txt`. Nothing pins it in the
caller; if you need to override it, that is the `unity-version` dispatch input.

---

## 2 — Add the caller workflow

```bash
mkdir -p .github/workflows
BASE=https://raw.githubusercontent.com/Cuvara/unity-build-workflows/main/templates
for f in 01-ci 09-build-diagnostics 10-build-development 11-build-release \
         20-release-android 21-release-ios 22-release-webgl \
         23-release-windows 24-release-linux; do
  curl -fsSL "${BASE}/consumer-${f}.yml" -o ".github/workflows/${f}.yml"
done
```

Keep only the release files for platforms you ship.

The template is ready as shipped. Two things in it must stay in step, and a
mismatch is silent:

```yaml
uses: Cuvara/unity-build-workflows/.github/workflows/unity-pipeline.yml@v3
...
toolkit-ref:  'v3'   # MUST equal the @ref above
```

The workflow and the scripts it runs have to come from the same version, and
nothing warns you when they do not.

`@v3` is the floating major tag and moves with each `3.x.y` release; `@v3.0.0`
pins exactly; `@main` is for developing the toolkit itself. **`@v2` predates the
release layer** — no Release Set, no promotion, no platform capability model —
and `@v1` is frozen at `v1.1.3`.

Commit and push it. Pushes to `develop`, `staging` and `release-*` and pull
requests against them now build; `workflow_dispatch` gives you manual control.

---

## 3 — Secrets

The pipeline path needs **either** a `.ulf` **or** account credentials — not
necessarily both. That is what `validate-license` enforces
(`unity-pipeline.yml:487-490`):

```
Need: (UNITY_EMAIL+UNITY_PASSWORD) or (UNITY_LICENSE)
```

Measured, not inferred: `Cuvara/IndieRPGMMOAdventure` builds Android and WebGL
green on this toolkit with **`UNITY_LICENSE` alone** — its only other secret is
`DISCORD_WEBHOOK_URL` (run `34189744276`, 45m17s, `Build Android` and
`Build WebGL` both `success`).

```bash
REPO="YOUR_ORG/YOUR_REPO"

# Simplest working setup — a .ulf and nothing else:
gh secret set UNITY_LICENSE  --repo "$REPO" < /path/to/Unity_lic.ulf

# Or account activation instead, with no .ulf:
gh secret set UNITY_EMAIL    --repo "$REPO"
gh secret set UNITY_PASSWORD --repo "$REPO"
```

`UNITY_LICENSE` is the **raw `.ulf` XML** — do not base64-encode it. All three
are declared `required: false` (`unity-pipeline.yml:125-134`); the job above is
what actually rejects an empty combination.

**When you do need all three.** Set the `.ulf` *and* the credentials together if
your `.ulf` cannot activate offline — a Unity **Personal** licence bound to a
different machine id fails with `TimeStamp validation failed`, and credentials
alone fail with `0 entitlements`. That combination is the `personal-combined`
strategy described in
[UNITY_PERSONAL_DOCKER_LICENSE.md](UNITY_PERSONAL_DOCKER_LICENSE.md), and it is
also what the Windows-runner docker lane needs
(`scripts/build/docker_windows_container.sh`). Start with the `.ulf` alone; add
credentials if activation fails.

Optional: `DISCORD_WEBHOOK_URL` for build-completion embeds — absent means the
notification step is a no-op, not an error.

Android release signing is optional: for a project with a custom keystore in
Player Settings, the native lanes' `PlayerBuilder` reads `ANDROID_KEYSTORE_PASS`
and `ANDROID_KEY_PASS` (never logged) — see
[TOOLKIT_BUILD_PACKAGE.md](TOOLKIT_BUILD_PACKAGE.md).

---

## 4 — First build

```bash
gh workflow run 10-build-development.yml --repo "$REPO" --ref develop \
  -f platform=Android -f run-tests=false
gh run watch --repo "$REPO" \
  "$(gh run list --repo "$REPO" --workflow 10-build-development.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
```

Jobs you should see, in stage order: `Resolve Build Config`,
`Validate Unity Project`, `Validate Unity License`, optionally
`Unity Tests`, `Quality Gate`, `Android`,
`Android / Validate APK`, `Final Report` — each rendered under its lane, so a
development build reads `Dev / Android`. Every job's summary carries
a progress ladder showing how far the run has got.

`platform` takes one name, a comma-separated list (`Android,WebGL`), `Desktop`
for Windows64 + Linux64, or `All` for whatever `*_BUILD_PLATFORMS` says for
that environment. A name it does not recognise fails the run rather than
building nothing and reporting success.

Artifacts, from `reusable-build-platform.yml`:

| Artifact | Contents |
|---|---|
| `unity-build-<Platform>` | the player, from `build/` (`:940`) |
| `unity-build-<Platform>-logs` | `Editor.log` and reports (`:971`) |
| `unity-build-Addressables` | catalog and bundles, when Addressables ran (`:954`) |

```bash
gh run download <RUN_ID> --repo "$REPO" --name unity-build-Android
```

**Do not trust the green tick alone — open the artifact.** This repository has
already shipped two fixes for builds that reported success while producing
nothing: `Final Report` counted a `cancelled` job as a pass, and the
self-hosted lane can run `-buildTarget` with no build method and exit 0. If
`unity-build-Android` is empty or missing, the run did not build a player
whatever the summary said.

---

## 5 — Optional: Repository Variables

All optional; the defaults below are what `scripts/common/resolve_build_flow.sh`
applies when a variable is unset. Use the **grouped** names; the older ungrouped
ones (`DEVELOP_BUILD_PLATFORMS`, `DEVELOP_RUN_TESTS`, `DEFAULT_RUNNER_MODE`, …)
still resolve as a fallback but log a deprecation note.

```bash
gh variable set BUILD_DEVELOP_PLATFORMS --repo "$REPO" --body "Android,WebGL"
gh variable set BUILD_STAGING_PLATFORMS --repo "$REPO" --body "Android,WebGL,Linux64,LinuxServer,Windows64"
gh variable set BUILD_RELEASE_PLATFORMS --repo "$REPO" --body "Android,WebGL,Linux64,LinuxServer,Windows64"
gh variable set TEST_DEVELOP_ENABLED    --repo "$REPO" --body "true"
gh variable set ADDRESSABLES_RELEASE_ENABLED --repo "$REPO" --body "true"
gh variable set BUILD_CLEAN             --repo "$REPO" --body "false"
```

| Branch flow | Platforms by default | Tests | Addressables |
|---|---|---|---|
| `develop` | `Android,WebGL` | on | off |
| `staging` | `Android,WebGL,Linux64,LinuxServer,Windows64` | on | off |
| `release-*` | same as staging | on | **on** |

Resolution order for every setting: **dispatch input → grouped variable →
legacy variable → default**. Full reference:
[REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md),
[BRANCH_FLOW_CONTRACT.md](BRANCH_FLOW_CONTRACT.md).

Platform notes worth knowing before you set these: **iOS never builds
automatically** — it is dispatch-only and needs a macOS runner.
**`Windows64` on the docker lane is Mono only**; IL2CPP requires a self-hosted
Windows runner (§6).

---

## 6 — Building on your own machine

### 6.1 The two axes

`RUNNER_TYPE` answers *where* the job runs, `BUILD_ENGINE` answers *how* Unity
builds. They are independent, and three of the four combinations are supported:

| `RUNNER_TYPE` | `BUILD_ENGINE` | Meaning |
|---|---|---|
| `github-hosted` | `docker` | the default; nothing to install |
| `self-hosted` | `local` | your machine, your installed Unity Editor |
| `self-hosted` | `docker` | your machine, Unity inside a container (needs a **Linux-container** Docker engine) |
| `github-hosted` | `local` | rejected up front — a hosted runner has no Unity |

### 6.2 What `self-hosted` + `local` requires on the machine

| Requirement | Detail | Enforced at |
|---|---|---|
| Unity for the project's exact version | Installed by hand, or provisioned by the job's Unity preflight step (Python 3.8+, writable `UNITY_PREFLIGHT_INSTALL_ROOT`) | `reusable-build-platform.yml` step `unity-preflight`; [UNITY_PREFLIGHT.md](UNITY_PREFLIGHT.md#in-ci-self-hosted-native-lanes) |
| The **exact** version from `ProjectVersion.txt` | a different installed version fails that path check | resolver → `unity-version` |
| Nothing for the build script | the job copies the toolkit package into `Packages/` and runs `Company.BuildPipeline.Editor.PlayerBuilder.Build` / `AddressableBuilder.Build` ([TOOLKIT_BUILD_PACKAGE.md](TOOLKIT_BUILD_PACKAGE.md)) | "Install toolkit build package" step |
| Unity activated once via Unity Hub | this lane never reads `UNITY_LICENSE`/`EMAIL`/`PASSWORD` | activation step skipped for `local` |
| Git + Git LFS on `PATH` | `actions/checkout` | — |

The project needs no `PlayerBuilder.cs`: the toolkit's package provides it
for every build and removes it afterwards.

iOS cannot build on the Windows lane (`:815`); it needs macOS.

### 6.3 Register the runner

Repository-scoped is the simplest and has no plan restrictions:

```bash
gh api -X POST /repos/<OWNER>/<REPO>/actions/runners/registration-token --jq .token
```

```powershell
mkdir C:\actions-runner; cd C:\actions-runner
$V = "2.328.0"
Invoke-WebRequest "https://github.com/actions/runner/releases/download/v$V/actions-runner-win-x64-$V.zip" -OutFile runner.zip
Expand-Archive runner.zip -DestinationPath . -Force
.\config.cmd --url https://github.com/<OWNER>/<REPO> `
             --token <TOKEN> --name unity-win-01 `
             --labels self-hosted,windows --work _work
```

Organization-scoped instead — one runner serving several repos — is
[SELF_HOSTED_ORG_RUNNER.md](SELF_HOSTED_ORG_RUNNER.md). Read its §3 first: on a
**free** organization the only runner group is `Default`, it cannot be narrowed
to selected repositories, and it ships with
`allows_public_repositories=false` — so a runner there **receives no jobs from a
public repository at all**, and no label change fixes that.

> **Public repositories and self-hosted runners do not mix.** A fork pull
> request runs *its own* workflow file, so whoever opens it chooses what
> executes on your machine. The caller template builds on `pull_request` by
> default. Before pointing a runner at a public repo: require approval for all
> outside contributors, or make the repository private (unlimited on the free
> plan), or keep using GitHub-hosted runners.

### 6.4 Point the pipeline at it

```bash
gh variable set RUNNER_TYPE   --repo "$REPO" --body "self-hosted"
gh variable set BUILD_ENGINE  --repo "$REPO" --body "local"
gh variable set RUNNER_LABELS --repo "$REPO" --body "self-hosted,windows"
```

Or per run, without changing any variable:

```bash
gh workflow run 10-build-development.yml --repo "$REPO" --ref develop \
  -f platform=Windows64 -f runner-type=self-hosted \
  -f build-engine=local -f runner-labels='["self-hosted","windows"]'
```

`RUNNER_LABELS` must equal the runner's registered labels after
comma-split/trim/dedup; `runs-on` requires the runner to carry **every**
requested label, and a mismatch queues the job forever with no error. Two traps:

- With `RUNNER_TYPE=self-hosted` and `RUNNER_LABELS` **empty**, labels fall back
  to the hardcoded `self-hosted,windows` regardless of the machine's OS
  (`resolve_build_flow.sh:588`). A Linux or macOS runner must set
  `RUNNER_LABELS` explicitly.
- A `unity` label is **not** required, whatever older revisions of
  [SELF_HOSTED_WINDOWS_RUNNER.md](SELF_HOSTED_WINDOWS_RUNNER.md) implied —
  nothing in this repository requests it. Extra labels are harmless.

### 6.5 What actually moves to your machine

Only the Unity work. The orchestration stays on GitHub-hosted runners
(`unity-pipeline.yml:146, 415, 463, 888, 1100`), so a run still uses a few
hosted minutes:

| Job | Runs on |
|---|---|
| `resolve-config`, `validate-project`, `validate-license`, `final-report`, `notify-discord` | `ubuntu-latest`, always |
| `unity-tests` | your runner (`reusable-unity-tests.yml:151`) |
| `build-*`, `build-addressables` | your runner (`reusable-build-platform.yml:230`) |
| `build-ios` | macOS only, never this lane |

---

## 7 — When it goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| `Project validation failed` | one of the three required paths is missing (§1) | check `ProjectVersion.txt`, `Packages/manifest.json`, `Assets/` |
| Activation fails, `0 entitlements` | credentials without the `.ulf` | set all three secrets (§3) |
| Activation fails, `TimeStamp validation failed` | `.ulf` without credentials | same |
| Green run, empty `unity-build-<Platform>` artifact | self-hosted lane with no `PlayerBuilder.Build` | §6.2 |
| Unity preflight step failed | No Python, an unwritable install root, a failed download, or a version pin that differs from `ProjectVersion.txt` | follow the remediation the step prints |
| Job stays *Queued*, runner Idle | label mismatch, or an org runner group that disallows public repos | §6.4, §6.3 |
| `Wrong Docker container mode … Server.Os=windows` | `BUILD_ENGINE=docker` on a Windows engine; `unityci/editor` images are Linux | switch Docker Desktop to Linux containers |
| iOS job refuses to run | iOS is macOS-only and dispatch-only | use a macOS runner |

More: [TROUBLESHOOTING.md](TROUBLESHOOTING.md),
[GITHUB_ACTIONS_BUILD_RUNBOOK.md](GITHUB_ACTIONS_BUILD_RUNBOOK.md).

---

## 8 — Where to read further

| Topic | Document |
|---|---|
| The pipeline in detail | [CONSUMER_SETUP.md](CONSUMER_SETUP.md) |
| Coming from `unity-build.yml` (removed in 7.0.0) | [MIGRATION_V7.md](MIGRATION_V7.md) |
| Branch → flow rules, every variable | [BRANCH_FLOW_CONTRACT.md](BRANCH_FLOW_CONTRACT.md), [REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md) |
| Runner vs build engine | [RUNNER_AND_BUILD_ENGINE.md](RUNNER_AND_BUILD_ENGINE.md) |
| Own machine, per repo / per org | [SELF_HOSTED_WINDOWS_RUNNER.md](SELF_HOSTED_WINDOWS_RUNNER.md), [SELF_HOSTED_ORG_RUNNER.md](SELF_HOSTED_ORG_RUNNER.md) |
| Licensing in containers | [UNITY_PERSONAL_DOCKER_LICENSE.md](UNITY_PERSONAL_DOCKER_LICENSE.md) |
| Images: build, scan, pin, deprecate | [IMAGE_LIFECYCLE.md](IMAGE_LIFECYCLE.md) |
| Lane and entry-point rationale | [ARCHITECTURE.md](ARCHITECTURE.md) |
