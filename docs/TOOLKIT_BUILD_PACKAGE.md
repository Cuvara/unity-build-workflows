# The Toolkit Build Package

A project needs **no build script of its own**. The pipeline brings the build
code with it: the toolkit's Unity package `com.company.build-pipeline` is
copied into the project for one build, its builders run, and the copy is
removed. A fix to the build code is made once, in the toolkit, and every
project picks it up with its next build on `@v6`.

| Before | Now |
|---|---|
| `Assets/.../PlayerBuilder.cs` copied from `templates/`, one per project | `Company.BuildPipeline.Editor.PlayerBuilder` in the package |
| `Assets/.../AddressableBuilder.cs` + an asmdef referencing Addressables | `Company.BuildPipeline.Editor.AddressableBuilder` in the package |
| `DisableBitcode.cs` style post-processors | the package's iOS post-processor (bitcode off) |
| Version / build number code in each copy | the package applies `APP_VERSION` / `BUILD_NUMBER` |

---

## 1. How it works

In every build job of `reusable-build-platform.yml`:

```
Checkout toolkit                 → .toolkit/   (the toolkit-ref of this run)
Install toolkit build package    → <project>/Packages/com.company.build-pipeline
   ... Unity steps run Company.BuildPipeline.Editor.PlayerBuilder.Build ...
Remove toolkit build package     (always, even after a failure)
```

- **Embedded package, no manifest edit.** Unity loads any folder under
  `Packages/` as an embedded package. `manifest.json` stays as committed.
  Unity rewrites `Packages/packages-lock.json` to list the embedded package;
  the remove step restores the committed file.
- **Same revision as the workflow.** The package comes from the toolkit
  checkout of this run (`toolkit-ref`, normally `v6`), so the C# and the
  workflow that calls it never drift apart.
- **Tests are not copied.** `Tests/` needs the Test Framework and belongs to
  the toolkit.
- **Self-hosted workspaces stay clean.** The copy carries a marker file; the
  remove step deletes only a copy it made. A copy left behind by a killed run
  is replaced at the start of the next one.

Script: `scripts/common/install_build_package.sh install|remove`.

### Which build method runs

| Project has | `-executeMethod` (player) | Addressables |
|---|---|---|
| nothing (recommended) | `Company.BuildPipeline.Editor.PlayerBuilder.Build` | `Company.BuildPipeline.Editor.AddressableBuilder.Build` |
| its own global `PlayerBuilder` class in `Assets/` | `PlayerBuilder.Build` (the project's) | the package's, unless the project has its own `AddressableBuilder` |
| `UNITY_BUILD_METHOD` / `build-method` set | that method | unchanged |

A project script in the global namespace wins, so existing projects keep
working unchanged until they delete their copy.

### Which lanes

| Lane | Uses the package for |
|---|---|
| Self-hosted Windows / macOS (`BUILD_ENGINE=local`) | player build, Addressables |
| Docker on a Windows runner | player build |
| Docker on Linux (game-ci) | Addressables pre-step only. The player build uses game-ci's own builder unless `UNITY_BUILD_METHOD` names one |
| Native iOS (`unity-build-ios.yml`) | `BuildCommand.Execute`, as before (needs `BuildConfig/`) |

---

## 2. `PlayerBuilder`

Builds the **active build target** with the project's own Player Settings,
the enabled scenes of the Build Profiles / Build Settings list and the
project's keystore. It changes only what the pipeline decided for this run,
in memory, and restores it afterwards:

| Input | Effect |
|---|---|
| `-buildOutput` / `BUILD_OUTPUT_DIR` | output root (default `build`, relative to `GITHUB_WORKSPACE`); the player is written to `<root>/<BuildTarget>/<productName><ext>` |
| `ANDROID_APP_BUNDLE` = `1` / `true` | `.aab`, else `.apk` |
| `BUILD_NUMBER` | Android `versionCode`, iOS `CFBundleVersion`, macOS build number ([VERSIONING.md](VERSIONING.md)) |
| `APP_VERSION` | version name (`bundleVersion`) |
| `ANDROID_KEYSTORE_PASS`, `ANDROID_KEY_PASS` | passwords for the project's custom keystore. Without them a development APK is signed with the debug key; an App Bundle fails with the fix |
| `-standaloneBuildSubtarget Server` | dedicated server for `LinuxServer` (applied by Unity) |
| `BUILD_PROFILE` | Unity 6 Build Profile to build with — see below. Empty or `ProjectSettings` = the project's Player Settings |
| `BUILD_PROFILE_DIR` | where a bare profile name is looked up (default `Assets/Settings/Build Profiles`) |

### Build Profiles

The pipeline input `build-profiles` maps platforms to Unity 6 Build Profiles,
as comma-separated `Platform=Profile` pairs:

```yaml
build-profiles: 'Android=Android-Staging,iOS=iOS-dev'
```

Each build leg receives its own platform's entry as `BUILD_PROFILE`. A
platform that is not listed, or is set to `ProjectSettings`, builds exactly as
before: the project's Player Settings and the enabled scene list. The value is
an asset name in `Assets/Settings/Build Profiles/` (`Android-Staging` →
`Android-Staging.asset`) or a project-relative `.asset` path.

`PlayerBuilder` then:

1. loads the profile and checks it builds the job's platform. A missing
   profile fails and lists the profiles the project has; an `iOS-dev` profile
   on an Android leg fails;
2. activates it **before** applying `APP_VERSION`, `BUILD_NUMBER` and the
   keystore passwords, so a profile that overrides Player Settings still gets
   this run's version and build number (the log line
   `effective version …, Android versionCode …` shows the values that go into
   the player);
