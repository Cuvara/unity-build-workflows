# Fastlane — Release Automation

Fastlane handles all store uploads and distribution for the Unity CI/CD pipeline.
It replaces the custom Python/shell release scripts with a single `Fastfile`
containing platform-specific lanes.

## Quick reference

```bash
# From the toolkit root (unity-build-workflows/)
bundle install                          # Install Fastlane + plugins
bundle exec fastlane lanes              # List all available lanes
bundle exec fastlane android upload_internal package_name:com.studio.game aab_path:./build/game.aab
```

## Available lanes

### Shared

| Lane | Purpose |
|---|---|
| `generate_changelog` | Auto release notes from recent git commits |

### Android

| Lane | Purpose | Credentials |
|---|---|---|
| `upload_internal` | Upload AAB/APK to Google Play internal track | `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` |
| `promote_track` | Promote from one track to another | `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` |
| `update_rollout` | Update staged rollout % or halt | `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` |
| `firebase_distribute` | Upload to Firebase App Distribution | `FIREBASE_SERVICE_ACCOUNT_JSON` (file) |
| `download_metadata` | Pull Google Play store listing to local | `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` |
| `upload_metadata` | Push local metadata to Google Play | `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` |

### iOS

| Lane | Purpose | Credentials |
|---|---|---|
| `upload_testflight` | Upload IPA to TestFlight | ASC API key (env) |
| `submit_for_review` | Submit to App Store review | ASC API key (env) |
| `firebase_distribute` | Upload to Firebase App Distribution | `FIREBASE_SERVICE_ACCOUNT_JSON` (file) |
| `add_testers` | Manage TestFlight beta testers | ASC API key (env) |
| `download_metadata` | Pull App Store metadata to local | ASC API key (env) |
| `upload_metadata` | Push local metadata to App Store | ASC API key (env) |

## Credentials

All credentials come from environment variables — never hardcoded.

### Google Play (Android)

```bash
# Environment variable (JSON string, not file path)
export GOOGLE_PLAY_SERVICE_ACCOUNT_JSON='{"type":"service_account",...}'

# Or in GitHub Actions:
gh secret set GOOGLE_PLAY_SERVICE_ACCOUNT_JSON < service-account.json
```

### App Store Connect (iOS)

```bash
export APP_STORE_CONNECT_KEY_ID="ABC123"
export APP_STORE_CONNECT_ISSUER_ID="def-456-ghi"
export APP_STORE_CONNECT_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n..."

# Or in GitHub Actions:
gh secret set APP_STORE_CONNECT_KEY_ID
gh secret set APP_STORE_CONNECT_ISSUER_ID
gh secret set APP_STORE_CONNECT_PRIVATE_KEY < AuthKey.p8
```

### Firebase App Distribution

Firebase uses a service account JSON **file** (not env var) because the Fastlane
plugin expects a file path:

```bash
# The pipeline writes the JSON to a temp file and passes the path.
# You only need to set the secret:
gh secret set FIREBASE_SERVICE_ACCOUNT_JSON < firebase-sa.json
```

## How it fits in the pipeline

### `ARTIFACT_STORAGE=firebase` (recommended for private repos)

```
Build APK/IPA
    ↓
Fastlane firebase_distribute → Firebase App Distribution (tester link)
    ↓ (if release build)
Fastlane upload_internal → Google Play internal track
Fastlane upload_testflight → TestFlight
    ↓
Discord notification with Firebase link
```

All in one job. No separate release pipeline. No GitHub artifact storage cost.

### `ARTIFACT_STORAGE=github` (default)

```
Build APK/IPA
    ↓
Upload GitHub artifact → validate-artifact → release-manifest
    ↓ (separate release workflow)
Fastlane upload_internal / upload_testflight / submit_for_review
```

Traditional promote-based flow. GitHub artifacts stored for promotion.

## Auto-generated release notes

Upload lanes (`upload_internal`, `upload_testflight`) automatically generate
changelogs from recent git commits when no explicit release notes are provided:

```
- Fix login screen crash on Android 14 (cuongnd)
- Add new character selection UI (artist01)
- Update Firebase SDK to 12.0 (cuongnd)
```

Format: `- <commit message> (<author>)`, last 10 non-merge commits.

Override with `release_notes:"Custom notes here"` parameter.

## Store metadata as code

Keep your store listings version-controlled:

```bash
# Pull current metadata from stores
bundle exec fastlane android download_metadata package_name:com.studio.game
bundle exec fastlane ios download_metadata bundle_id:com.studio.game

# Edit locally in metadata/android/ and metadata/ios/
# Then push back:
bundle exec fastlane android upload_metadata package_name:com.studio.game
bundle exec fastlane ios upload_metadata bundle_id:com.studio.game
```

Directory structure:

```
metadata/
  android/
    en-US/
      title.txt
      short_description.txt
      full_description.txt
      changelogs/
        default.txt
  ios/
    en-US/
      name.txt
      subtitle.txt
      description.txt
      keywords.txt
      release_notes.txt
```

## Plugins

The `Gemfile` includes:

| Plugin | Purpose |
|---|---|
| `fastlane` ~> 2.225 | Core |
| `fastlane-plugin-firebase_app_distribution` ~> 0.9 | Firebase delivery |

## Files

| File | Purpose |
|---|---|
| `Gemfile` | Ruby dependencies |
| `.ruby-version` | Pin Ruby 3.2 for CI |
| `fastlane/Fastfile` | All lanes |
| `fastlane/Appfile` | Minimal — values injected at runtime |
| `.github/actions/setup-fastlane/action.yml` | Ruby + Bundler caching for CI |
