# Changelog

All notable changes to `unity-build-workflows` are documented here.

This project adheres to [Semantic Versioning](https://semver.org/) and [Conventional Commits](https://www.conventionalcommits.org/).

The public API is the set of reusable workflow inputs/outputs documented in [docs/BUILD_CONFIG.md](docs/BUILD_CONFIG.md). Changes to that interface that require consumer updates are marked as **BREAKING**.

---

## [6.0.0] — 2026-09-14

### Changed

- **BREAKING: Artifact naming includes product name, version and build number.**
  Build artifacts are now named
  `{product}_{version}_{build}_{environment}_{platform}_{type}` (e.g.
  `NDCGame_1.4.2_42_release_android_aab`) instead of the old
  `{environment}-{platform}-{type}` pattern. The product name is read from
  Unity's `ProjectSettings.asset` and sanitized for filenames (original casing
  preserved). Consumer release templates now require the artifact name as input
  (no static default) because the name varies per build.

### Fixed

- **Duplicate `type:` key in iOS release consumer template** caused actionlint
  failure.
- **Artifact download pattern in notify-discord and release-manifest** updated
  from `{build-type}-*` to `*_{build-type}_*` to match the new naming.

---

## [5.8.0] — 2026-09-14

### Added

- **Discord thread/forum-post routing for `discord-notify`.** The `discord-notify`
  composite action now reads `DISCORD_THREAD_ID` from the environment and appends
  `?thread_id=<id>` to the webhook URL when set. Existing webhook-only
  configurations require zero changes — the variable is optional and the action
  falls back to the channel root when it is absent. Malformed thread IDs produce
  a `::warning::` and fall back safely. The four workflows that call
  `discord-notify` (`unity-build`, `unity-build-ios`, `unity-release`,
  `unity-release-ios`) now pass `vars.DISCORD_THREAD_ID` through their job
  environment. Documentation in `DISCORD_NOTIFICATIONS.md` previously described
  this variable but the `discord-notify` action did not implement it — the
  implementation and documentation are now consistent.

### Changed

- **`discord-upload-build` URL construction hardened.** The `?thread_id=` append
  now uses `&thread_id=` when the webhook URL already contains a query string,
  matching the same logic added to `discord-notify`.

- **iOS signing is now matrix-driven.** The `sign-ios` job in `unity-pipeline.yml`
  is now controlled by a dynamically generated `sign-matrix` instead of a static
  boolean gate. The matrix is populated only when iOS is selected AND the build
  type is release; for every other platform the matrix is empty. This makes the
  pipeline structurally incapable of scheduling iOS signing operations for a
  non-iOS build. Downstream job dependencies (`validate-artifact`,
  `release-manifest`) are unchanged — the job ID remains `sign-ios`.

---

## [5.7.0] — 2026-09-14

### Fixed

- **The Addressables stage shipped no bundles, and said `size unknown` instead of
  saying so.** Discord reported `✅ Addressables: success — size unknown · run`
  on every content build. Three defects stacked up, each invisible on its own:

  1. `artifact_manifest.py` matched `catalog*.json` only. Addressables 2.x writes
     a **binary** `catalog.bin` unless "Build Remote Catalog" is on, so the glob
     matched nothing and the manifest came out empty — `artifactSizeBytes=0`,
     which Discord renders as `size unknown`.
  2. The manifest and the upload both looked only in `ServerData`. That is the
     **remote** build path; the Unity default is a **local** group, which builds
     to `Library/com.unity.addressables/aa`. A project with no remote group
     therefore uploaded an artifact containing the settings folder and nothing
     else — 16 KB of `.asset` files, no bundles — and still reported success.
     (Observed on IndieRPGMMOAdventure run 34833428560.) Both trees are now
     searched and uploaded, remote first so projects that use it are unaffected.
  3. For `ADDRESSABLES`, discovery returned a single bundle, whose size would
     have been reported as the size of the whole content build. Content builds
     are trees: the containing directory is returned and the tree is summed.

  `--search-root` is now repeatable; roots are tried in order and the first with
  a match wins. A single `--search-root` behaves exactly as before.

- **A content build with zero bundles now says `no content built`** rather than
  `size unknown`. The two are different facts and only one of them is a defect;
  they had been rendering identically.

---

## [5.6.0] — 2026-09-14

### Fixed

- **Every test run logged two `Resource not accessible by integration` errors.**
  `game-ci/unity-test-runner` posts its results as a check run when it is given a
  token, and it was given one unconditionally. The default `GITHUB_TOKEN` is
  **read-only** unless a repository says otherwise — `read` on this org and all
  three repositories — so the attempt failed on every run, once per test mode,
  for as long as the workflow has existed.

  It is now off by default and switched on with the `POST_TEST_CHECK_RUN`
  repository variable.

  Granting the permission is not free, which is why this is opt-in rather than a
  `permissions:` block added on everyone's behalf: an explicit `permissions:`
  block **replaces** the default set, so a caller that lists only `checks: write`
  also revokes `packages: read` — and the docker lane can no longer pull its
  image from GHCR. A consumer turning this on needs the whole set:

      permissions:
        contents: read
        packages: read
        actions:  read
        checks:   write

  Nothing is lost by leaving it off: the job already fails on a failed test, the
  failing test's name and file are in the job log, and the NUnit XML is uploaded
  as `unity-tests-<mode>`.

### Added

- **Unity's errors land on the commit that caused them.** A build cannot
  summarise its own Unity output on the game-ci lane: Unity streams to the job
  console and writes no `Editor.log`, and `GITHUB_TOKEN` cannot download a job
  log while the run is in progress — the endpoint 404s until the whole run
  completes. Both halves were proven the hard way.

  `templates/consumer-09-build-diagnostics.yml` runs afterwards on
  `workflow_run`, when the logs are readable, and posts a **check run** against
  the build's commit. A compiler error then shows on the line that caused it,
  in the pull request, instead of a hundred thousand lines down a console
  nobody opens. The check is `neutral`: the build already reported whether it
  passed, and a second red mark on the same commit for the same reason helps
  nobody.

### Fixed

- **The log parser could not read a job log at all.** Every line of an
  Actions job log carries an ISO timestamp, and some carry ANSI colour from the
  runner's echo — both sit in front of the text the patterns anchor to, so a
  compiler error never matched. Silently: a parser that finds nothing looks
  exactly like a clean build. Caught by a test written before the first run.
---

## [5.4.0] — 2026-09-14

### Fixed

- **A release Android build on a self-hosted runner produced an APK named
  `release-android-aab`.** Only the docker lane exported `ANDROID_APP_BUNDLE`.
  `PlayerBuilder.Build` — the default `-executeMethod` on both local lanes —
  reads it to choose the artifact, and **defaults to APK when it is absent**, so
  the wrong thing came out wearing the right label with nothing said. Both local
  lanes now export it.

- **The macOS lane mapped three platforms and hard-errored on the rest.** A Mac
  with the modules installed builds the standalone targets too; only the mapping
  said otherwise, so a project with `Windows64` in `RELEASE_BUILD_PLATFORMS`
  broke the moment its runner became a Mac. `Windows64`, `Linux64` and
  `LinuxServer` now map to the same targets the docker resolver uses, including
  `-standaloneBuildSubtarget Server` — dropping that flag builds a desktop player
  under a server artifact's name.

### Added

- `tests/test_local_lane_contract.py` — what the self-hosted lanes hand to
  `PlayerBuilder`. Anything a lane does not export, the builder cannot know, and
  PlayerBuilder's defaults are silent: a missing variable yields a plausible
  wrong artifact rather than an error.

---

## [5.5.0] — 2026-09-14

### Added

- **`RUNNER_LABELS=none`**, meaning what an empty value would mean. 5.3.0 made an
  absent or empty `RUNNER_LABELS` resolve to `github-hosted` + `docker`, which
  works — but GitHub will not *store* an empty value:

      422  Variable value cannot be empty.

  So the only way to say "no machine" was to delete the variable, and a deleted
  variable is an invisible switch: somebody has to already know it exists to use
  it. `none` (also `off`, `disabled`, any case, surrounding space ignored) keeps
  it on the settings page saying what it does.

  Only those exact words. A runner labelled `none-of-your-business` is still a
  machine.

---

## [5.3.0] — 2026-09-14

### Changed

- **Naming no machine means using GitHub's.** `RUNNER_LABELS` is the switch: when
  it is empty — and no per-OS label is set either — the pipeline resolves to
  `github-hosted` + `docker`, overriding `RUNNER_TYPE` and `BUILD_ENGINE` and
  saying so in a warning.

  `self-hosted` with no labels used to resolve to `self-hosted,<os>`, labels no
  runner carries unless one happens to be registered with exactly them. GitHub
  does not fail a job whose labels match nothing; it **queues it forever**, with
  no error and no timeout. A build on GitHub's runners is a worse answer than the
  one configured and a much better one than a run that never starts.

  Clearing `RUNNER_LABELS` is therefore the way back to GitHub-hosted, which is
  what makes it a switch rather than one setting among three.

  A machine counts as named by `RUNNER_LABELS` **or** by any of
  `RUNNER_LINUX_LABEL` / `RUNNER_WINDOWS_LABEL` / `RUNNER_MACOS_LABEL`, so the
  per-platform routing added in 5.2.0 still works without a global label.

  `github-hosted` + `local` is still a hard error, checked on what was asked for
  rather than on what the fallback leaves behind — repairing it silently would
  remove the one message that says the configuration cannot mean what it says.

### Fixed

- **iOS signing still went to a Windows machine.** 5.2.0 routed the build matrix
  per platform and left stage 03b — the IPA signing job — reading the global
  `runner-labels`, whose self-hosted default was `self-hosted,windows`. The build
  landed on the right machine and the signing did not, and that release said the
  defect was closed. It was closed for the build half only. Stage 03b now takes
  `runner-labels-ios`.

- **The global label default named Windows for every self-hosted project**,
  whatever machines it had. It now follows the Linux label, which is where most
  of the pipeline runs.

---

## [5.2.1] — 2026-09-14

### Fixed

- **`runner-labels` rejected the form its own description promised.** The
  dispatch input is described as *"Runner labels as a JSON array"*, and passing
  one produced labels of `["self-hosted"` and `"macOS"]` — which match no runner,
  so the job **queued forever instead of failing**. Only the CSV form worked.

  Either the description or the parser was wrong. The description is the one
  people read before typing, so the parser now accepts both, with or without
  spaces:

  ```
  self-hosted,macOS
  ["self-hosted","macOS"]
  self-hosted macOS
  [ "self-hosted" , "macOS" ]
  ```

---

## [5.2.0] — 2026-09-14

### Fixed

- **Each platform now builds on a runner its OS actually has.** Stage 03 handed
  one `runner-labels` list to every leg of the build matrix and stage 03b fell
  back to the same one, so a project on a self-hosted Windows runner sent its
  **iOS signing job to the Windows box** — and GitHub does not fail a job whose
  labels match no runner, it queues it forever.

  The three per-OS variables that fix this (`RUNNER_LINUX_LABEL`,
  `RUNNER_WINDOWS_LABEL`, `RUNNER_MACOS_LABEL`) were already resolved, emitted
  and re-exported as pipeline outputs, and read by nothing. They are now what
  the matrix routes on. Stage 2 of
  [ADR 004](docs/adr/004-runner-selection.md).

- **The per-OS defaults disagreed with each other.** Linux defaulted to
  `ubuntu-latest`, a GitHub-hosted label; windows and macos defaulted to
  `self-hosted-windows` / `self-hosted-macos`, which are self-hosted label
  *names* that no GitHub-hosted runner carries. A github-hosted project asking
  for Windows therefore got a label set matching no runner. The default now
  follows `RUNNER_TYPE`.

- **Unity tests and the Addressables build no longer ride the build matrix's
  labels.** They always run in the Linux container, whatever the matrix is
  doing, and took `runner-labels-linux` for it.

### Note

Labels follow the **executor**, not the target platform's OS. Under
`BUILD_ENGINE=docker` every platform but iOS builds in the Linux container —
including `Windows64`, which Unity cross-compiles from there. Only
`BUILD_ENGINE=local` routes a target to a runner of its own OS. For a
github-hosted docker project, which is what both consumer repositories run, the
only thing this release moves is iOS: from a Linux runner with no Xcode on it to
a macOS one.

`RUNNER_TYPE` and `BUILD_ENGINE` remain single switches for the whole run, so an
org with both GitHub-hosted runners and its own machine still cannot split the
work between them. That gap, and the per-platform executor that closes it, are
stage 2b in [ADR 004](docs/adr/004-runner-selection.md).

### Changed

- An explicit `RUNNER_LABELS` still overrides everything, for the project whose
  one runner does every platform.
- `scripts/common/matrix_runner_labels.py` builds a matrix row's `runs-on`. It
  is a script rather than a `jq` one-liner because the escaping has to survive
  two JSON levels, and getting it wrong kills the run at expression-evaluation
  time — before any step, with no log to read. A script can be run in a test;
  that one is `test_the_row_survives_both_json_levels`.

---

## [5.1.0] — 2026-09-14

### Added

- **A runner plan, so the machine a job lands on stops being a guess.** Eight
  settings feed one decision — `RUNNER_TYPE`, `BUILD_ENGINE`, `RUNNER_LABELS`,
  the three per-OS label variables, the legacy `RUNNER_DEFAULT_MODE` family and
  the derived `runner-mode` — and nothing printed the answer. Asking "why did
  this job land on that machine?" meant reading the resolver.

  `resolve_build_flow.sh` now emits `runner-plan`: one row per platform that
  will actually build, with `runsOn`, `engine`, `runnerType`, **the tier that
  decided the labels**, and a note. Stage 01 renders it as a table in the run
  summary and re-exports it as a pipeline output.

  **No routing changed.** Every row shares one `runs-on`, because that is what
  the pipeline does — stage 03 passes a single label list to every leg of the
  matrix and stage 03b falls back to it. That is the finding, not a rendering
  bug, and a test pins it while naming itself as the one to update when routing
  becomes per-platform.

  Two things the plan is careful about:

  - **The CI lane plans nothing.** `platform: None` yields an empty plan. The
    resolver's own platform flags still read `true` there, because on a push it
    takes them from `*_BUILD_PLATFORMS` and never looks at the input; the
    honouring happens later, where the matrix is built. Rows for builds that
    will not run would be a confident answer to the wrong question.
  - **iOS on non-macOS labels is reported.** The self-hosted default is
    `self-hosted,windows` whatever the OS, so an iOS build lands on a Windows
    box — and GitHub does not fail a job whose labels match no runner, it queues
    it forever. Reported as a note and a warning, not yet enforced: enforcement
    is breaking, and a warning that ships today beats a gate that ships next
    month.

  The full audit — including a "single source of truth" that
  `unity-pipeline.yml` never calls, and three per-OS label variables that are
  resolved, exported and read by nothing — is in
  [docs/adr/004-runner-selection.md](docs/adr/004-runner-selection.md), with the
  staged plan for fixing the routing itself.

---

## [5.0.0] — 2026-09-14

### Added

