# GitHub Environments & Deployments

Hygiene guide for GitHub Environments, deployment protection rules, and the
branch-based deployment flow in NDC-Unity-Template.

Related docs:
- [BRANCH\_FLOW\_CONTRACT.md](BRANCH_FLOW_CONTRACT.md) — branch → flow → environment mapping contract
- [GITHUB\_ACTIONS\_BUILD\_RUNBOOK.md](GITHUB_ACTIONS_BUILD_RUNBOOK.md) — operational runbook

---

## 1. Environments vs Repository Variables vs Secrets

These are three distinct GitHub concepts that are often conflated.

### GitHub Environments

A GitHub **Environment** (`Settings → Environments`) is a named deployment target
(`development`, `staging`, `production`). Environments provide:

- **Protection rules** — required human reviewers, wait timers, and
  deployment branch restrictions that gate which refs can deploy there.
- **Environment-scoped secrets** — secrets that are only available when a job
  explicitly targets that environment via the `environment:` key. They are
  **never** exposed to jobs without that key, and never to PR jobs that have
  an empty `environment:` value.
- **Deployment tracking** — each workflow run that targets an environment creates
  a GitHub Deployment record with a status (`in_progress`, `success`, `failure`,
  `inactive`) visible in the repo's Deployments panel.

When to use: production credentials (signing keys, distribution certificates,
Play Store/App Store tokens), environment-specific API keys that must be gated
by a human approval step.

### Repository Variables (`vars.*`)

**Repository Variables** (`Settings → Secrets and variables → Actions → Variables`)
are plaintext, non-secret configuration values readable by any workflow job
(no environment target needed). They appear as `${{ vars.NAME }}` in workflows
and as `VAR_NAME` env vars when mapped explicitly.

When to use: platform lists per branch (`DEVELOP_BUILD_PLATFORMS`), feature
flags (`DEVELOP_RUN_TESTS`), runner mode (`DEFAULT_RUNNER_MODE`). These are
non-sensitive and can be changed without a deploy key rotation.

### Secrets (`secrets.*`)

**Secrets** (`Settings → Secrets and variables → Actions → Secrets`) are encrypted
values. They can be scoped to the whole repository (all jobs) or to a specific
environment (environment-scoped, only available when `environment:` matches).

- **Repository secrets** — available to all workflow jobs (including PRs from
  forks, with `pull_request_target` caveats). Examples: `UNITY_LICENSE`,
  `UNITY_EMAIL`, `UNITY_PASSWORD`.
- **Environment secrets** — only available to jobs that declare
  `environment: <name>`. Examples: `ANDROID_KEYSTORE_BASE64` in production.

> **Rule:** Unity license secrets are repository-scoped (needed for all builds).
> Signing and distribution secrets must be environment-scoped to production so
> that only `push → release-*` runs (and approved manual dispatches) can access
> them.

---

## 2. Branch-Flow → GitHub Environment Mapping

The `resolve_build_flow.sh` script (toolkit: `scripts/common/resolve_build_flow.sh`)
emits a `gh-environment` output that is **distinct** from the Unity build
`environment` output. `gh-environment` is what the consumer workflow's
`final-report` job passes to its `environment:` key, creating a GitHub
Deployment record.

```
resolve_build_flow.sh
  ├── environment    → Unity build profile (development | staging | production)
  └── gh-environment → GitHub deployment target (development | staging | production | "")
```

### Mapping table

| Trigger | flow-type | `environment` (Unity) | `gh-environment` (GitHub deployment) |
|---|---|---|---|
| PR → `develop` | `pr-develop` | `development` | **`""`** (empty — no deployment) |
| PR → `staging` | `pr-staging` | `staging` | **`""`** (empty — no deployment) |
| PR → `release-*` | `pr-release` | `production` | **`""`** (empty — no deployment) |
| push → `develop` | `push-develop` | `development` | `development` |
| push → `staging` | `push-staging` | `staging` | `staging` |
| push → `release-*` | `push-release` | `production` | `production` |
| `workflow_dispatch` | `manual` | `IN_ENVIRONMENT` input | `IN_ENVIRONMENT` input |
| anything else | `none` | `development` | **`""`** (empty — no deployment) |

### How it flows through the consumer workflow

`final-report` is the only job that creates a deployment:

```yaml
# .github/workflows/unity-build.yml — final-report job
final-report:
  if: always()
  environment: ${{ needs.resolve-config.outputs.gh-environment }}
```

