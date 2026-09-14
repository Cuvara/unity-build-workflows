# Discord Build Notifications

This document describes the optional Discord build-completion notification system for the `unity-build-workflows` platform.

---

## Overview

The `.github/actions/discord-notify` composite action posts a Discord embed to a configured webhook URL at the end of each build run. It uses `curl` directly — no third-party GitHub Action is involved, eliminating supply-chain risk.

Notifications are **optional**: if `DISCORD_WEBHOOK_URL` is not set the action no-ops silently and the build succeeds normally. A webhook misconfiguration or Discord service outage will never fail your pipeline.

---

## Setup

### Secret: `DISCORD_WEBHOOK_URL`

| Secret | Scope | Required |
|---|---|---|
| `DISCORD_WEBHOOK_URL` | Repository or Environment | No (optional) |

The webhook URL is read from the `DISCORD_WEBHOOK_URL` environment variable inside the action — it is **never** an action `input`. This keeps the value away from workflow inputs (which can appear in run summaries and API responses) and limits exposure to the job environment.

#### How to create a Discord webhook

1. Open your Discord server → **Server Settings → Integrations → Webhooks**.
2. Click **New Webhook**, give it a name (e.g. `Unity CI`), select the target channel, and copy the URL.
3. In your GitHub repository: **Settings → Secrets and variables → Actions → New repository secret**.
4. Name: `DISCORD_WEBHOOK_URL` — Value: the copied webhook URL.

For production builds using GitHub Environments, add the secret to the environment instead of the repository if you want channel-level isolation between development and production.

### Variable: `DISCORD_THREAD_ID` (optional)

| Variable | Scope | Required |
|---|---|---|
| `DISCORD_THREAD_ID` | Repository or Environment | No (optional) |

Set this to route notifications into a specific **Discord thread or forum post** rather than the channel root.

The value must be a Discord **thread snowflake ID** (a 17–20 digit number). It is:

- **NOT** the parent channel ID (channel IDs look identical numerically — using one here silently fails; Discord ignores an invalid `thread_id`).
- **NOT** the webhook ID or webhook token.
- **NOT** a secret — store it as a repository **variable**, not a secret.

To find a thread ID: enable **Developer Mode** in Discord (User Settings → Advanced), then right-click the thread or forum post name → **Copy Thread ID**.

```bash
# Set the thread ID as a repository variable (not a secret — it is not sensitive)
gh variable set DISCORD_THREAD_ID --repo YOUR_ORG/YOUR_REPO --body "1234567890123456789"
```

#### Routing behaviour

| Configuration | Destination |
|---|---|
| `DISCORD_WEBHOOK_URL` only | Webhook's configured channel (root) |
| `DISCORD_WEBHOOK_URL` + `DISCORD_THREAD_ID` | Specified thread / forum post |

When `DISCORD_THREAD_ID` is set, both `discord-notify` and `discord-upload-build` append `?thread_id=<id>` to the webhook URL. When unset, messages go to the channel root — existing behaviour is unchanged.

#### Example

```
DISCORD_WEBHOOK_URL = <secret — set via gh secret set>
DISCORD_THREAD_ID   = 1234567890123456789
```

The `discord-notify` action reads `DISCORD_THREAD_ID` from the job environment (set via `vars.DISCORD_THREAD_ID` in the workflow env block). The `discord-upload-build` action receives it as the `thread-id` input.

#### Validation

The action validates the thread ID as a 17–20 digit numeric string (Discord snowflake format). A malformed value produces a `::warning::` annotation and the message falls back to the channel root — the pipeline is never affected.

---

## Which Workflows Notify

