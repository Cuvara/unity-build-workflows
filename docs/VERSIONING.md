# Versioning: version name and build number

Every build carries two numbers, and the stores treat them differently.

| | Example | Android | iOS | Store rule |
|---|---|---|---|---|
| **Version name** | `1.4.2` | `versionName` | `CFBundleShortVersionString` | Shown to players. May repeat across uploads. |
| **Build number** | `475` | `versionCode` | `CFBundleVersion` | **Must increase on every upload.** Google Play rejects a `versionCode` it has seen or moved past; App Store Connect rejects a repeated `CFBundleVersion` for the same version. |

Related: [PIPELINE_ARCHITECTURE.md](PIPELINE_ARCHITECTURE.md), [REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md),
[BUILD_DELIVERY.md](BUILD_DELIVERY.md).

---

## 1. Where each value comes from

### Version name — the Unity project

Read from `ProjectSettings/ProjectSettings.asset` (`bundleVersion`, Player
Settings → Version) by `scripts/common/extract_project_metadata.sh` in
`resolve-config`. To release a new version, change it in Unity and commit; the
pipeline never invents one.

It appears in artifact names (`<Product>_<version>_<build>_<type>_<platform>`),
Discord messages and the release manifest, and is passed to the build as
`APP_VERSION`. A git tag `vX.Y.Z` on a release must match it
(`scripts/common/version_resolver.py`).

### Build number — decided once per run

Computed in `resolve-config` (the build-matrix step of `unity-pipeline.yml`),
**before** anything is built, and handed to every platform job of the run, so
Android and iOS built from one run share it:

```
BUILD_NUMBER = github.run_number + offset
```

- `github.run_number` increases by one on every run of **that workflow file**
  and never goes back.
- The **offset** lifts the range above numbers a store has already seen and
  separates development builds from release builds (§2).

---

## 2. Offsets

| Variable | Used for | Default |
|---|---|---|
| `BUILD_NUMBER_OFFSET_RELEASE` | `build-type: release` (store uploads) | falls back to `BUILD_NUMBER_OFFSET` |
| `BUILD_NUMBER_OFFSET_DEVELOPMENT` | `build-type: development` | falls back to `BUILD_NUMBER_OFFSET` |
| `BUILD_NUMBER_OFFSET` | both, when the per-type variable is unset (the original, shared variable) | `0` |

Each must be a non-negative integer; anything else fails `resolve-config`. The
log line `[matrix] build number N = run R + offset O (<variable>)` says which
one was used.

### Why two offsets

Build / Development and Build / Release are separate workflow files, so each
has its own `run_number`. With one shared offset the development workflow —
which runs far more often — soon numbers above the next release:

```
offset 462     Build / Development run 21 → 483
               Build / Release     run  2 → 464   (lower than the dev build)
```

Both are valid for their stores, but a tester who installed development 483
cannot install release 464 over it (Android refuses a lower `versionCode`).
Separate ranges keep each sequence on its own:

```
BUILD_NUMBER_OFFSET_RELEASE=462          → releases 463, 464, 465 …
BUILD_NUMBER_OFFSET_DEVELOPMENT=100000   → development 100021, 100022 …
```

A development build then always outnumbers a release, so installing a release
on a device that has a development build still needs an uninstall — the
difference is that it is predictable, and release numbers stay a clean,
gap-free sequence for the stores. Stay below `2100000000`, Google Play's
`versionCode` ceiling.

### Offsets per GitHub Environment

Instead of two repository variables, set one `BUILD_NUMBER_OFFSET` in each
GitHub Environment. `resolve-config` declares the run's environment, so it
reads that environment's value
([ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md)):

```bash
gh variable set BUILD_NUMBER_OFFSET --env development --body 100000 -R OWNER/REPO
gh variable set BUILD_NUMBER_OFFSET --env staging     --body 100000 -R OWNER/REPO
gh variable set BUILD_NUMBER_OFFSET --env production  --body 462    -R OWNER/REPO
gh variable delete BUILD_NUMBER_OFFSET_DEVELOPMENT -R OWNER/REPO   # would win otherwise
gh variable delete BUILD_NUMBER_OFFSET_RELEASE     -R OWNER/REPO
```