3. builds with `BuildPlayerWithProfileOptions` — the profile's scene list,
   scripting defines and Player Settings overrides;
4. restores everything: the previously active profile is re-activated and the
   profile asset, which Unity saves during the build, is put back byte for
   byte. A `ProjectSettings` build deactivates a profile left active in
   `Library/` first, so a cached `Library/` never changes what a build uses.

Profiles are applied only by the toolkit's `PlayerBuilder`. The "Guard — build
profile needs PlayerBuilder" step fails a leg with a profile when it would not
run it:

| Situation | Why it is rejected |
|---|---|
| Docker on Linux (game-ci lane) | game-ci uses its own builder |
| The project has its own global `PlayerBuilder` | the project's script wins (above) and knows nothing about profiles — delete it (§6) |
| `build-method` / `UNITY_BUILD_METHOD` names another method | same |

Profiles need Unity 6; an older editor fails when one is requested.

A dispatch form can offer one dropdown per platform and join them; GitHub
forms cannot filter one input by another:

```yaml
android-build-profile:
  description: 'UNITY · Android build profile'
  type: choice
  options: [ProjectSettings, Android-Staging]
# …
build-profiles: ${{ format('Android={0},iOS={1}', inputs.android-build-profile, inputs.ios-build-profile) }}
```

A failed build logs `::error::[PlayerBuilder] ...` and exits 1; Unity's own
exit code under `-quit` does not reflect a failed `BuildPlayer`.

Project `IBuildHook` implementations run around the build, as under
`BuildCommand`: `BeforeValidation` and `BeforeBuild` (an exception fails the
build), then `AfterBuild` with the result.

## 3. `AddressableBuilder`

Runs `AddressableAssetSettings.BuildPlayerContent` and fails the step when it
reports an error. The Addressables code compiles only when the project has
`com.unity.addressables` (asmdef `versionDefines` →
`BUILD_PIPELINE_ADDRESSABLES`), so the package builds in a project without it;
calling it there fails with an explanation.

## 4. iOS post-processor

The package's `[PostProcessBuild(100)]` hook runs on every iOS build in a
project that has the package. Without `BuildConfig/` (the `PlayerBuilder`
path, a menu build) it only sets `ENABLE_BITCODE=NO`, and leaves Info.plist
and entitlements alone. With `BuildConfig/` (the `BuildCommand` path) it
applies the configured usage strings, frameworks and entitlements as before.

---

## 5. What stays in the project

| Keep | Why |
|---|---|
| Player Settings: product name, bundle id, version, icons, keystore file and alias | the project's identity; `PlayerBuilder` builds with them |
| The scene list (Build Profiles / Build Settings) | which scenes ship |
| Project-specific `[PostProcessBuild]` scripts (SDK workarounds, Podfile patches) | they belong to the project's dependencies |
| Optional `IBuildHook` implementations | project steps before or after a CI build |
| The caller workflows (`.github/workflows/*.yml`, from `templates/consumer-*.yml`) and `.github/discord.json` | the trigger and the per-project routing |

Configuration (platforms, tests, offsets, ...) lives in repository and
environment variables, not in files:
[REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md),
[ENVIRONMENT_VARIABLES.md](ENVIRONMENT_VARIABLES.md).

## 6. Moving an existing project

1. Delete the project's `PlayerBuilder.cs` and `AddressableBuilder.cs` (and an
   asmdef that only existed for them).
2. Delete post-processors the package now covers, for example one that only
   disables bitcode.
3. Unset `UNITY_BUILD_METHOD` if it only named `PlayerBuilder.Build`.
4. Run a build. The "Install toolkit build package" step logs
   `Installed com.company.build-pipeline <version>`, and the build step runs
   `Company.BuildPipeline.Editor.PlayerBuilder.Build`.

Nothing else changes: output paths, artifact names and environment variables
are the same contract the old script followed.

### Using the package in the Editor

The package is not needed for development, only in CI. To try a CI build
locally, add it as a git dependency:

```json
"com.company.build-pipeline": "https://github.com/Cuvara/unity-build-workflows.git?path=unity-package/Packages/com.company.build-pipeline#v6"
```

A project that references the package this way (or embeds its own copy) keeps
it: the pipeline then installs nothing and uses the project's copy.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `executeMethod class 'PlayerBuilder' could not be found` | `UNITY_BUILD_METHOD=PlayerBuilder.Build` set after the project script was deleted | unset `UNITY_BUILD_METHOD` |
| The old project builder still runs | it is still in `Assets/` (global namespace) | delete it (§6) |
| Compile error in `Company.BuildPipeline.Editor` | the project lacks `com.unity.nuget.newtonsoft-json` and the registry is unreachable | add the package to `manifest.json` |
| `packages-lock.json` modified on a self-hosted runner | the run was killed before "Remove toolkit build package" | the next run's checkout resets tracked files; locally, `git checkout -- Packages/packages-lock.json` |
| `Build profile needs the toolkit's PlayerBuilder` | a profile was chosen and the project still has its own `PlayerBuilder`, or `UNITY_BUILD_METHOD` is set | delete the project script (§6) / unset the variable, or choose `ProjectSettings` |
| `Build profile 'X' not found` | no `X.asset` in `Assets/Settings/Build Profiles/` on the built branch | fix the name (the error lists the project's profiles) |
| `Build profile … builds iOS, but this job builds Android` | the profile belongs to another platform | pick a profile of the leg's platform |
| `This project does not use Addressables` | `build-addressables` on, Addressables not installed | turn off `ADDRESSABLES_ENABLED` / the dispatch checkbox |
