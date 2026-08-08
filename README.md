# unity-build-workflows

Docker-mandatory reusable GitHub Actions workflows for building, testing, and releasing Unity projects. All Unity operations run inside pinned, versioned Docker containers — the CI runner never executes Unity directly.

---

## What This Repository Is

`unity-build-workflows` is a CI/CD platform toolkit. Unity project repositories call its reusable workflows as consumers. This repository manages:

- Docker image definitions for Unity build environments
- Container entrypoints that invoke Unity in batch mode
- Reusable GitHub Actions workflows for build orchestration
- Build configuration schemas and validation
- Image security scanning and SBOM generation

**Docker is mandatory.** There is no native Unity execution fallback (iOS uses a macOS runner with Xcode — not Docker).

**As a consumer, you own:**
- Your Unity project
- A `BuildConfig/` directory
- A small caller workflow
- GitHub secrets

**You copy nothing from this toolkit.** Your workflow calls `uses: <WORKFLOW_OWNER>/unity-build-workflows/...` and everything else is managed here.

---

## Architecture

```
Your Project Repo
  .github/workflows/build.yml
       │ uses: <WORKFLOW_OWNER>/unity-build-workflows/.github/workflows/unity-build.yml@<ref>
       │       secrets: inherit
       ▼
unity-build-workflows
  CI Runner (ubuntu-latest)
       │
       ▼
  Docker Engine
       │
       ▼
  Pinned Unity Build Image (ghcr.io/<IMAGE_NAMESPACE>/unity-builder@sha256:...)
       │
       ▼
  entrypoint.sh → Unity -batchmode -executeMethod BuildCommand.Execute
       │
       ▼
  Artifacts, logs, reports → bind-mounted host directories
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the complete layer diagram.

---

## Supported Platforms

> See [docs/PLATFORM_MATRIX.md](docs/PLATFORM_MATRIX.md) for the canonical platform support matrix (maintained by the platform architect).

| Platform | Executor | Runner OS | Status |
|---|---|---|---|
| Android | `docker-unity` | ubuntu-latest | **Supported** |
| WebGL | `docker-unity` | ubuntu-latest | **Supported** |
| Linux Standalone | `docker-unity` | ubuntu-latest | **Supported** |
| Linux Dedicated Server | `docker-unity` | ubuntu-latest | **Supported** |
| **iOS** | `macos-unity-xcode` | macos-13 | **Supported** (native macOS + Xcode lane) |
| Windows | — | — | **Unsupported** — see [docs/PLATFORM_LIMITATIONS.md](docs/PLATFORM_LIMITATIONS.md) |

Unsupported targets fail with an actionable error message. The repository never silently falls back to native Unity execution.

---

## Consumer Quickstart

### 1. Add the UPM Package

In your Unity project's `Packages/manifest.json`:

```json
{
  "dependencies": {
    "com.company.build-pipeline": "https://github.com/<WORKFLOW_OWNER>/unity-build-workflows.git?path=/unity-package/Packages/com.company.build-pipeline#<WORKFLOW_REF>"
  }
}
```

### 2. Add a BuildConfig

```bash
mkdir BuildConfig
cp templates/BuildConfig.base.example.json BuildConfig/base.json
# Edit: projectName, companyName, applicationId/bundleIdentifier, scenes
cp templates/BuildConfig.development.example.json BuildConfig/development.json
cp templates/BuildConfig.staging.example.json BuildConfig/staging.json
cp templates/BuildConfig.production.example.json BuildConfig/production.json
```

Environment files (`development.json`, etc.) are overlays — they contain only the fields that differ from `base.json`. See [docs/BUILD_CONFIG.md](docs/BUILD_CONFIG.md).

### 3. Add Secrets

At minimum: `UNITY_LICENSE`. See [templates/build-secrets.example.md](templates/build-secrets.example.md) for the full secret matrix.

### 4. Create `.github/workflows/build.yml`

**Android / WebGL / Linux (Docker lane):**
```yaml
name: Unity Build

on:
  pull_request:
  push:
    branches: [develop, staging]
  workflow_dispatch:

jobs:
  build-android:
    uses: <WORKFLOW_OWNER>/unity-build-workflows/.github/workflows/unity-build.yml@<ref>
    with:
      project-path: .
      unity-version: '6000.0.26f1'
      target-platform: Android
      environment: development
      build-config-path: BuildConfig
      test-level: editmode
      cache-mode: safe
      upload-artifact: true
    secrets: inherit
```

**iOS (macOS lane):**
```yaml
name: iOS Build

