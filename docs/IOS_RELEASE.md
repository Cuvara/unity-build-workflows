# iOS Release Pipeline

This document describes the iOS release workflow: protected environment, version strategy, TestFlight submission, and the tag-triggered production flow.

---

## Release Flow Overview

```
developer pushes tag v1.2.0
  │
  ▼
unity-release.yml triggered (tag: v*)
  │
  ├── Build + sign iOS IPA (unity-build-ios.yml, environment: production)
  │     └── Requires approval from release-approver team
  │
  ├── Export IPA to Builds/iOS/Export/
  │
  ├── Upload to TestFlight (upload_testflight.sh)
  │     └── xcrun altool / xcrun notarytool
  │
  └── Upload artifacts (ios-ipa, ios-archive, ios-symbols)
```

---

## GitHub Environment Setup

Production iOS builds use a `production` GitHub Environment with:

1. **Required reviewers** — at least one human must approve before the environment is unlocked
2. **Deployment branch rule** — only tags matching `v*` pushed to `main` trigger this environment
3. **Environment secrets** — ASC keys scoped here, not at repository level

### Creating the Environment

1. Go to **Settings → Environments → New environment**
2. Name: `production`
3. Enable **Required reviewers** — add your release team
4. Under **Deployment branches and tags** → Add rule: `v*`
5. Under **Environment secrets** → add:
   - `APP_STORE_CONNECT_KEY_ID`
   - `APP_STORE_CONNECT_ISSUER_ID`
   - `APP_STORE_CONNECT_PRIVATE_KEY`

---

## Versioning Strategy

### Semantic Versioning

iOS uses two version numbers:

| Field | Source | Example |
|---|---|---|
| `marketingVersion` (CFBundleShortVersionString) | `bundleVersion` in BuildConfig | `1.2.0` |
| `buildNumber` (CFBundleVersion) | `buildNumberStrategy` | `142` |

### buildNumberStrategy Options

| Strategy | Source | Monotonically increasing? |
|---|---|---|
| `github_run_number` | `$GITHUB_RUN_NUMBER` | Yes (within a repo) |
| `timestamp` | `YYYYMMDDHHmm` | Yes |
| `manual` | `BUILD_NUMBER` env var | Your responsibility |

**TestFlight requirement:** Build numbers must be unique per marketing version. `github_run_number` satisfies this for most teams. If you rebuild a failed release job, increment the version tag.

### Tag-Based Release

```bash
# Create and push a release tag
git tag v1.2.0
git push origin v1.2.0
```

The release workflow reads the tag to set `marketingVersion` in BuildConfig automatically. Do not hardcode version numbers in committed config files — let the workflow derive them from the tag.

---

## TestFlight Upload

### Prerequisites

- `uploadToTestFlight: true` in BuildConfig
- `APP_STORE_CONNECT_*` secrets configured in the `production` environment
- App registered in App Store Connect (bundle ID must match)
- Internal testers added to the TestFlight group

### Upload Mechanism

The `upload_testflight.sh` script uses:

```bash
xcrun altool --upload-app \
  --type ios \
  --file "$IPA_PATH" \
  --apiKey "$APP_STORE_CONNECT_KEY_ID" \
  --apiIssuer "$APP_STORE_CONNECT_ISSUER_ID"
```

Or for Xcode 14+:
```bash
xcrun notarytool submit "$IPA_PATH" \
  --key "$ASC_KEY_FILE" \
  --key-id "$APP_STORE_CONNECT_KEY_ID" \
  --issuer "$APP_STORE_CONNECT_ISSUER_ID" \
  --wait
```

### Processing Time

TestFlight processing takes **5–30 minutes** after upload. The pipeline does not wait for processing to complete — it exits after the upload is accepted. Monitor status in App Store Connect or via the App Store Connect API.

### Processing Limits

- Maximum IPA size: **4 GB**
- Maximum number of TestFlight builds in processing simultaneously: **no documented hard limit**, but large queues slow processing
- Builds expire from TestFlight after **90 days**

---

## Caller Workflow — Production Release

```yaml
# .github/workflows/release.yml  (in your game repository)
name: Release

on:
  push:
    tags:
      - 'v*'

jobs:
  build-ios-release:
    uses: <WORKFLOW_OWNER>/unity-build-workflows/.github/workflows/unity-build-ios.yml@<ref>
    with:
      project-path: .
      unity-version: '6000.0.26f1'
      target-platform: iOS
      environment: production
      build-config-path: BuildConfig
      upload-artifact: true
      upload-to-testflight: true
    secrets: inherit
```

### Key differences from staging builds

| Setting | Staging | Production |
|---|---|---|
| `environment` | `staging` | `production` |
| `upload-to-testflight` | `false` | `true` |
| `exportMethod` | `ad-hoc` or `app-store` | `app-store` |
| Requires approval | No | Yes (GitHub Environment) |
| Branch restriction | None | `v*` tags from `main` only |
| ASC secrets scope | Repository | `production` Environment |

---

## Fork Protection

Forks do not have access to repository or environment secrets. The upload script detects empty `APP_STORE_CONNECT_*` values and exits non-zero before attempting any upload. This is enforced by the resolver:

```bash
# From upload_testflight.sh
if [[ -z "$APP_STORE_CONNECT_KEY_ID" ]]; then
  echo "ERROR: APP_STORE_CONNECT_KEY_ID is empty." \
       "This workflow must not run in a fork context." >&2
  exit 1
fi
```

GitHub Actions' secret masking provides a second layer: even if a secret name is echoed, its value is replaced with `***`.

---

## Rollback

There is no automated rollback for TestFlight builds. To revert:

1. In App Store Connect → TestFlight → your app → stop distribution of the bad build
2. Submit the previous IPA manually, or re-trigger the workflow at the old tag:
   ```bash
   git tag -f v1.1.9 <old-sha>
   git push -f origin v1.1.9
   ```

For App Store submissions (not TestFlight), use App Store Connect to reject a pending review.

---

## Migration from Unsupported iOS State (v2.0.0 → v2.1.0)

In `v2.0.0`, iOS was listed as unsupported. The `v2.1.0` release adds the `unity-build-ios.yml` workflow.

**Migration steps:**

1. Update your workflow reference: use `@v3` (latest stable 3.x), `@v3.0.0` for an exact pin, or `@main` when developing the toolkit itself.
2. Add the iOS BuildConfig fields listed in [IOS.md](IOS.md)
3. Add the required GitHub Secrets listed in [IOS_SIGNING.md](IOS_SIGNING.md)
4. Create the `production` GitHub Environment if using TestFlight
5. Test with `environment: staging` and `uploadToTestFlight: false` first

See [CHANGELOG.md](../CHANGELOG.md) for the full change list.
