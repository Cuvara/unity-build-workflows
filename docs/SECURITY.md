# Security

This document describes the security model for the Docker-mandatory Unity CI/CD platform.

---

## Docker Trust Boundary

The CI runner trusts Docker Engine. The Docker container trusts the Unity image. The image is built, scanned, and published through a controlled pipeline.

```
Untrusted: Game project code, third-party Unity packages
Trusted:   Docker Engine, published Unity images, entrypoint scripts
```

### Image Trust

- Images are built from `build-unity-image.yml` workflow only
- Images are scanned for vulnerabilities before publication
- Images are referenced by digest in production workflows
- Image manifests record the source commit and build timestamp
- SBOM is generated for each published image

### Registry Access

- Images are published to `ghcr.io/<IMAGE_NAMESPACE>/unity-builder`
- Only the image build workflow has push access
- Game build workflows have pull access only
- Arbitrary caller-provided images are rejected in release mode

---

## Runtime Secret Injection

Secrets are injected into containers at runtime only. They are never baked into image layers.

### Injection Methods

| Secret | Method | Container Path |
|---|---|---|
| UNITY_LICENSE | Environment variable | Written to `/tmp/unity-license.ulf` at runtime |
| UNITY_EMAIL | Environment variable | Used for activation, then cleared |
| UNITY_PASSWORD | Environment variable | Used for activation, then cleared |
| ANDROID_KEYSTORE | Bind mount (temp file) | Read-only mount, deleted after signing |

### Security Rules

1. **No secrets in image layers** — `docker history` must not reveal any secret material
2. **No secrets in command-line arguments** — Secrets pass via environment variables or file mounts, never as CLI args (visible in `ps`)
3. **Restrictive file permissions** — Temporary secret files use mode 600
4. **Cleanup on all exit paths** — Trap handlers remove secret files even on failure
5. **No secret upload** — Artifact upload steps exclude secret file patterns
6. **Masked in logs** — GitHub Actions masks secret values in output

### Verification

The test suite includes `test_secret_redaction.py` which verifies:
- Docker commands do not contain secret values
- Image history does not contain secrets
- Artifact directories do not contain license files
- Known secret patterns are redacted from log output

---

## Container Security

### Runtime Restrictions

Containers run with:
- `--cap-drop=ALL` — Drop all Linux capabilities
- `--security-opt=no-new-privileges` — Prevent privilege escalation
- `--init` — PID 1 signal handling
- `--user "$(id -u):$(id -g)"` — Non-root execution
- No `--privileged` flag
- No Docker socket mount (`/var/run/docker.sock`)
- No host networking (unless justified)

### No Docker-in-Docker

Unity build containers do not run Docker inside Docker. The CI runner manages Docker; the container runs Unity only.

### Resource Limits

Workflows can set CPU and memory limits to prevent runaway builds:
```
--container-cpus 4
--container-memory 8g
```

### Timeout

Workflow-level timeout prevents zombie containers:
```yaml
timeout-minutes: 60
```

---

## Image Digest Enforcement

### Development Builds

Development builds may use human-readable tags:
```
ghcr.io/<IMAGE_NAMESPACE>/unity-builder:6000.0.26f1-android-v2.0.0
```

### Production/Release Builds

Production builds must use digest-pinned references:
```
ghcr.io/<IMAGE_NAMESPACE>/unity-builder@sha256:abc123...
```

The `resolve-unity-image` action enforces this when `release-mode: true`.

---

## Fork Pull Request Safety

GitHub Actions does not provide repository secrets to workflows triggered by pull requests from forks. This is a GitHub platform-level protection.

**What fork PR workflows can access:**
- Public repository contents
- `GITHUB_TOKEN` with read-only permissions on public repositories
- No repository secrets
- No environment secrets

**Recommended policy:**
- Run validation and schema checks on fork PRs (no secrets required)
- Skip Unity container builds on fork PRs
- Full builds run only on pushes to protected branches

---

## Secret Handling

### Where Secrets Live

