# Add the Unity Build Pipeline to a New Project

Step-by-step guide to wiring up `unity-build-workflows` in a new consumer
repository. After following these steps your project will have:

- Automatic CI builds on push to `develop`, `staging`, and `release-*`
- Pre-merge validation (tests only) on pull requests to those branches
- Manual `workflow_dispatch` builds with full input control
- Per-platform named jobs in the GitHub Actions UI (independently retryable)
- Discord build notifications (optional)
- Proper GitHub Environment gating so production secrets never reach PR runs

> **New here?** [NEW\_PROJECT\_END\_TO\_END.md](NEW_PROJECT_END_TO_END.md) walks the
> whole thing — repo requirements, secrets, first build, then your own runner — and
> chooses between the two paths for you. This document is the detail for the path it
> recommends.
>
> **This is the default onboarding path** — it wires up `unity-pipeline.yml`
> (branch-based CI, per-platform jobs, Repository Variables) and needs no
> `BuildConfig/`. If you instead want one explicit build per caller job with
> `target-platform` / `test-level` / `cache-mode` inputs and your own
> `BuildConfig/*.json`, follow [ADD\_NEW\_PROJECT.md](ADD_NEW_PROJECT.md).

Related docs:
- [BRANCH\_FLOW\_CONTRACT.md](BRANCH_FLOW_CONTRACT.md) — branch → flow rules and Repository Variables reference
- [GITHUB\_ENVIRONMENTS.md](GITHUB_ENVIRONMENTS.md) — environment protection rules, deployment hygiene
- [UNITY\_PERSONAL\_DOCKER\_LICENSE.md](UNITY_PERSONAL_DOCKER_LICENSE.md) — Unity Personal/free license setup
- [EXPLICIT\_PLATFORM\_FLOW.md](EXPLICIT_PLATFORM_FLOW.md) — job graph, dispatch inputs, platform selection rules

---

## Prerequisites

- A Unity project with `ProjectSettings/ProjectVersion.txt` present.
- A GitHub repository (public or private) where you have admin access.
- `gh` CLI installed and authenticated (`gh auth login`).
- No local Unity installation required for CI — all builds run in Docker on
  GitHub-hosted runners.

---

## Step 1: (Optional) Add the Toolkit as a Git Submodule

The submodule is **not required** for CI — the caller workflow references the
toolkit remotely via `uses: Cuvara/unity-build-workflows/...`. Add it only
if you want local access to templates, documentation, and the
`AddressableBuilder.cs` helper.

```bash
# From your project root
git submodule add https://github.com/Cuvara/unity-build-workflows unity-build-workflows
git submodule update --init --recursive
```

If you skip the submodule, download individual template files directly from
GitHub when needed, or reference this documentation online.

---

## Step 2: Add the Caller Workflow

Copy the thin caller template into your project:

```bash
mkdir -p .github/workflows

# If you added the submodule:
cp unity-build-workflows/templates/consumer-unity-build.yml \
   .github/workflows/unity-build.yml

# Without the submodule — download directly:
curl -fsSL \
  https://raw.githubusercontent.com/Cuvara/unity-build-workflows/main/templates/consumer-unity-build.yml \
  -o .github/workflows/unity-build.yml
```

The file is ready to use as-is. It calls
`Cuvara/unity-build-workflows/.github/workflows/unity-pipeline.yml@v2`
with `secrets: inherit` — no per-secret wiring needed.

**Version pinning (recommended):**

| Ref | Use for | Behavior |
|---|---|---|
| `@v2` | **production (default)** | latest stable `v2.x`; receives backward-compatible fixes automatically |
| `@v2.2.4` | locked / reproducible | exact release, never moves |
| `@main` | development only | bleeding edge; may break |

The template ships pinned to `@v2`. For fully reproducible builds, pin to an
exact tag (e.g. `@v2.2.4`) and bump it deliberately. Available tags:
`gh release list -R Cuvara/unity-build-workflows` or
`git ls-remote --tags https://github.com/Cuvara/unity-build-workflows`.
Set `toolkit-ref:` in the caller to the SAME ref so the toolkit scripts are
checked out from the matching version.

Commit and push the workflow file:

```bash
git add .github/workflows/unity-build.yml
git commit -m "ci: add Unity build pipeline caller workflow"
git push
```

> **Nothing else to copy.** The pipeline's logic, reusable workflows, Docker
> images, and build scripts all live in the toolkit repo. Your project only
> owns this single caller file.

---

## Step 3: Set Required Secrets

Set these in your repository: `Settings → Secrets and variables → Actions → Secrets`.

### Unity license (Personal / free)