- **BREAKING — stage 01 fails when `BuildConfig` and `ProjectSettings.asset`
  disagree about the app's identity.** A consumer shipped Android builds for
  weeks stamped `com.UnityTechnologies.com.unity.template.urpblank` at version
  `0.4.2`, while its `BuildConfig/base.json` said `com.cuvara.indierpgmmo` and
  `0.5.0` and its changelog said `0.5.0`. Every build was green.

  Two things kept it invisible. **On the Docker / game-ci lane `BuildConfig`
  never reaches PlayerSettings** — `Company.BuildPipeline.BuildCommand.Execute`
  runs only when `build-method` is set, and game-ci supplies its own builder,
  which stamps whatever `ProjectSettings.asset` holds. And nothing compared the
  two, so a config that was both inert and wrong looked exactly like one that
  was working.

  `scripts/common/check_identity_drift.py` compares `companyName`,
  `bundleVersion` and `applicationIdentifier.Android` against the resolved
  config and fails the build. Set the `IDENTITY_DRIFT` repository variable to
  `warn` or `off` to soften it.

  **What it does not do:** it catches *disagreement*, not wrongness. Against the
  real case it would have caught the version and not the identifier, because the
  production config and `ProjectSettings.asset` agreed with each other on the
  template id — both wrong, identically. Two sources agreeing is not evidence
  that either is right.

  A suffixed identifier (`com.acme.game.dev` against `com.acme.game`) is treated
  as a deliberate variant rather than drift — that is how a QA build installs
  alongside production, and a gate that reddens every development build is a
  gate that gets switched off. It is reported as a notice, which is worth having:
  on the Docker lane the suffix is not applied either, so the development build
  installs under the base identifier.

  `productName` is excluded for the same reason — `… [DEV]` is a legitimate
  overlay difference.

  **Migrating:** run the check locally before upgrading —

      python3 scripts/common/check_identity_drift.py --project-path . --environment production

  A consumer whose values already agree needs no change. `@v4` is unaffected.

- The entry templates pin `@v5`. `test_entry_points_pin_the_current_major`, added in
  4.2.0 after 4.0.0 shipped templates pinned to the previous engine, caught this one
  before it left the branch.

---

## [4.2.0] — 2026-09-14

### Fixed

- **4.0.0 renamed every node and shipped the templates still pinned to `@v3`.**
  `templates/consumer-*.yml` carried the new caller name (`name: Dev`) against the
  old engine, so a consumer copying the reference files got `Dev / 03 / Android /
  APK` — half of the rename, from the files that are supposed to *be* the
  reference. All eight now pin `@v4`, `uses:` and `toolkit-ref:` together.

- **The test that should have caught it asked the wrong question.**
  `test_all_entry_points_agree_on_the_engine_ref` asserts the entry points agree,
  and all eight agreed — on the stale ref. Agreement is not currency.
  `test_entry_points_pin_the_current_major` now reads the major out of
  `CHANGELOG.md` and fails when the templates lag it. Verified against the stale
  tree: it fails there and passes here.

---

## [4.1.0] — 2026-09-14

### Fixed

- **A 67 MB APK was posted to Discord as `0 MB (linked)`, with no download
  link.** Three faults in a row, each invisible on its own.

  The per-platform diagnostics step skipped any platform whose `Editor.log` it
  could not find:

      LOG=$(find ./artifacts -path "*${ART}-logs*Editor.log" ...)
      [ -z "${LOG}" ] && continue

  The Docker / game-ci lane streams Unity to the job console and writes no
  `Editor.log` at all, so that `find` comes back empty on *every* successful
  build. `continue` then threw away the platform's artifact **name** and
  artifact **id** as well as its counters. The Discord action fell back to the
  pre-build-type directory `./artifacts/unity-build-Android`, which has not
  existed since artifacts gained a build-type prefix, measured nothing, and
  reported `0`. The row is now emitted either way; only the counters depend on
  a log.

  The size itself no longer comes from zipping a downloaded copy. Stage 07
  already carries `artifactSizeBytes`, measured by the build that produced the
  artifact, and it is passed through as `platform-summary`. Zipping still
  happens — it decides whether the file is small enough to attach, which is a
  question about the upload, not about the build.

  A size nobody established now reads `size unknown` rather than `0 MB`. Zero
  is a measurement; the absence of one is not, and `success — 0 MB` read as a
  build that produced nothing.

- **`0 errors · 0 warnings` on a run where nothing was counted.** Same root
  cause: no `Editor.log`, nothing parsed, and the totals defaulted to zero. The
  pipeline now reports empty totals when no platform yielded a readable log,
  and the embed omits the Diagnostics field rather than issuing a clean bill of
  health nobody signed.

### Changed

- **The Discord embed leads with the artifact.** The download link sat last,
  under ten inline fields of run metadata, in a notification whose purpose is to
  get a build onto somebody's device. `📦 Platforms` is now `📦 Artifacts` and
  comes first.

  `Triggered by`, `Event` and `Flow` were three fields answering one question
  and fold into one `Trigger`; `flow-type` survives only when it says something
  the event does not, so `push-develop` shows and `manual` beside
  `workflow_dispatch` does not. `Configuration` repeated the environment already
  in the title and is gone. `Run` carries a link instead of a bare number, and
  `Branch` links to the branch.

---

## [4.0.0] — 2026-09-14

### Changed

- **BREAKING — node names no longer carry the stage number.** `03 / Android /
  APK` is now `Android / APK`, across `unity-pipeline.yml` and all five release
  pipelines (43 job names). The consumer template for development builds also
  shortens its caller job from `Development` to `Dev`, so the node a person
  actually reads goes from

      Development / 04 / Android / Validate APK

  to

      Dev / Android / Validate APK

  GitHub prefixes every called job with the *caller's* job name and offers no
  way to suppress it, so the lane is always there. The stage number was
  competing with the platform and the artifact type for the width left over —
  and it was the segment carrying the least, because the graph's edges already
  say what runs after what.

  **Stage order did not live in the names and still does not.** It lives in
  `PIPELINE_STAGES`, declared once per workflow, and each job announces its
  position through `current:`. The progress ladder renders from that and is
  unchanged.

  **What a consumer must do.** If any of these names is a *required status
  check* on a protected branch, update it in the same change as the version
  bump. A renamed required context is never reported again, so the branch
  protection does not fail loudly — it blocks every pull request from then on,
  with the check sitting in "Expected" forever. Check
  `Settings → Branches → <branch> → Require status checks`, or:

      gh api repos/<OWNER>/<REPO>/branches/<BRANCH>/protection/required_status_checks --jq '.contexts[]'

  **Why this is a major rather than a 3.3.0.** `@v3` is a floating tag that
  gets repointed at each 3.x release. Shipping this as a minor would rename
  every consumer's status checks the moment the tag moved, with no action on
  their part and no error to read. `@v3` therefore stays at 3.2.0 and this
  goes out as `@v4`, so the rename happens when a consumer asks for it.

- `tests/test_pipeline_stages.py` no longer derives stage coverage from
  `name[:2]`. Reading structure out of a display label meant shortening a label
  looked, to the test, exactly like deleting a stage; it now reads the `current:`
  values that feed the progress ladder, which is where stage identity is. The
  contract test that required an `NN / ` prefix is inverted: it now fails if one
  comes back.

Nothing yet.

---

## [3.2.0] — 2026-09-14

### Added

- **Build delivery: a download link that works for people without a GitHub
  account.** An Actions artifact URL 404s for anyone not signed in with
  repository access — on a public repo too — so the link in a build
  notification was unusable by exactly the testers and artists it was for.
  `BUILD_DELIVERY` chooses where a finished build is copied:

  | Value | Where |
  |---|---|
  | `none` | default — nothing published, nothing changes |
  | `r2` | Cloudflare R2 over the S3 API. Egress is free, which is the reason to prefer it |
  | `local` | a directory on your self-hosted runner, served by your own web server |

  Discord then links to that URL instead of the artifact. Objects are written
  with `Content-Disposition: attachment` so the browser downloads rather than
  displays, and the key is `<branch>/<run>/<sha>/<file>` so a public bucket is
  not a directory listing.

  SigV4 is signed with `hmac`/`hashlib` rather than shelling out to `aws-cli`,
  which is absent on most self-hosted runners; the implementation is checked
  against AWS's published test vector, because a signing bug produces a 403
  that reads like a credentials problem.

  Delivery never fails a build — a green build that could not be copied
  somewhere is still green. Misconfiguration does fail: asking for `r2`
  without credentials stops the step, because the alternative is a pipeline
  that looks healthy and quietly delivers nothing. See
  `docs/BUILD_DELIVERY.md`.

---

## [3.1.3] — 2026-09-13

### Removed

- **The API-based log summariser, which could not work.** Stage 07 fetched each
  build job's log through the API to summarise it. `GITHUB_TOKEN` cannot
  download a job's log while the run is still in progress — the endpoint 404s
  until the run completes — so every build ended with three
  "Could not fetch the log" warnings and no summary. Proven on two clean runs.
  Removed rather than left in place: a mechanism that always warns teaches
  people to ignore warnings.

  The file-based summariser stays and works wherever Unity writes a log — the
  native and self-hosted lanes, the addressables pre-pass, licence activation.
  On the game-ci docker lane Unity streams to the job console and writes no
  file, and the summary now says exactly that instead of reporting "no errors".

---

## [3.1.2] — 2026-09-13

### Fixed

- **The diagnostics summary reported "no build jobs" on every run.** Reading
  another job's log is an `actions: read` operation, and the default workflow
  token does not have it — the API returned 403, the job list came back empty,
  and the message read as though there had been nothing to summarise. The
  report job declares the permission, the entry templates grant it (a called
  workflow's permissions are capped by its caller's, so declaring it in one
  place alone achieves nothing), and a failure now says what it probably is
  instead of looking like a quiet success.

---

## [3.1.1] — 2026-09-13

### Fixed

- **The log summary read the wrong file, and said "clean" about it.** On the
  game-ci lane Unity streams its log to the job console rather than writing
  `Editor.log`, so the logs artifact contains only a shader-compiler log —
  nothing to parse. The summariser found that file, found no errors in it, and
  reported "No errors or warnings found in the Editor log", which is a
  different statement from "I could not find the log" and the reason a summary
  earns the right to be ignored. It now says which files it scanned and warns
  when the Editor log is absent.
- **Stage 07 fetches each build job's log through the API** and summarises
  that, so the diagnostics come from where Unity actually put them. One place,
  every lane, no per-platform plumbing.

---

## [3.1.0] — 2026-09-13

### Added

- **Unity errors and warnings reach the run page.** A failed build said
  "Process completed with exit code 1" and left the reason inside an 8 MB
  `Editor.log` in an artifact — one `error CS0103` line among a hundred
  thousand, findable only by downloading a zip and searching it, while the run
  page said nothing. `scripts/common/summarise_unity_log.py` now parses the log
  and emits GitHub **annotations** (inline on the file and line for compiler
  diagnostics), a job-summary table, and a JSON report. It runs on success too:
  a build that passed with two hundred new warnings is worth knowing about.
  Matching is deliberately narrow — Unity logs say "error" in hundreds of
  benign places, and noisy annotations are annotations people stop reading.
- **Stage 07 reports error and warning counts per platform**, plus the first
  error message, so a red run says *why* without opening anything.

### Fixed

- **Discord never attached a build, whatever its size.** The action looked for
  `./artifacts/unity-build-<Platform>/`, the naming from before artifacts
  gained a build-type prefix. No directory, no zip, no attachment, no error —
  every platform silently fell back to a link, including builds small enough to
  post as a file. The artifact name now travels with the per-platform
  diagnostics; the old path stays as a fallback for callers on an older
  pipeline.
- **The build lane exposes `artifact-url`** — the `/artifacts/<id>` endpoint the
  browser's own download button uses, so the link starts a download instead of
  opening a page to hunt through. It is still a GitHub artifact and still needs
  a signed-in account with repository access.

---

## [3.0.0] — 2026-09-13

A **major**.

> Tagged **lightweight**, deliberately. An annotated tag makes
> `uses: …@vX.Y.Z` fail with `startup_failure`, no jobs and no logs, while
> `@main` and a commit SHA keep working — so the fault looks like it is
> anywhere but the tag. See the Versioning Policy in the README. The release layer arrived — `Build / Release`
produces an immutable Release Set, and promotion publishes those exact bytes
without rebuilding — and a handful of inputs that never did anything were
removed along the way.

### Migrating from v2.x

**1. Install the numbered entry workflows.** The single `unity-build.yml`
caller still works and reaches only the build half: no release layer, no
immutable artifact boundary, no platform capability model. The numbered set
(`01-ci`, `10-build-development`, `11-build-release`, `20`–`24`) is the current
path — see [docs/CONSUMER_SETUP.md](docs/CONSUMER_SETUP.md) step 2.

**2. Stop passing removed inputs.** The three store promotion workflows dropped
twelve build inputs each that nothing read, `unity-version` among them, and the
release build form dropped `android-export`. A caller still passing one fails
to resolve. The shipped templates are already correct; if you hand-wrote a
caller, drop:

| Removed | Why |
|---|---|
| `unity-version`, `project-path`, `build-config-path`, `image-*`, `workflow-*`, `integration-mode`, `toolkit-path` on `pipeline-{android,ios,webgl}-release.yml` | A promotion runs no Unity. They were `required: true` on a workflow that never builds |
| `android-export` on the release form | The artifact follows the lifecycle: development → APK, release → signed AAB |
| `bundle-id` on the iOS release form | Never read |

**3. Promotion needs `source-run-id`.** Promotion pins one exact
`Build / Release` run rather than resolving "the latest artifact with this
name". `Build / Release`'s final report prints the ready-made command.

**4. iOS promotes the IPA, not the Xcode project.** If you pinned
`artifact-name: release-ios-xcodeproj`, change it to `release-ios-ipa`. The
project is an intermediate a promotion cannot turn into anything installable.

**5. Optional but recommended:** unset `ARTIFACT_RETENTION_DAYS` so retention
tiers by purpose (release 90 days, staging 14, development 7, logs 7). A
release artifact that expires can never be promoted again.

**6. Nothing changes for the build lane itself.** Docker and native builds,
`game-ci/unity-builder`, builder provenance, runner/engine selection and the
Unity version SSOT are all untouched.

### Added

- **iOS promotion validates the IPA it is about to publish.** Every other
  platform validated the artifact it downloaded; iOS checked the IPA once at
  build time and never again, leaving App Store Connect — which rejects a bad
  binary only after consuming a build number — as the next line of defence.

- **A progress ladder on every pipeline.** GitHub draws no progress indicator
  on a workflow node, so each stage job now renders one into its summary via
  `.github/actions/pipeline-progress`; because job summaries accumulate, the
  ladder grows while the run is still going. The stage list is declared once as
  a workflow-level `PIPELINE_STAGES` env so two jobs of the same run cannot
  disagree about the pipeline's shape, and `release-report` renders the same
  ladder from the final results. A phase skipped on purpose counts as distance
  covered; a phase skipped because something earlier failed renders as
  `not reached`, so a failed run cannot count its own wreckage as progress.
  Windows and Linux now use the shared release report as well, instead of their
  own inline summary.