on:
  push:
    branches: [main, release/*]
  workflow_dispatch:

jobs:
  build-ios:
    uses: <WORKFLOW_OWNER>/unity-build-workflows/.github/workflows/unity-build-ios.yml@<ref>
    with:
      project-path: .
      unity-version: '6000.0.26f1'
      target-platform: iOS
      environment: staging
      build-config-path: BuildConfig
      upload-artifact: true
    secrets: inherit
```

> **Choosing `<ref>`:**
> - Development / pre-release: `@main` (tracks latest) or `@<commit-sha>` (pinned)
> - Stable release: use an exact released tag (e.g. `@vX.Y.Z`) — **no tags have been published yet**; check [CHANGELOG.md](CHANGELOG.md) and the repository Releases page for the first published tag.
> - A floating `@vMAJOR` tag (e.g. `@v2`) is published only after the first stable release of that major version. It does not yet exist — do not reference it until announced.

The executor (Docker or macOS) is selected automatically from `target-platform`. No `executor-mode` input exists.

See [docs/ADD_NEW_PROJECT.md](docs/ADD_NEW_PROJECT.md) for the complete onboarding walkthrough.

### 5. Add iOS Secrets (iOS builds only)

For iOS, add at **Settings → Secrets and variables → Actions**:

```
UNITY_LICENSE                              # .ulf license content
UNITY_EMAIL                                # Unity account email
UNITY_PASSWORD                             # Unity account password
IOS_DISTRIBUTION_CERTIFICATE_BASE64        # Base64-encoded .p12 certificate
IOS_DISTRIBUTION_CERTIFICATE_PASSWORD      # .p12 export password
IOS_PROVISIONING_PROFILE_BASE64            # Base64-encoded .mobileprovision
APP_STORE_CONNECT_KEY_ID                   # ASC API key ID (for TestFlight)
APP_STORE_CONNECT_ISSUER_ID                # ASC issuer UUID (for TestFlight)
APP_STORE_CONNECT_PRIVATE_KEY              # .p8 key contents (for TestFlight)
```

Scope `APP_STORE_CONNECT_*` to the `production` GitHub Environment. See [docs/IOS_SIGNING.md](docs/IOS_SIGNING.md).

### 6. Enable Discord Notifications (optional)

Add a single secret to receive build-completion embeds in a Discord channel:

```
DISCORD_WEBHOOK_URL    # Discord webhook URL — omit to disable notifications
```

Notifications cover success, failure, and cancelled status. If the secret is not set the workflows skip the notification step silently. See [docs/DISCORD_NOTIFICATIONS.md](docs/DISCORD_NOTIFICATIONS.md).

---

## Local Build Commands

Local Android/WebGL/Linux builds also use Docker:

```bash
# Android build
python3 scripts/docker/run_unity_container.py \
  --project-path . \
  --unity-version 6000.0.26f1 \
  --target-platform Android \
  --environment development \
  --build-config-path BuildConfig

# EditMode tests
python3 scripts/docker/run_unity_container.py \
  --project-path . \
  --unity-version 6000.0.26f1 \
  --target-platform Android \
  --test-level editmode
```

**Prerequisites:** Docker Engine installed and running. No local Unity installation required for Docker-lane builds.

> **Image availability:** Local builds require a published Docker image for your Unity version and variant. If no image is published yet, see [docs/IMAGE_LIFECYCLE.md](docs/IMAGE_LIFECYCLE.md#bootstrap) for the bootstrap process.

---

## Docker Image Strategy

Images extend pinned [GameCI](https://game.ci/) base images with an organizational tooling layer:

```
unityci/editor:6000.0.26f1-android-3  (pinned GameCI base)
  └─ ghcr.io/<IMAGE_NAMESPACE>/unity-builder:6000.0.26f1-android-v2.0.0  (org layer)
       └─ entrypoint.sh, license scripts, healthcheck, python3, jq
```

Production workflows resolve and pin images by digest:
```
ghcr.io/<IMAGE_NAMESPACE>/unity-builder@sha256:<digest>
```

> **Important:** Images must be built and published before they can be used. See [docs/IMAGE_LIFECYCLE.md](docs/IMAGE_LIFECYCLE.md) for the full image lifecycle including the bootstrap process.

See [docs/IMAGE_LIFECYCLE.md](docs/IMAGE_LIFECYCLE.md) for the full image strategy.

---

## Required Docker Version

- Docker Engine 20.10+ (BuildKit support)
- Docker Compose is not required

---

## Versioning Policy

Semantic versioning (`MAJOR.MINOR.PATCH`).

```yaml
# Development / pre-release — tracks latest changes
uses: <WORKFLOW_OWNER>/unity-build-workflows/.github/workflows/unity-build.yml@main

# Pinned to exact commit SHA — fully reproducible
uses: <WORKFLOW_OWNER>/unity-build-workflows/.github/workflows/unity-build.yml@abc1234

# Stable release — use the exact published tag
uses: <WORKFLOW_OWNER>/unity-build-workflows/.github/workflows/unity-build.yml@vX.Y.Z
```

> **No floating major-version tags exist yet** (e.g. `@v2`). Floating major tags will be published alongside stable releases. Until then, use `@main` for development or an exact SHA/tag for reproducibility.
>
> To create release tags (run when ready to ship — NOT executed yet):
> ```bash
> git tag vX.Y.Z && git push origin vX.Y.Z           # e.g. git tag v2.0.0
> git tag -f vMAJOR && git push -f origin vMAJOR     # e.g. git tag -f v2 — only after first vMAJOR.x.x tag
> ```

Major version increments indicate breaking changes to the workflow input interface.

Current version: **2.2.0** — see [CHANGELOG.md](CHANGELOG.md).

---

## Documentation

| Document | Contents |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layer diagram, executor lanes (docker-unity + macos-unity-xcode), extension points |
| [docs/PLATFORM_MATRIX.md](docs/PLATFORM_MATRIX.md) | **Canonical platform support matrix** — authoritative source for all platform/executor/status combinations |
| [docs/DOCKER_BUILD.md](docs/DOCKER_BUILD.md) | Container flow, mounts, caches, licensing, debugging |
| [docs/IMAGE_LIFECYCLE.md](docs/IMAGE_LIFECYCLE.md) | Base image, variants, bootstrap, scanning, SBOM, tagging, deprecation |
| [docs/ADD_NEW_PROJECT.md](docs/ADD_NEW_PROJECT.md) | Step-by-step consumer onboarding guide |
| **[docs/CONSUMER\_SETUP.md](docs/CONSUMER_SETUP.md)** | **New — drop-in pipeline setup for new projects (thin caller + secrets + environments)** |
| [docs/BUILD_CONFIG.md](docs/BUILD_CONFIG.md) | Every BuildConfig field documented (including full iOS section) |
| [docs/ANDROID.md](docs/ANDROID.md) | Android signing, AAB, symbol export via Docker, image bootstrap |
| [docs/IOS.md](docs/IOS.md) | Full iOS pipeline: Unity → Xcode → archive → IPA → TestFlight |
| [docs/IOS_SIGNING.md](docs/IOS_SIGNING.md) | Certificate, provisioning profile, ASC key setup |
| [docs/IOS_RELEASE.md](docs/IOS_RELEASE.md) | Release workflow, protected environment, versioning, TestFlight |
| [docs/IOS_VERIFICATION.md](docs/IOS_VERIFICATION.md) | macOS runner verification runbook (Level 0–3, checklist) |
| [docs/WEBGL.md](docs/WEBGL.md) | WebGL compression, hosting via Docker |
| [docs/LINUX.md](docs/LINUX.md) | Linux standalone and dedicated server builds |
| [docs/PLATFORM_LIMITATIONS.md](docs/PLATFORM_LIMITATIONS.md) | iOS (macOS lane, supported), Windows (unsupported), GPU/native plugin limits |
| [docs/RELEASE_FLOW.md](docs/RELEASE_FLOW.md) | Tag-based release, environments, digest enforcement |
| [docs/SELF_HOSTED_RUNNER.md](docs/SELF_HOSTED_RUNNER.md) | Runner setup with Docker requirements |
| [docs/SECURITY.md](docs/SECURITY.md) | Secret handling, iOS credentials, image trust, fork safety |
| [docs/DISCORD_NOTIFICATIONS.md](docs/DISCORD_NOTIFICATIONS.md) | Discord build-completion notifications: setup, security, embed format |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Docker, Unity, and iOS errors; cert rotation; Xcode migration |
| [docs/adr/001-docker-mandatory-architecture.md](docs/adr/001-docker-mandatory-architecture.md) | Architecture decision record |
| [docs/adr/002-ios-native-exception.md](docs/adr/002-ios-native-exception.md) | iOS macOS-lane exception decision record |
| **[docs/UNITY\_PERSONAL\_DOCKER\_LICENSE.md](docs/UNITY_PERSONAL_DOCKER_LICENSE.md)** | **Unity Personal/free Docker licensing — `personal-combined` strategy, secret setup, troubleshooting** |
| **[docs/UNITY\_VERSION\_UPGRADE.md](docs/UNITY_VERSION_UPGRADE.md)** | **Step-by-step Unity version upgrade checklist — images, config, consumer builds** |
| **[docs/GITHUB\_ACTIONS\_BUILD\_RUNBOOK.md](docs/GITHUB_ACTIONS_BUILD_RUNBOOK.md)** | **Operational runbook — triggering builds, reading logs, artifacts, common errors** |

---

## Unity Personal / Free License Support

This toolkit uses the **`personal-combined` strategy** for Unity Personal/free
license activation in ephemeral Docker containers — the only reliable method:

- Provide `UNITY_LICENSE` (raw `.ulf` contents), `UNITY_EMAIL`, and
  `UNITY_PASSWORD` **together** as repository secrets.
- `.ulf` alone fails with `TimeStamp validation failed`; credentials alone fail
  with `0 entitlements`; all three together produce `Activation successful`.
- `UNITY_LICENSE` must be the raw `.ulf` XML. Do **not** base64-encode it.

See [docs/UNITY\_PERSONAL\_DOCKER\_LICENSE.md](docs/UNITY_PERSONAL_DOCKER_LICENSE.md)
for full setup instructions, secret commands, and a troubleshooting table.

---

## Docker Image Variants

Images are published to `ghcr.io/dycuong03/unity-editor:<version>-<variant>`.

| Variant | Build targets | Image suffix |
|---|---|---|
| `android` | Android (APK, AAB) | `-android` |
| `webgl` | WebGL | `-webgl` |
| `linux` | Linux64, LinuxServer | `-linux` |

Build variants using `build-unity-image.yml` in this repository:

```bash
gh workflow run build-unity-image.yml \
  --repo Cuvara/unity-build-workflows \
  --ref main \
  -f unity-version=6000.0.26f1 \
  -f image-variant=android \
  -f push-image=true
```

See [docs/UNITY\_VERSION\_UPGRADE.md](docs/UNITY_VERSION_UPGRADE.md) for the
full rebuild procedure.

---

## Using From a Consumer Repository

The consumer repository (e.g. `Cuvara/NDCUnityTemplate`) calls this
toolkit via reusable workflows:

```yaml
jobs:
  pipeline:
    uses: Cuvara/unity-build-workflows/.github/workflows/unity-pipeline.yml@main
    with:
      platform: Android
      project-path: '.'
      # Unity version is read from ProjectSettings/ProjectVersion.txt (SSOT);
      # platforms/tests/etc. are configured via Repository Variables — see
      # docs/REPOSITORY_VARIABLES.md.
    secrets:
      UNITY_LICENSE:  ${{ secrets.UNITY_LICENSE }}
      UNITY_EMAIL:    ${{ secrets.UNITY_EMAIL }}
      UNITY_PASSWORD: ${{ secrets.UNITY_PASSWORD }}
```

Required consumer secrets: `UNITY_LICENSE`, `UNITY_EMAIL`, `UNITY_PASSWORD`.

---

## Upgrading Unity Version

When the Unity version changes, you must rebuild all Docker image variants and
update the config. Follow the step-by-step checklist in
[docs/UNITY\_VERSION\_UPGRADE.md](docs/UNITY_VERSION_UPGRADE.md).

The Unity version single source of truth is the consumer's
`ProjectSettings/ProjectVersion.txt`. The toolkit default is stored in
`config/unity-build-defaults.json`.

---

## Use in Another Project (Quick Start)

The fastest way to add this pipeline to a new Unity project:

```bash
# 1. Copy the caller workflow template
mkdir -p .github/workflows
curl -fsSL \
  https://raw.githubusercontent.com/Cuvara/unity-build-workflows/main/templates/consumer-unity-build.yml \
  -o .github/workflows/unity-build.yml

# 2. Set required secrets
gh secret set UNITY_EMAIL    --repo YOUR_ORG/YOUR_REPO
gh secret set UNITY_PASSWORD --repo YOUR_ORG/YOUR_REPO
gh secret set UNITY_LICENSE  --repo YOUR_ORG/YOUR_REPO < Unity_lic.ulf

# 3. Commit and push — CI is now live
git add .github/workflows/unity-build.yml
git commit -m "ci: add Unity build pipeline"
git push
```

The caller workflow (`consumer-unity-build.yml`) uses a single
`uses: Cuvara/unity-build-workflows/.github/workflows/unity-pipeline.yml@v1`
call with `secrets: inherit` — no per-secret wiring, no build scripts to copy.

**Pin the version**: `@v1` (latest stable, auto-fixes) or `@v1.2.0` (exact) for
production; `@main` only for development. Keep `toolkit-ref:` set to the same
ref. See [docs/CONSUMER_SETUP.md](docs/CONSUMER_SETUP.md).

Automatic branch flows:
- push → `develop` → builds Android + WebGL
- push → `staging` → builds Android + WebGL + Linux64 + LinuxServer
- push → `release-*` → full build + Addressables + Android release signing
- PR → any of the above → tests only (no binary builds, no environment secrets exposed)

For the complete setup guide including GitHub Environments, Addressables,
Discord notifications, and Repository Variables, see
**[docs/CONSUMER\_SETUP.md](docs/CONSUMER_SETUP.md)**.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) — © 2024 <WORKFLOW_OWNER>
