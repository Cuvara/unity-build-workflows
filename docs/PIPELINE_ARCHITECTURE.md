# Pipeline Architecture

How the CI/CD graph is organised, what each node means, and how to extend it.

This document is the contract for pipeline **orchestration** — stages, node
names, artifact flow, publish flow, approval. It does not restate what any
individual build does; for that see [BUILD_CONFIG.md](BUILD_CONFIG.md),
[BRANCH_FLOW_CONTRACT.md](BRANCH_FLOW_CONTRACT.md) and
[REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md).

Pinned by `tests/test_pipeline_stages.py`. Change the graph, change that file.

---

## 1. Stages

Every user-visible node belongs to exactly one stage, and says so in its name.

| Stage | Name | What it is for |
|---|---|---|
| 01 | PREPARE | Resolve configuration, validate the project and the licence |
| 02 | QUALITY GATE | Unity Tests + the gate node that blocks everything expensive |
| 03 | BUILD ARTIFACTS | One node per platform, fanned out, independent |
| 04 | ARTIFACT VALIDATION | One node per artifact, independent |
| 05 | PUBLISH | Upload a validated artifact to a store / host |
| 06 | RELEASE | Promotion to production, behind an approval boundary |
| 07 | REPORT | Final report |
| 08 | NOTIFY | Discord notification |

Stages 03 and 04 are **matrix jobs**: the graph contains exactly the platforms
that were selected. An Android-only run draws one build node and one validation
node — not six greyed-out platforms nobody asked for.

```
01 PREPARE
   01 / Resolve Build Config
        ↓
   01 / Validate Unity Project ──┐
   01 / Validate Unity License ──┤
                                 ↓
02 QUALITY GATE
   02 / Unity Tests
        ↓
   02 / Quality Gate  ◀── everything below waits on this single node
        ↓
03 BUILD ARTIFACTS          (fan-out, mutually independent)
   03 / Addressables
        ↓
   03 / Android / Production / AAB
   03 / iOS / Production / Xcode Project
   03 / WebGL / Production / WebGL
   03 / Linux64 / Production / Linux64
   03 / LinuxServer / Production / LinuxServer
   03 / Windows64 / Production / Windows64
        ↓
04 ARTIFACT VALIDATION      (one edge each, no cross-platform coupling)
   04 / Android / Validate AAB
   04 / iOS / Validate Xcode Project
   04 / WebGL / Validate
        ↓
07 / Final Report
        ↓
08 / Notify Discord
```

Stages 05 and 06 do not exist in the build pipeline — they live in the release
pipelines. That separation is the point: see §4.

---

## 1a. Three layers — CI, Build, Release

Stages 01–08 describe the *engine*. What a developer clicks is a separate
concern, and it is three layers, not one.

| Workflow | Trigger | Question it answers |
|---|---|---|
| `01-ci.yml` — **CI / Validate & Test** | push / PR | *Is this code safe to merge?* Validates, tests, reports. **Builds no player.** |
| `10-build-development.yml` — **Build / Development** | manual | *Give me something to test with.* Android ships an APK. |
| `11-build-release.yml` — **Build / Release** | manual | *Give me something we could ship.* Signed, store-shaped, immutable. |
| `20-release-android.yml` — **Release / Android** | manual | Promote a release AAB to Google Play. |
| `21-release-ios.yml` — **Release / iOS** | manual | Promote a release IPA to App Store Connect. |
| `22-release-webgl.yml` — **Release / WebGL** | manual | Promote a release WebGL build to hosting. |

All of them call the same `unity-pipeline.yml` engine. None contains build
logic. Templates live in `templates/consumer-*.yml`;
`tests/test_entry_workflows.py` pins the contract.

The numeric prefixes order the Actions sidebar by layer instead of
alphabetically — the list reads CI, then build, then release.

### Build / Release is not Build / Development with `environment=production`

They differ on their own axis, `build-type`, which is what names the artifacts:

