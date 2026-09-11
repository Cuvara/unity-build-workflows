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

* **A `strategy: matrix` over the platforms would collapse them into one job
  id.** Per-leg results are not addressable from `needs:`, so the final report
  and the Discord message could no longer say *which* platform failed, and no
  downstream job could depend on a single platform. The platform nodes are
  therefore explicit call sites of the one shared executor
  (`reusable-build-platform.yml`) — the build logic is not duplicated; only a
  `with:` block is. Adding a platform is §9.
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

You should not need to read the rest of the pipeline.

1. **Executor** — confirm `reusable-build-platform.yml` can build it (or add the
   lane there, in one place). Register its artifact type in
   `DEFAULT_ARTIFACT_TYPE` and `ARTIFACT_PATTERNS` in
   `scripts/common/artifact_manifest.py`.
2. **Resolver** — add `build-<platform>` to `scripts/common/resolve_build_flow.sh`
   and document it in [BRANCH_FLOW_CONTRACT.md](BRANCH_FLOW_CONTRACT.md).
3. **Label** — add `label-<platform>` to the `Resolve node display labels` step
   and to `resolve-config`'s outputs in `unity-pipeline.yml`.
4. **Stage 03** — copy an existing platform job. Set `name: 03 / <Platform>`,
   `node-label`, `configuration`, `artifact-type`, and gate it on
   `needs.quality-gate.outputs.passed == 'true'`.
5. **Stage 04** — add a validator script and a `validate-artifact-<platform>`
   job depending only on its own build job.
6. **Stages 07/08** — add the result to `final-report`'s env, table and
   `check_result` list, and to `notify-discord`.
7. **Tests** — add the job id to `PLATFORM_BUILD_JOBS` / `VALIDATION_JOBS` in
   `tests/test_pipeline_stages.py`. The independence, gating and naming tests
   then cover it automatically.

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
