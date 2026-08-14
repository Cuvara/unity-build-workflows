# Adding a New Project

This guide walks through integrating a new Unity project as a **consumer** of the `unity-build-workflows` toolkit. As a consumer, you provide:

1. Your Unity project
2. A `BuildConfig/` directory with environment JSON files
3. A small caller workflow (`.github/workflows/build.yml`)
4. GitHub secrets
5. The `com.company.build-pipeline` UPM package dependency

The toolkit repo (`<WORKFLOW_OWNER>/unity-build-workflows`) owns all shared logic, Docker images, reusable workflows, schemas, and scripts. **You copy nothing from it** — your repo only calls its reusable workflows via `uses:`.

---

## Prerequisites

- GitHub repository for your Unity project
- Unity 6 (6000.x) or Unity 2022.3 LTS or later
- GitHub Actions enabled on your repository
- Read access to the `<WORKFLOW_OWNER>/unity-build-workflows` repository (or it is public)
- Docker Engine available on CI runners — GitHub-hosted `ubuntu-latest` includes Docker

**You do not need Unity installed locally to use CI builds.** Docker handles the Unity environment for Android/WebGL/Linux builds. iOS builds use a GitHub-hosted macOS runner.

---

## Step 1: Add the UPM Package Dependency

This package provides the `Company.BuildPipeline.Editor.BuildCommand.Execute` C# entry point.