| Platform | Development | Release |
|---|---|---|
| Android | `development-android-apk` | `release-android-aab` |
| iOS | `development-ios-xcodeproj` | `release-ios-ipa` (the Xcode project is uploaded too, marked as an intermediate) |
| WebGL | `development-webgl` | `release-webgl` |
| Windows | `development-windows` | `release-windows` |
| Linux | `development-linux` | `release-linux` |
| Linux (server) | `development-linux-server` | `release-linux-server` |

Slugs are the platform a person would name, not the internal Unity target id —
`release-windows`, not `release-windows64-exe`. The artifact type is appended
only where it distinguishes two real outputs, so Android carries `apk`/`aab`
and WebGL does not become `webgl-webgl`.

A caller that omits `build-type` gets the historical mapping from
`environment`, so consumers that predate the axis keep working.

`environment`, `build-type` and `platform` are three separate things:

```
build-type   development | release        what shape the artifact is
environment  development | staging | production   what config it is built with
platform     Android | iOS | WebGL | Windows64 | Linux64 | LinuxServer
```

### The CI lane builds nothing

`01-ci.yml` passes `platform: None`. That has to be honoured in the matrix
step rather than the resolver, because on a push the resolver takes the
platform set from the branch's `*_BUILD_PLATFORMS` variable and never looks at
the input. Without it, every merge check paid for six Unity builds.

### Why separate files rather than a smarter form

`workflow_dispatch` has **no conditional input visibility**. A single
multi-platform form therefore shows every platform's options to everyone:
choosing `Target platform: iOS` still left `Android output: APK/AAB` on screen,
and the form had grown to fourteen fields.

Faking conditionality — an input silently ignored for the platform you picked —
trades a visible problem for an invisible one. Splitting the **entry point** is
the only mechanism that yields a form containing just the applicable options,
and it costs nothing structurally because the implementation is shared:

```
01-ci.yml               ──┐
10-build-development.yml──┼──→ unity-pipeline.yml ──→ reusable-build-platform.yml
11-build-release.yml    ──┘        (stages 01–08)          (the one executor)

20-release-android.yml  ──→ pipeline-android-release.yml ──→ unity-build-android.yml
21-release-ios.yml      ──→ pipeline-ios-release.yml     ──→ unity-build-ios.yml
22-release-webgl.yml    ──→ pipeline-webgl-release.yml   ──→ unity-build-webgl.yml
```

Nesting: entry → pipeline → executor = **3 of GitHub's 4** levels.

### Input groups

The dispatch form cannot group fields, so each input's description carries its
group as a prefix and the declaration order is the display order:

| Group | Inputs | Who changes them |
|---|---|---|
| `GENERAL` | platform, environment | everyone |
| `ANDROID` | `android-export` — **Build / Release only** | on a release |
| `QUALITY` | run-tests, test-mode | everyone |
| `CONTENT` | build-addressables | everyone |
| `UNITY` | unity-version, clean-build, define-symbols | occasionally |
| `ADVANCED` | runner-type, build-engine, runner-labels | rarely |

`ADVANCED` defaults to `auto`, meaning "use the repository variable", which the
engine spells as an empty string. Write that translation as
`inputs.x != 'auto' && inputs.x || ''` — the reverse,
`inputs.x == 'auto' && '' || inputs.x`, can never produce `''`, because
GitHub's `&&`/`||` return operands and `''` is falsy. That bug shipped once and
failed every dispatch with `Invalid runner-type='auto'`.

### What is deliberately not on these forms

A knob the engine ignores is a lie in the form.

- **`android-export` on Build / Development.** Development always ships an APK;
  offering the choice would invite an AAB labelled `development-`.
- **WebGL compression.** `compress_webgl.sh` runs in `unity-build-webgl.yml`,
  the deployment lane, not the lane these entry points drive. Stage 04 still
  validates that whatever Unity emitted is *consistent*.
- **iOS signing/export.** Certificates live in the release pipeline. Stage 03
  emits the Xcode project; the IPA comes from `pipeline-ios-release.yml`.
- **A platform picker on Release / \*.** The platform *is* the workflow.

### Windows and Linux

