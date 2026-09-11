# Validation status

What has actually been proven, and how. Three columns, kept apart on purpose:

| Level | Means |
|---|---|
| **Runtime verified** | Observed on a real GitHub Actions run against the real project. Run IDs below. |
| **Static verified** | Proven by tests or the invariant checker. The logic is right; nobody has watched it run. |
| **Blocked** | Cannot be verified here yet — missing runner hardware or credentials. Not a claim of correctness. |

A static-verified item is not a weaker version of a runtime-verified one. Every
defect listed at the bottom of this page had passing tests and still failed the
moment it ran.

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

### Release platforms (this round)

| What | Evidence |
|---|---|
| Linux release build, validated by the new desktop validator, in a Release Set | run `34598599579` — `release-linux`, 101,471,032 B, sha `630995e9…`, provenance `immutable` (`unityci/editor:ubuntu-6000.0.26f1-linux-il2cpp-3 @ sha256:ef7f911a…`) |
| Windows release build, validated, in a Release Set | run `34600714552` — `release-windows`, sha `68fa909e…`, provenance `immutable` (`…-windows-mono-3 @ sha256:0e813038…`) |
| Linux Steam promotion — verify + validate | run `34599425877` — identity and checksum matched; validator passed with the executable-bit warning |
| Windows Steam promotion — all three phases | run `34602636861` — every phase re-verified the artifact independently and staged it |
| Steam staging does not mutate the artifact | runs `34600550319`, `34602636861` — `Staged content matches the verified artifact` before anything would upload |
| The executable bit the artifact zip drops is restored on the staging copy | run `34600550319` — `Restored the executable bit on 8 ELF binar(y/ies)` |
| Steam fails closed with no configuration | run `34600004389` — `STEAM_APP_ID is not set`, publish job failed, later phases skipped |
| A Windows/Linux build needs no Steam configuration | runs `34598599579`, `34600714552` — both built and produced Release Sets with no Steam variables or secrets set |

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
| 51 invariant checks total | `scripts/common/validate_pipeline_invariants.py` |
| The iOS Release Set contains the signed IPA, never the Xcode project; two shippable artifacts for one platform raise rather than being guessed between | `tests/test_ios_immutable_ipa.py` |
| iOS promotion contains no `xcodebuild`, export, sign or Unity operation | same |
| The desktop validator catches a missing `_Data` directory, engine payload, game code or Unity runtime | `tests/test_desktop_and_steam_release.py` |
| Steam configuration is fail-closed, one depot object covers both platforms, no id is hardcoded in the toolkit | same |
| Steam credentials are optional at the workflow level, so a repo with no Steam account can resolve and dry-run the promotion | same |
| SHA-256, run-id and artifact-name mismatches all fail a desktop promotion | same |
| Every promotion job carries both a status function and the verify-result check | same, plus `tests/test_platform_capabilities.py` |

## Blocked

| What | Blocked by | Note |
|---|---|---|
| iOS stage 03b — sign & export IPA | No macOS runner available | The job is written and statically checked: signing sits **before** the immutable boundary (I-004), and it now writes the IPA's artifact manifest so the Release Set contains the IPA rather than the Xcode project. None of it has run. Treat iOS as unverified end to end. |
| The iOS IPA reaching a real Release Set | Same | The contract is proven by tests against synthetic manifests, not by a build. |
| Steam upload itself | No Steam account, app or depot for this project | Everything up to the upload is runtime verified with Valve's public test ids (app `480`), which were set for the runs above and removed afterwards. `steamcmd +run_app_build` has never executed. |
| iOS promotion to App Store Connect | No macOS runner, no App Store credentials | Same. |
| Android publish to Google Play | No `GOOGLE_PLAY_SERVICE_ACCOUNT_JSON` | The guard in front of the upload *is* runtime verified — it refused to proceed without credentials. The upload itself is not. |
| WebGL deploy to hosting | No Cloudflare Pages project configured | Verification and validation are runtime verified; the deploy step is not. |

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
4. **The desktop validator failed every Linux build.** GitHub artifacts are
   zipped without POSIX modes, so a downloaded Linux binary is always `0644`.
   The check was right about what matters and wrong about where: as a stage-04
   gate it would have failed every Linux release forever while blaming the
   build.
5. **A promotion started at a later phase did nothing and reported success.**
   Gating on `verify-artifact.result` replaced the `always()` those jobs
   carried, and GitHub propagates a skip from `needs` unless the `if` has a
   status function. Every publishing job inherited the skip from
   `validate-artifact`, which is skipped by design on a later start phase.
6. **A Steam dry run never reached the staging path it promises.** The phase
   gate excluded dry runs outright, so the staging copy, the no-mutation
   fingerprint and the executable-bit restore were unreachable — and the one
   way to exercise the promotion path without a Steam account did nothing.

All six are fixed, and each now has a test that fails if it returns.