**Your project still needs a build entry point of its own for the default lanes.** The Docker and
self-hosted lanes invoke `PlayerBuilder.Build` unless you say otherwise — see
[Step 1b](#step-1b-provide-the-build-entry-point) — and only the iOS-native workflows call
`BuildCommand.Execute`. Installing the package is necessary for those workflows and for the
validation rules; it is not sufficient on its own.

**Option A — Git URL (recommended for most projects):**

In your Unity project's `Packages/manifest.json`:

```json
{
  "dependencies": {
    "com.company.build-pipeline": "https://github.com/<WORKFLOW_OWNER>/unity-build-workflows.git?path=/unity-package/Packages/com.company.build-pipeline#<WORKFLOW_REF>",
    "com.unity.nuget.newtonsoft-json": "3.2.1"
  }
}
```

Replace `<WORKFLOW_OWNER>` with the GitHub organization or user that hosts the toolkit, and `<WORKFLOW_REF>` with a release tag (e.g. `v1.0.0`) or commit SHA. Avoid using `main` in production.

**Option B — Local file path (for submodule consumers):**

If you have added `unity-build-workflows` as a git submodule at `tools/unity-build-workflows/`:

```json
{
  "dependencies": {
    "com.company.build-pipeline": "file:../tools/unity-build-workflows/unity-package/Packages/com.company.build-pipeline",
    "com.unity.nuget.newtonsoft-json": "3.2.1"
  }
}
```

The `file:` path is relative to `Packages/manifest.json`. See [SUBMODULE_INTEGRATION.md](SUBMODULE_INTEGRATION.md) for the full submodule setup guide.

---

## Step 1b: Provide the Build Entry Point

Which C# method Unity runs depends on **which lane builds you**, and the two do not currently agree.

| Lane | Entry point | Who implements it |
|---|---|---|
| Docker / game-ci and self-hosted — Android, WebGL, Windows, Linux, and the pipeline's own `Build iOS` job | `PlayerBuilder.Build` | **You**, in your Unity project |
| iOS native — `unity-build-ios.yml`, `unity-release-ios.yml` | `Company.BuildPipeline.Editor.BuildCommand.Execute` | This package |

The default comes from `reusable-build-platform.yml` (`BUILD_METHOD` defaults to `PlayerBuilder.Build`;
the Windows variant does the same), so a project that adds the package and nothing else has no method
to run on those lanes.

You have two options:

1. **Write a `PlayerBuilder`.** A public static parameterless method Unity can reach by name:

   ```csharp
   using UnityEditor;

   public static class PlayerBuilder      // global namespace: -executeMethod PlayerBuilder.Build
   {
       public static void Build()
       {
           // Read what CI passes through the environment, then call BuildPipeline.BuildPlayer.
           var outputRoot = System.Environment.GetEnvironmentVariable("BUILD_OUTPUT_DIR");
           // ANDROID_APP_BUNDLE, ANDROID_KEYSTORE, ANDROID_KEYSTORE_PASS,
           // ANDROID_KEYALIAS_NAME, ANDROID_KEYALIAS_PASS are also supplied for Android.
       }
   }
   ```

   It must be **in the global namespace** if you want `-executeMethod PlayerBuilder.Build` to resolve,
   and it must live in an Editor assembly. There is no reference implementation in this repository;
   copy one from a project that already builds with this toolkit.

2. **Point the lane at this package instead**, by setting the repository variable
   `UNITY_BUILD_METHOD` to `Company.BuildPipeline.Editor.BuildCommand.Execute`. It flows through
   `unity-pipeline.yml` into the `build-method` input.

**Known inconsistency:** `reusable-build-platform.yml` never calls `scripts/ios/run_unity_ios.sh`, and
that script hardcodes `BuildCommand.Execute` as `readonly` with no override. So the two iOS routes use
different entry points, and `UNITY_BUILD_METHOD` does not affect the native one. Making
`run_unity_ios.sh` accept `build-method` would resolve it; that is a code change and has not been made.

---

## Step 2: Create Your BuildConfig Files

Create a `BuildConfig/` directory in your repository root:

```bash
mkdir -p BuildConfig
```

Copy the base template from the toolkit and adapt it:

```bash
cp path/to/unity-build-workflows/templates/BuildConfig.base.example.json BuildConfig/base.json
```

Edit `BuildConfig/base.json` — replace all placeholder values:

| Field | Change To |
|---|---|
| `projectName` | Your project identifier (e.g. `my-unity-game`) |
| `companyName` | Your company name |
| `productName` | Display name of your app/game |
| `android.applicationId` | Your Android package name (e.g. `com.yourcompany.yourgame`) |
| `iOS.bundleIdentifier` | Your iOS bundle ID |
| `iOS.developmentTeamId` | Your 10-character Apple Developer Team ID |
| `scenes` | Paths to your scenes in load order |
| `metadata.repository` | URL of your project repository |

Then create environment overlays (each file contains only the fields that **differ** from base):

```bash
cp path/to/unity-build-workflows/templates/BuildConfig.development.example.json BuildConfig/development.json
cp path/to/unity-build-workflows/templates/BuildConfig.staging.example.json BuildConfig/staging.json
cp path/to/unity-build-workflows/templates/BuildConfig.production.example.json BuildConfig/production.json
```

> **Overlay files** — Environment JSONs are deep-merged on top of `base.json` at build time. They only need to contain the fields that change per environment (e.g., `outputDirectory`, `developmentBuild`, `android.keystoreMode`). The base config provides all required fields.

See [BUILD_CONFIG.md](BUILD_CONFIG.md) for every field reference.

---

## Step 3: Add GitHub Secrets

Navigate to **Settings → Secrets and variables → Actions** and add:

**Minimum required (all builds):**
```
UNITY_LICENSE        # Complete .ulf file content (not base64-encoded)
```

**For Android staging/production builds (custom signing):**
```
ANDROID_KEYSTORE_BASE64       # base64-encoded .jks or .keystore
ANDROID_KEYSTORE_PASSWORD     # Keystore password
ANDROID_KEY_ALIAS             # Key alias
ANDROID_KEY_PASSWORD          # Key alias password
```

**For iOS builds (manual signing):**
```
IOS_DISTRIBUTION_CERTIFICATE_BASE64       # base64-encoded .p12 certificate
IOS_DISTRIBUTION_CERTIFICATE_PASSWORD     # .p12 export password
IOS_PROVISIONING_PROFILE_BASE64           # base64-encoded .mobileprovision
```

**For App Store Connect / TestFlight (production environment):**
```
APP_STORE_CONNECT_KEY_ID         # ASC API key ID
APP_STORE_CONNECT_ISSUER_ID      # ASC issuer UUID
APP_STORE_CONNECT_PRIVATE_KEY    # .p8 key contents
```

**For Google Play deployment (production environment):**
```
GOOGLE_PLAY_SERVICE_ACCOUNT_JSON   # Full service account JSON
```

See [templates/build-secrets.example.md](../templates/build-secrets.example.md) for the complete secret matrix with required-for columns.

---

## Step 4: Create Your Caller Workflow

Create `.github/workflows/build.yml` in your project repository. Your workflow calls the reusable workflow from the toolkit — it does **not** copy any scripts, actions, or schemas.

**Android / WebGL / Linux builds (Docker lane):**

```yaml
name: Unity Build

on:
  pull_request:
  push:
    branches: [develop, staging]
  workflow_dispatch:
    inputs:
      platform:
        type: choice
        options: [Android, WebGL, Linux64]

jobs:
  build:
    uses: <WORKFLOW_OWNER>/unity-build-workflows/.github/workflows/unity-build.yml@<ref>
    with:
      project-path: .
      unity-version: '6000.0.26f1'
      target-platform: ${{ inputs.platform || 'Android' }}
      environment: development
      build-config-path: BuildConfig
      test-level: editmode
      cache-mode: safe
      upload-artifact: true
    secrets: inherit
```

**iOS build (macOS lane):**

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

> **`<ref>` / `<WORKFLOW_REF>` values:**
> - **Development / pre-release:** use `@main` to track the latest toolkit changes, or pin to a specific commit SHA for reproducibility.
> - **Stable release:** use an exact published tag (e.g. `@vX.Y.Z`) — **no tags have been published yet**; check [CHANGELOG.md](../CHANGELOG.md) and the repository Releases page for the first available tag.
> - A floating `@vMAJOR` tag (e.g. `@v2`) does not exist yet — will be created alongside the first stable release of that major version.
>
> **Toolkit checkout:** The reusable workflow internally checks out the toolkit repository to access shared scripts and actions. Your project repository is also checked out in the correct workspace. No additional setup is needed in your caller workflow.

The executor (Docker or macOS) is selected automatically from `target-platform` — no `executor-mode` input exists.

---

## Step 5: Configure GitHub Environments

For staging and production builds:

1. **Settings → Environments → New environment**
2. Create `staging`:
   - Deployment branch: `main`
3. Create `production`:
   - Required reviewers: at least one person
   - Deployment branch: tags matching `v*`
   - Add `APP_STORE_CONNECT_*` and `GOOGLE_PLAY_*` secrets to this environment (not repository-level)

Production secrets scoped to a GitHub Environment cannot be accessed by forks or by jobs targeting other environments.

---

## Step 6: Test a Development Build

Push to a feature branch or trigger manually:

```bash
git push origin feature/ci-setup
```

Or: **Actions → Unity Build → Run workflow → development**

Verify:
- [ ] Schema validation passes
- [ ] Docker image pulled successfully
- [ ] Unity runs inside container (check logs for `docker run`)
- [ ] Build completes without errors
- [ ] Artifact is uploaded and downloadable
- [ ] Editor.log is present in build reports

---

## Step 7: Test Locally with Docker

Run Android/WebGL/Linux builds locally using the same Docker images as CI:

```bash
python3 path/to/unity-build-workflows/scripts/docker/run_unity_container.py \
  --project-path /path/to/your/project \
  --unity-version 6000.0.26f1 \
  --target-platform Android \
  --environment development \
  --build-config-path BuildConfig
```

Prerequisites: Docker Engine running locally. No local Unity installation needed.

> **Image availability:** Local builds require a published Docker image for your Unity version. See [IMAGE_LIFECYCLE.md](IMAGE_LIFECYCLE.md) and [ANDROID.md](ANDROID.md#image-bootstrap) for the bootstrap process if no image exists yet.

---

## Step 8: Configure Discord Notifications (Optional)

To receive build-completion messages in a Discord channel:

1. In Discord: **Server Settings → Integrations → Webhooks → New Webhook**. Select the target channel and copy the webhook URL.
2. In GitHub: **Settings → Secrets and variables → Actions → New repository secret**.
   - Name: `DISCORD_WEBHOOK_URL`
   - Value: the Discord webhook URL

The `unity-build.yml`, `unity-build-ios.yml`, `unity-release.yml`, and `unity-release-ios.yml` workflows automatically post embeds on success, failure, and cancellation.

If `DISCORD_WEBHOOK_URL` is not set the notification step skips silently — the build is never affected.

See [DISCORD_NOTIFICATIONS.md](DISCORD_NOTIFICATIONS.md) for full details including embed format, security notes, and troubleshooting.

---

## Supported Platforms

| Platform | Workflow | Executor | Notes |
|---|---|---|---|
| Android | `unity-build.yml` | Docker (ubuntu-latest) | `target-platform: Android` |
| WebGL | `unity-build.yml` | Docker (ubuntu-latest) | `target-platform: WebGL` |
| Linux Standalone | `unity-build.yml` | Docker (ubuntu-latest) | `target-platform: Linux64` |
| Linux Server | `unity-build.yml` | Docker (ubuntu-latest) | `target-platform: LinuxServer` |
| **iOS** | `unity-build-ios.yml` | macos-13 | `target-platform: iOS` — uses native Xcode lane |
| Windows | — | — | **Unsupported** — see [PLATFORM_LIMITATIONS.md](PLATFORM_LIMITATIONS.md) |

> See [docs/PLATFORM_MATRIX.md](PLATFORM_MATRIX.md) for the authoritative platform support matrix (maintained by the platform architect).

---

## Troubleshooting First-Time Setup

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

Common issues:
- **"Unity license activation failed"** — Check `UNITY_LICENSE` contains the complete `.ulf` file content (not base64-encoded).
- **"Cannot connect to Docker daemon"** — Ensure Docker is available on the runner.
- **"Image not found"** — The Unity image for your version may need to be built first. See [IMAGE_LIFECYCLE.md](IMAGE_LIFECYCLE.md#bootstrap).
- **"Schema validation failed"** — Run validation locally: `python3 path/to/unity-build-workflows/scripts/common/validate_build_config.py BuildConfig/base.json`
- **iOS: "No such file or directory: xcodebuild"** — The iOS build must run on a macOS runner; check that `unity-build-ios.yml` is used (not `unity-build.yml`).