Both are first-class Unity build targets (`StandaloneWindows64`,
`StandaloneLinux64`, and `+Server` for the dedicated-server subtarget) and
appear in both build matrices.

Both have a release workflow now that a real distribution target exists:
`23-release-windows.yml` and `24-release-linux.yml` promote to Steam. They
arrived with no change to the build layer, which is what the split was for.

Platform and distribution provider stay separate (I-015). Windows and Linux are
PLATFORMS: `Build / Release` never mentions Steam, so no Steam variable or
secret can prevent a desktop build, and a test fails if one appears there.
Steam is a DISTRIBUTION PROVIDER: its configuration gates publishing, and it
fails loudly when absent rather than skipping quietly. See
`docs/STEAM_DISTRIBUTION.md`.

Both artifacts go through stage 04 like everyone else, using the shared
`desktop` validator (`scripts/desktop/validate_desktop_artifact.py`): the
executable exists and is the right kind, its `<Product>_Data` directory is
beside it with an engine payload and game code inside, the Unity runtime
library is present, and on Linux the executable bit survived the artifact
round-trip. A player missing any of those starts to nothing on a user's
machine and looks perfectly fine in an artifact listing, which is why desktop
having no validator at all was a hole rather than an omission.

---

## 2. Node naming convention

A node name must answer *platform, configuration, artifact type* without the
reader opening it.

```
<stage> / <platform> / <configuration> / <artifact type>

03 / Android / Production / AAB
03 / iOS / Staging / IPA
03 / WebGL / Development / WebGL
04 / Android / Validate AAB
05 / Android / Publish — Internal Testing
06 / iOS / Release — App Store
```

Job **ids** are internal and unchanged (`build-android`, `validate-artifact-ios`,
…) — they are referenced by `needs:`, by required status checks and by the test
suite. Only the `name:` is user-visible.

### How the two halves are assembled

GitHub renders a called reusable workflow as
`<caller job name> / <inner job name>`. The caller owns the stage + platform
half; `reusable-build-platform.yml` takes a `node-label` input for the rest:

```yaml
build-android:
  name: 03 / Android                      # caller half
  uses: ./.github/workflows/reusable-build-platform.yml
  with:
    node-label: Production / AAB          # inner half
# renders as: 03 / Android / Production / AAB
```

`node-label` defaults to the previous `Build <platform>`, so a caller that does
not set it keeps its old node name.

The casing (`Production`, not `production`) and the artifact type (`AAB`, not
`aab`) are resolved **once**, in `01 / Resolve Build Config`, and exported as
`configuration`, `android-artifact-type` and `label-<platform>`. GitHub
expressions have no `upper()`, so deriving them per job would mean seven copies
of the same `tr`.

> Display labels live in the workflow, not in `resolve_build_flow.sh`. The
> resolver's contract is *what builds*; how a node is captioned is presentation
> and does not belong in a 39 KB bash contract pinned by
> `tests/test_build_flow.py`.

---

### 2a. The progress ladder

GitHub renders no progress indicator on a workflow node. The graph shows shape
and colour but not distance: with eleven nodes, three of them matrix legs and
four legitimately skipped, "how far did this get?" is a question the graph
cannot answer and a reader has to reconstruct by eye.

So each pipeline draws its own. Every stage job calls
`.github/actions/pipeline-progress` as it finishes, and because job summaries
accumulate on the run page, the ladder grows while the run is still going:

```text
Release / Android
                                     ████████████░░░░░░░░░░░░  3/6 stages

  [x] 04 Verify Release Identity     ████████ done
  [x] 04 Validate AAB                ████████ done
  [x] 04 Release Notes               ████████ done
  [>] 05 Publish — Internal Testing  ████░░░░ running
      06 Release — External Testing  ░░░░░░░░ pending
      06 Release — Production        ░░░░░░░░ pending
```

The stage list is declared **once**, as a workflow-level `PIPELINE_STAGES`
env, and every job renders that same list with itself marked. Per-job lists
would drift, and a ladder that disagrees with itself between two jobs of one
run is worse than no ladder.

The final report (`.github/actions/release-report`) renders the same ladder
from the actual results, so what you watched during the run and what you read
afterwards are the same picture.