```bash
REPO="YOUR_ORG/YOUR_REPO"   # e.g. Cuvara/NDCUnityTemplate

# Unity account credentials (required)
gh secret set UNITY_EMAIL    --repo "${REPO}"   # paste email when prompted
gh secret set UNITY_PASSWORD --repo "${REPO}"   # paste password when prompted

# .ulf license file (optional but strongly recommended)
# Generate the .ulf with the generate-license workflow in the toolkit, or
# via Unity Hub. See UNITY_PERSONAL_DOCKER_LICENSE.md for instructions.
gh secret set UNITY_LICENSE  --repo "${REPO}" < /path/to/Unity_lic.ulf
```

**You need either the `.ulf` or the credentials, not necessarily both.**
`validate-license` rejects only the empty case
(`unity-pipeline.yml:487-490`): `Need: (UNITY_EMAIL+UNITY_PASSWORD) or
(UNITY_LICENSE)`. `Cuvara/IndieRPGMMOAdventure` builds green with
`UNITY_LICENSE` alone.

Set all three together when your `.ulf` cannot activate offline — a Unity
Personal licence bound to another machine id fails with `TimeStamp validation
failed`, and credentials alone fail with `0 entitlements`. That pairing is the
`personal-combined` strategy, and it is what the toolkit's own container
entrypoint requires on the `unity-build.yml` path.

See [UNITY\_PERSONAL\_DOCKER\_LICENSE.md](UNITY_PERSONAL_DOCKER_LICENSE.md) for
`.ulf` generation, troubleshooting, and the full explanation.

### Discord notifications (optional)

```bash
gh secret set DISCORD_WEBHOOK_URL --repo "${REPO}"
# Paste the Discord channel webhook URL when prompted.
# Omit this step entirely to disable Discord notifications.
```

### Android release signing (optional — production only)

Scope these to the `production` GitHub Environment (see Step 6):

```bash
# Set as environment-scoped secrets, not repository secrets
gh secret set ANDROID_KEYSTORE_BASE64 --repo "${REPO}" --env production < keystore.jks.b64
gh secret set ANDROID_KEYSTORE_PASS   --repo "${REPO}" --env production
gh secret set ANDROID_KEY_ALIAS       --repo "${REPO}" --env production
gh secret set ANDROID_KEY_PASS        --repo "${REPO}" --env production
```

---

### Private git submodules (optional)

`actions/checkout` fetches submodules by rewriting every URL in `.gitmodules` to HTTPS and
injecting `GITHUB_TOKEN`. That token is scoped to the repository being built, so a submodule
that lives in **another organization and is private** cannot be read, and git reports it the
same way it reports a typo:

```
fatal: repository 'https://github.com/<other-org>/<repo>.git/' not found
fatal: clone of 'git@github.com:<other-org>/<repo>.git' into submodule path '...' failed
```

Set `submodule-auth: ssh` on the pipeline to fetch submodules in a separate step over SSH
instead, leaving the URLs in `.gitmodules` exactly as written:

```yaml
    with:
      submodule-auth: ssh
```

Where the key comes from:

| Runner | What to provide |
|---|---|
| Self-hosted | Nothing. The runner's own SSH credentials are used — if `git ls-remote git@github.com:<org>/<repo>.git` works for the account the runner service runs as, the build works. |
| GitHub-hosted | A `SUBMODULE_SSH_KEY` secret holding a private key that can read every private submodule. |

A **deploy key** authenticates one repository only, so it covers a single private submodule.
For several, use one key belonging to a machine account that has read access to all of them.

The public submodules of a public parent need none of this — leave `submodule-auth` at its
default `token`.

## Step 4: Set Optional Repository Variables

Repository Variables control per-branch build behaviour without touching the
workflow file. All are optional — hardcoded defaults apply when unset.

