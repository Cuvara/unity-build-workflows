# New Project, End to End

One pass from an empty Unity repository to a green build, then to building on
your own machine. Every command and every line reference here was exercised
against this toolkit; where something was not verified, it says so.

The other guides go deeper on single topics and are linked at the end. This one
is the order to do things in.

---

## 0 — Pick a path first

There are two ways to consume this toolkit, they wire up different workflows,
and **they do not use the same Docker images**. Choosing wrong is the most
expensive mistake available here.

| | **Path A — pipeline** (recommended) | **Path B — explicit builds** |
|---|---|---|
| Caller uses | `unity-pipeline.yml` | `unity-build.yml` |
| Configuration | Repository Variables | workflow inputs per job |
| `BuildConfig/*.json` | **not needed** | **required** |
| `PlayerBuilder.Build` in your project | not needed on the docker lane | required |
| Docker image | `unityci/editor` from Docker Hub, via `game-ci/unity-builder` | `ghcr.io/<org>/unity-editor`, which **you must publish first** |
| Branch-based CI on `develop`/`staging`/`release-*` | yes, built in | you wire it |

Verified, because it is counter-intuitive: the pipeline path never touches this
organization's own images. `reusable-build-platform.yml` builds either through
`game-ci/unity-builder@v5` or, on a self-hosted Windows runner, through
`docker run unityci/editor:ubuntu-<version>-<variant>-3` (`:642`). Only the
Path B workflows call the `resolve-unity-image` action and therefore
`ghcr.io/<org>/unity-editor`.

**Start with Path A.** Everything below is Path A unless a section says
otherwise; Path B is in §7.

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
curl -fsSL \
  https://raw.githubusercontent.com/Cuvara/unity-build-workflows/main/templates/consumer-unity-build.yml \
  -o .github/workflows/unity-build.yml
```

The template is ready as shipped. Two things in it must stay in step, and a
mismatch is silent:

```yaml
uses: Cuvara/unity-build-workflows/.github/workflows/unity-pipeline.yml@v2
...
toolkit-ref:  'v2'   # MUST equal the @ref above
```

`@v2` is the floating major tag and moves with each `v2.x.y` release; `@v2.2.1`
pins exactly; `@main` is for developing the toolkit itself. **`@v1` is frozen at
`v1.1.3`** and receives nothing further.

Commit and push it. Pushes to `develop`, `staging` and `release-*` and pull
requests against them now build; `workflow_dispatch` gives you manual control.

---

## 3 — Secrets

Three, and the docker lane needs **all three together** — this is Unity's
`personal-combined` activation:

```bash
REPO="YOUR_ORG/YOUR_REPO"
gh secret set UNITY_EMAIL    --repo "$REPO"
gh secret set UNITY_PASSWORD --repo "$REPO"
gh secret set UNITY_LICENSE  --repo "$REPO" < /path/to/Unity_lic.ulf
```

`UNITY_LICENSE` is the **raw `.ulf` XML** — do not base64-encode it. The
workflow interface marks all three `required: false`
(`unity-pipeline.yml:125-134`) because other lanes do not need them; on the
docker lane, omitting any one of them fails activation:

| What you provide | Failure |
|---|---|
| `.ulf` only | `TimeStamp validation failed` |
| credentials only | `0 entitlements` |
| all three | activation succeeds |

Optional: `DISCORD_WEBHOOK_URL` for build-completion embeds — absent means the
notification step is a no-op, not an error.

Android release signing (`ANDROID_KEYSTORE_BASE64`, `ANDROID_KEYSTORE_PASS`,
`ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASS`) is only consulted on Path B and the
release workflows. It is **not** passed to the Editor on the pipeline path.

---

## 4 — First build

```bash
gh workflow run unity-build.yml --repo "$REPO" --ref develop \
  -f platform=Android -f environment=development -f run-tests=false
gh run watch --repo "$REPO" \
  "$(gh run list --repo "$REPO" --workflow unity-build.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
```

Jobs you should see, in order: `resolve-config`, `validate-project`,
`validate-license`, optionally `Unity Tests`, then `Build Android`, then
`final-report`.

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
| Unity at the Hub default path | `C:\Program Files\Unity\Hub\Editor\<version>\Editor\Unity.exe`, or `C:\Program Files\Unity <version>\Editor\Unity.exe` | `reusable-build-platform.yml:794`, `:797`; hard error `:800` |
| The **exact** version from `ProjectVersion.txt` | a different installed version fails that path check | resolver → `unity-version` |
| **`PlayerBuilder.Build`** in an Editor assembly, global namespace | the lane substitutes it when `build-method` is empty; `-buildTarget` alone builds nothing and still exits 0 | `:842-843` (Windows), `:903` (bash) |
| `AddressableBuilder.Build`, if you build Addressables | called directly | `:806` |
| Unity activated once via Unity Hub | this lane never reads `UNITY_LICENSE`/`EMAIL`/`PASSWORD` | activation step skipped for `local` |
| Git + Git LFS on `PATH` | `actions/checkout` | — |

Copy the reference implementation rather than writing one:

```bash
mkdir -p Assets/BuildScripts/Editor
curl -fsSL \
  https://raw.githubusercontent.com/Cuvara/unity-build-workflows/main/templates/PlayerBuilder.cs \
  -o Assets/BuildScripts/Editor/PlayerBuilder.cs