- **Windows and Linux release promotion through Steam.**
  `pipeline-windows-release.yml` and `pipeline-linux-release.yml`, with
  `templates/consumer-23-release-windows.yml` and
  `consumer-24-release-linux.yml` as entry points. Promote-only: they consume
  `source-run-id`, `release-windows` / `release-linux` and the release
  manifest, verify identity and checksum in *every* phase, and publish through
  `steam-internal` → `steam-external` → `steam-production` Environments.
  SteamCMD needs a content root of its own, so the verified artifact is copied
  into a staging workspace and both trees are fingerprinted before anything
  uploads — the artifact itself is never touched. App and depot ids come from
  `STEAM_APP_ID` / `STEAM_DEPOTS` repository variables; nothing project-specific
  is hardcoded in the toolkit. See `docs/STEAM_DISTRIBUTION.md`.
- **A stage-04 validator for desktop players**
  (`scripts/desktop/validate_desktop_artifact.py`). Windows, Linux and the
  Linux dedicated server previously had no validator at all, so a build missing
  its `<Product>_Data` directory — a player that cannot start — became an
  immutable release artifact unchallenged. Checks the executable and its kind,
  the data directory, the engine payload, game code, the Unity runtime library,
  and on Linux that the executable bit survived the artifact round-trip.


- **`.github/pipeline-policy/validation-status.md`** — what has actually been
  proven, split into runtime verified (with run IDs), static verified, and
  blocked by missing hardware or credentials. iOS is listed as blocked: stage
  03b is written and statically checked but has never run, and no macOS runner
  exists to run it on.


- **Builder provenance (I-008).** Every artifact manifest now carries a
  `builderProvenance` block — builder, kind (`docker`/`native`), image
  reference and digest when resolvable, Unity version and runner — so "exactly
  what produced this binary?" is answerable months later. `provenanceStrength`
  is derived rather than asserted: `immutable` requires a content digest,
  `auditable` covers a tag or a runner's own Unity install, and a caller
  claiming `immutable` without a digest is downgraded, not believed. A Release
  Set reports the weakest strength among its artifacts and
  `release_manifest.py generate` fails closed on one recorded as `unknown`.


- **Stage 07 — Report & Notify in the release pipelines.** They ended at stage
  06, so a release run produced no report at all: the only way to see how far
  a promotion got was to read the graph. Each pipeline now ends with
  `07 / <Platform> / Report & Notify`, which says which phase the release
  reached — or which one stopped it — with a progress bar, the artifact that
  was promoted, and the destination, and posts the same to Discord. One shared
  composite action (`.github/actions/release-report`), three thin call sites;
  a composite rather than a reusable workflow because those already spend 3 of
  GitHub's 4 `workflow_call` levels.
- **Progress visualisation in the Final Report.** GitHub renders no progress
  indicator on a workflow node, so the report draws the pipeline's shape
  instead — a stage strip (`01 Prepare ████████ 3/3 ok`) and a per-platform
  bar — making it obvious at a glance how far a run got and where it stopped.
- **A release build now hands off to the release workflow.** `Build / Release`
  produces immutable artifacts and stops; nothing said what to do with them,
  which is why the release layer looked absent. The report now names the
  artifact, the workflow that publishes it and where it goes, with a ready
  `gh workflow run` line. A development build says plainly that it is not
  signed and cannot be published.
- Stage 04 nodes name what they validate — `04 / Android / Validate AAB`
  rather than `04 / Android / Validate`.
- **`build-type` is now its own axis** (`development` | `release`), separate
  from `environment`. It drives the artifact names, so a development APK and a
  release AAB can never be confused: `development-android-apk` versus
  `release-android-aab`. Artifact slugs use the platform a person would name
  (`release-windows`, `release-linux`), not the internal Unity target id.
  Omitting it derives the old behaviour from `environment`, so existing callers
  are unaffected.
- **Three build-layer entry templates**, one question each —
  `consumer-01-ci.yml` ("is this safe to merge?", builds no player),
  `consumer-10-build-development.yml` (APK, for QA) and
  `consumer-11-build-release.yml` (AAB, signed, immutable). Release is not
  Development with `environment=production`: the two differ in build type,
  artifact shape and artifact name.
- **`platform: None`** — the CI lane. It wins over the branch flow, which on a
  push has already chosen platforms from the `*_BUILD_PLATFORMS` variables, so
  a merge check no longer pays for six Unity builds.
- **Three per-platform release entry templates** (`consumer-20-release-android.yml`,
  `-21-release-ios.yml`, `-22-release-webgl.yml`). Each defaults to promoting a
  stored `release-*` artifact rather than rebuilding, so the binary QA approved
  is the binary that ships. No Windows or Linux release workflow: they produce
  standalone artifacts and this project has no distribution target for them.
- `pipeline-ios-release.yml` gained an `artifact-name` input, so iOS can
  promote a stored IPA like Android and WebGL already could.
- **Stages 03 and 04 are matrix jobs**, replacing six per-platform build jobs
  and three per-platform validation jobs with two jobs driven by a matrix that
  stage 01 computes. The graph now contains exactly the platforms that were
  selected: an Android-only run draws one build node and one validation node,
  where before it drew Android plus five greyed-out platforms and two
  greyed-out validations. Adding a platform is one `add` line in the resolver
  instead of a 45-line job, and `unity-pipeline.yml` loses 284 lines.
- Per-leg result artifacts (`pipeline-result-build-<Platform>`,
  `pipeline-result-validate-<Platform>`). A matrix job's legs are not
  addressable through `needs`, so each leg publishes its own result and stage
  07 aggregates them. The Final Report is now generated from that data — it
  covers whichever platforms actually ran and carries real per-platform
  artifact type, size and duration rather than a hardcoded table.
- **Per-platform entry workflows** — `templates/consumer-build-{android,ios,webgl,all}.yml`.
  `workflow_dispatch` has no conditional input visibility, so the single
  multi-platform form had grown to fourteen fields and showed Android's
  APK/AAB choice to somebody building iOS. Splitting the *entry point* is the
  only mechanism GitHub offers for a form that contains just the applicable
  options; all five entry workflows call the same `unity-pipeline.yml` engine
  and none contains build logic. Inputs carry a group prefix
  (`GENERAL` / `ANDROID` / `QUALITY` / `CONTENT` / `UNITY` / `ADVANCED`) since
  the form cannot group them natively.
- `tests/test_entry_workflows.py` (55 tests) — platform-specific inputs appear
  only in their own workflow, Build All carries no per-platform output format,
  infrastructure inputs are grouped `ADVANCED`, every entry point delegates to
  the shared engine with no steps of its own, entry points agree on the engine
  ref, and their concurrency groups are distinct.
- **CI now lints `templates/`.** Those files are copied verbatim into consumer
  repositories but live outside `.github/workflows/`, so the actionlint gate
  could not see them — and the first draft of the new templates shipped two
  invalid-workflow errors (an empty `choice` option, and unescaped quotes in a
  description) that only surfaced when linting the consumer copies.
- **Pipeline stage architecture** — every user-visible node in `unity-pipeline.yml`,
  `release-orchestrator.yml` and the three release pipelines is now named
  `NN / Platform / Configuration / Artifact` (`03 / Android / Production / AAB`)
  instead of `Build Android` / `build`. Job **ids** are unchanged, so `needs:`,
  required status checks and existing consumer references keep working.
  New: [docs/PIPELINE_ARCHITECTURE.md](docs/PIPELINE_ARCHITECTURE.md).
- **`02 / Quality Gate`** in `unity-pipeline.yml` and `release-orchestrator.yml`.
  Unity Tests previously ran *beside* the platform builds, so a red suite still
  paid for a full Android + WebGL + Windows matrix. Every expensive build now
  depends on this one node and checks its verdict
  (`needs.quality-gate.outputs.passed == 'true'`). `skipped` stays a pass, so
  `run-tests: false` and the local build engine behave as before.
- **Stage 04 — artifact validation**, three new independent nodes in
  `unity-pipeline.yml` (`04 / Android / Validate AAB`, `04 / WebGL / Validate`,
  `04 / iOS / Validate Xcode Project`), each depending on exactly one build job:
  - `scripts/android/validate_android_artifact.py` — AAB/APK type, zip structure,
    dex, signing (v1 JAR entries and the v2+ APK Signing Block), package id,
    versionName, versionCode, size floor/ceiling.
  - `scripts/android/axml.py` — minimal compiled-AndroidManifest reader, so APK
    identity is verifiable without the Android SDK on the runner.
  - `scripts/webgl/validate_webgl_artifact.py` — index.html, the four Unity
    player files, compression consistency (the loader-plain/wasm-brotli
    blank-canvas trap), StreamingAssets, size.
  - `scripts/ios/validate_xcode_project.py` — `.xcodeproj`, `project.pbxproj`,
    Unity's `Classes/`/`Libraries/`/`Data/`, `Info.plist` identity, size.
- **`scripts/common/artifact_manifest.py`** — stage 03 writes
  `artifact-manifest.json` into the build output and uploads it as
  `build-manifest-<Platform>`. `reusable-build-platform.yml` gained matching
  outputs (`artifact-type`, `artifact-path`, `artifact-size-bytes`,
  `artifact-sha256`, `manifest-artifact-name`, `configuration`) so stages 04–07
  no longer rediscover the artifact location or re-derive its version.
- **`pipeline-webgl-release.yml`** — WebGL gains the build → validate → deploy
  pipeline Android and iOS already had, with `start-phase` retry and a
  `production` environment approval boundary. Wired into
  `release-orchestrator.yml` as an independent platform node.
- `release-orchestrator.yml`: `WebGL` and `All` platform choices (`Both` still
  accepted), plus `run-tests`, `test-mode`, `ios-bundle-id` and
  `cloudflare-pages-project` inputs.
- `reusable-build-platform.yml`: `node-label`, `configuration`, `artifact-type`,
  `app-version`, `build-number` and `toolkit-repo` inputs. `node-label` defaults
  to the previous `Build <platform>`, so existing callers keep their node names.
- `pipeline-ios-release.yml`: `bundle-id` input, checked against the IPA's
  `Info.plist` in stage 04 instead of letting App Store Connect reject the upload.
- `discord-upload-build`: `failed-stage`, `configuration` and
  `result-validation-{android,webgl,ios}` inputs. The message now names the stage
  the pipeline stopped at and marks artifacts that built but failed validation.
- **`actionlint` as a third CI gate** (`.github/workflows/ci.yml`), with
  `.github/actionlint.yaml` declaring the self-hosted runner labels. A workflow
  that parses as YAML but is invalid as a workflow is accepted by the pytest
  suite and rejected by GitHub at dispatch — as a run with no jobs and no logs.
  Three such defects were live on `main` before this change; the gate is what
  stops the fourth.
- `tests/test_pipeline_stages.py` (84 tests) — stage naming, quality-gate
  ordering, platform fan-out independence, validation independence, publish
  depends on validation, `start-phase` retry, production approval, artifact
  metadata propagation, and the workflow_call nesting budget.
- `tests/test_artifact_validation.py` (52 tests) — the manifest generator, the
  AXML reader and all three validators, against synthesised artifacts.

### Changed

- **Documentation reorganised around what is current.** Thirty-seven documents
  had accumulated across three architectures, and several taught the
  pre-refactor setup as if it were the path — a reader following them ended up
  with no release layer at all. `docs/README.md` is now an index that says which
  documents are current, which are reference and which are superseded; the
  superseded ones carry a banner naming their replacement rather than being
  deleted, since the reasoning they record is worth keeping.
- **`PLATFORM_LIMITATIONS.md` said Windows was unsupported.** It contradicted
  `PLATFORM_MATRIX.md` — which the README calls the authoritative matrix — and
  a real release build disproved it. The limit is the scripting backend, not
  the platform: the Docker lane cross-compiles Windows with Mono, and IL2CPP
  needs a self-hosted Windows runner because MSVC does not run in a Linux
  container. ADR-002 keeps its original text with an amendment note; an ADR
  records what was decided when, not what is true now.
- **Version pinning advice pointed at `@v2`,** which predates the release
  layer, the capability model and the immutable artifact boundary — so anyone
  following it got the build half and none of the release half.

- **The build trigger no longer asks what an artifact should be.** The release
  form offered APK-vs-AAB, which made `release + Android + apk` a configuration
  the pipeline accepted: an artifact that passes every gate, carries a release
  identity, enters a Release Set — and cannot be published, because Google Play
  takes App Bundles. The format now follows from the lifecycle (development →
  APK, release → signed AAB), resolved in `resolve_build_flow.sh`. A
  `workflow_call` caller may still pass `android-export`; it is validated
  against the lifecycle and the run fails if they disagree, rather than the
  value being silently ignored.
- **The lifecycle is resolved once.** The matrix step derived `build-type` a
  second time from the same two inputs — one divergence away from a release
  build naming its artifacts `development-*`. It now reads the resolver's.
- **Platform labels come from one table.** The fold that keeps a multi-word
  label intact and the alias that expands it were two separate pieces of code,
  so adding an option meant remembering both — and forgetting the fold would
  tokenise "Linux Server" into "Linux" plus "Server", selecting the desktop
  build alongside the dedicated server and exiting 0. One table now drives
  both, and a test asserts every dropdown option resolves to the exact set it
  names rather than merely not failing.
- **The platform selector speaks human.** `Windows`, `Linux`, `Linux Server`
  and `Desktop` in the form and in the Actions graph; `Windows64`, `Linux64`,
  `LinuxServer` remain the identifiers in the matrix, the capability variables
  and the artifact names. Renaming those would change the meaning of every
  `*_BUILD_PLATFORMS` already configured, for nothing but appearance.

- **The release manifest no longer outlives the artifacts it describes.** Its
  retention was hardcoded to 90 days while the artifacts followed the project's
  setting, so a project retaining artifacts for 30 days kept a manifest for 60
  days after the bytes it points at had gone — a Release Set that reads as
  promotable and is not.
- **Artifact retention is tiered by what the artifact is for.** Thirty days
  for everything turned a storage default into a promotion deadline: a
  promotion consumes the exact artifact its Release Set names, so once that
  expired the release could never be promoted again — the immutable boundary
  still held and there was nothing left on the other side of it. Release
  artifacts now keep 90 days, staging 14, development 7 (disposable by I-002),
  and logs, validation reports and per-platform result files keep 7 regardless,
  since nothing downstream reads them. `ARTIFACT_RETENTION_DAYS` still wins
  where a project set it — lengthening a default is help, overruling an
  explicit choice is not.

- **`All` now means the platforms the project configured.** It was a hardcoded
  list of five, so a project with `RELEASE_BUILD_PLATFORMS=Android` that picked
  "All" got five builds — about fifty runner-minutes instead of nine — while
  the form said "All uses RELEASE_BUILD_PLATFORMS". The form was right about
  the intent and wrong about the behaviour. iOS still never joins an `All`
  build: it needs a macOS runner, so including it would make the result depend
  on infrastructure rather than on the request.