When `gh-environment` is empty (`""`), GitHub treats this as no environment —
no deployment record is created and no environment-scoped secrets or
protection rules are triggered.

When `gh-environment` is `production`, the `final-report` job must pass the
production environment's protection rules (required reviewers, branch policy)
before it can run.

### One deployment per push run

Each push to `develop`, `staging`, or `release-*` creates **exactly one**
GitHub Deployment (on `final-report`). The build jobs themselves do not create
deployments — they run without an `environment:` key.

---

## 3. Why PRs Must Never Deploy to Production

All PR flows (`pr-develop`, `pr-staging`, `pr-release`) emit an **empty**
`gh-environment`. This is intentional and enforced in `resolve_build_flow.sh`:

```bash
# scripts/common/resolve_build_flow.sh
case "${flow_type}" in
    pr-develop|pr-staging|pr-release|none) gh_environment="" ;;
    *)                                     gh_environment="${environment}" ;;
esac
```

**Why:** PRs can originate from fork branches or contributor branches. A PR
workflow job targeting `environment: production` would:

1. Expose **environment-scoped secrets** (signing keys, Play Store tokens) to
   untrusted code from the PR author.
2. Trigger the **required-reviewers gate** on every PR — creating approval
   fatigue and defeating the purpose of having a review step only on actual
   releases.
3. Create **spurious deployment records** in the production environment for
   every PR, polluting the Deployments history.

`PR → release-*` still uses `environment=production` for the Unity build profile
(so tests run against production config), but `gh-environment` is empty — no
secrets, no approvals, no deployment record.

