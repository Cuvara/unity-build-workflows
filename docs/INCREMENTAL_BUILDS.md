# Incremental Builds on Self-Hosted Runners

How a self-hosted runner keeps the Unity `Library/` folder between runs, what that
saves, how to force a clean build, and how to set runners up so the saving is
not lost to platform switches.

Applies from **v7.1.1**. Before it, the project checkout deleted `Library/` on
every run (see [Before v7.1.1](#before-v711)).

---

## Contents

1. [What is kept, and where](#1-what-is-kept-and-where)
2. [Recommended settings](#2-recommended-settings)
3. [Forcing a clean build](#3-forcing-a-clean-build)
4. [Keep each runner on one build target](#4-keep-each-runner-on-one-build-target)
5. [Checking that it works](#5-checking-that-it-works)
6. [What to expect](#6-what-to-expect)
7. [Troubleshooting](#7-troubleshooting)
8. [Before v7.1.1](#before-v711)

---

## 1. What is kept, and where

A self-hosted runner reuses one workspace per repository, e.g.
`<runner>/_work/<repo>/<repo>/`. Unity's `Library/` lives inside the project in
that workspace, so it can survive from one run to the next. Everything an
incremental build reuses is in it:

| `Library/` content | Saves |
|---|---|
| Asset database and import artifacts | The full asset import on editor start |
| `PackageCache/` | Package download and resolution |
| `Bee/`, `ScriptAssemblies/` | Script compilation, IL2CPP C++ compilation, Gradle project |
| `ShaderCache/` | Shader variant compilation |
| `BurstCache/` | Burst AOT compilation |
| `BuildCache/` (Addressables / Scriptable Build Pipeline) | Rebuilding unchanged bundles and content archives |

The build and tests jobs check the project out with `clean: false`, then run
`scripts/common/clean_workspace.sh`:

- `git reset --hard HEAD` — tracked files match the commit, whatever the last
  build changed.
- `git clean -ffdx` — every untracked and ignored file is removed (old build
  output, logs, stray files), **except** `<project-path>/Library/` and the
  toolkit checkout `.toolkit/`.
- With a clean build (`BUILD_CLEAN=true` or `clean-build: true`) `Library/` is
  removed as well.

Only the project's own `Library/` is spared, anchored at the repository root
through `project-path`; a folder named `Library` anywhere else is cleaned like
any other untracked file.

GitHub-hosted runners start from an empty machine on every job, so none of this
applies there. Their only reuse is the `CACHE_LIBRARY_ENABLED` cache.

## 2. Recommended settings

For a project that builds on its own machines:

```bash
gh variable set BUILD_CLEAN            --repo "${REPO}" --body "false"  # incremental (default)
gh variable set CACHE_LIBRARY_ENABLED  --repo "${REPO}" --body "false"  # the local Library is faster
gh variable set CACHE_GRADLE_ENABLED   --repo "${REPO}" --body "false"  # Gradle lives in Library/Bee
```

`CACHE_LIBRARY_ENABLED=true` uploads and downloads `Library/` through the GitHub
Actions cache. On a self-hosted runner that is a multi-GB transfer to restore
something already on disk, and a restore that times out (`Failed to restore`)
costs more than it saves. Leave it on only for GitHub-hosted runners.

## 3. Forcing a clean build

A clean build deletes `Library/` on the runner that runs the job and rebuilds it
from nothing. Use it when the cache is suspected: import errors that do not
reproduce locally, a Unity version change that left stale artifacts, a build
that differs from a clean checkout.

**One run.** In the Run workflow form of Build / Development or Build /
Release, set **UNITY · Clean build** to `true`, or:

```bash
gh workflow run "Build / Development" --ref develop -f platform=Android -f clean-build=true
```

| `clean-build` input | Meaning |
|---|---|
| `auto` (default) | Use the `BUILD_CLEAN` variable (default `false`) |
| `true` | Clean this run, whatever the variable says |
| `false` | Incremental this run, whatever the variable says |

`auto` does not depend on the build type: a release build is incremental too
unless `BUILD_CLEAN` or the input says otherwise.

**Every run** (gives up the whole saving — use only while investigating):

```bash
gh variable set BUILD_CLEAN --repo "${REPO}" --body "true"
```

**By hand on the machine**, with no run: delete
`<runner>/_work/<repo>/<repo>/<project-path>/Library`. Do it while the runner
is idle.

Notes:

- `Library/` is per machine. A clean build empties it only on the runner that
  ran that job.
- CI / Validate & Test has no `clean-build` input on its form; its tests job
  follows `BUILD_CLEAN`.
- The first run after a clean build is a full build again; the next one is
  incremental.

## 4. Keep each runner on one build target

`Library/` holds import results for the active build target. When the same
machine alternates between targets — an Android build, then Unity tests in the
editor's Standalone target, then an iOS build — each switch re-imports every
platform-dependent asset (textures above all) before the build can start. On a
mid-size 2D/DOTS project that was about 3,400 textures on every switch.

Avoid it with a runner policy that gives each platform its own machine (or
pool), so each `Library/` stays on one target:

```json
{
  "$schema": "https://raw.githubusercontent.com/<owner>/unity-build-workflows/v7/schemas/unity-runner-policy.schema.json",
  "version": 1,
  "mode": "self-hosted-only",
  "availability": { "scope": "org", "on-busy": "next", "on-unavailable": "first" },
  "pools": {
    "windows": { "labels": ["self-hosted", "Windows"], "os": "windows", "build-engines": ["local"] },
    "macos":   { "labels": ["self-hosted", "macOS"],   "os": "macos",   "build-engines": ["local"] }
  },
  "platforms": {
    "Android":    { "priority": ["windows"], "fallback": ["macos"] },
    "UnityTests": { "priority": ["windows"], "fallback": ["macos"] },
    "iOS":        { "priority": ["macos"] }
  }
}
```

Saved as `.github/unity-runner-policy.json`, this sends Android and the tests to
the Windows machines and keeps the Mac for iOS. Pools are defined by labels, so
a machine registered later with the same labels joins its pool with no edit.
Android and iOS also build in parallel instead of queueing on one machine.
Details: [MULTI_RUNNER_SCHEDULING.md](MULTI_RUNNER_SCHEDULING.md).

Two more ways to lose the saving:

- **One runner, several projects or branches.** The workspace is per
  repository, so branches share it. Builds that alternate between branches with
  very different assets re-import the difference each time.
- **Several runners on one machine.** Each registered runner has its own
  `_work` folder and therefore its own `Library/`; each warms up separately.

## 5. Checking that it works

In the build or tests job log:

| Step | Expect |
|---|---|
| Checkout | No `Removing <project>/Library/` line |
| Clean workspace | `Incremental build: keeping <project>/Library/` and `Library kept: <project>/Library/` |
| Unity build | No `Rebuilding Library because the asset database could not be found!` |

In the Unity log (the `*-logs` artifact, `Editor.log`):

- `Asset Pipeline Refresh (...) Initiated by InitialRefreshV2` — the import on
  start; seconds rather than minutes when warm.
- The number of `Importing 'GUID:` lines — tens when warm, thousands when cold
  or after a target switch.
- Shader passes report `Local cache hits N ... compiled 0 variants` when the
  shader cache was reused.
- `[PlayerBuilder] result=Succeeded ... duration=` — the player build alone.

## 6. What to expect

Measured on a Unity 6 DOTS project (Android APK, IL2CPP, Mac mini runner), the
same commit built three times:

| | `Library/` deleted (before v7.1.1) | Kept, but last used for another target | **Kept, same target** |
|---|---|---|---|
| Whole run | 17m34s | 13m50s | **5m16s** |
| Unity step | 12m02s | 10m56s | **2m23s** |
| Player build (`[PlayerBuilder] duration`) | 8m25s | 7m59s | **1m01s** |
| Assets imported on start | 10,837 | 3,859 | **43** |
| Shader variants compiled | all (0 cache hits) | all | **0 (all cache hits)** |

The first run on a machine, and the first after a clean build, is always the
slow one.

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Every build still imports everything | Toolkit older than v7.1.1, or the caller pins an older ref | Use `@v7` (or `@v7.1.1` or later) for both `uses:` and `toolkit-ref` |
| Thousands of texture imports on a warm runner | The runner last built another target, or ran tests (Standalone) | Give the platform its own runner — [§4](#4-keep-each-runner-on-one-build-target) |
| `Failed to import asset`, `Asset database is corrupted`, odd compile errors only in CI | A broken `Library/` (crashed or killed run, Unity version change) | One clean build — [§3](#3-forcing-a-clean-build) |
| `Failed to restore` / long cache step on a self-hosted runner | `CACHE_LIBRARY_ENABLED=true` downloads a copy of what is already on disk | `CACHE_LIBRARY_ENABLED=false` |
| Runner disk fills up | Each repository (and each registered runner) keeps its own `Library/` | Delete `Library/` of projects no longer built there, or run a clean build |

## Before v7.1.1

Up to v7.1.0 the project checkout used `actions/checkout`'s default
`clean: true`, whose `git clean -ffdx` deleted `<project>/Library/` at the start
of every job. `BUILD_CLEAN=false` was resolved and reported, but the build that
followed was a full one anyway. Upgrading the caller's ref is the whole
migration; no setting changes.

---

## See also

- [REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md) — `BUILD_CLEAN`, `CACHE_*_ENABLED`
- [MULTI_RUNNER_SCHEDULING.md](MULTI_RUNNER_SCHEDULING.md) — runner policies
- [SELF_HOSTED_WINDOWS_RUNNER.md](SELF_HOSTED_WINDOWS_RUNNER.md) · [SELF_HOSTED_MACOS_RUNNER.md](SELF_HOSTED_MACOS_RUNNER.md)
