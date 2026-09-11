# Validation status

What has actually been proven, and how. Three columns, kept apart on purpose:

| Level | Means |
|---|---|
| **Runtime verified** | Observed on a real GitHub Actions run against the real project. Run IDs below. |
| **Static verified** | Proven by tests or the invariant checker. The logic is right; nobody has watched it run. |
| **Blocked** | Cannot be verified here yet — missing runner hardware or credentials. Not a claim of correctness. |

A static-verified item is not a weaker version of a runtime-verified one. Three
of the defects listed at the bottom of this page passed their tests and still
failed the moment they ran.

Runs are in `Cuvara/NDCUnityTemplate`.

## Runtime verified

| What | Evidence |
|---|---|
| `Build / Release` produces a Release Set with a full identity (version, build number, commit, run) | run `34589238800` — version `0.1.0`, build `5`, commit `bbcdc97` |
| Android and WebGL build from one Release Set and share that identity | same run — `release-android-aab` 36,320,122 B, `release-webgl` 13,690,722 B |
| Builder provenance is captured with a real image digest (I-008 `immutable`) | Android `unityci/editor:ubuntu-6000.0.26f1-android-3 @ sha256:92131a7a…`, WebGL `…-webgl-3 @ sha256:99863e01…` |
| Android promotion moves the exact bytes, unchanged | run `34590803470` — recomputed SHA-256 over the downloaded artifact matched the declared `201fe459…`; run id, version and artifact name all matched |
| WebGL promotion moves the exact bytes, unchanged | run `34590954312` — declared `9877c1ed…` matched |
| Promotion rebuilds nothing | both promotions ran the whole pipeline with no Unity job present |
| An identity mismatch fails closed | run `34590920059` — `version: manifest says '0.1.0', expected '9.9.9'`, exit 1 |
| Publishing is unreachable when identity verification fails | run `34591502379` — `start-phase: internal`, `dry-run: false`, every publish job skipped |
| The store build-number guard runs *before* any upload and fails closed without credentials | run `34591565436` — identity passed, guard refused, publishing skipped |
| The platform capability gate filters the build matrix | run `34589238800` — `RELEASE_BUILD_PLATFORMS=Android,iOS,WebGL,Windows64` with `PLATFORMS=Android,WebGL` produced Android and WebGL jobs only; no iOS or Windows64 node existed |

## Static verified

| What | Evidence |
|---|---|
| Capability matrix for Android / WebGL / Windows64 / Linux64 / LinuxServer, and 12 invalid combinations | `tests/test_platform_capabilities.py` — resolver **and** the real matrix step |
| A disabled platform yields no build row, no validate row, no artifact name, `has-builds=false` | same |
| Linux desktop and Linux server do not collide on an artifact name | same |
| The Play versionCode decision table: above passes, duplicate fails, below fails, first upload passes, unreachable store fails closed | `tests/test_android_build_number.py` against a fake Play API |
| No credential material reaches a job summary | same |
| `immutable` provenance requires a digest; a caller claiming it without one is downgraded | `tests/test_platform_capabilities.py` |
| A Release Set fails closed on an artifact with unknown provenance, or with no version | same |
| Promotion contains no build, export or signing operation | `validate_pipeline_invariants.py` I-005 |
| Every job downstream of identity verification requires it to have passed | `validate_pipeline_invariants.py` I-007 |
| 34 invariant checks total | `scripts/common/validate_pipeline_invariants.py` |

## Blocked

| What | Blocked by | Note |
|---|---|---|
| iOS stage 03b — sign & export IPA | No macOS runner available | The job is written and statically checked (signing sits **before** the immutable boundary, per I-004), but it has never run. Treat iOS as unverified end to end. |
| iOS promotion to App Store Connect | No macOS runner, no App Store credentials | Same. |
| Android publish to Google Play | No `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` | The guard in front of the upload *is* runtime verified — it refused to proceed without credentials. The upload itself is not. |
| WebGL deploy to hosting | No Cloudflare Pages project configured | Verification and validation are runtime verified; the deploy step is not. |
| Windows / Linux distribution | No distribution target exists | Deliberate: an artifact is not a release channel, and inventing one would be worse than leaving it out. |

## Defects these runs found

Each of these had passing tests before it ran.

1. **The platform capability gate was inert.** `unity-pipeline.yml` never passed
   `vars.PLATFORMS` to the resolver. Every capability test set the variable in
   the resolver's own environment, so they tested a call shape the pipeline
   never made. A project declaring `Android,WebGL` built Windows64 on request.
2. **Release Sets had no version.** The stage-01 shallow checkout fetched
   `ProjectVersion.txt` and not `ProjectSettings.asset`, so `app-version`
   resolved empty. Because `verify` only compares non-empty expectations,
   `--expect-version ''` passed vacuously — the identity check had silently
   lost one of its three fields.
3. **A failed identity check did not stop a promotion.** Every job listed
   `verify-artifact` in `needs` and then guarded itself with `if: always()`,
   which ignores the result. `needs` orders jobs; it does not gate them under
   `always()`. A promotion started at `start-phase: internal` would have
   published an artifact whose verification had just failed.

All three are fixed, and each now has a test that fails if it returns.
