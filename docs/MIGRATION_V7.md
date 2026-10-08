# Migrating to 7.0.0

7.0.0 removes the `unity-build.yml` family of reusable workflows. They
predated `unity-pipeline.yml` and the `pipeline-*-release.yml` promotions, had
been deprecated since 6.23.0, and nothing in the current flow or the numbered
`templates/consumer-NN-*.yml` entry workflows called them.

**If your workflows call only `unity-pipeline.yml` and `pipeline-*-release.yml`,
nothing changes**: move `@v6` to `@v7` and you are done. The inputs, secrets
and outputs of those workflows are the same in 7.0.0 as in the last 6.x release.

`@v6` stays where it is (the last 6.x release, which still ships the removed
workflows), so a project that has not migrated keeps building until it moves.

## What was removed

| Removed | Use instead |
|---|---|
| `unity-build.yml` (orchestrator) and the per-platform `unity-build-android.yml`, `unity-build-webgl.yml`, `unity-build-linux.yml`, `unity-build-ios.yml` it called | `unity-pipeline.yml` — templates `consumer-01-ci.yml`, `consumer-10-build-development.yml`, `consumer-11-build-release.yml` |
| `unity-build-gameci.yml` | `unity-pipeline.yml`; its Docker lane runs game-ci itself |
| `unity-validate.yml`, `unity-test.yml`, `unity-test-ios.yml` | the validate and test stages of `unity-pipeline.yml` (`reusable-unity-tests.yml`) |
| `unity-nightly.yml` | a scheduled caller of `unity-pipeline.yml` (`on: schedule` in your own entry workflow) |
| `unity-release.yml`, `unity-release-ios.yml` | Build / Release (`consumer-11`) produces the artifact; `pipeline-<platform>-release.yml` (`consumer-20…24`) promotes it |
| `templates/project-workflow.yml`, `templates/consumer-unity-build.yml`, `examples/sample-unity-project-integration/` | `templates/consumer-*.yml` |
| Composite actions used only by those workflows: `activate-unity`, `build-ios`, `build-unity`, `collect-container-output`, `discord-notify`, `resolve-unity-image`, `restore-docker-cache`, `run-unity-container`, `upload-build-report`, `validate-unity-project` | not needed: `unity-pipeline.yml` / `reusable-build-platform.yml` do the work, and Discord is `discord-upload-build` |
| `docs/ADD_NEW_PROJECT.md` | [CONSUMER_SETUP.md](CONSUMER_SETUP.md) |

The scripts under `scripts/` stay, including `scripts/docker/run_unity_container.py`
for local Docker builds.

## Moving a project

1. **Replace the caller.** Copy the entry workflows you need from `templates/`
   (`consumer-01`, `10`, `11` for builds, `20`–`24` for releases) into
   `.github/workflows/`, and delete the workflow that called `unity-build.yml`.
2. **Configuration moves from inputs to repository variables.** The old
   per-call inputs (`target-platform`, `test-level`, `cache-mode`, …) become
   variables read by stage 01 — `BUILD_PLATFORMS`, `TEST_*`, `CACHE_*` and the
   rest in [REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md). Branches decide
   development / staging / release builds ([BRANCH_FLOW_CONTRACT.md](BRANCH_FLOW_CONTRACT.md)).
3. **`BuildConfig/` is not read by `unity-pipeline.yml`.** Product name, version
   and bundle id come from the project's Player Settings and the repository
   variables. Keep the folder only if your own editor code uses it.
4. **The build entry point is the toolkit's `PlayerBuilder`.** The removed
   native iOS route ran `Company.BuildPipeline.Editor.BuildCommand.Execute`;
   every lane of `reusable-build-platform.yml` runs
   `Company.BuildPipeline.Editor.PlayerBuilder.Build` (copied into the project
   per build, see [TOOLKIT_BUILD_PACKAGE.md](TOOLKIT_BUILD_PACKAGE.md)), or
   your project's own global `PlayerBuilder`, or `UNITY_BUILD_METHOD`.
5. **Secrets keep their names** (`UNITY_LICENSE`, `UNITY_EMAIL`,
   `UNITY_PASSWORD`, the Android keystore and iOS signing secrets,
   `DISCORD_WEBHOOK_URL`). See [CONSUMER_SETUP.md](CONSUMER_SETUP.md).
6. **Move the ref to `@v7`.**
