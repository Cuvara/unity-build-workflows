# Changelog

All notable changes to `unity-build-workflows` are documented here.

This project adheres to [Semantic Versioning](https://semver.org/) and [Conventional Commits](https://www.conventionalcommits.org/).

The public API is the set of reusable workflow inputs/outputs documented in [docs/BUILD_CONFIG.md](docs/BUILD_CONFIG.md). Changes to that interface that require consumer updates are marked as **BREAKING**.

---

## [Unreleased]

### Added

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

### Fixed

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

### Changed

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
