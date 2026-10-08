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
