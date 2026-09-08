# Adding Your Own Machine as an Organization Runner

How to register a self-hosted runner at the **organization** level
(`https://github.com/organizations/<ORG>/settings/actions/runners`) and have this
toolkit route builds to it.

Organization runners differ from repository runners in three ways that matter here:
the registration token comes from the org, access is granted through a **runner
group**, and one runner can serve every repository in the org. The per-repository
flow is in [SELF_HOSTED_WINDOWS_RUNNER.md](SELF_HOSTED_WINDOWS_RUNNER.md); the
two-axis model behind `RUNNER_TYPE` / `BUILD_ENGINE` is in
[RUNNER_AND_BUILD_ENGINE.md](RUNNER_AND_BUILD_ENGINE.md).

---

## Read this first — public repositories

> **A self-hosted runner attached to a public repository lets anyone who opens a
> pull request execute code on your machine.** A fork PR runs the workflow from
> *its own* branch, so the attacker controls what the build step does. GitHub
> documents this and recommends against self-hosted runners on public repos.

Both repositories in this org are currently public:

```bash
gh api repos/<ORG>/unity-build-workflows -q .visibility   # public
gh api repos/<ORG>/IndieRPGMMOAdventure  -q .visibility   # public
```

The consumer caller also runs on `pull_request` for `develop`, `staging` and
`release-*`, so fork PRs would reach the runner by default. Do **all** of the
following before the runner is allowed near a public repo:

1. **Require approval for every outside contributor.**
   `Settings → Actions → General → Fork pull request workflows from outside
   collaborators` → *Require approval for all external contributors*.
2. **Restrict the runner group to named repositories** — never "All
   repositories" (§3). **On a free-plan organization this is not available**;
   read §3 before relying on it.
3. **Register the runner as `--ephemeral`** so a compromised job cannot persist
   into the next one (§2).
4. **Keep no credentials on the machine.** No `UNITY_PASSWORD`, no signing
   keystore, no cloud CLI logged in. The local lane needs none of them.
5. Run the runner service as a **dedicated low-privilege Windows user**, not
   your own account, and not an administrator.

If the project will stay public and you cannot accept the above, use the docker
lane on GitHub-hosted runners instead — that is the default and needs no runner
of your own.

---

## 1 — What the local lane requires on your machine

The self-hosted lane runs Unity directly; there is no container. Verified against
`reusable-build-platform.yml`:

| Requirement | Detail | Where enforced |
|---|---|---|
| Unity Editor installed at the Hub default path | `C:\Program Files\Unity\Hub\Editor\<UNITY_VERSION>\Editor\Unity.exe`, or the fallback `C:\Program Files\Unity <UNITY_VERSION>\Editor\Unity.exe` | `:794`, `:797` — anything else is a hard error at `:800` |
| The **exact** Editor version the project pins | `UNITY_VERSION` comes from the consumer's `ProjectSettings/ProjectVersion.txt`; a different installed version fails the path check | resolver → `inputs.unity-version` |
| A `PlayerBuilder.Build` method in the project | `-buildTarget` only switches the active target; an `-executeMethod` that calls `BuildPipeline.BuildPlayer` is what produces a build. When `build-method` is empty the lane substitutes `PlayerBuilder.Build`. Copy [`templates/PlayerBuilder.cs`](../templates/PlayerBuilder.cs) if the project has none | `:842-843` (Windows), `:903` (bash) |
| An `AddressableBuilder.Build` method, if you build Addressables | The Addressables-only path calls it directly | `:806` |
| Unity activated once through Unity Hub | The local lane never touches `UNITY_LICENSE` / `UNITY_EMAIL` / `UNITY_PASSWORD` | activation step is skipped for `build-engine=local` |
| Git and Git LFS on `PATH` | `actions/checkout` needs them | — |

