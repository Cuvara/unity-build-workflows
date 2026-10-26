# iOS Release Pipeline

This document describes the iOS release workflow: promoting a Build / Release IPA to TestFlight and the App Store through `pipeline-ios-release.yml`. The tag-triggered `unity-release.yml` flow it used to describe was removed in 7.0.0 ([MIGRATION_V7.md](MIGRATION_V7.md)).

---

## Release Flow Overview

A release is two runs. **Build / Release** (`templates/consumer-11-build-release.yml` →
`unity-pipeline.yml`) builds, signs and exports the IPA and records it in the Release Set.
**Release / iOS** (`templates/consumer-21-release-ios.yml` → `pipeline-ios-release.yml`)
builds nothing: it promotes that exact IPA, pinned by `source-run-id` + `artifact-name`.

```
pipeline-ios-release.yml
  │
  ├── 04 Verify Release Identity   IPA + release manifest from the source run, checksum
  ├── 04 Validate IPA
  ├── 04 Release Notes
  ├── 05 Publish — TestFlight Internal   environment: internal-testing
  ├── 06 Release — External Testing      environment: external-testing
  ├── 06 Release — Production            environment: production (App Store review)
  └── 07 Report                          step summary + Discord (production thread)
```

Every job that holds the IPA re-verifies it against the release manifest
(`.github/actions/verify-release-artifact`). Details: [RELEASE_FLOW.md](RELEASE_FLOW.md),
[PIPELINE_ARCHITECTURE.md](PIPELINE_ARCHITECTURE.md).

---

## Binary source: GitHub artifact or App Store Connect build

`pipeline-ios-release.yml` takes the build it promotes from one of two places, chosen by
`binary-source`:

| `binary-source` | Build comes from | Needs | Use when |
|---|---|---|---|
| `github` (default) | The signed IPA artifact of a Build / Release run, re-verified against the Release Set | `source-run-id`, `artifact-name` | `ARTIFACT_STORAGE=github` |
| `app-store-connect` | A build already on App Store Connect, picked by version + build number. No IPA is downloaded or uploaded | `version-string`, `build-number`, `app-id` | `ARTIFACT_STORAGE=firebase`: Build / Release uploaded straight to TestFlight and left no GitHub artifact |

Inputs added in 7.1.0 (all optional; existing callers are unchanged):

| Input | Default | Meaning |
|---|---|---|
| `binary-source` | `github` | `github` or `app-store-connect`. Any other value fails the run in `verify-artifact`. |
| `build-number` | `''` | `CFBundleVersion` of the build, e.g. `474`. Required for `app-store-connect`. `version-string` stays the marketing version (`1.2.0`). |
| `testflight-groups` | `''` | External TestFlight group names, comma-separated. Required for the external phase of `app-store-connect`. |

`source-run-id` and `artifact-name` are no longer `required` at the call boundary (a workflow
cannot require an input conditionally); the `github` source fails in its first job, "Check
inputs", when either is empty.

With `app-store-connect` the graph is the same, with the IPA-bound jobs swapped out:

```
  ├── 04 Verify App Store Connect Build   version + build number exist and are VALID (replaces
  │                                       Verify Release Identity and Validate IPA)
  ├── 04 Release Notes
  ├── 05 Publish — TestFlight Internal    uploads nothing; re-verifies the build is processed
  ├── 06 Release — External Testing       distributes the build to testflight-groups and submits
  │                                       it for Beta App Review (What to Test = release notes)
  ├── 06 Release — Production             submits the build for App Store review
  └── 07 Report
```

- **Verify** fails with the exact `version X build N (app ID)` it looked for when the build is
  missing, still processing after 10 minutes, `FAILED`, `INVALID` or expired, and lists the
  build numbers that do exist for that version.
- The GitHub Environments (`internal-testing`, `external-testing`, `production`), `start-phase`,
  `dry-run` and `phased-release` behave identically for both sources. `dry-run` still runs the
  verification.
- Production uses the same `deliver` submission as the `github` source
  (`skip_binary_upload`, `build_number`, `app_version`), so metadata and phased-release
  behaviour match.
- Auth is the same App Store Connect API key (team or individual) as everywhere else.
- The integrity guarantee differs: `github` proves the bytes are the Release Set's (SHA-256);
  `app-store-connect` can only prove App Store Connect holds a processed build with that version
  and number. Use the `github` source if you need the former.

The consumer template `templates/consumer-21-release-ios.yml` exposes a `binary-source` choice
that defaults to `auto`: dispatch input, then the `ARTIFACT_STORAGE` repository variable
(`firebase` → `app-store-connect`), then `github`.

Example caller (version 1.2.0, build 474):

```yaml
jobs:
  release:
    uses: Cuvara/unity-build-workflows/.github/workflows/pipeline-ios-release.yml@v7
    secrets: inherit
    with:
      toolkit-ref: 'v7'
      binary-source: app-store-connect
      build-version: '1.2.0'
      version-string: '1.2.0'
      build-number: '474'
      app-id: '1234567890'
      testflight-groups: 'Beta Testers'   # external phase
      start-phase: external               # or internal / production
```

---

## GitHub Environment Setup

Each store phase is a job behind its own GitHub Environment — `internal-testing`,
`external-testing`, `production`. Add required reviewers to the ones that need a human,
and scope the App Store Connect secrets there:

- `APP_STORE_CONNECT_KEY_ID`
- `APP_STORE_CONNECT_ISSUER_ID`
- `APP_STORE_CONNECT_PRIVATE_KEY`

See [GITHUB_ENVIRONMENTS.md](GITHUB_ENVIRONMENTS.md).

---

## Versions

The marketing version (`CFBundleShortVersionString`) and build number (`CFBundleVersion`)
are set by Build / Release (Player Settings version, build number from the run number plus
`BUILD_NUMBER_OFFSET*`). The promotion reads them from the IPA itself and checks them against
the Release Set; it never rebuilds or re-signs.

---

## Rollback

There is no automated rollback for TestFlight builds. To revert:

1. In App Store Connect → TestFlight → your app → stop distribution of the bad build
2. Promote the previous good build again: run Release / iOS with that Build / Release
   run's `source-run-id` and `artifact-name` (while its artifacts are retained).

For App Store submissions (not TestFlight), use App Store Connect to reject a pending review.