```bash
REPO="YOUR_ORG/YOUR_REPO"

# Platform lists per branch (comma-separated, no spaces)
# Defaults, from scripts/common/resolve_build_flow.sh:
#   develop = Android,WebGL
#   staging = Android,WebGL,Linux64,LinuxServer,Windows64
#   release = Android,WebGL,Linux64,LinuxServer,Windows64
gh variable set BUILD_DEVELOP_PLATFORMS  --repo "${REPO}" --body "Android,WebGL"
gh variable set BUILD_STAGING_PLATFORMS  --repo "${REPO}" --body "Android,WebGL,Linux64,LinuxServer,Windows64"
gh variable set BUILD_RELEASE_PLATFORMS  --repo "${REPO}" --body "Android,WebGL,Linux64,LinuxServer,Windows64"

# Test toggles per branch (default: true for all)
gh variable set TEST_DEVELOP_ENABLED  --repo "${REPO}" --body "true"
gh variable set TEST_STAGING_ENABLED  --repo "${REPO}" --body "true"
gh variable set TEST_RELEASE_ENABLED  --repo "${REPO}" --body "true"

# Addressables toggles per branch (default: false for develop/staging, true for release)
gh variable set ADDRESSABLES_DEVELOP_ENABLED  --repo "${REPO}" --body "false"
gh variable set ADDRESSABLES_STAGING_ENABLED  --repo "${REPO}" --body "false"
gh variable set ADDRESSABLES_RELEASE_ENABLED  --repo "${REPO}" --body "true"

# Default runner mode (default: docker)
gh variable set RUNNER_DEFAULT_MODE  --repo "${REPO}" --body "docker"

# Clean Library cache before building (default: false; the caller's clean-build
# input defaults to `auto`, which defers to this variable)
gh variable set BUILD_CLEAN  --repo "${REPO}" --body "false"

# Discord thread ID (optional — pin notifications to a specific forum thread)
gh variable set DISCORD_THREAD_ID  --repo "${REPO}" --body "1234567890123456789"
```

> **These are the current, grouped variable names** (`BUILD_*`, `TEST_*`,
> `ADDRESSABLES_*`, `RUNNER_*`). The older ungrouped names —
> `DEVELOP_BUILD_PLATFORMS`, `DEVELOP_RUN_TESTS`, `DEFAULT_RUNNER_MODE`, … — are
> deprecated but still honoured: the resolver reads a legacy name only when the new
> one is unset, and logs a note naming the replacement. Use the new names in a new
> repository.

For the full variable reference, the legacy → new migration table, and validation
rules, see [BRANCH\_FLOW\_CONTRACT.md](BRANCH_FLOW_CONTRACT.md) and
[REPOSITORY\_VARIABLES.md](REPOSITORY_VARIABLES.md).

---

## Step 5: (If Using Addressables) Add the AddressableBuilder Script

If your project uses Unity Addressables and you want the `build-addressables`
pipeline step to work, you need a project-side Editor entry point that the
pipeline calls via `-executeMethod AddressableBuilder.Build`.

```bash
# Create the Editor scripts directory (adjust path as needed)
mkdir -p Assets/BuildScripts/Editor

# Copy the template
# From submodule:
cp unity-build-workflows/templates/AddressableBuilder.cs \
   Assets/BuildScripts/Editor/AddressableBuilder.cs

# Without submodule:
curl -fsSL \
  https://raw.githubusercontent.com/Cuvara/unity-build-workflows/main/templates/AddressableBuilder.cs \
  -o Assets/BuildScripts/Editor/AddressableBuilder.cs
```

Create an Editor assembly definition file next to it:

```json
// Assets/BuildScripts/Editor/BuildScripts.Editor.asmdef
{
    "name": "BuildScripts.Editor",
    "references": [
        "Unity.Addressables.Editor"
    ],
    "includePlatforms": ["Editor"],
    "excludePlatforms": [],
    "autoReferenced": false
}
```

Commit both files:

```bash
git add Assets/BuildScripts/Editor/
git commit -m "ci: add AddressableBuilder Editor script for pipeline"
git push
```

If you do **not** use Addressables, skip this step entirely and keep
`build-addressables=false` (the default).

> **Moving builds to your own runner later?** The self-hosted lanes also need a
> `PlayerBuilder.Build` method, which the default docker lane does not.
> [`templates/PlayerBuilder.cs`](../templates/PlayerBuilder.cs) is a working
> implementation — drop it in the same Editor assembly. See
> [SELF\_HOSTED\_ORG\_RUNNER.md](SELF_HOSTED_ORG_RUNNER.md).

---

## Step 6: Configure GitHub Environments

The pipeline creates GitHub Deployment records in three named environments:
`development`, `staging`, and `production`. These must be configured before
your first push to `release-*`.

### Create the environments (one-time setup)

```bash
REPO="YOUR_ORG/YOUR_REPO"

# Create environments with deployment branch policies
# development → only deploy from develop
gh api "repos/${REPO}/environments/development" --method PUT \
  --field deployment_branch_policy='{"protected_branches":false,"custom_branch_policies":true}'
gh api "repos/${REPO}/environments/development/deployment-branch-policies" --method POST \
  --field name=develop --field type=branch

# staging → only deploy from staging
gh api "repos/${REPO}/environments/staging" --method PUT \
  --field deployment_branch_policy='{"protected_branches":false,"custom_branch_policies":true}'
gh api "repos/${REPO}/environments/staging/deployment-branch-policies" --method POST \
  --field name=staging --field type=branch

# production → only deploy from release-* and main
gh api "repos/${REPO}/environments/production" --method PUT \
  --field deployment_branch_policy='{"protected_branches":false,"custom_branch_policies":true}'
gh api "repos/${REPO}/environments/production/deployment-branch-policies" --method POST \
  --field name='release-*' --field type=branch
gh api "repos/${REPO}/environments/production/deployment-branch-policies" --method POST \
  --field name=main --field type=branch
```