| Workflow | Job | Condition | Platform(s) |
|---|---|---|---|
| `unity-build.yml` | `report` | `if: always()` — fires on success, failure, and cancelled | Android, WebGL, Linux64, LinuxServer, iOS (via orchestrator) |
| `unity-build-ios.yml` | `build` | `if: always()` — fires on success, failure, and cancelled | iOS (direct caller) |
| `unity-release-ios.yml` | `release-build` | `if: always()` — fires on success, failure, and cancelled | iOS production release |
| `unity-release.yml` | `notify` (dedicated job) | `if: always()` — fires on success, failure, and cancelled | Android, WebGL, Linux64 production release |

The `unity-build.yml` orchestrator report job also covers iOS when called via `unity-build.yml`. `unity-build-ios.yml` called directly (e.g. from your project workflow) sends its own notification.

---

## Behavior by Build Status

| Status | Embed colour | Emoji | Description |
|---|---|---|---|
| `success` | Green (`#2ECC71`) | ✅ | Build completed successfully |
| `failure` | Red (`#E74C3C`) | ❌ | Build failed — includes platform, version, and a link to the failed run |
| `cancelled` | Grey (`#9B9B9B`) | ⚠️ | Workflow was cancelled mid-run |

Any unrecognised status value is treated as `cancelled` (grey / ⚠️).

---

## No-op When Unset

If `DISCORD_WEBHOOK_URL` is empty or unset the action writes a `::notice::` log line and exits with code `0`. The build outcome is unchanged. No network call is made.

This means:
- Projects that don't configure Discord work with zero changes.
- Removing the secret disables notifications without touching workflow YAML.
- CI forks (e.g. open-source PRs) that don't have the secret skip notifications automatically.

---

## Embed Structure

The `discord-upload-build` composite action (`actions/discord-upload-build/action.yml`)
builds a Discord embed with up to four logical sections.

### Title and description

- **Title:** `<emoji> Unity Build <Status> — <environment>` — links to the GitHub Actions run URL.
- **Description:** `commit-sha` (7 chars, hyperlinked to commit page) · first line of commit message (≤120 chars) · commit author name.

### Inline fields (3 per row)

| Group | Field | Value |
|---|---|---|
| 1 | Branch | Branch name (≤50 chars) |
| 1 | Flow | `flow-type` from `resolve-config` (e.g. `push-release`, `manual`) |
| 1 | Run | `#<run-number>` |
| 2 | Triggered by | `github.actor` |
| 2 | Event | `github.event_name` (`push`, `workflow_dispatch`, …) |
| 2 | Duration | Human-readable build duration (e.g. `4m 23s`) |
| 3 | Unity | Unity version used (e.g. `6000.0.26f1`) |
| 3 | Tests | Result emoji + result string (`✅ success`, `❌ failure`, `⏭️ skipped`) |
| 3 | Diagnostics | `❌ N errors · ⚠️ N warnings` (omitted when both counts are absent) |

### 🏗️ Build Info block (full-width field)

Shown only when at least one build-metadata value is provided. Lines are omitted individually when their values are empty.

| Line | Content |
|---|---|
| 1 | `**Product:** <name>  •  **Version:** <app-version>` |
| 2 | `**Bundle ID:** <bundle-id>` |
| 3 | `**Backend:** <IL2CPP\|Mono>  •  **Arch:** <ARM64\|…>  •  **Orientation:** <Portrait\|…>  •  **Output:** APK` or `AAB (App Bundle)` |
| 4 | `**Defines:** N symbols` (omitted when 0 or absent) |
| 5 | `**Store:** [Google Play](<url>)` (omitted when `store-link` is empty) |

The **Output** line on row 3 is driven by the `android-export-type` resolver output (`apk` or `aab`). On `push → release-*` flows the resolver always emits `aab`; on all other flows it defaults to `apk` unless overridden by the `android-export` dispatch input.

### 📦 Platforms block (full-width field)

One row per platform in the order: Android, WebGL, Linux64, LinuxServer, Windows64, iOS, Addressables. Skipped platforms are shown with ⏭️.

Each row format:

```
<emoji> **<Platform>**: `<result>` — <size> MB · [⬇️ download](<url>) · ⚠️ N · ❌ N · [📄 logs](<url>)
```