**The arithmetic is the part worth knowing.** A phase skipped *on purpose* —
a dry run, a later `start-phase`, a platform this project does not ship —
counts as distance covered, because it is not a stall and drawing it as one
would make every normal run look stuck. A phase skipped *because something
earlier failed* does not count, and renders as `not reached`: otherwise a
failed run counts its own wreckage as progress, and "4/5 stages" beside a red
cross reads as nearly finished when nothing after the failure was attempted.

The ladder is `continue-on-error` everywhere. A progress indicator is not worth
failing a release over.

---

## 3. The quality gate

`02 / Quality Gate` is a cheap ubuntu job that every stage-03 build depends on.

Before it existed the builds depended only on stage 01, so Unity Tests ran
*beside* Android, WebGL and Windows: a red test suite still paid for a full
matrix of builds before anyone was told.

**Pass semantics — `skipped` is a pass:**

| Node | Skipped when | Gate verdict |
|---|---|---|
| `01 / Validate Unity Project` | never | must succeed |
| `01 / Validate Unity License` | `build-engine != docker` | pass |
| `02 / Unity Tests` | `run-tests` resolves false | pass |

Anything else — `failure`, `cancelled`, `timed_out`, or an empty result from a
job that never reported — closes the gate, and the gate names the node it closed
on in `blocking-stage`.

Builds consult the verdict, not just the edge:

```yaml
needs: [resolve-config, quality-gate, build-addressables]
if: >-
  !cancelled() &&
  needs.quality-gate.outputs.passed == 'true' && …
```

A `needs:` edge alone is not enough — a skipped gate would still let the build
start.

**Cost:** one extra node (a few seconds) per run. **Benefit:** a failed test
suite costs zero build minutes.

---

## 4. Build is not publish

Three things are separate concepts, in this order, and nothing skips a step:

```
Build  →  Artifact  →  Validate  →  Publish  →  Release
```

| Workflow | Stages | Publishes? |
|---|---|---|
| `unity-pipeline.yml` | 01–04, 07, 08 | **No.** Asserted by a test. |
| `pipeline-android-release.yml` | 03–06 | Google Play |
| `pipeline-ios-release.yml` | 03–06 | App Store Connect |
| `pipeline-webgl-release.yml` | 03–06 | Cloudflare Pages |
| `release-orchestrator.yml` | 01, 02, then the three above | via the pipelines |

Every publish node **downloads the stored artifact**; none of them can rebuild.
That is what makes "the Play upload failed, retry the upload" a real operation
rather than a 40-minute rebuild.

### Three run shapes

| Shape | How |
|---|---|
| **Build Only** | `dry-run: true` — stages 03 + 04 run, every publish node is skipped |
| **Build + Publish** | default — 03 → 04 → 05 (internal / staging) |
| **Build + Publish + Release** | 05 succeeds → 06 waits for the `production` environment approval |

Store credentials are declared `required: false` at the `workflow_call`
boundary and checked where they are used. A `required: true` secret is validated
when the *call is resolved*, which made a build-only run impossible in a
repository that has no store credentials at all.

---

## 5. Artifacts are first-class

Stage 03 writes `artifact-manifest.json` into the build output
(`scripts/common/artifact_manifest.py`), uploads it as `build-manifest-<Platform>`,
and exposes the same facts as job outputs:

| Output | Meaning |
|---|---|
| `artifact-name` | CI artifact name (`unity-build-<Platform>`) |
| `artifact-type` | `AAB` \| `APK` \| `IPA` \| `XCODEPROJ` \| `WEBGL` \| `EXE` \| `LINUX` \| `ADDRESSABLES` |
| `artifact-path` | Workspace-relative path inside the build output |
| `artifact-size-bytes` | Size (0 when nothing was produced) |
| `artifact-sha256` | Content hash of the artifact file |
| `manifest-artifact-name` | `build-manifest-<Platform>` |
| `configuration` | `Development` \| `Staging` \| `Production` |
| `build-duration-seconds` | Wall-clock build time |