Secrets are stored in GitHub's encrypted secret store (at repository or environment scope). They are never:
- Written to disk outside of a temporary file used immediately and deleted
- Printed in logs (GitHub masks known secret values; scripts additionally use `set +x` around secret expansion)
- Passed as command-line arguments (use environment variables instead)
- Baked into Docker image layers

### Secret Injection Pattern

All sensitive values are injected via environment variables, not shell arguments:

```bash
# Good — value not visible in process list
export UNITY_LICENSE="${{ secrets.UNITY_LICENSE }}"
docker run -e UNITY_LICENSE ...

# Bad — value visible in process list
docker run --env UNITY_LICENSE="<actual-content>" ...
```

---

### Secrets in log artifacts

GitHub masks secret values in the job console, **not** in files. Unity writes
the whole process environment into `Editor.log` when a Gradle build fails, so
any secret exported to a build step (e.g. `ANDROID_KEYSTORE_PASS` on native
lanes) would reach the uploaded `-logs` artifact. The build job therefore runs
`scripts/common/redact_log_secrets.py` (`if: always()`) before the log summary
and the log upload: every secret value the job can see is replaced with `***`
in all log files. A new secret exported to a build step must be added to that
step's `--env` list.

## Temporary Credential Cleanup

All temporary credential files are removed after container execution:

1. Unity license file (`/tmp/unity-license.ulf`)
2. Android keystore (temporary mount)
3. Any authentication tokens

Cleanup runs via bash `trap` on EXIT, INT, and TERM signals. Cleanup failures are logged but do not mask build failures.

---

## Environment Protection Rules

Production secrets are scoped to the `production` GitHub Environment, which enforces:

1. **Required reviewers** — at least one human must approve the deployment
2. **Branch/tag restriction** — only tags matching `v*` from `main` can deploy to production
3. **No fork access** — forks cannot trigger environment deployments

---

## `GITHUB_TOKEN` Permissions

Workflows use the minimum required `GITHUB_TOKEN` permissions:

```yaml
permissions:
  contents: read
  packages: read    # for image pull
  checks: write     # for test result annotations
```

Image build workflows additionally require `packages: write` for image push.

---

## Dependency Pinning

All actions in the workflows are pinned to their full commit SHA (not a floating tag), to prevent supply chain attacks via compromised action tags:

```yaml
# Pinned to SHA — safe
- uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2

# Not pinned — vulnerable to tag hijacking
- uses: actions/checkout@v4
```

---

## Artifact Integrity

Each build produces a `build-metadata.json` alongside the artifact containing:
- SHA-256 hash of the build output
- Build configuration hash
- Image reference and digest used
- Build timestamp
- GitHub run ID and SHA

---

## iOS-Specific Secret Handling

iOS builds introduce additional secrets that require careful management.

### iOS Secrets Inventory

| Secret | Type | Scope | Notes |
|---|---|---|---|
| `IOS_DISTRIBUTION_CERTIFICATE_BASE64` | P12 certificate | Repository or `production` env | Base64-encoded, no line breaks |
| `IOS_DISTRIBUTION_CERTIFICATE_PASSWORD` | P12 export password | Repository or `production` env | Never passed as CLI arg |
| `IOS_PROVISIONING_PROFILE_BASE64` | `.mobileprovision` | Repository | Base64-encoded |
| `APP_STORE_CONNECT_KEY_ID` | ASC API key ID | `production` env | Safe to treat as non-sensitive |
| `APP_STORE_CONNECT_ISSUER_ID` | UUID | `production` env | Safe to treat as non-sensitive |
| `APP_STORE_CONNECT_PRIVATE_KEY` | `.p8` file contents | `production` env | **High sensitivity** — rotate on compromise |

### iOS Temporary Credential Lifecycle