| Element | Present when |
|---|---|
| Result emoji | Always — ✅ success, ❌ failure, ⏭️ skipped, 🚫 cancelled, ⛔ blocked |
| Size + download link | `result == success` and a binary artifact ID is available |
| `(attached)` | File was attached to the Discord message (under size threshold) |
| `([linked])` | File exceeded threshold — links to the Actions run instead |
| ⚠️ / ❌ counts | Platform diagnostic data is present |
| 📄 logs link | Logs artifact ID is available |

---

## File Attachment vs Link Behaviour

The action zips each successful platform's `unity-build-<Platform>/` artifact directory and attaches it directly to the Discord message when the **cumulative** size of all attachments is under the threshold (default **24 MB** — safely under the Discord 25 MB non-boosted server limit).

Attachment order: Android → WebGL → Linux64 → LinuxServer → Windows64 → iOS → Addressables.

When adding a platform's zip would push the cumulative total over the threshold, that platform (and all subsequent ones) switch to a `[⬇️ download](<GitHub artifact URL>)` link instead of a file attachment. The download link requires the viewer to be logged in to GitHub.

The threshold is configurable via the `attach-size-threshold-mb` action input (default `24`).

---

## Example Embed

```
✅ Unity Build Success — production
`a3f7c9d` Initial release build — cuongnd

Branch        develop      Flow    push-release    Run   #42
Triggered by  cuongnd      Event   push            Duration  6m 14s
Unity         6000.0.26f1  Tests   ✅ success      Diagnostics  ❌ 0 errors · ⚠️ 3 warnings

🏗️ Build Info
Product: NDC Game  •  Version: 1.0.0
Bundle ID: com.archergame.ndc
Backend: IL2CPP  •  Arch: ARM64  •  Orientation: Portrait  •  Output: AAB (App Bundle)
Defines: 12 symbols
Store: [Google Play](https://play.google.com/store/apps/details?id=com.archergame.ndc)

📦 Platforms
✅ **Android**: `success` — 48 MB · [⬇️ download](...) · ⚠️ 3 · ❌ 0 · [📄 logs](...)
✅ **WebGL**: `success` — 12 MB (attached) · ⚠️ 0 · ❌ 0 · [📄 logs](...)
⏭️ **Linux64**: `skipped`
⏭️ **LinuxServer**: `skipped`
⏭️ **iOS**: `skipped`
```

---

## Security Notes

1. **Webhook URL never logged.** The action calls `set +x` before any reference to the URL and calls `::add-mask::` to add the value to GitHub's log masker. Even if the value somehow leaks to output, Actions redacts it.
2. **No third-party action.** All HTTP calls go through `curl` in a `bash` step — no code from an external action repository is executed.
3. **Failure is non-fatal.** `curl` errors (network timeouts, non-2xx responses) log a `::warning::` and exit `0`. A Discord outage cannot block a release.
4. **Payload validated before sending.** The JSON payload is validated with `python3 -c "import json,sys; json.load(sys.stdin)"` before the `curl` call. If validation fails the notification is skipped (not a pipeline failure).
5. **No sensitive build values in payload.** The embed contains version strings, commit SHAs, actor names, and platform names — nothing from signing secrets or license files.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| No notification, no warning | `DISCORD_WEBHOOK_URL` not set in repo secrets | Add the secret (see above) |
| `::warning:: Discord notify: unexpected HTTP 401` | Invalid or revoked webhook | Regenerate the webhook in Discord and update the secret |
| `::warning:: Discord notify: unexpected HTTP 404` | Webhook deleted in Discord | Recreate the webhook |
| `::warning:: Discord notify: curl failed` | Network timeout / runner DNS issue | Transient — retry or check runner network; build is unaffected |
| Embed posted but fields are empty | `github.*` context not available (unusual) | File an issue with the workflow name and trigger |
