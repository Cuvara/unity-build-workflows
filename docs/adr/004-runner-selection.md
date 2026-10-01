# ADR 004 — Runner selection: make the decision visible before changing it

**Status:** accepted (stages 1, 2 and 2c implemented; 2b delivered inside a
runner policy; 3 and 4 in part. See [Stage 2c](#stage-2c--a-runner-scheduler-minor))
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

**Labels follow the executor, not the target platform's OS.** This is the
obvious thing to get backwards, and it is wrong exactly where it matters: under
`BUILD_ENGINE=docker`, Unity cross-compiles a Windows player from inside the
Linux container, which is how every Windows build this toolkit has produced was
made. Routing `Windows64` to a Windows runner because the target is Windows
sends it to a machine that cannot run the container at all. The first draft of
stage 2 did precisely that, in a change whose own ADR warns about a disconnected
platform→executor map.

| | `docker` | `local` |
|---|---|---|
| Android / WebGL / Linux / **Windows64** | linux labels | target's own OS |
| iOS | macOS labels — no docker path exists | macOS labels |

### Stage 2b — one runner pool is not always the whole answer (minor)

`RUNNER_TYPE` and `BUILD_ENGINE` are single switches for the whole run, so an
org with both GitHub-hosted runners **and** its own machine cannot express
"Android on GitHub, Windows on my box". Measured, not assumed:

| Attempt | Result |
|---|---|
| `github-hosted` + `docker`, `RUNNER_WINDOWS_LABEL=self-hosted,windows` | override ignored — under docker, Windows routes on the linux labels |
| `github-hosted` + `BUILD_ENGINE=local` | rejected: *"GitHub-hosted runners have no local Unity install"* |
| `self-hosted` + `local` | everything moves, including Unity tests and Addressables |

The fix is to make the executor a **per-platform** decision — each platform
carrying its own `{labels, engine}`, defaulted from the global pair and
overridable per OS:

```
RUNNER_TYPE=github-hosted        # the default for everything else
BUILD_ENGINE=docker
RUNNER_MACOS_LABEL=self-hosted,macOS
BUILD_ENGINE_MACOS=local         # a Mac runs no Linux container
RUNNER_WINDOWS_LABEL=self-hosted,windows
BUILD_ENGINE_WINDOWS=local
```

The `github-hosted + local` rejection has to become per-platform rather than
disappear: it is still true for the platforms left on GitHub's runners.

### Stage 2c — a runner scheduler (minor)

Stages 1–2 answer *which labels*. They cannot answer *which machine*:
- `runs-on: [a, b]` is an AND, so there is no priority or fallback;
- nothing knows whether a runner is online or busy;
- nothing checks a runner can actually build the job.

A studio with two Macs and a Linux Docker host has no way to say "the first Mac
that is up; Android on the Docker host, GitHub-hosted if it is down".

**Decision.** Add `scripts/common/runner_scheduler.py`, run in stage 01 between the
flow resolver and the matrix step. It makes one decision per Unity job: each
matrix platform, Unity tests and Addressables. The decision is made before the
job exists, because `runs-on` cannot be chosen from inside the job it schedules.

- **Self-hosted is the primary path.** The scheduler *finds* a self-hosted runner.
  GitHub-hosted is a managed provider: availability `managed`, never `idle`, and
  only used when the policy allows it. The modes are `self-hosted-only`,
  `self-hosted-preferred` (default) and `github-hosted`.
- **Separate stages.**
  1. requirements
  2. discovery: an `InventoryProvider`, with `runner_inventory.py` for the
     GitHub API and a snapshot provider for tests
  3. capability
  4. availability
  5. priority
  6. fallback
  7. `runs-on`

  A provider can be replaced without touching policy.
- **Ownership.**
  - `resolve_build_flow.sh` keeps the legacy answer, and the scheduler consumes
    it rather than re-deriving it. The resolver gained only the per-engine
    activation outputs and a policy-aware log line.
  - `resolve_platform_executor.allowed_runner_os()` is the platform-safety table,
    derived from the build steps that actually exist. This is partial stage 4,
    and it settles Windows64 for scheduling: Linux under docker, Windows or macOS
    under local.
  - `matrix_runner_labels.py` only serializes.
- **One source of truth.** The `runner-selection` JSON document. Everything
  downstream reads it:
  - matrix rows (`runs-on`, engine, activation, selected target)
  - Unity tests and Addressables `runs-on`
  - the license gate
  - the summary and the final report
- **Fallback is deterministic** and never reaches an ineligible target:

  | `on-busy` | Order |
  |---|---|
  | `wait` | idle primary → busy primary → idle fallback → busy fallback |
  | `next` | idle primary → idle fallback → busy primary → busy fallback |

- **Policy cannot weaken platform safety.** An incompatible target written for a
  platform is a policy error. A generic default list is filtered instead.
- **Stage 2b, inside a policy.** A row may use `local` while the run uses
  `docker`, so engine and activation travel per matrix row. The license gate
  follows "any docker job".
- **Stage 3, in part.** With a policy, "nothing eligible" fails stage 01 with a
  report instead of queueing.
- **Availability is advisory.** Detection is not reservation, and GitHub still
  schedules. The build job's first step prints the selected target next to
  `runner.name`. Without a `RUNNER_STATUS_TOKEN` (`GITHUB_TOKEN` cannot read
  runner state), availability is `unknown`, and `on-unavailable: first` picks the
  first eligible self-hosted target without ever jumping to GitHub-hosted.
- **The standalone workflows route through it as well.**
  `unity-build-{android,webgl,linux,ios}.yml`, `unity-test{,-ios}.yml` and
  `unity-release-ios.yml` each have a `resolve-runner` job.
  - That job runs on GitHub-hosted `ubuntu-latest` only when a policy is opted in
    by variable (`RUNNER_POLICY` / `RUNNER_POLICY_FILE`) and no explicit label
    was passed.
  - Otherwise it is skipped, and the Unity job uses the explicit label, else
    today's literal. A project without a policy therefore gains no GitHub-hosted
    job.

**Hardening before release.** Found by the pre-merge audit:
- **Group membership is never inferred from labels.** It is read from the group
  endpoints, and each group is confirmed, not-found or unknown.
  - **Unknown:** availability `unknown`, no member pinning, `runs-on` = group +
    labels.
  - **Not found:** an actionable ineligibility.
- **The tier order includes `unknown`**, right after `idle` in each list.
  GitHub-hosted ranks after every idle or unknown self-hosted fallback target.
- **An inherited `default` that cannot serve a job safely** falls back to that
  job's legacy routing, and the legacy labels are themselves checked. An explicit
  platform section still fails.
- **Unity and Xcode share one rule:** declared lists match exactly; undeclared is
  unknown and never satisfies a requirement.
- **Duplicate ids are rejected while parsing.**

**Compatibility.** With no policy, every `runs-on`, engine and activation is
byte-identical. This is pinned by `tests/test_runner_selection_golden.py` against
a baseline captured before the change. `ios-runner-label` defaults to `''`
instead of `macos-unity-xcode`, which resolves to the same label. In the
standalone workflows, the Unity job's `runs-on` without a policy is exactly the
pre-scheduler literal, and no extra job runs.

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
