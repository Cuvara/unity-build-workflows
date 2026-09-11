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

## 1a. Entry workflows — the user-facing layer

Stages 01–08 describe the *engine*. What a person actually clicks is a separate
concern, and it is solved with separate files rather than a bigger form.

| Workflow | Trigger | Purpose |
|---|---|---|
| `unity-build.yml` | push / pull_request | **Automatic** CI. No form at all — every setting is resolved per branch by `resolve_build_flow.sh` from the repository variables. |
| `build-android.yml` | manual | Android only. The **only** place the APK/AAB choice appears. |
| `build-ios.yml` | manual | iOS only. Exposes the macOS runner labels the lane needs. |
| `build-webgl.yml` | manual | WebGL only. |
| `build-all.yml` | manual | Every platform in the environment's `*_BUILD_PLATFORMS` variable. |

All five call the same `unity-pipeline.yml` engine. None of them contains build
logic — each is a single delegating job with a `with:` block. Templates live in
`templates/consumer-build-*.yml`; `tests/test_entry_workflows.py` pins the
contract.

### Why separate files instead of one form

`workflow_dispatch` has **no conditional input visibility**. A single
multi-platform form therefore has to show every platform's options to everyone:
choosing `Target platform: iOS` still left `Android output: APK/AAB` on screen,
and the form had grown to fourteen fields, most irrelevant to any given run.

GitHub offers no way to hide an input based on another input's value, and
faking one (a `platform` input that silently ignores the fields that do not
apply) trades a visible problem for an invisible one. Splitting the **entry
point** is the only mechanism that produces a form containing just the
applicable options — and it costs nothing structurally, because the
implementation is shared underneath:

```
build-android.yml ──┐
build-ios.yml     ──┤
build-webgl.yml   ──┼──→ unity-pipeline.yml ──→ reusable-build-platform.yml
build-all.yml     ──┤        (stages 01–08)          (the one build executor)
unity-build.yml   ──┘
```

Nesting: entry → pipeline → platform executor = **3 of GitHub's 4** levels.

### Input groups

The dispatch form cannot group fields, so each input's description carries its
group as a prefix and the declaration order is the display order:

| Group | Inputs | Who changes them |
|---|---|---|
| `GENERAL` | environment | everyone |
| `ANDROID` / `IOS` / `WEBGL` | platform-specific (e.g. output format) | everyone, on that platform |
| `QUALITY` | run-tests, test-mode | everyone |
| `CONTENT` | build-addressables | everyone |
| `UNITY` | unity-version, clean-build, define-symbols | occasionally |
| `ADVANCED` | runner-type, build-engine, activation-strategy, runner-labels | rarely — infrastructure |

A normal developer needs `GENERAL` and the platform group. Everything under
`ADVANCED` defaults to `auto`, meaning "use the repository variable"; the engine
spells that as an empty string, and the entry point translates.

### What is deliberately *not* on these forms

- **`android-export` on `build-all.yml`.** An output format for one platform
  does not belong on a multi-platform form. Build All takes the format from the
  environment (release → AAB, otherwise APK). Use `build-android.yml` to
  override.
- **A platform picker on `build-all.yml`.** The platform set is the
  environment's `*_BUILD_PLATFORMS` variable, so a dropdown could only drift
  from it. Build one platform with its own workflow.
- **WebGL compression.** `compress_webgl.sh` runs in `unity-build-webgl.yml`,
  the deployment lane used by `pipeline-webgl-release.yml` — *not* in the
  `reusable-build-platform.yml` lane these entry points drive. A knob the
  engine does not read would be a lie in the form. On this lane compression is
  whatever the Unity project's WebGL settings emit, and stage 04 validates that
  it is at least *consistent*.
- **iOS signing/export.** Certificates live in the release pipeline. Stage 03
  for iOS emits the Unity-exported Xcode project; the IPA is produced,
  signed and validated by `pipeline-ios-release.yml`.
- **`runner-mode`.** Superseded by `runner-type` + `build-engine`. The engine
  still accepts it for backward compatibility; new forms do not offer it.

### Adding a new entry workflow

Copy the nearest `templates/consumer-build-*.yml`, change `platform:`, the job
`name:`, and the concurrency group, then add the platform's own inputs under
their own group. Add the key to `ENTRY_POINTS` in
`tests/test_entry_workflows.py` and the shared contract is enforced for it.

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

4. **Stage 04** — if the platform has a validator, add its script and one
   `case` branch in the validate job's dispatch. Leave the validator field
   empty to skip stage 04 entirely; the platform then contributes no node
   rather than a permanently skipped one.
5. **Stages 07/08** — nothing. The report and the Discord message are built
   from the matrix legs' own result artifacts, so a new platform appears
   automatically.
6. **Tests** — add the platform to `PLATFORM_BUILD_PLATFORMS` in
   `tests/test_platform_selection.py`. The selection, isolation and gating
   tests then cover it.
7. **Entry workflow** — optional; only if the platform deserves its own
   Run-workflow form (§1a).

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