- **`platform` accepts a list, and rejects a typo.** `Android,WebGL` used to
  fall through to a warning and build nothing — a green run with no artifacts,
  which is the worst shape a build failure can take because it looks finished.
  Lists and the `Desktop` alias (Windows64 + Linux64) now resolve; an
  unrecognised name fails the run. A *recognised* platform the project has not
  enabled is still skipped with a note, which is I-016 working as intended.
- **The disk reclaim only runs when the disk is short.** Current GitHub-hosted
  runners ship 145G with ~87G free and the Unity image needs ~15G, so the
  unconditional reclaim spent 57s on the Android job and **276s on WebGL**
  deleting 30G nobody was going to use. Guarded at 40G free, which keeps the
  safety net for smaller and self-hosted runners.
- **The Gradle cache no longer runs on the docker lane.** Unity runs Gradle
  inside the container with its own JDK, under `Library/Bee/Android/Prj`, so
  the host's `~/.gradle` is never written: the step cached an empty directory,
  missed on every run, and told anyone reading the log that Gradle was cached.
  It stays on the native lane, where it works. `Library/` is cached separately
  and does cover Bee's output.

- **Promotion workflows no longer hold credentials they cannot use.**
  `pipeline-{android,ios,webgl}-release.yml` declared `UNITY_LICENSE`,
  `UNITY_EMAIL`, `UNITY_PASSWORD`, the Android keystore set and the iOS
  distribution certificate — every one unread by anything in the file. I-005
  says a promotion must not sign; holding the keystore anyway left that
  capability one line of YAML away, which is how signing ended up inside
  promotion the first time. The rule is now "could not sign", enforced by
  `check_promotion_holds_no_signing_secrets`.
- **Every job that downloads the artifact re-verifies it.** The Steam pipelines
  did this from the day they were written; the store pipelines verified once in
  a dedicated job and then published from a second, unchecked download. An
  approval on an earlier phase is not evidence about the bytes a later job is
  holding. Enforced by `check_every_download_is_verified` (I-017).
- **BREAKING — promotion inputs pruned.** The three store pipelines each
  declared 12 build inputs nothing read, `unity-version` among them and marked
  `required: true` on a workflow that never runs Unity. All removed, along with
  a `workflow_dispatch` form that predated promote-only and never asked for
  `source-run-id` — running it could only ever fail. The dispatch surface is
  the consumer's numbered entry point. Callers passing `unity-version` or
  `bundle-id` must drop them; the shipped templates already have.
- **`docs/CONSUMER_SETUP.md` described the previous architecture.** Step 2 told
  a new project to install `consumer-unity-build.yml`, the single pre-refactor
  caller, so anyone following the docs got a setup with no release layer, no
  immutable artifact boundary and no platform capability model. It now installs
  the numbered `01/10/11/20–24` set, says what each file is for, and walks
  through a real promotion. The older templates are documented as the previous
  generation rather than silently shipped alongside.

- **I-008 redefined** from "release uses immutable Unity image references" to
  "release builds have immutable **or** auditable builder provenance,
  recorded". The old rule could only be satisfied by Docker with a pinned
  digest, which for `game-ci/unity-builder` means forking it or shipping a
  custom image, and which a native macOS iOS build could never satisfy at all
  — the invariant was deciding the architecture. Docker is not required and no
  custom image is introduced. The three CI checks now verify that every lane
  writing an artifact manifest records provenance, that `immutable` is gated on
  a digest, and that an untraceable artifact cannot enter a Release Set. The
  builder itself is unchanged; limitations are stated in
  `docs/PIPELINE_ARCHITECTURE.md` §5b rather than hidden.

- **Platform capabilities.** A project declares which targets it can build via
  the `PLATFORMS` variable (`Android,WebGL`). That is a different question from
  `*_BUILD_PLATFORMS`, which says which of them a branch builds — capability
  wins, so asking for a platform the project does not support produces no job
  at all. Enforced at `set_platforms_from_list`, the one chokepoint every path
  (branch flow and manual dispatch) already went through, so there is no second
  configuration system. Unset means all platforms, leaving existing projects
  unaffected. Windows and Linux go through the identical gate — no separate
  code path, no distribution provider required to produce an artifact.
- **Release Set manifest** (`scripts/common/release_manifest.py`). One
  `Build / Release` run is one Release Set: a commit, a version, a build
  number, a Unity version and the artifacts built from them. Stage 05 collects
  the per-platform manifests, hashes the real bytes, refuses a set whose
  artifacts disagree on commit or version, and uploads `release-manifest` with
  90-day retention.
- **Artifact identity verification.** Each promotion now begins with
  `04 / <Platform> / Verify Release Identity`, which downloads the manifest
  from the source run and checks run id, version, build number, commit,
  artifact name and SHA-256 before anything else runs. It fails closed: a
  promotion that cannot prove what it is holding does not publish it. A
  filename establishes nothing.
- **Pipeline invariant policy and checker** —
  `.github/pipeline-policy/invariants.md` and
  `scripts/common/validate_pipeline_invariants.py`, wired into CI as a required
  gate. 25 static checks covering the immutable-artifact boundary, promotion
  purity, release-set consistency, capability filtering, build-number
  resolution and production Environment protection. These are properties that
  do not fail a build when broken — they produce a pipeline that looks healthy
  and ships the wrong bytes.


- **BREAKING — iOS production signing moved into `Build / Release` (stage 03b).**
  Signing used to run during promotion, which broke the invariant the whole
  design rests on: the artifact QA validated was an Xcode *project*, and the
  artifact that shipped was an IPA built from it afterwards. Those are not the
  same binary. `Build / Release` now emits a signed `release-ios-ipa`, stage 04
  validates *that* with `REQUIRE_SIGNED` against the resolved version and build
  number, and `Release / iOS` only downloads, verifies and publishes. The
  distribution secrets moved with the work.

  The boundary is machine-checked, not reviewed:
  `test_promotion_cannot_modify_the_binary` scans every promotion job's `uses`
  and `run` for anything that builds, archives, signs, re-exports or
  recompresses, and fails the suite if one appears.
- **BREAKING — Android's store counter no longer comes from the major version.**
  `bundleVersionCode` was `cfg.BundleVersion.Split('.')[0]`, so every `1.x.y`
  release uploaded versionCode `1` and Google Play refused the second one.
  `game-ci/unity-builder` was also invoked with no version at all, falling back
  to Semantic versioning from git tags and generating its own counter — two
  runs of the same commit could disagree, and nothing guaranteed the number
  increased.

  Stage 01 now resolves the build number once and every platform receives the
  same value, reusing the convention `IOSBuilder` already applied for
  `CFBundleVersion` (`BUILD_NUMBER` → `GITHUB_RUN_NUMBER`) rather than
  inventing a second scheme, plus a `BUILD_NUMBER_OFFSET` repository variable
  for projects whose store history predates this pipeline. It reaches the
  builder three ways because three consumers need it: `androidVersionCode`,
  `version`, and the `BUILD_NUMBER` environment variable.
- **BREAKING — the release pipelines are promote-only.** They can no longer
  build. `start-phase: build` is gone, the Unity build job is removed from
  each, and `release-orchestrator.yml` — which built and released in one run —
  is retired. "The binary QA approved is the binary that ships" stops being a
  convention and becomes structural: there is no code path in the release layer
  that can produce a binary.

  A release is now always two steps:

      Build / Release   →  release-android-aab  (immutable)
      Release / Android →  validate → publish → release

  iOS keeps its IPA export, because turning an Xcode project into a signed IPA
  needs the distribution certificate — that is a release concern, not a build
  one.

  Migration: `Release / *` gains a **required** `source-run-id` input naming
  the `Build / Release` run that produced the artifact.
  `actions/download-artifact` only sees the current run by default, so without
  it a promotion cannot physically find the binary. The `Build / Release`
  report prints the exact command, run id included.

  `start-phase` options are now `validate | internal | external | production`
  (`validate | staging | production` for WebGL), defaulting to the first
  publish phase.


- **`unity-build.yml` is the automatic lane only.** Its `workflow_dispatch`
  form is removed; manual builds use the per-platform entry workflows. Push and
  pull_request behaviour is unchanged — those events never carried inputs, so
  every value already came from `resolve_build_flow.sh` and the repository
  variables. The concurrency key drops the lane placeholders it could never
  populate.
- Stage-03 node labels no longer repeat the platform name: `03 / WebGL /
  Production` rather than `03 / WebGL / Production / WebGL`. An artifact type
  is appended only where it adds information (`03 / Android / Production / AAB`).
- **Platform builds no longer start until Unity Tests finish.** This is the
  intended trade: one extra gate node of latency, in exchange for never paying
  for a build the test suite would have rejected. Wall-clock time for a green
  run grows by roughly the test duration.
- `unity-pipeline.yml`'s iOS stage-03 artifact type is `XCODEPROJ`, not `IPA` —
  that lane exports an Xcode project; the IPA is produced by
  `pipeline-ios-release.yml`.
- The final report is grouped by stage and carries artifact type, size and build
  duration per platform; its error line names the stage and node that failed.

### Fixed

- **`templates/AddressableBuilder.cs` did not compile.** The template carried one using directive
  for Addressables, `UnityEditor.AddressableAssets.Settings`, but neither type it uses lives there:
  `AddressableAssetSettingsDefaultObject` is in `UnityEditor.AddressableAssets` and
  `AddressablesPlayerBuildResult` in `UnityEditor.AddressableAssets.Build`. A consumer who followed
  `CONSUMER_SETUP.md` step 5 got

  ```
  error CS0103: The name 'AddressableAssetSettingsDefaultObject' does not exist in the current context
  error CS0246: The type or namespace name 'AddressablesPlayerBuildResult' could not be found
  ```

  and the failure only surfaced at their first Addressables build. Both directives added.

- **A Steam dry run never reached the staging path it promises.** The input is
  documented as "verify, validate and stage the Steam content without
  uploading" and `deploy_steam.sh` implements exactly that, but the phase gate
  excluded dry runs outright — so the staging and no-mutation checks were
  unreachable, and the one way to exercise the promotion path in a repository
  with no Steam account did nothing. The phases now run under a dry run,
  skipping the credential requirement, the SteamCMD install and the upload.
- **A promotion started at a later phase did nothing and reported success.**
  Gating the publishing jobs on `verify-artifact.result == 'success'` replaced
  the `always()` they used to carry, and GitHub skips a job whenever anything
  in its `needs` was skipped *unless* the `if` contains a status function. So
  every publishing job inherited the skip from `validate-artifact`, which is
  skipped by design whenever `start-phase` is a later phase — the retry path
  the phase input exists for. `!cancelled()` restores the override without
  restoring the hole: the explicit verify check still gates on identity. Found
  by running a Linux promotion at `start-phase: internal`.
- **The desktop validator failed every Linux build on its first real run.**
  GitHub stores artifacts in a zip, which carries no POSIX modes, so a Linux
  binary downloaded from any artifact is always `0644` — the transport dropped
  the executable bit, not the build. Stage 04 sees only downloaded artifacts,
  so the check belonged there as a warning that names the cause;
  `--require-executable-bit` keeps it a gate for a caller checking a build
  directory in place. `deploy_steam.sh` restores the bit on the staging copy,
  since a depot built from a `0644` binary ships a game nobody can launch. File
  content is untouched and the staging fingerprint still matches.
- **The iOS promotion consumed the wrong artifact.** Stage 03b signs and
  exports the IPA before the immutable boundary, but the IPA carried no
  artifact manifest — so the only iOS manifest in a release run was the Xcode
  project's, the Release Set listed the project, and
  `consumer-21-release-ios.yml` defaulted to promoting `release-ios-xcodeproj`.
  A promote-only pipeline cannot turn a project into anything installable, so
  the iOS release path was a dead end that looked configured. The IPA now
  writes and uploads its own manifest, `XCODEPROJ` is marked
  `"intermediate": true`, a Release Set skips intermediates and raises if two
  shippable artifacts claim one platform, verifying an intermediate fails
  closed, and the promotion default is `release-ios-ipa`.
- **`verify` accepted a Release Set with missing identity fields.** It only
  compared a field when the caller supplied an expectation, so an empty one
  passed unchallenged. All seven — run id, commit, version, build number,
  platform, artifact name, SHA-256 — must now be present, and the artifact's
  own commit/version/build number must agree with the Release Set's (I-009).


- **A missing Play service account read as a corrupt one.** An unset secret
  interpolates as an empty string, which reached the JSON parser and produced
  "service account JSON is not valid JSON" — sending whoever read the log
  looking for a broken key instead of an absent one. The value is stripped
  before the emptiness check, so a blank or whitespace secret reports as
  missing.
- **A failed identity check did not stop a promotion.** Every job in the three
  promotion pipelines listed `verify-artifact` in `needs` but guarded itself
  with `if: always()`, which ignores the result. Publishing was reachable: a
  promotion started at `start-phase: internal` would have uploaded an artifact
  whose identity verification had just failed. Found by running a deliberate
  version mismatch. Every job downstream of the check now requires it to have
  succeeded — the report job excepted, since reporting a failure is the one
  thing that must survive it — and `validate_pipeline_invariants.py` fails CI
  if a new job forgets.
- **Release Sets were written with no version.** The stage-01 shallow checkout
  fetched `ProjectVersion.txt` but not `ProjectSettings.asset`, so the metadata
  step took its "file not found" path and `app-version` resolved empty.
  `verify --expect-version ''` then compared nothing and passed, leaving the
  promotion identity check running on two of its three fields. The file is now
  checked out, and generating a *release* manifest without a version fails
  closed.
- **The platform capability gate was inert.** `unity-pipeline.yml` never passed
  `vars.PLATFORMS` to `resolve_build_flow.sh`, so a project declaring
  `Android,WebGL` still built Windows64 when asked — found by running it, not
  by the tests, every one of which set `PLATFORMS` in the resolver's own
  environment and so tested a call shape nobody made. The variable is now
  forwarded (with `BUILD_PLATFORMS_ENABLED` as the grouped name), and a test
  parses the workflow to assert the resolver step actually receives it.


- **`resolve-unity-image` required an `image-namespace` the toolkit already
  knew.** A release pipeline asked only to build an AAB failed with
  `image-namespace input is required for resolve-unity-image` before Unity
  started, even though `config/unity-build-defaults.json` records the
  namespace. It now falls back to that default and logs where the value came
  from, erroring only when there is genuinely nothing to use.
- **`start-phase: build` could never work in any release pipeline.** They call
  the platform build with `integration-mode: remote`, which requires
  `workflow-repository` and `workflow-ref`, but passed neither unless the
  caller supplied them — so the first real `Release / Android` dry run failed
  before Unity started with `workflow-repository input is required
  (integration-mode: remote)`. Both now fall back to the toolkit the pipeline
  was called from.
- **The release node chain repeated the platform three times** —
  `Android / 03 / Android / Release / AAB / Build Android (production)`. The
  `node-label` split already used by `reusable-build-platform.yml` now applies
  to `unity-build-{android,ios,webgl}.yml` too, and the release entry
  workflows name their job after the layer rather than the platform:
  `Release / 03 / Android / AAB / 0.1.0`.