```
CI Start
  │
  ├── setup_signing.sh
  │     ├── Creates temp keychain with random password
  │     ├── Imports cert into temp keychain (cert P12 decoded from env, deleted immediately after import)
  │     ├── Installs provisioning profile to ~/Library/MobileDevice/Provisioning Profiles/
  │     └── Writes ASC key to $RUNNER_TEMP/asc_key.p8 (mode 600)
  │
  ├── [Build, archive, export, upload steps]
  │
  └── cleanup_ios.sh (trap on EXIT — always runs)
        ├── security delete-keychain "$TEMP_KEYCHAIN_PATH"
        ├── rm -f "$PROVISIONING_PROFILE_PATH"
        └── rm -f "$ASC_KEY_PATH"
```

### No Secrets in Xcode Build Logs

`xcodebuild` can emit verbose logs. The archive script uses:
```bash
CODE_SIGN_IDENTITY="${IOS_CODE_SIGN_IDENTITY}"   # env var, not literal
```
Never inline secret values in xcodebuild `-xcconfig` flags.

### Scoping ASC Secrets to production Environment

`APP_STORE_CONNECT_*` secrets must be scoped to the `production` GitHub Environment, not the repository. This prevents:
- Staging builds from accidentally uploading to TestFlight
- Fork PRs from accessing production credentials (GitHub does not provide environment secrets to fork PRs)

### SHA-Pinned Actions

All GitHub Actions in iOS workflows are pinned to their full commit SHA:
```yaml
- uses: maxim-lobanov/setup-xcode@60606e260d2fc5762a71e64e74b2174e8ea3c8bd  # v1.6.0
```
This prevents supply chain attacks via compromised action tags.

---

## Discord Webhook Secret Handling

`DISCORD_WEBHOOK_URL` is an optional secret that controls build-completion Discord notifications.

### Why it is treated as a secret

Discord webhook URLs grant anyone who holds them the ability to post messages to a channel. Leaking the URL is low-severity (no write access to code or infrastructure) but still enables spam and channel hijacking. It is stored as a GitHub secret as a matter of good practice.

### Protection measures in the `discord-notify` action

1. `set +x` — disables shell trace before the URL is referenced; the curl command (which contains the URL) is never echoed to the log.
2. `::add-mask::${DISCORD_WEBHOOK_URL}` — registers the URL value with GitHub's log masker. If it appears anywhere in subsequent output (including third-party tool output) it is replaced with `***`.
3. The URL is passed via environment variable at the **job** level, not as an action `input`. Action inputs can be surfaced in the step summary and API; environment variables cannot.
4. The URL never appears in the JSON payload sent to Discord. Payload fields contain version strings, commit SHAs, and actor names only.
5. Curl errors are logged as `::warning::` without printing the URL or response body.

### Rotation

Rotate `DISCORD_WEBHOOK_URL` by regenerating the webhook in Discord (**Server Settings → Integrations → Webhooks → Edit → Regenerate**) and updating the GitHub secret. No workflow changes are required.

---

## Secret Rotation Policy

| Secret | Rotation Frequency | Trigger for Immediate Rotation |
|---|---|---|
| `ANDROID_KEYSTORE_*` | Annual | Departing team member, suspected compromise |
| `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` | Annual | Departing team member, role change |
| `IOS_DISTRIBUTION_CERTIFICATE_BASE64` | Annual (certificate expiry) | Compromised private key, departing team member |
| `IOS_DISTRIBUTION_CERTIFICATE_PASSWORD` | On certificate rotation | — |
| `IOS_PROVISIONING_PROFILE_BASE64` | Annual (profile expiry) | Bundle ID change, team change |
| `APP_STORE_CONNECT_PRIVATE_KEY` | Annual | Compromised key, role change |
| `UNITY_LICENSE` | Per subscription renewal | License revocation |
| `DISCORD_WEBHOOK_URL` | On channel deletion or suspected leak | Regenerate in Discord Server Settings |

---

## Reporting Security Vulnerabilities

Do not open a public GitHub issue for security vulnerabilities. Contact the toolkit maintainer (`<WORKFLOW_OWNER>`) through your organization's security disclosure process with:
- Description of the vulnerability
- Steps to reproduce
- Potential impact

We will respond within 72 hours and coordinate a fix and disclosure timeline with you.