> **This is the difference from the default lane.** On docker/game-ci,
> `build-method` defaults to `''` and game-ci supplies its own builder — no
> project-side method is needed. Moving to a self-hosted runner makes
> `PlayerBuilder.Build` **mandatory**. See
> [ARCHITECTURE.md](ARCHITECTURE.md#build-entry-points--two-lanes-that-do-not-agree).

**iOS cannot build on this lane** — the Windows path errors out for `platform=iOS`
(`:815`). iOS needs a macOS runner.

**Why choose it at all:** `Windows64` on the docker lane is Mono-only. IL2CPP
Windows builds require a local Windows Editor, i.e. this lane.

---

## 2 — Register the runner against the organization

Get an **organization** registration token. Verified against `Cuvara` on
2026-09-08: returns a 29-character token, `expires_at` one hour out. Needs the
`admin:org` scope — `gh auth refresh -h github.com -s admin:org`.

```bash
gh api -X POST /orgs/<ORG>/actions/runners/registration-token --jq .token
```

Treat it as a credential: it registers a machine into your organization. Do not
paste it into a shared terminal or commit it.

Download and configure. Note the URL is the **org**, with no repository path:

```powershell
# PowerShell, in a directory you created for the runner (e.g. C:\actions-runner)
mkdir C:\actions-runner; cd C:\actions-runner
$V = "2.328.0"   # check the version offered on the org runners page
Invoke-WebRequest -Uri "https://github.com/actions/runner/releases/download/v$V/actions-runner-win-x64-$V.zip" -OutFile runner.zip
Expand-Archive -Path runner.zip -DestinationPath . -Force

.\config.cmd --url https://github.com/<ORG> `
             --token <REGISTRATION_TOKEN> `
             --name unity-win-01 `
             --runnergroup unity-builders `
             --labels self-hosted,windows `
             --work _work `
             --ephemeral
```

**On the labels:** `self-hosted,windows` is what the resolver asks for — nothing
more is needed. Older docs in this repo told you to add a third `unity` label; no
workflow or script in this repository ever requests it (`grep '"unity"'` over
`.github/workflows/` and `scripts/` returns nothing). Extra labels are harmless,
missing ones are not: `runs-on` requires the runner to carry **every** requested
label.

**`--ephemeral` and services are mutually exclusive.** An ephemeral runner
deregisters after one job, so it cannot be `--runasservice`. Run it under a
scheduled task or a supervisor that re-registers with a fresh token, or drop
`--ephemeral` and use `.\svc.sh`-style service install only on a private repo:

```powershell
.\run.cmd            # foreground, one job, then exits (ephemeral)
```

To remove the runner later:

```bash
gh api -X POST /orgs/<ORG>/actions/runners/remove-token --jq .token
# then:  .\config.cmd remove --token <REMOVE_TOKEN>
```

Both token endpoints were exercised against `Cuvara` while writing this
document; the runner-group creation call was not, because the org is on the free
plan (§3).

---

## 3 — Grant the runner to specific repositories

Org runners are reachable only through a runner group. **Custom runner groups are
a GitHub Team / Enterprise feature.** Check the plan before planning around them:

```bash
gh api /orgs/<ORG> -q .plan.name                 # free | team | enterprise
gh api /orgs/<ORG>/actions/runner-groups \
  -q '.runner_groups[] | "\(.id) \(.name) visibility=\(.visibility) allows_public=\(.allows_public_repositories)"'
```

On a **free** organization the only group is `Default` (id `1`, `visibility=all`),
it cannot be narrowed to selected repositories, and a new group cannot be created.
Every repository in the org can therefore reach the runner. Measured on `Cuvara`
on 2026-09-08:

```
plan=free
1 Default visibility=all allows_public=false
```

### The blocker on a free plan with public repositories

`Default` ships with **`allows_public_repositories=false`**, and a runner in a
group that disallows public repositories **never receives jobs from a public
repository** — the job simply stays queued. Both repositories in this org are
public, so a runner registered today would sit idle no matter how correct the
labels are.

There are only three honest ways out, and the middle one is the recommended one:

| Option | Consequence |
|---|---|
| Flip the flag: `Settings → Actions → Runner groups → Default → Allow public repositories` | The runner becomes reachable by **every public repo in the org**, and on a free plan you cannot narrow it back to a subset. Any fork PR then runs its code on your machine. Do not do this on a machine you care about |
| **Make the repository private** | The runner works with `Default` as shipped, no flag change, and fork-PR exposure disappears. Private repos are unlimited on the free plan (`plan.private_repos` reported 10000) |
| Upgrade to GitHub Team | Custom runner groups become available, so you can scope a group to named repositories and keep the repo public |

On Team or Enterprise, create the scoped group with:

```bash
gh api -X POST /orgs/<ORG>/actions/runner-groups \
  -f name='unity-builders' \
  -f visibility='selected' \
  -F selected_repository_ids[]="$(gh api repos/<ORG>/<CONSUMER_REPO> -q .id)"
```

Verify the runner registered and is idle:

```bash
gh api /orgs/<ORG>/actions/runners \
  --jq '.runners[] | {name, status, labels: [.labels[].name]}'
```

---

## 4 — Point the toolkit at it

Three variables select the lane. Set them **once at the organization level** and
every consumer repo inherits them, or set them per repository to move one project
at a time.

```bash
ORG="<ORG>"

# Organization-wide (applies to every repo that can see them)
gh variable set RUNNER_TYPE    --org "$ORG" --body "self-hosted"
gh variable set BUILD_ENGINE   --org "$ORG" --body "local"
gh variable set RUNNER_LABELS  --org "$ORG" --body "self-hosted,windows"

# …or scoped to one repository instead
gh variable set RUNNER_TYPE    --repo "$ORG/<CONSUMER_REPO>" --body "self-hosted"
gh variable set BUILD_ENGINE   --repo "$ORG/<CONSUMER_REPO>" --body "local"
gh variable set RUNNER_LABELS  --repo "$ORG/<CONSUMER_REPO>" --body "self-hosted,windows"
```

`RUNNER_LABELS` must match the registered labels exactly after comma-split, trim
and dedup, or the job queues forever with *no runner matching labels found*. It
also has a trap worth knowing: when `RUNNER_TYPE=self-hosted` is set and
`RUNNER_LABELS` is left empty, the resolver falls back to the hardcoded
`self-hosted,windows` regardless of the runner's actual OS
(`scripts/common/resolve_build_flow.sh:588`). A Linux or macOS self-hosted runner
therefore **must** set `RUNNER_LABELS` explicitly.

Per-run override, without touching any variable:

```bash
gh workflow run unity-build.yml --repo "$ORG/<CONSUMER_REPO>" --ref develop \
  -f platform=Windows64 \
  -f runner-type=self-hosted \
  -f build-engine=local \
  -f runner-labels=self-hosted,windows
```

> The legacy single-axis variable `RUNNER_DEFAULT_MODE=self-hosted-windows` still
> works and maps to `RUNNER_TYPE=self-hosted` + `BUILD_ENGINE=local` with labels
> `self-hosted,windows` (`resolve_build_flow.sh:538-541`), but the resolver logs a
> deprecation note. Use the two-axis variables in a new setup.

### What actually moves to your machine

Only the Unity jobs. The orchestration jobs stay on GitHub-hosted runners
(`unity-pipeline.yml:146, 415, 463, 888, 1100`), so you still consume a small
number of hosted minutes per run:

| Job | Runs on |
|---|---|
| `resolve-config`, `validate-project`, `validate-license`, `final-report`, `notify-discord` | `ubuntu-latest` — always |
| `unity-tests` | your runner (`reusable-unity-tests.yml:151`, local Windows lane at `:239`) |
| `build-android`, `build-webgl`, `build-linux64`, `build-linuxserver`, `build-windows64`, `build-addressables` | your runner (`reusable-build-platform.yml:230`) |
| `build-ios` | never this lane — macOS only |

---

## 5 — First run and what to check

```bash
gh workflow run unity-build.yml --repo "<ORG>/<CONSUMER_REPO>" --ref develop \
  -f platform=Windows64 -f run-tests=false -f environment=development
```

| Symptom | Cause | Fix |
|---|---|---|
| Job stays *Queued*, runner shows Idle | **On a free plan: the repo is public and `Default` has `allows_public_repositories=false`** — the most likely cause, and no label change fixes it | See §3: make the repo private, or accept the flag's consequences |
| Job stays *Queued* | Requested labels are not all present on the runner, or the repo is not in the runner group | Compare `RUNNER_LABELS` with `gh api /orgs/<ORG>/actions/runners`; check the group's repository access |
| `Unity.exe not found for version <v>` | Editor not installed, or not at the Hub default path, or a different version than `ProjectVersion.txt` | Install that exact version via Unity Hub |
| Build "succeeds" with no player in `build/` | The project has no `PlayerBuilder.Build`; `-buildTarget` alone builds nothing | Add the method (§1) or pass `build-method` |
| `error CS0246` / missing packages on first run | Fresh `Library/` — the first build resolves packages and is slow | Let it finish; later runs reuse `Library/` |
| Runner disappears after one job | Expected with `--ephemeral` | Re-register, or drop `--ephemeral` on a private repo |

Read the build log on the machine at `_work\<repo>\<repo>\Editor.log`; the job
also uploads it as an artifact.