**Additionally:** the production GitHub Environment is configured with a
**deployment branch policy** restricting deployments to `release-*` and `main`
branches only (configured via API — see [Section 5](#5-current-configured-state)).
This is a second enforcement layer: even if `gh-environment` were accidentally
set to `production` by a PR, the branch policy would block it.

---

## 4. Configuring Environment Protection Rules

### Navigate to environment settings

```
GitHub repo → Settings → Environments → [environment name]
```

Or directly:
```
https://github.com/Cuvara/NDCUnityTemplate/settings/environments
```

### Required reviewers (production — manual UI step)

> **Recommended:** Add at least one human reviewer to the `production` environment.
> This is a **manual UI step** — there is no way to set required reviewers via
> the standard REST API; it must be done in the browser.

1. Open `Settings → Environments → production`.
2. Under **Deployment protection rules**, enable **Required reviewers**.
3. Search for and add the reviewer (person or team).
4. Click **Save protection rules**.

Effect: any `final-report` job targeting `production` will pause and send an
approval request to the reviewers before the job can proceed. The workflow
run stays `waiting` until approved or rejected.

### Deployment branches (already configured — see Section 5)

The production environment already restricts deployments to `release-*` and `main`.
To view or change this:

1. Open `Settings → Environments → production`.
2. Under **Deployment branches and tags**, the current policy is shown.
3. To add a branch pattern, click **Add deployment branch or tag rule**.
4. To remove one, click the trash icon next to it.

Or via the API:
```bash
# List current deployment branch policies for production
gh api repos/Cuvara/NDCUnityTemplate/environments/production/deployment-branch-policies \
  --jq '.branch_policies[] | {id: .id, name: .name, type: .type}'
```

### Optional wait timer

A wait timer (0–43,200 minutes) delays a deployment by a fixed period after
all other protection rules pass. Useful to catch last-minute issues before
a release proceeds.

Set under `Settings → Environments → production → Wait timer`.

### Summary of recommended production configuration

| Rule | Recommended value | Current state |
|---|---|---|
| Required reviewers | ≥1 human approver | **0 (not configured)** — action required |
| Deployment branches | `release-*`, `main` | ✓ Configured |
| Wait timer | Optional (0 minutes = off) | Not configured (off) |

---

## 5. Current Configured State

The following environments were created and configured via the GitHub API.
All three currently have **0 required reviewers** — see recommendation above
for production.

### `development`

| Setting | Value |
|---|---|
| Deployment branch restriction | `develop` (exact match) |
| Required reviewers | 0 |
| Associated flow | `push → develop` → `push-develop` |

### `staging`

| Setting | Value |
|---|---|
| Deployment branch restriction | `staging` (exact match) |
| Required reviewers | 0 |
| Associated flow | `push → staging` → `push-staging` |

### `production`

| Setting | Value |
|---|---|
| Deployment branch restriction | `release-*`, `main` |
| Required reviewers | **0 — add a reviewer** |
| Associated flow | `push → release-*` → `push-release` |

Verify the current state:
```bash
# List environments
gh api repos/Cuvara/NDCUnityTemplate/environments \
  --jq '.environments[] | {name: .name, protection_rules: .protection_rules}'

# List branch policies for each environment
for env in development staging production; do
  echo "=== ${env} ==="
  gh api "repos/Cuvara/NDCUnityTemplate/environments/${env}/deployment-branch-policies" \
    --jq '.branch_policies[] | {name: .name, type: .type}'
done
```

---

## 6. Cleaning Up Stale Deployments

### Background

Stale deployments accumulate from removed or early-iteration workflows.
As of 2026-06-27, the repo has:

- **11 `development` deployments** — from the pre-branch-flow `unity-build.yml`
  iterations (workflow removed/replaced).
- **1 `production` deployment** with status `failure` — from an early test
  dispatch against the production environment.

These show in the Deployments panel and pollute the environment history. They
are safe to deactivate and delete.

### How GitHub deployment deletion works

GitHub requires a deployment to have status `inactive` before it can be deleted.
The deletion workflow is:

1. **Mark as inactive** — `POST /repos/{owner}/{repo}/deployments/{id}/statuses`
   with `state: inactive`.
2. **Delete** — `DELETE /repos/{owner}/{repo}/deployments/{id}`.

> ⚠️ **Verify before deleting.** List the deployments first, confirm the IDs,
> then deactivate and delete. Deletion is permanent.

### Step-by-step cleanup

```bash
REPO="Cuvara/NDCUnityTemplate"
ENVIRONMENT="development"   # or "production"

# 1. List all deployments for the environment — note the IDs
gh api "repos/${REPO}/deployments?environment=${ENVIRONMENT}&per_page=100" \
  --jq '.[] | {id: .id, sha: .sha, created_at: .created_at, ref: .ref}'

# 2. Check current status of each deployment
gh api "repos/${REPO}/deployments?environment=${ENVIRONMENT}&per_page=100" \
  --jq '.[] | {id: .id, ref: .ref, created_at: .created_at}' | \
  while read -r line; do
    id=$(echo "${line}" | jq -r '.id')
    gh api "repos/${REPO}/deployments/${id}/statuses?per_page=1" \
      --jq ".[0] | {deployment_id: ${id}, state: .state, created_at: .created_at}"
  done

# 3. Mark a deployment inactive (required before deletion)
DEPLOYMENT_ID=<id>
gh api "repos/${REPO}/deployments/${DEPLOYMENT_ID}/statuses" \
  --method POST \
  --field state=inactive

# 4. Delete the deployment
gh api "repos/${REPO}/deployments/${DEPLOYMENT_ID}" \
  --method DELETE

# 5. Confirm it is gone
gh api "repos/${REPO}/deployments?environment=${ENVIRONMENT}&per_page=100" \
  --jq 'length'
```

### Bulk deactivate-and-delete (use with caution)

This script deactivates and deletes **all** deployments for a given environment.
Run the list step first and confirm the IDs are the stale ones before executing.

```bash
REPO="Cuvara/NDCUnityTemplate"
ENVIRONMENT="development"   # change to "production" for the failed production deployment

# Print what will be deleted first — review before proceeding
echo "Deployments to delete in ${ENVIRONMENT}:"
gh api "repos/${REPO}/deployments?environment=${ENVIRONMENT}&per_page=100" \
  --jq '.[] | "  id=\(.id)  ref=\(.ref)  created=\(.created_at)"'

echo "Press Ctrl+C to abort, or wait 5 seconds to proceed..."
sleep 5

# Deactivate then delete each
gh api "repos/${REPO}/deployments?environment=${ENVIRONMENT}&per_page=100" \
  --jq '.[].id' | while read -r id; do
    echo "Deactivating deployment ${id}..."
    gh api "repos/${REPO}/deployments/${id}/statuses" \
      --method POST --field state=inactive --silent
    echo "Deleting deployment ${id}..."
    gh api "repos/${REPO}/deployments/${id}" --method DELETE --silent
    echo "  Done: ${id}"
done

echo "Remaining deployments in ${ENVIRONMENT}:"
gh api "repos/${REPO}/deployments?environment=${ENVIRONMENT}&per_page=100" --jq 'length'
```

> **Note:** The failed `production` deployment should be deleted individually
> (not in bulk) to avoid accidentally removing a legitimate future deployment.
> Mark it inactive first, confirm its ID, then delete.