The manifest itself additionally carries version, build number, commit, branch,
tag, Unity version, timestamp and the CI run id/url.

Downstream stages **read these**; they never re-scan `build/` guessing which
file is the artifact. A failed build still gets a manifest, so the report can
say what was being built when it broke.

### 5a. iOS: the shippable artifact is not what Unity emits

Every other platform's build produces the thing that ships. iOS produces an
Xcode project, and what ships is a signed IPA exported from it — so a release
run uploads two iOS artifacts:

| Artifact | Role |
|---|---|
| `release-ios-xcodeproj` | Stage 03b's input. Marked `"intermediate": true`. |
| `release-ios-ipa` | The signed, validated binary. This is what a Release Set contains and what a promotion publishes. |

Both carry a manifest saying `"platform": "iOS"`. The Release Set skips
intermediates, so it takes the IPA; if two *shippable* artifacts ever claim one
platform it raises rather than picking, because picking silently is how a
Release Set ended up promising to promote an Xcode project.

Signing happens in `Build / Release` (stage 03b), before the immutable
boundary, and stage 04 validates the IPA rather than the project. That ordering
is I-004 and it is not negotiable: if the IPA were exported during promotion,
the binary QA validated (a project) would not be the binary that ships, and the
promotion would be building — which I-005 forbids outright.

### 5b. Builder provenance (I-008)

Every artifact manifest carries a `builderProvenance` block answering "exactly
what produced this binary?":

```json
"builderProvenance": {
  "builder": "game-ci/unity-builder@v5",
  "builderKind": "docker",
  "imageReference": "unityci/editor:6000.0.26f1-android-3",
  "imageDigest": "sha256:…",
  "unityVersion": "6000.0.26f1",
  "runner": "Linux/X64",
  "runnerName": "gh-hosted-4",
  "provenanceStrength": "immutable"
}
```

`provenanceStrength` is the honest part. It is derived, not asserted:

| Strength | When | What it guarantees |
|---|---|---|
| `immutable` | an image digest was resolved | re-running the same reference gets the same environment |
| `auditable` | a tag, or a runner's own Unity, with no digest | you can say what built it; you cannot guarantee re-running reproduces it |
| `unknown` | nothing was recorded | nothing — a Release Set containing one is refused |

A caller that passes `--provenance-strength immutable` without a digest is
**downgraded to `auditable`**, not believed. An overstated provenance is worse
than an absent one: it invites trust that is not there.

**What this does not claim.** The default Docker lane uses
`game-ci/unity-builder`, which selects and pulls its own image. The pipeline
does not choose that image, so the digest is read back from the local Docker
daemon *after* the build (`Capture builder provenance` in
`reusable-build-platform.yml`). That read can legitimately come up empty — a
warm layer cache, a daemon that reports no `RepoDigests`, a self-hosted lane
with no Docker at all — and when it does, the manifest says `auditable` rather
than pretending. Native builds have no image whatsoever; their provenance is
the Unity version, the runner identity and the commit, which is `auditable` by
construction and is a legitimate way to satisfy I-008. Docker is **not**
required, and no custom Unity image is introduced to reach `immutable`.

A Release Set reports the weakest strength among its artifacts
(`buildEnvironment.provenanceStrength`), and `release_manifest.py generate`
fails closed if any artifact recorded `unknown`. `--allow-unknown-provenance`
exists for bringing up a new lane and logs loudly; a real release should never
use it.

---

## 6. Artifact validation

"The build exited 0" and "the artifact is shippable" are different claims. Unity
will happily write a 4 KB APK with no dex, a WebGL folder with a plain loader
next to a brotli wasm, or an Xcode export missing `Libraries/` — all exit 0, all
fail hours later in a store upload or as a blank browser canvas.