This is equivalent only when **each workflow targets environments of a single
build type**: the offset now follows the environment, the run number still
follows the workflow. The usual setup qualifies: Build / Development
dispatches `development` or `staging` (build type `development`), and
Build / Release always builds `production` (build type `release`). If a
workflow can build a `release` type into `development` (or the reverse), keep
the `BUILD_NUMBER_OFFSET_<TYPE>` names. You can also set those in an
environment; they still win over `BUILD_NUMBER_OFFSET`.

Pull requests and `BUILD_ENVIRONMENT_SECRETS=false` have no environment, so
they use the repository value (or `0`). Neither uploads to a store.

### Which projects need an offset

| Project | What to set |
|---|---|
| **New, never uploaded** | Nothing for releases (`run_number` starts at 1). Optionally `BUILD_NUMBER_OFFSET_DEVELOPMENT` to keep development builds in their own range. |
| **Already in a store** | `BUILD_NUMBER_OFFSET_RELEASE` = the **highest** build number the store has (Play Console → App bundle explorer; App Store Connect → TestFlight). Set once. |

### When to raise an offset again

Only when `run_number` cannot carry the sequence on its own:

- the build workflow file was **renamed or recreated** — its `run_number`
  restarts at 1;
- releases moved to **another workflow file**;
- someone **uploaded by hand** a build number above the pipeline's.

The mistake is caught either way: `PlayerBuilder` warns when the build number
is below the project's own `versionCode`, and the Google Play release pipeline
compares the number with every track before uploading
(`scripts/android/check_build_number.py`).

```bash
gh variable set BUILD_NUMBER_OFFSET_RELEASE     --repo OWNER/REPO --body 462
gh variable set BUILD_NUMBER_OFFSET_DEVELOPMENT --repo OWNER/REPO --body 100000
```

---

## 3. How the numbers reach the app

| Lane | Applied by |
|---|---|
| Docker / game-ci | `game-ci/unity-builder` inputs: `version`, `androidVersionCode` (Android), `BUILD_NUMBER` env |
| **Native** (`BUILD_ENGINE=local`, self-hosted macOS / Windows) | The project's `PlayerBuilder.Build`, from the environment the build step sets: `BUILD_NUMBER` and `APP_VERSION` |
| iOS native (`unity-build-ios.yml`) | `Company.BuildPipeline.Editor.BuildCommand` (`IOSBuilder`) |

On the native lane the project's `PlayerBuilder` must apply them —
`templates/PlayerBuilder.cs` does:

| Target | Setting |
|---|---|
| all | `PlayerSettings.bundleVersion = APP_VERSION` (when set) |
| Android | `PlayerSettings.Android.bundleVersionCode = BUILD_NUMBER` |
| iOS | `PlayerSettings.iOS.buildNumber = BUILD_NUMBER` (→ `CFBundleVersion` of the Xcode project and the IPA) |
| macOS | `PlayerSettings.macOS.buildNumber = BUILD_NUMBER` |

The values are applied in memory for that build and **restored afterwards**, so
the runner's `ProjectSettings.asset` is not modified. Without `BUILD_NUMBER`
(a manual Editor build) the project's own number is used, with a warning; a
`BUILD_NUMBER` that is not a positive integer fails the build.

**A project whose `PlayerBuilder` predates this** ships its own
`versionCode` / `CFBundleVersion` on every native build — identical every time —
and the second store upload of a version is rejected. Copy the
`ApplyVersion` / `VersionState` part of `templates/PlayerBuilder.cs` into it.

---

## 4. Checking a build

- **Run log**, `resolve-config`: `[matrix] build number 475 = run 13 + offset 462 (BUILD_NUMBER_OFFSET_RELEASE)`.
- **Build log** (`Editor.log`): `[PlayerBuilder] version 1.4.2 build 475 (Android).`
- **Artifact name**: `Game_1.4.2_475_release_android_aab`.
- **Android**: `aapt dump badging app.apk | grep version` → `versionCode='475' versionName='1.4.2'`.
- **iOS**: `CFBundleVersion` in the IPA's `Info.plist`, or the build number App Store Connect shows.

---

## 5. Limits

- `run_number` is per workflow file, so the offsets are per project, not per
  store track.
- Release build numbers are not yet read from the stores. A planned addition
  asks Google Play / App Store Connect for the highest number and adds one, which
  removes the need for `BUILD_NUMBER_OFFSET_RELEASE` entirely.