### Add a required reviewer to production (manual UI step)

> ⚠️ **Action required:** Required reviewers cannot be set via the REST API.
> This must be done in the browser.

1. Go to `Settings → Environments → production`.
2. Under **Deployment protection rules**, enable **Required reviewers**.
3. Add at least one human approver (yourself or a release manager).
4. Click **Save protection rules**.

This gates every `push → release-*` build: the `final-report` job will pause
and send an approval request before completing. PR runs are never affected
(PRs do not target any GitHub Environment).

For the full environments guide, protection rule explanation, and stale
deployment cleanup, see [GITHUB\_ENVIRONMENTS.md](GITHUB_ENVIRONMENTS.md).

---

## Step 7: Trigger a Test Build

Trigger a manual build to verify everything is wired up:

```bash
REPO="YOUR_ORG/YOUR_REPO"

# Single platform, tests enabled
gh workflow run unity-build.yml \
  --repo "${REPO}" \
  --ref develop \
  -f platform=Android \
  -f run-tests=true \
  -f test-mode=EditMode \
  -f environment=development

# Watch progress
gh run list --repo "${REPO}" --workflow unity-build.yml --limit 5
gh run watch --repo "${REPO}" $(gh run list --repo "${REPO}" --workflow unity-build.yml --limit 1 --json databaseId --jq '.[0].databaseId')
```

Check the GitHub Actions UI: you should see named jobs (`resolve-config`,
`validate-project`, `Unity Tests (EditMode)`, `Build Android`, `final-report`)
as separate, independently-coloured nodes.

Download the build artifact:

```bash
RUN_ID=$(gh run list --repo "${REPO}" --workflow unity-build.yml --limit 1 --json databaseId --jq '.[0].databaseId')
gh run download "${RUN_ID}" --repo "${REPO}" --name unity-build-Android
```

---

## What You Get

After setup, the pipeline provides:

| Feature | How it works |
|---|---|
| **Branch-based CI** | Push to `develop` → builds Android+WebGL; push to `staging` → adds Linux64+LinuxServer+Windows64; push to `release-*` → full build (same set) + Addressables + Android release signing. Windows64 uses the Mono scripting backend in the docker lane — use `runner-mode=self-hosted-windows` for IL2CPP ([EXPLICIT\_PLATFORM\_FLOW.md §5](EXPLICIT_PLATFORM_FLOW.md#5-platform-selection-rules)) |
| **PR validation** | Tests only on PRs to `develop`/`staging`/`release-*`; no binary builds; no environment secrets exposed |
| **Manual dispatch** | 9 inputs for full control (platform, tests, addressables, environment, runner mode, etc.) |
| **Per-platform UI jobs** | Each platform is a separate, independently-retryable job node in GitHub Actions |
| **Discord notifications** | Build-completion embeds with status, platform, and artifact links (when `DISCORD_WEBHOOK_URL` is set) |
| **GitHub Environments** | One deployment record per push run; production gated by branch policy + optional human approval |
| **Addressables support** | `build-addressables` step runs before platform builds; pre-built catalog is available to all builds |

### Further reading

| Document | Description |
|---|---|
| [EXPLICIT\_PLATFORM\_FLOW.md](EXPLICIT_PLATFORM_FLOW.md) | Job graph, all dispatch inputs, platform selection rules, iOS requirements |
| [BRANCH\_FLOW\_CONTRACT.md](BRANCH_FLOW_CONTRACT.md) | Branch → flow mapping, Repository Variable reference, flow-type table |
| [GITHUB\_ENVIRONMENTS.md](GITHUB_ENVIRONMENTS.md) | Environment protection rules, deployment hygiene, stale deployment cleanup |
| [UNITY\_PERSONAL\_DOCKER\_LICENSE.md](UNITY_PERSONAL_DOCKER_LICENSE.md) | Unity Personal/free license — `.ulf` generation, `personal-combined` strategy, troubleshooting |
| [GITHUB\_ACTIONS\_BUILD\_RUNBOOK.md](GITHUB_ACTIONS_BUILD_RUNBOOK.md) | Operational runbook — triggering builds, reading logs, downloading artifacts, common errors |
| [SELF\_HOSTED\_RUNNER.md](SELF_HOSTED_RUNNER.md) | Self-hosted Windows / macOS runner setup |