| Platform | Script | Checks |
|---|---|---|
| Android | `scripts/android/validate_android_artifact.py` | artifact exists; AAB vs APK matches the build config; zip readable; `AndroidManifest`/`BundleConfig.pb`/dex present; signed (v1 JAR entries and/or the v2+ APK Signing Block); package id, versionName, versionCode; size floor and ceiling |
| iOS (IPA) | `scripts/ios/validate_ipa.sh` | IPA exists and unzips; `.app` present; codesign verification; embedded provisioning profile and its expiry; bundle id, version, build number; size |
| iOS (build) | `scripts/ios/validate_xcode_project.py` | `.xcodeproj` with a real `project.pbxproj`; Unity's `Classes/`, `Libraries/`, `Data/`; `Info.plist` identity; size |
| WebGL | `scripts/webgl/validate_webgl_artifact.py` | `index.html` referencing the loader; `Build/` with loader + framework + data + wasm; compression mode consistent across all four and equal to the expected one; optional `StreamingAssets`; size |

Each validation node depends on **exactly one** build node, so a failed Android
build never leaves the WebGL artifact unvalidated, and re-running one failed
validation rebuilds nothing.

### Android identity, honestly

Package id / version are read in this order:

1. `aapt2 dump badging` (APK) or `bundletool dump manifest` (AAB), when installed
2. the compiled `AndroidManifest.xml` inside an APK (`scripts/android/axml.py`)
3. `artifact-manifest.json` shipped inside the artifact

A field no source can supply is reported as `unverified` and, by default, does
**not** fail the job — a missing Android SDK on a self-hosted runner is not an
artifact defect. Pass `--require-identity` where that is unacceptable.

**Limitation:** an AAB's `AndroidManifest.xml` is protobuf-encoded, not binary
XML, so route 2 does not apply to bundles. Without `bundletool` an AAB's
identity comes from the shipped manifest or not at all. Guessing it out of the
protobuf by scanning strings would be a plausible-looking wrong answer on a
release artifact, which is worse than saying `unverified`.

---

## 7. Independent failure and retry

After the quality gate the platforms are disconnected from each other. The test
suite asserts that no platform build, and no artifact validation, transitively
depends on another platform.

| Situation | What to re-run | What is *not* rebuilt |
|---|---|---|
| Android ✅, iOS ❌, WebGL ✅ | **Re-run failed jobs** on the run | Android, WebGL |
| Build ✅, validation ❌ | Re-run the one validation job | the artifact (it is downloaded) |
| Build ✅, validate ✅, Play upload ❌ | Dispatch the release pipeline with `start-phase: internal` | the AAB |
| Internal ✅, production rejected | Dispatch with `start-phase: production` | everything before it |

`start-phase` is the mechanism: every build job is gated on
`inputs.start-phase == 'build'`, and every publish phase can be entered directly
by name (`internal`, `external`, `production`; `staging`/`production` for WebGL).
The artifact is fetched from artifact storage, so re-publishing publishes the
*same bytes* that were validated — there is no rebuild-and-hope path.

### Limitations imposed by GitHub Actions

* **A matrix job's legs are not addressable from `needs:`.** `needs.build.result`
  is one aggregate for the whole matrix, so the report cannot ask "how did
  Android do?" directly. Each leg therefore uploads a small
  `pipeline-result-<stage>-<platform>` artifact and stage 07 aggregates them.
  That is the one piece of indirection the matrix costs — and it buys a report
  that covers whichever platforms actually ran, with real per-platform artifact
  type, size and duration instead of a hardcoded table.
* **A skipped matrix job shows its name uninterpolated.** On a run where no
  platform builds — a PR to `develop`, which is validation-only — the graph
  renders `03 / ${{ matrix.platform }}` rather than a resolved name, because
  GitHub does not evaluate the `matrix` context for a job it never expanded.
  The alternative is a static name such as `03 / Build`, which would cost the
  per-platform node names on every run that *does* build. The trade is
  deliberate: the ugly text appears only when there is nothing to look at.
* **Stage 04 waits for the whole build matrix.** `needs: [build]` cannot depend
  on a single leg, so Android's validation starts once every build leg has
  settled. It costs ordering, never correctness: each leg downloads its own
  stored artifact, and re-running one validation rebuilds nothing.
* **Nesting depth is capped at 4** (`caller → unity-pipeline →
  reusable-build-platform` already spends 3). Stage-04 nodes are therefore plain
  `runs-on` jobs, not `workflow_call`s. Asserted by a test.