- **`pipeline-android-release.yml` and `pipeline-webgl-release.yml` used
  `DISCORD_WEBHOOK_URL` without declaring it.** An undeclared secret in a
  reusable workflow is always empty, so the notification would have silently
  never fired.
- **Every entry-workflow dispatch failed at stage 01.** The `ADVANCED` inputs
  use `auto` to mean "take the repository variable", which the engine spells as
  an empty string — but the translation was written
  `inputs.runner-type == 'auto' && '' || inputs.runner-type`, and GitHub's
  `&&`/`||` return operands rather than booleans. `''` is falsy, so the `||`
  always fell through and the literal `auto` reached the resolver:
  `[ERROR] resolve-build-flow: Invalid runner-type='auto'`. Inverted to
  `inputs.runner-type != 'auto' && inputs.runner-type || ''`. The test that
  should have caught it only grepped for the expression text; it now evaluates
  the expression for both `auto` and a concrete lane.
- **A failed `resolve-config` reported the whole run green.** Every job
  downstream of it is *skipped*, and `skipped` is a pass in both the quality
  gate and the final report — neither of which checked `resolve-config` itself.
  A dispatch that died in stage 01 therefore showed a green Quality Gate and a
  green Final Report with nothing built. Both now check it, and the report
  checks it first.
- **The Final Report failed a fully green build.** The build engine uploaded
  each platform's result artifact *before* the step that writes it, so
  `pipeline-result-build-<Platform>` was empty on every run and
  `if-no-files-found: ignore` said nothing. Stage 07 then found no result for
  platforms that had built and validated fine and failed the run — Android and
  WebGL both green, report red (NDCUnityTemplate run 34567145749). The upload
  now runs after the writer and warns on an empty result, and the report
  distinguishes a *reporting gap* (matrix succeeded, file missing → reported as
  `unreported`, not fatal) from a *dead leg* (matrix did not succeed → fatal
  and named).
- **The WebGL validator failed every brotli build.** It required all four
  player files to share one compression mode, but Unity deliberately leaves
  `.loader.js` uncompressed — the browser fetches and executes it *before* any
  decompression logic exists — so `loader=none, data=brotli, framework=brotli,
  wasm=brotli` is the normal, correct output of a brotli WebGL build. The
  consistency check now covers the payload (`framework`, `data`, `wasm`) only,
  which still catches the half-compressed artifact that renders as a blank
  canvas. A *compressed* loader now warns instead, since it only works when the
  host negotiates `Content-Encoding`. Caught on NDCUnityTemplate run
  34564185431, where a perfectly good WebGL build was failed.
- **The artifact manifest was never written on the docker lane.** `build/` is
  created by the Unity container under a different UID, so the runner could not
  write into it — `PermissionError: [Errno 13] Permission denied:
  'build/artifact-manifest.json'`. The step is `continue-on-error`, so every
  Android build silently shipped without a manifest. It is now written to
  `$RUNNER_TEMP`, uploaded as `build-manifest-<Platform>`, and copied into the
  build output only as a best effort. Stage 04 downloads it alongside the
  binary so the validators keep their identity fallback.
- **`pipeline-android-release.yml` was an invalid workflow file.** It referenced
  `inputs.artifact-name`, which was declared as a workflow *output*, never as an
  input. GitHub rejects such a file when the call is resolved, producing a run
  with no jobs, no logs and a bare `failure` — the shape that made the
  NDCUnityTemplate release dispatch fail with `Android Release` absent from the
  job list entirely. `artifact-name` is now a real input (it is also the
  publish-without-rebuild handle for `start-phase`).
- **`unity-build-{android,webgl,linux}.yml` exported an empty `artifact-name`.**
  The output read `steps.upload.outputs.artifact-name`, but
  `actions/upload-artifact` returns `artifact-id` / `artifact-url` /
  `artifact-digest` and has no `artifact-name` output. Every downstream
  `download-artifact` therefore received an empty name and silently fell back to
  "download every artifact in the run". The name is now resolved in its own step
  and used both for the upload and for the output.
- **`release-orchestrator.yml` called `version-bump.yml` with no secrets**, though
  that workflow requires `APP_ID` / `APP_PRIVATE_KEY` to push the bump commit, so
  a version bump could never have succeeded. Now `secrets: inherit`.

- **`game-ci/unity-test-runner` pinned to `v4.3.2`.** The `@v4` tag moved to **v4.4.0** on
  2026-09-09 — not a bug-fix release but a rewrite: a thin wrapper around the new `game-ci`
  CLI, which it resolves at `cliVersion: latest`. Two floating versions stacked, one of
  them a different program. Every consumer's test job broke the next morning.

  It did not look like a version problem, which is the part worth recording:

  ```
  Unclassified error occured while trying to activate license.
  [Licensing::Client] Error: Code 400 while processing request (status: TimeStamp validation failed)
  ::error::No test result files were produced (runner/activation error).
  Total — passed: 0, failed: 0
  ```

  That reads as a licence fault and sends the reader to `UNITY_LICENSE` and the runner
  clock. The tell is that **builds in the same run activated fine with the same secret** —
  they use `unity-builder`, a different action. game-ci's own v4.3.2 notes warn about this
  exact confusion, describing an unrelated failure "which surfaced as a retry-then-fail
  loop that looked like a license activation problem".

  Diagnosed by re-running the **last known-green run** on today's runners: the old commit
  failed identically, which separates "a consumer's change broke CI" from "CI broke" in one
  command.

  `v4.3.2` is the last release on the old, long-stable architecture and carries the
  `--shm-size` fix Unity 6.6+ needs. Adopting `v4.4.0` should be a tested branch, not a
  floating tag.

- **Release pipelines were unusable when called cross-repository.** Nine
  step-level `uses: ./.github/actions/…` references and
  `python3 scripts/android/upload_google_play.py` in
  `pipeline-android-release.yml` / `pipeline-ios-release.yml` resolve against the
  runner *workspace*, which for a reusable workflow is the **caller's**
  repository — where the toolkit's actions and scripts do not exist. Both
  pipelines now check the toolkit out to `.toolkit/` (new `toolkit-repo` /
  `toolkit-ref` inputs) and reference `./.toolkit/…`, matching the pattern
  already used by `unity-pipeline.yml`'s Discord job.
- **Build Only could not run without store credentials.** A `required: true`
  secret is validated when a `workflow_call` is *resolved*, so
  `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` (Android) and the six iOS signing/ASC
  secrets made a `dry-run` build impossible in a repository that has none. They
  are now optional at the call boundary and checked in the publish jobs that use
  them.
- **Artifact validation was a file-size check.** `pipeline-android-release.yml`
  accepted any AAB/APK over 1000 bytes and `pipeline-ios-release.yml` any IPA
  over 5000 bytes — an unsigned bundle, the wrong artifact type or a mismatched
  package id all reached the store. Both now run the real validators.
- `scripts/ios/validate_ipa.sh`: verifies the embedded provisioning profile and
  its expiry, checks expected bundle id / version / build number, emits a step
  summary, and reports every failing check instead of exiting on the first. It
  no longer requires macOS — `Info.plist` falls back to `plistlib`, and an
  unverifiable signature is reported as unverified rather than as a pass.
- `scripts/android/axml.py`: attribute offsets are relative to
  `ResXMLTree_attrExt`, not to the chunk start (caught by the new tests).
- `unity-pipeline.yml`'s final report and Discord status now gate on the stage-04
  results too; previously an artifact could fail validation while the pipeline
  reported green.


- **Submodule clones half-landed on Windows when a path passed MAX_PATH.** A runner workspace is
  already deep before the project path starts, and a Unity package that vendors a plugin tree runs
  past 260 characters:

  ```
  error: unable to create file Plugins/Com.ForbiddenByte/OSA/Utilities/Editor/Resources/
         Com.ForbiddenByte.OSA/Templates/ScrollViews/TableView/Input/...
  ```

  Git reports this per file and still exits non-zero, so the submodule is left partly written —
  the next failure lands in Unity, far from the cause. The ssh lane now runs
  `git -c core.longpaths=true submodule update`, which propagates to the child git processes that
  clone each submodule and is inert on other platforms.

  Note that `core.longpaths` is git's own switch: on Windows the OS-level `LongPathsEnabled`
  registry value does not cover git, and git ignores paths over MAX_PATH without it.

### Removed

- **`templates/consumer-build-{android,ios,webgl,all}.yml`** — the draft that
  the numbered entry points replaced. Nothing referenced them; shipping two
  generations side by side only made it unclear which was current.
  `consumer-unity-build.yml` stays, marked deprecated, because removing it
  would break projects still on the single-caller architecture; it goes in the
  next major version with a migration note.


## [2.3.0] — 2026-09-10

Tagged but never written up at the time; recorded here from the commits it
contains, so the tag list and this file stop disagreeing.

### Added

- **Phased store release pipelines for Android and iOS** — the first version of
  `pipeline-{android,ios}-release.yml`, with the internal → external →
  production phase chain behind GitHub Environments. Everything that followed
  in 3.0.0 — promote-only, the Release Set, the immutable artifact boundary —
  was built on this shape.

---

## [2.2.5] — 2026-09-10

### Fixed
- **The ssh submodule lane left a reused workspace's submodules empty.** `actions/checkout` runs
  `git clean -ffdx` before fetching, which empties each submodule's working tree while leaving its
  gitdir behind at the recorded commit. `git submodule update --init --recursive` then sees the
  right SHA, concludes there is nothing to do, and restores nothing:

  ```
  Packages/com.gdk.core     1 entry (.git only, no package.json)
  Assets/DOTSFoundation     1 entry (.git only)
  ```

  The build then proceeds against packages Unity cannot load. It fails much later, in the Unity
  step, with hundreds of errors that point away from the cause — `BlueprintReader`, `Zenject`,
  `Unity.Physics.Systems` and friends "not found", none of which name a submodule. Only a
  self-hosted runner sees it, because a GitHub-hosted one starts from an empty workspace every time.

  Pass `--force`, which is what `actions/checkout` itself uses on the token lane
  (`submodule update --init --force --depth=1 --recursive`).
  `tests/test_workflow_contract.py::TestSshSubmoduleUpdateIsForced` pins it.


## [2.2.4] — 2026-09-10

### Added
- **`submodule-auth` — a way to fetch a private submodule that lives in another organization.**
  `actions/checkout` does not simply fetch submodules; before it does, it writes

  ```
  git config --global --add url.https://github.com/.insteadOf git@github.com:
  ```

  which rewrites every SSH URL in `.gitmodules` to HTTPS and authenticates with `GITHUB_TOKEN`.
  That token is scoped to the repository being built, so a private submodule in another
  organization is unreachable and git reports it the way it reports a typo:

  ```
  fatal: repository 'https://github.com/<other-org>/<repo>.git/' not found
  ```

  No key on the runner helps, because the rewrite happens before git chooses a transport — the
  `ssh` binary is never invoked. `submodule-auth: ssh` skips the checkout action's submodule pass
  and fetches them in a separate step under `GIT_SSH_COMMAND`, so the URLs in `.gitmodules` are
  used as written: the `SUBMODULE_SSH_KEY` secret when one is set, the runner's own SSH
  credentials otherwise (what a self-hosted runner usually already has).

  The default is `token`, which is the historical behaviour line for line, so existing consumers
  are untouched. `tests/test_workflow_contract.py::TestSubmoduleAuthIsForwarded` pins the
  passthrough across every job, since one build job missing it would silently fall back.


## [2.2.3] — 2026-09-10

### Fixed
- **A CRLF `ProjectVersion.txt` failed the Unity version check against a value equal to it.**
  Unity writes `ProjectSettings/ProjectVersion.txt` with CRLF on Windows, so consumers commit it
  that way. `resolve-config` extracted the version with `awk '{print $2}'`, keeping the trailing
  CR, then compared it against the `unity-version` input / `UNITY_VERSION` variable — which has no
  CR. The two are unequal, and the error prints them as identical because the log swallows the CR:

  ```
  ::error::Unity version mismatch:
  ::error::  ProjectVersion.txt: 6000.3.9f1
  ::error::  Pin (input/UNITY_VERSION var): 6000.3.9f1
  ```

  Every shell reader now strips CR: `unity-pipeline.yml`, `unity-build-gameci.yml`,
  `unity-release.yml`, `unity-release-ios.yml` and `scripts/common/ensure_repo_variables.sh`.
  `resolve_build_flow.sh` already did (`tr -d '[:space:]'`), and `resolve_project_version.py`
  is safe through `.strip()`. Without the fix the CR also travelled into `$GITHUB_OUTPUT` as the
  resolved version, so it would have corrupted image tags and Editor paths downstream even when
  no pin was set. `tests/test_workflow_contract.py::TestProjectVersionParsingIsCRLFSafe` pins it.


## [2.2.2] — 2026-09-10

### Added
- **`docs/NEW_PROJECT_END_TO_END.md`** — the guide that did not exist: one ordered pass from an
  empty Unity repository to a green build, and from there to building on your own machine. Repo
  requirements, the caller workflow, the three Unity secrets and why all three are needed, the first
  dispatch, what the artifacts are called, the optional variables with their real defaults, then the
  runner setup, then troubleshooting keyed to the errors this toolkit actually emits.

  It opens by choosing between the two consumption paths, because they **do not use the same Docker
  images** and that is not obvious anywhere else: the `unity-pipeline.yml` path builds through
  `game-ci/unity-builder@v5`, or `docker run unityci/editor:ubuntu-<version>-<variant>-3` on a
  self-hosted Windows runner (`reusable-build-platform.yml:642`). It never touches this
  organization's `ghcr.io/<org>/unity-editor` images — only the `unity-build.yml` workflows do, via
  the `resolve-unity-image` action. A new project on the recommended path therefore needs no
  published image at all.

  Also records, in one place, the traps found while verifying the toolkit this cycle: `toolkit-ref`
  must match the `uses:` ref; a green run can still mean an empty artifact; `RUNNER_LABELS` left
  empty falls back to `self-hosted,windows` whatever the machine's OS; a `unity` runner label is not
  required; and a self-hosted runner on a public repository lets fork pull requests execute code on
  your machine.

### Fixed
- **A Unity project in a subdirectory could never resolve its Unity version.** `resolve-config`
  checked out `ProjectSettings/ProjectVersion.txt` sparsely with
  `sparse-checkout-cone-mode: false` (`unity-pipeline.yml:207-209`), then read the file back at
  `${PROJECT_PATH%/}/ProjectSettings/ProjectVersion.txt` (`:223`). A non-cone pattern containing a
  slash is a gitignore-style pattern anchored to the repository root, so for any caller passing a
  `project-path` other than `.` the checkout produced nothing at the path the next step reads, and
  the job failed on its first step:

  ```
  ::error::ProjectVersion.txt not found at UnityBackpackRoguelike/ProjectSettings/ProjectVersion.txt
  ```

  The file was present in the repository and `project-path` was correct — only the checkout was
  wrong. The pattern now leads with `**/`, which matches at any depth including the repository
  root, so both `project-path: '.'` and a subdirectory work, and a trailing slash on the input is
  harmless. `tests/test_workflow_contract.py::TestSparseCheckoutHonoursProjectPath` pins it for
  every workflow, not just this one.

