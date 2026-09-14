# ADR 004 — Runner selection: make the decision visible before changing it

**Status:** accepted (stages 1–2 implemented)
**Date:** 2026-09-14

## Context

Eight settings feed one decision — *which machine does this job run on?*

| Setting | Meaning |
|---|---|
| `RUNNER_TYPE` | `github-hosted` \| `self-hosted` — **where** |
| `BUILD_ENGINE` | `docker` \| `local` — **how** Unity runs |
| `RUNNER_LABELS` | the actual `runs-on` list |
| `RUNNER_WINDOWS_LABEL` / `RUNNER_MACOS_LABEL` / `RUNNER_LINUX_LABEL` | per-OS labels |
| `RUNNER_DEFAULT_MODE` (+ `DEFAULT_RUNNER_MODE`, `VAR_DEFAULT_RUNNER_MODE`, `LEG_DEFAULT_RUNNER_MODE`) | legacy, one setting under four names |
| `runner-mode` | derived backwards from the two above it, for unmigrated consumers |
| `execution-strategy` | `github-docker` \| `selfhosted-local` \| `selfhosted-docker` |
| `resolve_platform_executor.py` | platform → executor map |

Nothing printed the answer. Asking "why did this job land on that machine?"
meant reading `resolve_build_flow.sh`.

### What the audit found

1. **The file that calls itself the single source of truth is not in the path.**
   `resolve_platform_executor.py` opens with *"Single source of truth for mapping
   a Unity build target platform to its required CI executor"*. It is called only
   from `unity-build.yml`, `unity-build-ios.yml` and `unity-test-ios.yml` — all
   legacy. `unity-pipeline.yml`, the engine behind every numbered entry workflow,
   never calls it. It also lists `Windows64` as `UNSUPPORTED_PLATFORMS` while the
   toolkit ships `pipeline-windows-release.yml` and a Windows build target.

2. **The fix for the hardest part is written and unplugged.**
   `RUNNER_WINDOWS_LABEL` / `RUNNER_MACOS_LABEL` / `RUNNER_LINUX_LABEL` are
   resolved, emitted as script outputs, and re-exported as pipeline job outputs.
   Nothing reads them.

3. **One global `runs-on` for a multi-platform matrix.** Stage 03 passes a single
   `runner-labels` list to every leg, and stage 03b (iOS signing) falls back to
   the same list. A project on `RUNNER_TYPE=self-hosted` therefore sends its iOS
   signing job to whatever the global labels name — by default, a Windows box.

4. **The self-hosted default guesses Windows.** `runner_labels_raw` defaults to
   `self-hosted,windows` for any self-hosted project, regardless of OS. GitHub
   does not fail a job whose labels match no runner; it queues it. A self-hosted
   Linux project that forgets `RUNNER_LABELS` waits forever with no error.

5. **Only one impossible combination is rejected.** `github-hosted + local` fails
   with a clear message. `self-hosted + docker` on a machine without Docker fails
   deep inside the build; labels are never cross-checked against `RUNNER_TYPE`.

6. **The resolver and the matrix disagree about what will build.** On a push the
   resolver takes platforms from `*_BUILD_PLATFORMS` and ignores the `platform`
   input, so a CI-lane run logs `android=true webgl=true`; `platform: None` is
   honoured later, where `unity-pipeline.yml` builds the matrix. Both are
   correct in the end, and the log in between is not.

## Decision

Report before routing. Stage 1 changes no routing at all.

### Stage 1 — the runner plan (this ADR, implemented)

`resolve_build_flow.sh` emits `runner-plan`: one row per platform that will
actually build, carrying `runsOn`, `engine`, `runnerType`, the **tier that
decided** the labels, and a note. `unity-pipeline.yml` renders it as a table in
the stage-01 summary, and re-exports it as a job output.

Two properties matter more than the format:

- **It reflects what happens, not what should.** Every row shares one `runs-on`,
  because that is what the pipeline does today. `test_the_plan_reports_the_shared_runs_on_it_actually_has`
  pins that, and names itself as the test to update when routing becomes
  per-platform.
- **The CI lane plans nothing.** `platform: None` produces an empty plan rather
  than rows for builds that will not run (finding 6).

iOS on non-macOS labels is reported as a note and a warning — finding 3 made
visible without yet being enforced, because enforcement is breaking and a
warning that ships today beats a gate that ships next month.

### Stage 2 — per-platform labels (minor)

Wire the three per-OS label variables that already exist (finding 2) into the
stage-03 matrix and stage 03b, so each platform gets its own `runs-on`.
`RUNNER_LABELS` becomes an override-everything escape hatch. Closes finding 3.

Implemented in 5.2.0. Three details worth keeping:

- **The per-OS defaults used to disagree with each other.** Linux defaulted to
  `ubuntu-latest` — a GitHub-hosted label — while windows and macos defaulted to
  `self-hosted-windows` / `self-hosted-macos`, which are self-hosted label
  *names* no GitHub-hosted runner carries. The default now follows
  `RUNNER_TYPE`: GitHub's own labels for `github-hosted`, `self-hosted,<os>`
  otherwise.
- **An explicit global `RUNNER_LABELS` still wins.** One runner that does every
  platform is a real setup, and taking its escape hatch away would break exactly
  those projects.
- **Unity tests and the Addressables build stopped riding the matrix labels.**
  They always run in the Linux container, whatever the matrix is doing; on a
  Windows-labelled self-hosted project they were being sent to a machine with no
  Linux container on it.

The stage-1 iOS warning survives, but is now reachable only by explicit
override rather than by the default — which is when a person most needs telling.

### Stage 3 — fail fast instead of queueing (major)

Cross-check labels against `RUNNER_TYPE`; preflight `docker info` on
`self-hosted + docker`; give every build job a `timeout-minutes` so finding 4
becomes a readable failure. Drop the `self-hosted,windows` default in favour of
"declare it" — guessing the OS silently is what makes it dangerous.

### Stage 4 — one source of truth (minor)

Either call `resolve_platform_executor.py` from `unity-pipeline.yml` or fold it
into `resolve_build_flow.sh` and delete it. Settle `Windows64`. The list of
valid platforms must exist in exactly one place.

### Stage 5 — retire the legacy surface (major)

`RUNNER_DEFAULT_MODE` and its three aliases, the `runner-mode` bridge, and
`execution-strategy` if it never grows a consumer beyond the summary table.

## Consequences

Stage 1 costs one output and one summary table, and changes no behaviour.
Stages 2–5 are each separately shippable; 3 and 5 are breaking and belong in
majors.

The order is deliberate. Changing how a runner is chosen while nobody can read
what it currently chooses is how findings 3 and 4 survived this long.