```

It reads the whole CI contract and nothing more — `BUILD_OUTPUT_DIR` (always
`build`) and `ANDROID_APP_BUNDLE` — and exits non-zero when
`BuildPipeline.BuildPlayer` reports failure. It is shipped as reviewed but
**uncompiled**: `templates/` is not part of a Unity project, so no CI builds it.

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
gh workflow run unity-build.yml --repo "$REPO" --ref develop \
  -f platform=Windows64 -f runner-type=self-hosted \
  -f build-engine=local -f runner-labels=self-hosted,windows
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

## 7 — Path B: explicit builds, and the images it needs

Choose this only if you want one build per caller job with
`target-platform` / `test-level` / `cache-mode` inputs and your own
`BuildConfig/*.json`. Walkthrough: [ADD_NEW_PROJECT.md](ADD_NEW_PROJECT.md).

This is the path that consumes `ghcr.io/<org>/unity-editor`, through the
`resolve-unity-image` action, which **requires an `image-namespace`** and fails
with an actionable error when it is absent. Publish the images first:

```bash
gh workflow run build-unity-image.yml --repo Cuvara/unity-build-workflows --ref main \
  -f unity-version=6000.0.26f1 -f image-variant=android -f push-image=true
```

Published in this organization as of 2026-09-08 — mutable tag and the
run-numbered tag resolve to the same digest, which the build now asserts:

| Tag | Digest (short) |
|---|---|
| `6000.0.26f1-android` | `sha256:bbf4a306…`¹ |
| `6000.0.26f1-webgl` | `sha256:b89248b5…` |
| `6000.0.26f1-linux` | `sha256:2561e050…` |
| `6000.3.9f1-android` | `sha256:c82e7267…` |
| `6000.3.9f1-webgl` | `sha256:411257ff…` |
| `6000.3.9f1-linux` | `sha256:86cabf50…` |

¹ Rebuilt to correct a mutable tag that had been left on an older image; confirm
the current digest before pinning:

```bash
docker buildx imagetools inspect ghcr.io/cuvara/unity-editor:6000.0.26f1-android \
  --format '{{.Manifest.Digest}}'
```

**The package is private.** A workflow in the same organization pulls it with
`GITHUB_TOKEN`; a local `docker pull` needs `docker login ghcr.io`, and anything
outside the org cannot pull it until the package visibility is changed (only
possible in the UI — the REST API has no endpoint for it).

Production releases should pin by digest, not by the mutable tag:
[IMAGE_LIFECYCLE.md](IMAGE_LIFECYCLE.md), [RELEASE_FLOW.md](RELEASE_FLOW.md).

---

## 8 — When it goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| `Project validation failed` | one of the three required paths is missing (§1) | check `ProjectVersion.txt`, `Packages/manifest.json`, `Assets/` |
| Activation fails, `0 entitlements` | credentials without the `.ulf` | set all three secrets (§3) |
| Activation fails, `TimeStamp validation failed` | `.ulf` without credentials | same |
| Green run, empty `unity-build-<Platform>` artifact | self-hosted lane with no `PlayerBuilder.Build` | §6.2 |
| `Unity.exe not found for version <v>` | Editor absent, not at the Hub path, or a different version than `ProjectVersion.txt` | install that exact version |
| Job stays *Queued*, runner Idle | label mismatch, or an org runner group that disallows public repos | §6.4, §6.3 |
| `image-namespace input is required` | Path B without a published image namespace | §7, or move to Path A |
| `Wrong Docker container mode … Server.Os=windows` | `BUILD_ENGINE=docker` on a Windows engine; `unityci/editor` images are Linux | switch Docker Desktop to Linux containers |
| iOS job refuses to run | iOS is macOS-only and dispatch-only | use a macOS runner |

More: [TROUBLESHOOTING.md](TROUBLESHOOTING.md),
[GITHUB_ACTIONS_BUILD_RUNBOOK.md](GITHUB_ACTIONS_BUILD_RUNBOOK.md).

---

## 9 — Where to read further

| Topic | Document |
|---|---|
| Path A in detail | [CONSUMER_SETUP.md](CONSUMER_SETUP.md) |
| Path B in detail, `PlayerBuilder`, iOS | [ADD_NEW_PROJECT.md](ADD_NEW_PROJECT.md) |
| Branch → flow rules, every variable | [BRANCH_FLOW_CONTRACT.md](BRANCH_FLOW_CONTRACT.md), [REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md) |
| Runner vs build engine | [RUNNER_AND_BUILD_ENGINE.md](RUNNER_AND_BUILD_ENGINE.md) |
| Own machine, per repo / per org | [SELF_HOSTED_WINDOWS_RUNNER.md](SELF_HOSTED_WINDOWS_RUNNER.md), [SELF_HOSTED_ORG_RUNNER.md](SELF_HOSTED_ORG_RUNNER.md) |
| Licensing in containers | [UNITY_PERSONAL_DOCKER_LICENSE.md](UNITY_PERSONAL_DOCKER_LICENSE.md) |
| Images: build, scan, pin, deprecate | [IMAGE_LIFECYCLE.md](IMAGE_LIFECYCLE.md) |
| Lane and entry-point rationale | [ARCHITECTURE.md](ARCHITECTURE.md) |