- **The onboarding docs demanded three Unity secrets where the pipeline needs one.**
  `CONSUMER_SETUP.md` and `NEW_PROJECT_END_TO_END.md` both stated that `UNITY_EMAIL`,
  `UNITY_PASSWORD` and `UNITY_LICENSE` "must be set together", quoting the `personal-combined`
  failure table. The pipeline's own gate says otherwise (`unity-pipeline.yml:487-490`):

  ```
  Need: (UNITY_EMAIL+UNITY_PASSWORD) or (UNITY_LICENSE)
  ```

  And it is not just the code: `Cuvara/IndieRPGMMOAdventure` builds Android and WebGL green on this
  toolkit with **`UNITY_LICENSE` as its only Unity secret** — run `34189744276`, 45m17s, both build
  jobs `success`, with `DISCORD_WEBHOOK_URL` the only other secret in the repository. The
  requirement was carried over from the toolkit's own container entrypoint on the `unity-build.yml`
  path, where it does hold, and applied to a path that delegates activation to
  `game-ci/unity-builder`.

  Both documents now lead with "a `.ulf` **or** credentials", show the one-secret setup first, and
  keep the all-three case where it belongs: a `.ulf` that cannot activate offline.

- **`6000.0.26f1-android`'s mutable tag pointed at a superseded image; rebuilt.** It resolved to the
  image from run 39 while run 42 had published `-42`, so `docker pull ...:6000.0.26f1-android`
  returned neither the newest build nor the one that had been scanned. Rebuilt under the corrected
  push logic (run 54): the mutable tag and `-54` now resolve to the same digest, and the run's own
  verification step asserted it. All six published tags were then checked directly against the
  registry and are consistent.

  The superseded `-39` and `-42` tags still exist and still point at the old images.
  `docs/NEW_PROJECT_END_TO_END.md` now carries the measured digests for all six tags and tells you
  to re-read them from the registry rather than trust a printed table.

  This also narrows the earlier claim about the double-build defect: four of the five variants built
  under the old code had consistent tags. The divergence needed a **pre-existing** tag on the same
  image name, which only `6000.0.26f1-android` had. The fix in `build-unity-image.yml` remains
  correct — it guarantees the pushed bytes are the scanned bytes — but the blast radius was one
  variant, not all of them.

- **Image build jobs were labelled by an input that names neither case.** `build-unity-image.yml`
  used `name: Build ${{ inputs.image-variant || 'all' }} image`, so a dispatch without a variant
  showed three jobs all called "Build all image", and a validation push — which builds `android`
  only — also read "Build all image". The label is now `matrix.variant`, so every job in the Actions
  UI says which image it is building. Cosmetic; no behaviour change.
- **Merging any `docker/**` change rebuilt all three Unity images and then threw them away.**
  `build-unity-image.yml` triggers on pushes to `main`, and every gated step was written as
  `if: inputs.<x> != false`. On a push event `inputs` is empty, an absent input is null, and null
  loosely equals false in GitHub expressions — so the condition evaluated **false** and the push,
  the manifest, the SBOM upload *and the vulnerability scan* were all skipped. Three matrix jobs
  built a Unity editor image each, ~18 minutes apiece, published nothing, scanned nothing, and
  reported success. Roughly 55 runner-minutes per merge for a green tick that verified less than it
  appeared to.

  Both flags are now resolved in bash from `github.event_name` and printed to the step summary, so
  what a run will do is stated rather than implied:

  | Trigger | Builds | Scans | Publishes |
  |---|---|---|---|
  | push to `main` (`docker/**` or this file) | `android` only | yes | **no** — a push is never a release |
  | `workflow_dispatch` | the chosen variant, or all three | unless `run-vulnerability-scan=false` | unless `push-image=false` |

  A validation push now costs one job instead of three, and it actually scans. Publishing stays
  deliberate: it requires a dispatch.

  A `concurrency` group was added at the same time, cancelling superseded **push** runs while
  leaving dispatches alone — this session accumulated several overlapping image builds, one of
  which had to be cancelled by hand.
- **A Trivy timeout stopped an image from being published at all.** `build-unity-image.yml` ran
  `aquasecurity/trivy-action` with no `timeout`, so it used the 5-minute default. Unity editor
  images are tens of gigabytes, and the `6000.3.9f1-linux` build died on it in run #47 after 19m46s:

  ```
  FATAL Fatal error run error: image scan error: scan error: scan failed:
  failed analysis: analyze error: pipeline error: context deadline exceeded
  ```

  The build, the smoke tests and the target check had all passed; only the scan timed out. Because
  the scan sits before the push, the job failed and **no image was published** — the same variant at
  `6000.0.26f1` had scanned fine minutes earlier, so the failure looked arbitrary. `timeout` is now
  `30m`.

  The action was also referenced as `@master`. That is an unpinned third-party action — a
  supply-chain risk and a reason a green run cannot be reproduced — so it is pinned to `v0.36.0`.
  `CONTRIBUTING.md` asks for SHA pins on all action references; the rest of this repository uses
  major-version tags, and this change matches that de-facto convention rather than being the only
  SHA-pinned entry.
- **`build-unity-image.yml` pushed a different image than the one it validated, and recorded a
  digest belonging to neither.** The workflow built twice: once with `push: false, load: true` (the
  copy the smoke tests, Trivy scan and SBOM examined) and again through a second
  `build-push-action` with `push: true`. The second invocation produces a fresh image — new
  provenance, new timestamps — so the registry received an artefact nothing in the run had
  inspected. `Publish image manifest` then recorded `steps.build.outputs.digest`, the digest of the
  **un-pushed** local build, into `pinned_reference` — the field `unity-release.yml` pins production
  releases to.

  Observed on `ghcr.io/cuvara/unity-editor` after run #42 built the `6000.0.26f1-android` image:

  | Reference | Digest |
  |---|---|
  | `6000.0.26f1-android` (mutable) | `sha256:1027ff7d…` |
  | `6000.0.26f1-android-39` | `sha256:1027ff7d…` — same image, an older run |
  | `6000.0.26f1-android-42` (that run) | `sha256:bbf4a306…` |
  | manifest artifact from that run | `sha256:e3b988b5…` |

  Three digests for one build, and a mutable tag left pointing at an older image while the run
  reported success. Pulling `6000.0.26f1-android` did not get you what had just been built or
  scanned.

  The push step now pushes the tags already carried by the loaded, validated image and resolves the
  digest back out of the registry, so validated, scanned, pushed and recorded are the same artefact.
  A new step then asserts that **every** tag from the run resolves to that digest and fails the run
  otherwise — the divergence above would not have passed silently.

  Not established: why the mutable tag sat on the older run's digest rather than the second build's.
  The double-build explains three digests existing; it does not by itself explain that assignment,
  and the run logs no longer hold enough to say. The new verification step makes the question moot
  going forward.
- **`docs/SELF_HOSTED_ORG_RUNNER.md` recommended a mitigation that a free-plan organization cannot
  use, and missed the reason a runner there receives no jobs at all.** Both were found by exercising
  the org API with `admin:org` after the document was written.

  Custom runner groups are a GitHub Team / Enterprise feature. `Cuvara` is on the **free** plan, so
  the only group is `Default` (id `1`, `visibility=all`) — it cannot be narrowed to selected
  repositories and no new group can be created. "Restrict the runner group to named repositories"
  was therefore not actionable there, despite being listed as mitigation 2.

  Worse, `Default` ships with **`allows_public_repositories=false`**, and a runner in such a group
  never receives jobs from a public repository — the job stays queued regardless of labels. Both
  repositories in this org are public, so a runner registered by following the original document
  would have sat idle with no diagnosable cause. The document now leads the troubleshooting table
  with that case and gives the three real options: flip the flag (org-wide exposure, and
  un-narrowable on free), **make the repository private** (recommended; `plan.private_repos`
  reports 10000), or upgrade to Team for scoped groups.

  Also recorded what was and was not exercised: both token endpoints were verified against `Cuvara`
  (29-character tokens, one-hour expiry); the runner-group creation call was not, for the plan
  reason above.

### Added
- **`templates/PlayerBuilder.cs`** — a working reference implementation of the build entry point the
  self-hosted lanes require. `docs/ADD_NEW_PROJECT.md` previously ended that section with "There is
  no reference implementation in this repository; copy one from a project that already builds with
  this toolkit", which left the one mandatory project-side piece as an exercise.

  It reads the real contract and nothing else — `BUILD_OUTPUT_DIR` (always `build`) and
  `ANDROID_APP_BUNDLE` — builds the enabled `EditorBuildSettings` scenes for
  `EditorUserBuildSettings.activeBuildTarget`, names the output per target (`.aab`/`.apk`, `.exe`,
  extensionless Linux, `.app`, WebGL directory), and **exits non-zero when
  `BuildPipeline.BuildPlayer` reports a failed result** — necessary because an Editor started with
  `-quit` otherwise exits 0 on a failed build, which is how a red build reaches CI as a green one.

### Fixed
- **`docs/ADD_NEW_PROJECT.md` promised Android keystore variables that never arrive.** Its
  `PlayerBuilder` sketch said `ANDROID_KEYSTORE`, `ANDROID_KEYSTORE_PASS`, `ANDROID_KEYALIAS_NAME`
  and `ANDROID_KEYALIAS_PASS` "are also supplied for Android". On this path they are not:
  `reusable-build-platform.yml` passes only `ANDROID_APP_BUNDLE` (`:770`) and `BUILD_OUTPUT_DIR`,
  and signing happens after the build on the host (`scripts/android/sign_android_build.sh`, invoked
  from `unity-build-android.yml:348`). A `PlayerBuilder` written against that sketch would read
  empty strings and silently produce an unsigned build.

- **The same document still described the docker lane as needing a consumer `PlayerBuilder`.** Its
  entry-point table now matches `ARCHITECTURE.md`: docker/game-ci uses game-ci's own builder, and
  only the self-hosted lanes substitute `PlayerBuilder.Build`.
- **`docs/SELF_HOSTED_ORG_RUNNER.md`** — how to register your own machine as an
  *organization* runner and route this toolkit's builds to it. Covers the org registration token,
  runner groups and repository access, `RUNNER_TYPE`/`BUILD_ENGINE`/`RUNNER_LABELS` set at org or
  repo scope, which jobs actually move to the runner (the orchestration jobs stay on
  `ubuntu-latest`), and the local-lane prerequisites — the exact Unity Editor version at the Hub
  default path, and a `PlayerBuilder.Build` method, which the docker lane does not need.

  It opens with the public-repository risk, because both repositories in this org are currently
  public and the consumer caller runs on `pull_request`: a fork PR on a public repo executes its
  own workflow code on the runner. The mitigations (approval for outside contributors, a runner
  group scoped to named repos, `--ephemeral`, no credentials on the machine, low-privilege service
  account) are stated before the registration commands.

- **`runner-type`, `build-engine` and `runner-labels` inputs on the caller template**, plus
  `self-hosted-macos` in `runner-mode`. Previously the template exposed only `runner-mode`
  `[docker, self-hosted-windows, auto]`, so a consumer could not select the two-axis runner
  configuration per run — only through repository variables.

### Fixed
- **`docs/SELF_HOSTED_WINDOWS_RUNNER.md` demanded a `unity` runner label that nothing requests.**
  It stated "All three labels must be present" (`self-hosted,Windows,unity`) and its troubleshooting
  table blamed queued jobs on a missing `unity` label. `grep '"unity"'` over `.github/workflows/`
  and `scripts/` returns nothing; the resolver requests `self-hosted,windows`. An extra label is
  harmless — `runs-on` needs a superset — so no runner needs re-registering, but the diagnosis was
  wrong.

- **The same document described a `runs-on` mechanism that no longer exists.** §5 showed
  "pseudo-logic implemented in reusable-build-platform.yml" picking labels from `runner-mode`. The
  build job consumes the resolver's label list verbatim (`reusable-build-platform.yml:230`).

- **No self-hosted document mentioned `PlayerBuilder.Build`**, which the local lane substitutes
  when `build-method` is empty (`:842-843` Windows, `:903` bash). A project moved to a self-hosted
  runner without that method builds nothing while the job reports success.

### Fixed
- **The consumer caller template pinned the workflow at `@v2` but the toolkit scripts at `v1`.**
  `templates/consumer-unity-build.yml` carried `toolkit-ref: 'v1'` next to
  `uses: ...unity-pipeline.yml@v2` — a combination its own comment and
  `docs/CONSUMER_SETUP.md` Step 2 both forbid. `v1` is frozen at `v1.1.3`, so every new repository
  copying the template ran v2 workflows against v1 scripts. Introduced by the 2.2.1 release edits,
  which repointed `uses:` and the comments but missed `toolkit-ref`.

- **`BUILD_CLEAN` could not take effect through the caller template.** The template declared
  `clean-build` as `type: boolean, default: false` while `unity-pipeline.yml` takes a tri-state
  string (`auto|true|false`, default `auto`, where `auto` defers to the `BUILD_CLEAN` repo
  variable). `${{ inputs.clean-build || false }}` resolved to `"false"` on every push and pull
  request, so `auto` was unreachable and the documented variable did nothing. It is now a choice
  input defaulting to `auto`.

- **`docs/CONSUMER_SETUP.md` Step 4 omitted `Windows64`** from the staging and release platform
  defaults, contradicting both `resolve_build_flow.sh:230-231` and the "What You Get" table in the
  same file. It also taught the deprecated ungrouped variable names
  (`DEVELOP_BUILD_PLATFORMS`, `DEVELOP_RUN_TESTS`, `DEFAULT_RUNNER_MODE`); it now uses the current
  grouped names and states that legacy names still resolve as a fallback.

- **`docs/ARCHITECTURE.md` named the wrong entry point for the default build lane.** It said the
  docker/game-ci lane runs `PlayerBuilder.Build` "implemented by the consuming project", citing
  `:872` and `:812` — line numbers that point at the macOS Unity-binary lookup and the Windows
  Addressables block. In fact `build-method` defaults to `''` (`:146`) and is passed straight to
  game-ci (`:574`), which means game-ci's own builder; the `PlayerBuilder.Build` fallback exists
  only in the self-hosted lanes (`:843`, `:903`). A docker-only project needs no `PlayerBuilder`.
  `CLAUDE.md` repeated the same error and is corrected too.

- **`docs/ADD_NEW_PROJECT.md` still said no tags were published** and that `@v2` did not exist.
  Both guides now open by stating which workflow they wire up, so it is clear that
  `CONSUMER_SETUP.md` (→ `unity-pipeline.yml`, no `BuildConfig/`) is the default path and
  `ADD_NEW_PROJECT.md` (→ `unity-build.yml`, `BuildConfig/` required) is the explicit-build path.

### Changed
- **`LICENSE` copyright holder is now `Cuvara`**, not `BuzzelStudio` — the original studio, left
  over from before the repository changed hands. The MIT terms themselves are untouched; only the
  holder line changed.

