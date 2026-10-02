# Migrating to Environment-Scoped Secrets

From v6.7.0 the platform build job declares a GitHub Environment —
`development`, `staging` or `production`, matching the build environment — and
reads its **signing secrets from that environment**. This guide moves an
existing project from repository-level (often renamed) secrets to one set of
secrets per environment, under the toolkit's own names.

Related: [GITHUB_ENVIRONMENTS.md](GITHUB_ENVIRONMENTS.md#environment-scoped-build-secrets),
[REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md), [IOS_SIGNING.md](IOS_SIGNING.md),
[ANDROID.md](ANDROID.md).

---

## 1. What changes

| | Before (≤ v6.6) | From v6.7 |
|---|---|---|
| Where signing secrets live | Repository secrets, shared by every environment | One set per GitHub Environment |
| Secret names | Anything; the caller workflow renamed them (`IOS_DISTRIBUTION_CERTIFICATE_BASE64: ${{ secrets.P12_BASE64 }}`) | The toolkit's names, in each environment |
| Build job `environment:` | none | `development` \| `staging` \| `production` (push and manual dispatch); none for PRs |
| Deployments | One per push run (`final-report`) | Also one per platform build job |

How a secret is resolved inside the build job:

1. The **environment secret** with that exact name, if the environment has one.
2. Otherwise the secret the **caller passed** (a repository secret).

So nothing breaks on upgrade: a project that has not created environment
secrets keeps building with its repository secrets. The migration is complete
when every environment has its own copy and the repository copies are deleted.

> Why the job has to declare the environment: a reusable workflow cannot receive
> environment secrets from its caller (`workflow_call` has no `environment`
> key). A job-level `environment:` inside the reusable workflow is the only way
> in, and there the environment secret wins over the caller's.

---

## 2. Which secrets go where

**Per environment** (`development`, `staging`, `production`) — signing keys:

| Secret | Platform | Notes |
|---|---|---|
| `ANDROID_KEYSTORE_PASS` | Android | Keystore password. Native lanes, project with Player Settings > Custom Keystore |
| `ANDROID_KEY_PASS` | Android | Alias password. **Do not create it** when it equals the keystore password — unset, it defaults to `ANDROID_KEYSTORE_PASS` |
| `IOS_DISTRIBUTION_CERTIFICATE_BASE64` | iOS | Base64 `.p12` |
| `IOS_DISTRIBUTION_CERTIFICATE_PASSWORD` | iOS | `.p12` export password |
| `IOS_PROVISIONING_PROFILE_BASE64` | iOS | Base64 `.mobileprovision` — typically Development/Ad Hoc for `development`, App Store for `production` |

**Repository level** — the same for every environment:

| Secret | Why it stays at repository level |
|---|---|
| `UNITY_LICENSE`, `UNITY_EMAIL`, `UNITY_PASSWORD` | Read by jobs that declare no environment (license validation, tests, Addressables) |
| `SUBMODULE_SSH_KEY` | Read by checkout in every job |
| `DISCORD_WEBHOOK_URL`, `R2_*`, `RUNNER_STATUS_TOKEN` | Not environment-specific |
| `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON`, `APP_STORE_CONNECT_*` | Read by the Release / Android and Release / iOS pipelines, whose jobs use their own environments (`internal-testing`, `external-testing`, `production`) and an artifact-verification job with none |

Repository **variables** (`vars.*`) stay at repository level: the resolver job
that reads them declares no environment.

---

## 3. Migration steps

Replace `OWNER/REPO` below. Run from a shell with `gh` authenticated as a
repository admin.

### Step 1 — Upgrade the toolkit ref

Callers pinned to `@v6` / `toolkit-ref: 'v6'` pick up v6.7.0 automatically.
Callers pinned to an exact version move to `v6.7.0` or later.

### Step 2 — Create the environments

GitHub creates an environment the first time a job references it, but create
them explicitly so the secrets can be added before the first build:

```bash
REPO=OWNER/REPO
for env in development staging production; do
  gh api -X PUT "repos/${REPO}/environments/${env}" >/dev/null && echo "created ${env}"
done
```

### Step 3 — Add the signing secrets to each environment

```bash
for env in development staging production; do
  gh secret set ANDROID_KEYSTORE_PASS                 -R "$REPO" -e "$env"
  gh secret set IOS_DISTRIBUTION_CERTIFICATE_BASE64   -R "$REPO" -e "$env" < cert.p12.b64
  gh secret set IOS_DISTRIBUTION_CERTIFICATE_PASSWORD -R "$REPO" -e "$env"
  gh secret set IOS_PROVISIONING_PROFILE_BASE64       -R "$REPO" -e "$env" < "profile-${env}.mobileprovision.b64"
done
```

`gh secret set` without `--body` prompts for the value, keeping it out of shell
history. Secrets cannot be read back — copy them from their source (keychain,
password manager), not from the old repository secrets.

### Step 4 — Remove the renames from the caller workflow

A caller that renamed secrets passes the **repository** value under the
toolkit's name. Keep the line only while the repository copy still exists as a
fallback; once every environment has the secret, drop it:

```yaml
# Before
secrets:
  IOS_DISTRIBUTION_CERTIFICATE_BASE64: ${{ secrets.P12_BASE64 }}
  IOS_DISTRIBUTION_CERTIFICATE_PASSWORD: ${{ secrets.P12_PASSWORD }}
  IOS_PROVISIONING_PROFILE_BASE64: ${{ secrets.MOBILEPROVISION_BASE64 }}
  ANDROID_KEYSTORE_PASS: ${{ secrets.ANDROID_KEYSTORE_PASS }}

# After — environment secrets reach the build job directly
secrets:
  SUBMODULE_SSH_KEY: ${{ secrets.SSH_PRIVATE_KEY }}   # still repository-level
  UNITY_LICENSE: ${{ secrets.UNITY_LICENSE }}
  UNITY_EMAIL: ${{ secrets.UNITY_EMAIL }}
  UNITY_PASSWORD: ${{ secrets.UNITY_PASSWORD }}
```

A caller using `secrets: inherit` needs no change.

### Step 5 — Check environment protection rules

The build job now has to pass the environment's protection rules before it
starts:

- **Required reviewers** on `production` → every release build waits for
  approval. Usually what you want; remove the rule if not.
- **Deployment branch policy** → a dispatch from a branch the policy excludes is
  rejected. For example, a `production` policy of `release-*` blocks a
  `Build / Release` dispatched from `develop`. Add the branches you dispatch
  release builds from.

```bash
gh api "repos/${REPO}/environments" \
  --jq '.environments[] | {name, rules: [.protection_rules[]?.type], branches: .deployment_branch_policy}'
```

### Step 6 — Verify, then delete the repository copies

1. Dispatch a build per environment (`Build / Development` with `development`
   and `staging`, `Build / Release`).
2. In the run, the platform build job shows the environment name next to its
   title, and the Resolve Config summary prints
   `secrets-environment: <env>`.
3. When every environment builds and signs, delete the repository copies so
   nothing can fall back to them:

```bash
for s in ANDROID_KEYSTORE_PASS ANDROID_KEY_PASS \
         IOS_DISTRIBUTION_CERTIFICATE_BASE64 IOS_DISTRIBUTION_CERTIFICATE_PASSWORD \
         IOS_PROVISIONING_PROFILE_BASE64; do
  gh secret delete "$s" -R "$REPO" 2>/dev/null && echo "deleted $s"
done
# plus any old aliases, e.g. P12_BASE64, P12_PASSWORD, MOBILEPROVISION_BASE64
```

---

## 4. Opting out

Set the repository variable `BUILD_ENVIRONMENT_SECRETS=false`. The build job
then declares no environment, creates no deployment, ignores environment
protection rules and reads repository secrets only — the ≤ v6.6 behaviour.

---

## 5. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Build job waits on "Waiting for review" | Environment has required reviewers | Approve, or remove the rule (Step 5) |
| Build job fails immediately: branch not allowed to deploy | Deployment branch policy excludes the ref | Add the branch to the policy (Step 5) |
| Signing uses an old key | A repository copy is still the fallback, or the environment secret name differs from the toolkit's | Create the secret in the environment under the exact toolkit name; delete the repository copy |
| `Keystore was tampered with, or password was incorrect` | `ANDROID_KEY_PASS` set to a wrong value | Delete it when the alias password equals the keystore password |
| PR build cannot sign | Expected: PR flows never declare an environment | Sign on push or dispatch builds |