* **Re-running a single job re-runs its dependents.** GitHub has no "re-run this
  job only" — "Re-run failed jobs" re-runs the failed set and anything
  downstream of it. Successful independent platforms are untouched.

---

## 8. Approval boundary

Production is never automatic. Each production node declares
`environment: production`; add required reviewers to that GitHub Environment and
the node waits for a human.

| Pipeline | Node | Environment |
|---|---|---|
| Android | `06 / Android / Release — Production` | `production` |
| Android | `05 / Android / Publish — Internal Testing` | `internal-testing` |
| Android | `06 / Android / Release — External Testing` | `external-testing` |
| iOS | `06 / iOS / Release — App Store` | `production` |
| WebGL | `05 / WebGL / Deploy — Staging` | `staging` |
| WebGL | `06 / WebGL / Deploy — Production` | `production` |

Development and staging stay automated. See
[GITHUB_ENVIRONMENTS.md](GITHUB_ENVIRONMENTS.md) for creating them; a repository
that has not created `internal-testing` / `external-testing` needs them before
its first release run.

---

## 9. Adding a new platform

Stages 03 and 04 are **matrix jobs**, so a platform is a row of data, not a new
job. There is no YAML to copy.

1. **Executor** — confirm `reusable-build-platform.yml` can build it (or add the
   lane there, in one place). Register its artifact type in
   `DEFAULT_ARTIFACT_TYPE` and `ARTIFACT_PATTERNS` in
   `scripts/common/artifact_manifest.py`.
2. **Resolver** — add `build-<platform>` to `scripts/common/resolve_build_flow.sh`
   and document it in [BRANCH_FLOW_CONTRACT.md](BRANCH_FLOW_CONTRACT.md).
3. **Matrix** — add one `add` line to the `Resolve build matrix` step in
   `unity-pipeline.yml`:

   ```bash
   add "${SEL_SWITCH}" Switch NSP "${CONFIG}" switch
   #    selected?      platform  artifact  node suffix  validator (empty = no stage 04)
   ```

4. **Artifact name** — if the platform's artifact type distinguishes two real
   outputs (as Android's APK/AAB does), add it to the `Android|iOS` arm of
   `add()`; otherwise the default `<build-type>-<slug>` is right. Add a slug to
   `slug_of` only if the Unity target id is not what a person would call the
   platform.
5. **Stage 04** — if the platform has a validator, add its script and one
   `case` branch in the validate job's dispatch. Leave the validator field
   empty to skip stage 04 entirely; the platform then contributes no node
   rather than a permanently skipped one.
6. **Stages 07/08** — nothing. The report and the Discord message are built
   from the matrix legs' own result artifacts, so a new platform appears
   automatically.
7. **Tests** — add the platform to `PLATFORM_BUILD_PLATFORMS` in
   `tests/test_platform_selection.py`. The selection, isolation and gating
   tests then cover it.
8. **Release workflow** — only if the platform has a real distribution
   target. A standalone artifact with nowhere to go does not need one.

## 10. Adding a build configuration

Configurations are `Development`, `Staging`, `Production`, derived from the
resolver's `environment` output in the `Resolve node display labels` step. To
add one:

1. Teach `resolve_build_flow.sh` the new environment value.
2. Add a `case` arm in `Resolve node display labels` for its display casing.
3. Add the GitHub Environment if it needs an approval boundary.

Nothing in stage 03 or 04 changes — the configuration travels as an input.

---

## 11. Reading a failure

The final report groups results by stage and the error line names the stage and
node:

```
::error::Pipeline failed at: 02/unity-tests=failure. See the matching job logs.
```

```
02 — QUALITY GATE
   ❌ 02 / Unity Tests
   ⛔ Gate closed at 02 QUALITY GATE / Unity Tests — no stage 03 build was started.
```

The Discord message carries the same `🚦 Failed at` field, the build
configuration, per-platform artifact sizes and download links, and a `🔎`
validation mark distinguishing "the build broke" from "the artifact is bad".