### Fixed
- **Restored CRLF line endings on two files that were normalised to LF by accident.**
  `examples/sample-unity-project-integration/README.md` and
  `unity-package/Packages/com.company.build-pipeline/package.json` were committed to CRLF, and the
  2.2.1 release edits rewrote them as LF — turning a two-line change into a 186-line whole-file
  diff. Content is unchanged; the files are back to 68 and 25 CR bytes, matching what they carried
  before. `.gitattributes` normalises only `*.sh` and `*.bash`, so nothing enforced this either way.

- **The documented image namespace still named the pre-migration owner.**
  `config/unity-build-defaults.json` read `imageNamespace: dycuong03/unity-editor`, and `README.md`,
  `docs/GITHUB_ACTIONS_BUILD_RUNBOOK.md` and `docs/UNITY_VERSION_UPGRADE.md` all pointed at
  `ghcr.io/dycuong03/unity-editor`. Every workflow actually pushes to
  `ghcr.io/${{ github.repository_owner }}/unity-editor`, which is `ghcr.io/cuvara/unity-editor` —
  so anyone following the docs looked for images in a namespace nothing publishes to.

  Nothing was broken at runtime: no code reads `imageNamespace` (only
  `tests/test_unity_version_resolution.py` asserts the field exists), and `.unityVersion` is the
  single field `resolve_unity_version.sh` actually consumes. This was stale documentation left over
  from the `dyCuong03` → `Cuvara` migration in `ee9a822`.

  The GHCR verification snippet in `docs/UNITY_VERSION_UPGRADE.md` also called
  `/users/dycuong03/packages/...`; `Cuvara` is an Organization, so that path is now
  `/orgs/Cuvara/packages/...` — the `/users/` form returns 404 for an org.

### Added
- **`CLAUDE.md` at the repository root** — onboarding notes for Claude Code sessions. Records the
  contracts that are not discoverable from a directory listing: that the CI gates are pytest and
  `bash -n` (shellcheck is advisory), that build logic lives in `scripts/common/resolve_build_flow.sh`
  rather than the workflow YAML, that runner and build engine are independent axes, which guard-rail
  tests fail on unrelated-looking changes, the documented disagreement between the two iOS entry
  points, and the `personal-combined` licensing requirement. Documentation only — no behaviour change.

---

## [2.2.1] — 2026-09-08

### Fixed
- **`Unity Release` announced a production release on this repository's own tags, having built
  nothing.** `pre-release-checks` carries `if: github.repository != 'Cuvara/unity-build-workflows'`
  — the toolkit repo is not a Unity project, so the gates and every build job that needs them skip
  correctly. `notify` was `if: always()`, so it alone survived and posted a Discord embed with
  `environment: production`, `status: skipped`, and an empty build-version and platform. It now
  also requires that the gates actually ran.

  This is what the ten failed `production` deployments dated 2026-06-30 to 2026-07-13 were the tail
  of. Their own cause — `image-digest is required for production releases` on tag-push — was fixed
  in `4bdf851`, and the repository guard in `ee9a822` stopped the chain from running here at all;
  the misleading notification was the last piece still firing.
- **`Final Report` — a required check on consumers' `develop` — passed a pipeline whose tests had
  failed, and passed a cancelled one.** Two defects, either of which turns a red run green.

  1. The gate matched only the literal string `failure`:
     ```bash
     [ "${result}" = "failure" ] && FAILED=1
     ```
     A **`cancelled`** job — the shape a timeout produces — sailed straight through. Not
     hypothetical: `Cuvara/IndieRPGMMOAdventure` collected three cancelled Unity Build runs on
     `staging` inside a week, one of them a 120-minute Addressables timeout that took every
     platform build down with it, and this gate called each of them a pass.
  2. **`R_TESTS`, `R_ADDR` and `R_IOS` were printed in the summary table but absent from the
     loop.** Final Report went green when Unity Tests failed. On `develop` that was masked,
     because Unity Tests is separately a required check there — nothing masks it anywhere else.

  Now every result the table prints is gated, `success` and `skipped` pass, and everything else —
  `failure`, `cancelled`, `timed_out`, or an empty value from a job that never reported — fails
  **and is named**. The old message was "One or more build jobs failed", which sent people to read
  seven job logs to find out which one.

- **`Notify Discord` announced a green pipeline when the tests had failed.** Same missing three
  results, in the env block and in both loops. This job already handled `cancelled` correctly, so
  the omission was its whole defect. Tests, validate and Addressables now count toward `fail` but
  not toward `ok` — `ok` exists to detect a *partial platform build*, and a passing test run is not
  a platform that built.

- **A build lane that has never completed can now start from another lane's warm `Library`.**
  `restore-keys` gained a bare `Library-` fallback behind the platform-scoped one.

  The cache key is `Library-<platform>-<hashFiles(ProjectVersion.txt, manifest.json)>`. Neither
  hashed file is platform-specific, so **the hash is identical for every lane** — only the label
  in front of it differs. Measured on `Cuvara/IndieRPGMMOAdventure`, 2026-08-21:

  ```
  Library-Android-ba667c16…    3666 MB
  Library-WebGL-ba667c16…      1649 MB
  Library-test-All-ba667c16…   1334 MB
  ```

  The Addressables lane asked for `Library-Addressables-ba667c16…`, missed, fell back to
  `Library-Addressables-`, missed again, and did a **fully cold import of 6000+ package assets on
  every run** — with 3.6 GB of a warm `Library` at the *identical content hash* sitting unused.

  It could not recover on its own: `actions/cache` writes only in the post step of a job that
  finishes, and that lane was being cancelled by the 120-minute timeout, so it never saved a cache
  to hit next time. A lane that cannot finish can never warm itself; only a cross-lane fallback
  breaks that.

  A `Library` from another build target is not wrong, only incomplete — Unity reimports the
  platform-specific artefacts and keeps the rest, which is far cheaper than importing everything
  from nothing.

### Changed
- **The `Addressables` lane names its build target instead of leaving it empty.** An empty target
  let GameCI apply its own default, which *is* `StandaloneLinux64` — confirmed from a build log
  (`-buildTarget StandaloneLinux64`), not from documentation. Behaviour is unchanged; the lane's
  actual target is now readable from the workflow instead of recoverable only from a log.

### Fixed

- **`ARCHITECTURE.md` drew the Docker lane as running `BuildCommand.Execute`; it runs
  `PlayerBuilder.Build`.** Both diagrams and the package-layer bullet corrected, and a *Build Entry
  Points* section added stating the two lanes side by side. `IOS.md` was right for the iOS-native
  lane and is left saying `BuildCommand.Execute`, now with the part that was missing: that entry
  point is `readonly` in `scripts/ios/run_unity_ios.sh:46`, unaffected by `build-method` or
  `UNITY_BUILD_METHOD`, and the package is a hard requirement there. Both files record that
  `reusable-build-platform.yml` never calls that script, so the two iOS routes disagree.
- **`docs/ADD_NEW_PROJECT.md` claimed "Your project does not need to implement build logic", which is
  false on the default lanes.** `reusable-build-platform.yml` runs `PlayerBuilder.Build` unless
  `build-method` says otherwise, and `PlayerBuilder` is implemented **by the consuming project** — so
  a project that installs the package and stops there has no entry point on Android, WebGL, Windows,
  Linux or the pipeline's own `Build iOS` job. New *Step 1b* states which lane runs which entry point,
  shows the signature `PlayerBuilder.Build` must have (public static, parameterless, global namespace,
  Editor assembly) and the environment variables CI supplies, and documents `UNITY_BUILD_METHOD` as
  the supported override. It also records the inconsistency rather than smoothing it: the iOS-native
  scripts hardcode `BuildCommand.Execute` as `readonly`, `reusable-build-platform.yml` never calls
  them, and `UNITY_BUILD_METHOD` does not reach that route.

- **`com.company.build-pipeline` did not compile against Unity 6, so `BuildCommand` did not exist at
  all.** Two unrelated causes, both fatal:
  - **Namespace shadowing, not a missing reference.** All four platform builders call
    `BuildPipeline.BuildPlayer(...)` from inside `namespace Company.BuildPipeline.Editor`, where the
    identifier `BuildPipeline` binds to the enclosing `Company.BuildPipeline` namespace rather than
    to `UnityEditor.BuildPipeline` — the nearer scope wins, and `using UnityEditor;` cannot override
    it. Hence `CS0234: 'BuildPlayer' does not exist in the namespace 'Company.BuildPipeline'`. Call
    sites are now fully qualified as `UnityEditor.BuildPipeline.BuildPlayer(...)`. This was never
    version-specific: the code could not have compiled on any Unity version.
  - **Unity 6 API removal.** `PlayerSettings.iOS.architecture` and the `iOSArchitecture` enum are
    gone (verified absent from the 6000.3.9f1 managed assemblies); the iOS player is ARM64-only, so
    there is no setting left to write. ARM64 was already the only App Store-valid value, so no
    capability is lost, but a config requesting anything else now logs a warning instead of being
    silently ignored.

- `unity-package/Packages/com.company.build-pipeline/package.json` — version was `1.0.0` while the
  consumer-pin tags had run to `v1.1.0`, so a package resolved by tag reported a manifest version
  that disagreed with the tag it came from — misleading to anyone resolving a version conflict.
  Set to `1.1.2` to match the tag that carries it. The repo-level `VERSION` (2.2.0) and the release
  history below are a separate series and are untouched.

### Added

#### Generic Consumer Integration (`feature/generic-consumer-integration`)

- `templates/BuildConfig.*.json` — De-game-ified all four BuildConfig templates: replaced `Acme Studios` / `com.acmestudios.myunitygame` / multi-scene game setup with generic `ExampleCompany` / `ExampleProject` / `com.example.project` / `Assets/Scenes/Main.unity`. Overlay templates (`development`, `staging`, `production`) now carry only environment-specific diffs; `base.json` is the complete, schema-valid source of truth.
- `templates/build-secrets.example.md` — Rewritten as a full secret matrix with required-for columns: **Development / Staging / Production / Artifact-only / Store-deploy**. Covers all secret groups: `UNITY_*`, `ANDROID_*`, `GOOGLE_PLAY_*`, `IOS_*`, `APP_STORE_CONNECT_*`, `DISCORD_WEBHOOK_URL`. Production secrets (`APP_STORE_CONNECT_*`, `GOOGLE_PLAY_*`) marked for GitHub Environment scoping.
- `docs/ADD_NEW_PROJECT.md` — Rewritten as a consumer-centric onboarding guide. Documents the consumer contract: Unity project + BuildConfig + UPM package dependency + small caller workflow + secrets. Includes the canonical caller YAML pattern (`uses: <WORKFLOW_OWNER>/unity-build-workflows/...@<ref>`), UPM manifest example, toolkit-checkout note, and `<ref>` guidance (dev=`@main`/SHA, stable=exact tag). Fixes iOS/Windows platform status: **iOS is supported** via the macOS lane; Windows is unsupported.
- `docs/ARCHITECTURE.md` — Updated consumer diagram to use `<WORKFLOW_OWNER>` placeholder and correct `<ref>` guidance.
- `docs/IMAGE_LIFECYCLE.md` — Added "Bootstrap" section: explains that a compatible image must be published before consumers can use it; documents the dev bootstrap path (manual `build-unity-image.yml` dispatch), the production digest-pinned path, and the actionable error when no image is found in the registry.
- `docs/ANDROID.md` — Added image bootstrap reference and aligned `<WORKFLOW_OWNER>` placeholders.
- `docs/SECURITY.md` — Standardized iOS secret names (`IOS_DISTRIBUTION_CERTIFICATE_BASE64`, `APP_STORE_CONNECT_PRIVATE_KEY`) throughout; aligned with secret matrix; added `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` to rotation policy.

### Changed

- `README.md` — Consumer-centric rewrite: UPM package step added as Step 1; `<WORKFLOW_OWNER>` placeholder used everywhere `BuzzelStudio` appeared; platform table references `docs/PLATFORM_MATRIX.md` as canonical source; versioning policy clarified: `@vMAJOR` tags do not exist yet, dev→`@main`/SHA, stable→exact tag; image disclaimer added (images must be published before use).
- `CHANGELOG.md` — All `BuzzelStudio` GitHub URL references replaced with `<WORKFLOW_OWNER>` placeholder.
- `CONTRIBUTING.md` — Clone URL and `@BuzzelStudio/mobile` team reference replaced with `<WORKFLOW_OWNER>` placeholder.
- All docs — `ghcr.io/buzzelstudio/unity-builder` replaced with `ghcr.io/<WORKFLOW_OWNER>/unity-builder` throughout owned files.

### Fixed

- **iOS supported/unsupported contradiction** — `docs/ADD_NEW_PROJECT.md` previously stated "iOS and Windows are **not supported**" — corrected to reflect that iOS is supported via the `macos-unity-xcode` executor (added in v2.1.0). Windows remains unsupported. Platform status is now consistent across `README.md`, `docs/ARCHITECTURE.md`, `docs/PLATFORM_LIMITATIONS.md`, and `docs/ADD_NEW_PROJECT.md`.
- **`macos-latest` claim removed** — `docs/ARCHITECTURE.md` and `docs/PLATFORM_LIMITATIONS.md` previously listed `macos-latest` as a valid runner for iOS. Corrected to `macos-13` only (approved, validated runner).
- **`@v2` / `@v2.0.0` tag claims** — All references claiming a specific version tag (e.g. `@v2`, `@v2.0.0`, `@v2.1.0`) is currently valid have been corrected. No version tags have been published yet. Documentation now distinguishes `@main` (dev), exact SHA (pinned), and `@vX.Y.Z` (stable — use once a release is published).
- **Package ID / UPM path** — `com.example.build-pipeline` was incorrectly used; corrected to `com.company.build-pipeline` (the canonical, intentionally neutral identifier). UPM path corrected to `/unity-package/Packages/com.company.build-pipeline#<WORKFLOW_REF>` (verified against on-disk structure).
- **Registry namespace** — Image references now use `ghcr.io/<IMAGE_NAMESPACE>/unity-builder` (configurable via `--image-namespace`) rather than a hardcoded org name.

### Changed

- **The git tag series now matches this changelog.** Tags and GitHub releases had run on a `v1.x`
  series (latest `v1.1.3`) while `VERSION`, `README.md` and this file documented `2.2.0` — two
  version numbers for one commit. Tags are the series consumers pin, so the tags move to the
  documented number: this release is tagged `v2.2.1`, and `@v2` is published as the floating major
  tag.

  **`@v1` is frozen at `v1.1.3` and receives no further releases.** Consumers pinned to `@v1` or
  `@v1.x` keep building against that commit until they repin to `@v2`. Every shipped pin — the
  `templates/`, `examples/` and `docs/CONSUMER_SETUP.md` references — now reads `@v2`.

- **`unity-package/.../package.json` bumped `1.1.3` → `2.2.1`** to stay aligned with the tag that
  carries it, since the UPM pin is a tag reference (`?path=…#<tag>`). The `v1.1.3` tag is untouched,
  so an existing `#v1.1.3` pin keeps resolving.

---

## [2.2.0] — 2026-06-18

### Added

#### Discord Build Notifications

- `.github/actions/discord-notify/action.yml` — New composite action that posts a Discord embed on build completion via `curl` (no third-party action, no supply-chain exposure).
  - Inputs: `status`, `platform`, `environment`, `build-version`, `run-url`, `artifact-name` (optional), `extra-text` (optional).
  - Webhook URL read from `DISCORD_WEBHOOK_URL` environment variable — never an action input.
  - No-ops gracefully when `DISCORD_WEBHOOK_URL` is unset or empty (exit 0, notice log).
  - Uses `set +x` and `::add-mask::` to prevent the webhook URL from appearing in logs.
  - `curl` failures are non-fatal (`::warning::` only); a Discord outage cannot block a release.
  - Payload JSON constructed via `python3` for safe quoting; validated before sending.
  - Embed includes: title with status emoji (✅/❌/⚠️), color (green/red/grey), repository, platform, environment, version, short commit SHA, triggered-by, run URL, and (when available) artifact name.

#### Workflow wiring (`if: always()` — notifies on success AND failure)

- `unity-build.yml` — `DISCORD_WEBHOOK_URL` added to `secrets:` block; `discord-notify` step added to `report` job; reports the platform-resolved build result.
- `unity-build-ios.yml` — `DISCORD_WEBHOOK_URL` added to `secrets:` block; `discord-notify` step added as final step of `build` job.
- `unity-release-ios.yml` — `DISCORD_WEBHOOK_URL` passed via job-level `env:`; `discord-notify` step added as final step of `release-build` job (after signing cleanup).
- `unity-release.yml` — New dedicated `notify` job added with `if: always()`, depending on `pre-release-checks`, `release-test`, `release-build`, and `create-github-release`. Reports `release-build` result.

#### Documentation

- `docs/DISCORD_NOTIFICATIONS.md` — Full guide: what it does, creating a Discord webhook, which workflows notify, success/failure/cancelled behaviour, no-op-when-unset, security notes, example embed, and troubleshooting table.

### Changed

- `README.md` — Added `DISCORD_WEBHOOK_URL` to secrets section, added Step 5 for Discord setup, added `docs/DISCORD_NOTIFICATIONS.md` to the documentation index, bumped current version to 2.2.0.
- `docs/SECURITY.md` — Added "Discord Webhook Secret Handling" section documenting `set +x`, `::add-mask::`, env-var-not-input pattern, payload content policy, and rotation instructions. Added `DISCORD_WEBHOOK_URL` to the rotation policy table.
- `docs/ADD_NEW_PROJECT.md` — Replaced placeholder Step 7 with concrete Discord notification setup instructions.
- `docs/ARCHITECTURE.md` — Added `discord-notify/` to the Composite Action Layer diagram.

---

## [2.1.0] — 2026-06-18

This release adds production iOS support via a dedicated macOS executor lane.
iOS was previously unsupported (Docker-only platform). It is now a first-class
build target via the `macos-unity-xcode` executor.

**Semver guidance:** This is a **minor** (feature) release. The workflow input
interface gains a new reusable workflow (`unity-build-ios.yml`) and new
BuildConfig `ios` fields, but the existing Docker-lane interface is unchanged.
Consumer repositories targeting Android/WebGL/Linux do not need to update.

### Added

#### iOS Pipeline
- `unity-build-ios.yml` — Reusable workflow for the full iOS pipeline:
  Unity → Xcode project generation → archive → IPA export → (optional) TestFlight upload.
- `scripts/ios/build_ios.sh` — Unity batch-mode iOS build (Xcode project generation).
- `scripts/ios/setup_signing.sh` — Temp keychain creation, certificate import, provisioning profile install, ASC key write.
- `scripts/ios/archive_ios.sh` — `xcodebuild archive` with workspace/project auto-detection and scheme resolution.
- `scripts/ios/export_ios.sh` — `xcodebuild -exportArchive` with auto-generated `ExportOptions.plist`.
- `scripts/ios/upload_testflight.sh` — TestFlight upload via `xcrun altool` / `notarytool`.
- `scripts/ios/cleanup_ios.sh` — Unconditional cleanup: temp keychain, provisioning profile, ASC key file.
- `scripts/common/resolve_platform_executor.py` — Platform→executor resolver. iOS+macOS → `macos-unity-xcode`; Docker platforms+linux → `docker-unity`. Exits non-zero for cross-lane mismatches.

#### iOS BuildConfig Fields (new in `iOS` section)

> **Canonical key is `iOS`.** The lowercase alias `ios` is deprecated-but-accepted
> (schema `$refs` both to one definition). Use `iOS` in all new configs and templates.
- `marketingVersion` — CFBundleShortVersionString
- `sdkVersion` — `iphoneos` or `iphonesimulator`
- `architecture` — `ARM64` or `x86_64`
- `xcodeVersion` — pinned Xcode version
- `developmentTeamId` — 10-char Apple Team ID
- `signingStyle` — `manual` or `automatic`
- `provisioningProfileSpecifier` — profile name for manual signing
- `codeSignIdentity` — signing identity string
- `enableBitcode` — boolean (default false)
- `generateSymbols` — boolean (default true)
- `uploadSymbols` — boolean (default false)
- `uploadToTestFlight` — boolean (default false)

#### Tests
- `tests/test_ios_build_config.py` — iOS BuildConfig schema validation (valid, invalid bundle IDs, enums, contract fields).
- `tests/test_ios_executor_resolution.py` — Platform→executor resolution, iOS-on-Linux rejection, Docker-on-macOS rejection.
- `tests/test_ios_shell_scripts.py` — Shell script tests: archive/export success/failure, secret redaction, cleanup, artifact contract, missing IPA/archive, TestFlight rejection, fork rejection, Unity failure simulations.
- `tests/fixtures/valid_ios_config.json` — Full iOS config fixture.
- `tests/fixtures/valid_ios_minimal.json` — Minimal iOS config fixture.
- `tests/fixtures/invalid_ios_bundle_id.json` — Invalid bundle ID fixture.
- `tests/fixtures/fake_xcodebuild.sh` — Fake xcodebuild for macOS-free testing.

#### Documentation
- `docs/IOS.md` — Full iOS pipeline guide.
- `docs/IOS_SIGNING.md` — Certificate, provisioning profile, and ASC key setup.
- `docs/IOS_RELEASE.md` — Release workflow, GitHub Environment, versioning, TestFlight.

### Changed
- `docs/PLATFORM_LIMITATIONS.md` — iOS section rewritten from "unsupported" to "supported via macOS". Windows remains unsupported.
- `docs/ARCHITECTURE.md` — Added `macos-unity-xcode` executor lane alongside `docker-unity`. Added iOS to supported platforms table.
- `docs/BUILD_CONFIG.md` — `ios` object section fully documented with all contract fields.
- `docs/SECURITY.md` — Added iOS secret inventory, temp credential lifecycle, macOS-specific guidance, and updated rotation policy table.
- `docs/TROUBLESHOOTING.md` — Added iOS section: cert errors, Xcode migration, TestFlight issues, profile expiry, cert rotation.
- `README.md` — Added iOS integration example, iOS secrets reference, updated platform table and documentation index.

### Migration from v2.0.0

Existing Android/WebGL/Linux workflows are **unaffected**. iOS migration:

1. Add the `ios` BuildConfig block to your `base.json` (see [docs/IOS.md](docs/IOS.md))
2. Add iOS GitHub Secrets (see [docs/IOS_SIGNING.md](docs/IOS_SIGNING.md))
3. Create a caller workflow using `unity-build-ios.yml@v2`
4. For releases: create a `production` GitHub Environment (see [docs/IOS_RELEASE.md](docs/IOS_RELEASE.md))
5. If you previously redirected users away from iOS using `docs/PLATFORM_LIMITATIONS.md`, update any internal documentation pointing to that "unsupported" section.

---

## [2.0.0] — 2026-06-12

### BREAKING CHANGES

This release migrates the entire build platform from native Unity Editor execution to Docker-mandatory containers. **All consuming repositories must update their workflow references from `@v1` to `@v2`.**

#### Removed Workflows
- **`unity-build-ios.yml`** — iOS is unsupported by the Docker-only platform. Use a dedicated macOS pipeline. See [docs/PLATFORM_LIMITATIONS.md](docs/PLATFORM_LIMITATIONS.md).
- **`unity-build-windows.yml`** — Windows is unsupported by the Docker-only platform. Use a dedicated Windows pipeline.

#### Removed Workflow Inputs
- `executor-mode` — Docker is now mandatory and implicit.
- `use-docker` — Removed; Docker is the only executor.
- `native-runner` — Removed; no native execution path exists.

#### Removed Actions
- `actions/setup-unity/` — Docker images have Unity pre-installed. No host-side Unity installation needed.

#### Removed Scripts
- `scripts/run_unity_build.py` — Replaced by `scripts/docker/run_unity_container.py`.
- `scripts/ios/` — iOS pipeline removed.
- `scripts/windows/` — Windows pipeline removed.

#### Changed Workflow API
- All build/test workflows now require Docker Engine on the runner.
- `runs-on` changed to `ubuntu-latest` for all supported builds (was platform-specific).
- Image resolution is automatic from the approved image manifest.
- Nightly build matrix reduced to `[Android, WebGL, Linux64]`.

### Added

#### Docker Platform
- `docker/unity/Dockerfile` — Base Unity image extending pinned GameCI base.
- `docker/variants/android.Dockerfile` — Android build image with SDK/NDK/JDK.
- `docker/variants/webgl.Dockerfile` — WebGL build image.
- `docker/variants/linux.Dockerfile` — Linux standalone and dedicated server image.
- `docker/unity/entrypoint.sh` — Strict container entrypoint supporting build, test, validate, and inspect commands.
- `docker/unity/healthcheck.sh` — Image health verification.
- `docker/unity/activate-license.sh` — Ephemeral license activation.
- `docker/unity/return-license.sh` — License cleanup.
- `docker/metadata/image-manifest.schema.json` — Image manifest JSON Schema.

#### Docker Scripts
- `scripts/docker/run_unity_container.py` — Single entry point for running Unity in Docker (CI and local).
- `scripts/docker/build_unity_image.py` — Image build automation.
- `scripts/docker/validate_unity_image.py` — Image validation and compliance.
- `scripts/docker/resolve_image_reference.py` — Target platform to image resolution.
- `scripts/docker/generate_sbom.sh` — SBOM generation.
- `scripts/docker/scan_image.sh` — Vulnerability scanning.

#### New Workflows
- `build-unity-image.yml` — Dedicated image build, scan, and publish workflow.
- `scan-unity-image.yml` — Scheduled image vulnerability scanning.
- `unity-build-linux.yml` — Linux standalone and dedicated server builds.

#### New Actions
- `actions/resolve-unity-image/` — Resolve target platform to approved image.
- `actions/run-unity-container/` — Standardized Docker container execution.
- `actions/restore-docker-cache/` — Docker volume-based Library cache.

#### New Documentation
- `docs/DOCKER_BUILD.md` — Container execution flow documentation.
- `docs/IMAGE_LIFECYCLE.md` — Image strategy, scanning, SBOM, tagging.
- `docs/LINUX.md` — Linux platform documentation.
- `docs/PLATFORM_LIMITATIONS.md` — iOS/Windows exclusion rationale and alternatives.
- `docs/adr/001-docker-mandatory-architecture.md` — Architecture decision record.

#### Schemas
- `schemas/unity-image-manifest.schema.json` — Image manifest validation schema.

#### Tests
- `tests/test_docker_command.py` — Docker command construction tests.
- `tests/test_image_resolution.py` — Image reference resolution tests.
- `tests/test_image_manifest.py` — Image manifest schema tests.
- `tests/test_secret_redaction.py` — Secret leak prevention tests.
- `tests/test_no_native_unity_invocation.py` — Regression test preventing native Unity execution.
- `tests/test_entrypoint.py` — Entrypoint integration tests with fake Unity.

#### Platform Support
- Linux Standalone (Linux64) builds via Docker.
- Linux Dedicated Server (LinuxServer) builds via Docker.

### Changed
- All Unity compilation, tests, and builds now execute inside Docker containers.
- Cache strategy updated to use Docker volumes instead of host filesystem.
- License handling redesigned for ephemeral containers.
- All workflows use `ubuntu-latest` runners.
- Security model updated for container isolation.

### Migration Guide

1. Update workflow references: `@v1` → `@v2`
2. Remove `executor-mode`, `use-docker`, `native-runner` inputs from caller workflows.
3. Remove iOS and Windows build jobs (use dedicated non-Docker pipelines).
4. Ensure runners have Docker Engine available.
5. Update `UNITY_LICENSE` secret to contain the `.ulf` file content.
6. See [docs/ADD_NEW_PROJECT.md](docs/ADD_NEW_PROJECT.md) for the updated integration guide.

---

## [1.0.0] — 2024-06-12

### Added

#### Core Platform
- Reusable workflow `android.yml` — Android APK and AAB builds with debug and custom keystore signing, IL2CPP and Mono backends, configurable SDK versions and architectures.
- Reusable workflow `ios.yml` — Unity → Xcode project generation → IPA export pipeline with manual and automatic signing, App Store Connect upload via API key.
- Reusable workflow `windows.yml` — Windows Standalone x86_64 and x86 builds with IL2CPP (MSVC) and Mono support, optional output compression.
- Reusable workflow `webgl.yml` — WebGL builds with Brotli, Gzip, and Disabled compression formats; configurable memory size and HTML template.
- Reusable workflow `test.yml` — Unity Test Runner (EditMode and PlayMode) as a standalone workflow step.
- Reusable workflow `release.yml` — Tag-triggered production build and promotion pipeline with environment gating.

#### Configuration
- `schemas/unity-build-config.schema.json` — JSON Schema (Draft-07) validating the full BuildConfig object.
- Template BuildConfig files for base, development, staging, and production environments.

#### Unity Package
- `unity-package/com.company.build-pipeline/` — Unity Editor package with BuildCommand, validation rules, platform builders, and build hooks.

#### Documentation
- Complete documentation suite covering architecture, onboarding, platform guides, security, and troubleshooting.

---

[Unreleased]: https://github.com/<WORKFLOW_OWNER>/unity-build-workflows/compare/v2.2.5...HEAD
[2.2.5]: https://github.com/<WORKFLOW_OWNER>/unity-build-workflows/compare/v2.2.4...v2.2.5
[2.2.4]: https://github.com/<WORKFLOW_OWNER>/unity-build-workflows/compare/v2.2.3...v2.2.4
[2.2.3]: https://github.com/<WORKFLOW_OWNER>/unity-build-workflows/compare/v2.2.2...v2.2.3
[2.2.2]: https://github.com/<WORKFLOW_OWNER>/unity-build-workflows/compare/v2.2.1...v2.2.2
[2.2.1]: https://github.com/<WORKFLOW_OWNER>/unity-build-workflows/compare/v2.2.0...v2.2.1
[2.2.0]: https://github.com/<WORKFLOW_OWNER>/unity-build-workflows/compare/v2.1.0...v2.2.0
[2.1.0]: https://github.com/<WORKFLOW_OWNER>/unity-build-workflows/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/<WORKFLOW_OWNER>/unity-build-workflows/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/<WORKFLOW_OWNER>/unity-build-workflows/releases/tag/v1.0.0
