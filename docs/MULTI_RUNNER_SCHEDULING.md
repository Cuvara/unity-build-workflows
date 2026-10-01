# Multi-Runner Scheduling

How the toolkit decides **which runner each Unity job runs on** when you have
more than one machine — and how to tell it what you have.

> **Nothing changes until you add a policy.** With no runner policy configured,
> every job goes exactly where it went before (`RUNNER_TYPE`, `BUILD_ENGINE`,
> `RUNNER_LABELS`, the per-OS labels and `RUNNER_DEFAULT_MODE` behave as
> documented in [RUNNER_AND_BUILD_ENGINE.md](RUNNER_AND_BUILD_ENGINE.md)). A
> golden test pins that byte for byte.

Design record: [ADR 004, stage 2c](adr/004-runner-selection.md).

---

## Contents

1. [The model in one picture](#1-the-model-in-one-picture)
2. [Runner labels](#2-runner-labels)
3. [Runner capabilities](#3-runner-capabilities)
4. [Runner groups](#4-runner-groups)
5. [Execution modes and the default runner](#5-execution-modes-and-the-default-runner)
6. [Platform-specific runner pools](#6-platform-specific-runner-pools)
7. [Priority](#7-priority)
8. [Fallback](#8-fallback)
9. [Availability detection](#9-availability-detection)
10. [Race conditions](#10-race-conditions)
11. [GitHub-hosted vs self-hosted](#11-github-hosted-vs-self-hosted)
12. [Matrix behaviour](#12-matrix-behaviour)
13. [Examples: Android and iOS](#13-examples-android-and-ios)
14. [Configuration reference](#14-configuration-reference)
15. [What you see in a run](#15-what-you-see-in-a-run)
16. [Migration](#16-migration)
17. [Known limitations](#17-known-limitations)

---

## 1. The model in one picture

GitHub Actions cannot express priority or fallback in `runs-on`: a list of labels
is an **AND** (a runner must carry all of them), and GitHub picks *any* runner that
matches. So the decision is made **before** the build job exists, in stage 01:

```
resolve_build_flow.sh      legacy answer: RUNNER_TYPE, BUILD_ENGINE, RUNNER_LABELS, per-OS labels
        │
        ▼
runner_scheduler.py        for EACH Unity job (each platform, Unity tests, Addressables):
   1 requirements            platform, build engine, lane → allowed runner OS, Unity, Xcode, labels
   2 discover                self-hosted runner inventory (GitHub API, optional)
   3 capability filter       hard constraints — an ineligible runner is never a candidate
   4 availability            idle | busy | offline | not-found | unknown
   5 priority                the policy's order
   6 fallback                on-busy wait|next, mode rules, GitHub-hosted only if allowed
   7 runs-on                 labels, or {group, labels}
        │
        ▼
runner-selection (one JSON document) ──▶ matrix rows ──▶ build jobs' runs-on
                                     ──▶ Unity tests / Addressables runs-on
                                     ──▶ step summary, final report
```

**Self-hosted runners are the primary execution path.** The scheduler's job is
to find a self-hosted runner that can build the job and is available.
GitHub-hosted is a *managed provider* the policy may allow as a fallback, or
select outright — never a runner with an "idle" state.

Four ideas are kept apart on purpose:

| Concept | Question | Where it lives |
|---|---|---|
| **Identity** | Which machine is this? | the runner's GitHub name (= its key under `runners`) and a label unique to it |
| **Capability** | What can it build? | `os`, `platforms`, `build-engines`, `unity`, `xcode`, labels |
| **Availability** | Can it take a job now? | the GitHub runner API: `status`, `busy` |
| **Priority** | Which eligible machine do we prefer? | the order of `priority` / `fallback` |

---

## 2. Runner labels

`runs-on` matches **labels**, never runner names. To target one specific
machine, give it a label nobody else has — by convention, its own name:

```bash
# macOS / Linux
./config.sh --url https://github.com/<owner>/<repo> --token <TOKEN> \
            --name ios-build-01 --labels ios-build-01,xcode-16,unity
```

GitHub adds `self-hosted`, the OS (`macOS`, `Linux`, `Windows`) and the
architecture automatically. In the policy, the runner is then:

```json
"ios-build-01": { "labels": ["self-hosted", "macOS", "ios-build-01"] }
```

Rules the scheduler enforces:

- every runner and pool lists at least one label, and its OS must be known
  (declared with `"os"` or implied by an OS label such as `macOS` / `linux`);
- a declared `os` that contradicts its labels is a policy error;
- if a runner's label set would also match **another** runner, the scheduler
  warns — GitHub could start the job on that other machine.

Labels you require for a job (`require.labels`, e.g. `xcode-16`) are appended
to `runs-on`, so GitHub enforces them as well.

## 3. Runner capabilities

Capabilities are what a runner *can* build. They are checked before
availability, and a runner that fails them is never selected — not as a
priority entry, not as a fallback.

| Field | Meaning | Checked against |
|---|---|---|
| `os` | `linux` \| `macos` \| `windows` | the job's allowed OS (below) — and the OS the API reports |
| `platforms` | jobs this runner may run | the job (`Android`, `iOS`, …) |
| `build-engines` | `docker`, `local` | the job's engine |
| `unity` | installed editor versions | `require.unity` (`"project"` = `ProjectVersion.txt`) |
| `xcode` | installed Xcode versions | `require.xcode` |
| labels | — | `require.labels`, against the runner's **actual** labels when the API is available (`missing=xcode-16`) |

### Unity and Xcode versions: one model

Unity and Xcode are checked the same way:

| Job requires… | Target declares… | Result |
|---|---|---|
| nothing | anything, or nothing | not checked |
| `6000.0.26f1` | a list containing exactly `6000.0.26f1` | eligible |
| `6000.0.26f1` | `["6000.3.9f1"]` | ineligible: comparison is exact, with no prefix or "compatible minor" matching |
| `16` (Xcode) | `["16.2"]` | ineligible: exact string; declare `"16"` too if that is what you mean |
| a version | no list at all | ineligible: **an undeclared capability is unknown, and unknown never satisfies a requirement** |
| a version | `[]` | ineligible: declares support for nothing |

`require.unity: "project"` uses the version in `ProjectVersion.txt`. If the
workflow does not know that version, the policy is rejected rather than the
requirement being silently dropped.

A pool's own `unity` / `xcode` lists apply to all of its members. A member that
is also declared under `runners` is checked against its own lists. The
GitHub-hosted provider builds with a Docker image resolved from the project's
Unity version, so it satisfies `require.unity` only for that version. It never
satisfies `require.xcode`.

The scheduler cannot inspect a machine's installed software. Declared versions
are trusted, unless you also express them as labels (e.g. `xcode-16`), which the
API *can* verify against the runner's actual labels.

### Platform safety (cannot be overridden)

Before any policy is read, every job has a fixed set of runner operating systems
it can execute on — derived from the build steps that actually exist
(`scripts/common/resolve_platform_executor.py :: allowed_runner_os`):

| Job | `docker` engine | `local` engine |
|---|---|---|
| iOS | **rejected** — no Docker path for Xcode | macOS |
| Windows64 | Linux | Windows, macOS |
| Android, WebGL, Linux64, LinuxServer | Linux, Windows¹ | Windows, macOS |
| Unity tests, Addressables | Linux | Windows, macOS |

¹ A Windows host running Docker in Linux-container mode (pipeline lane only).
There is no Linux + `local` build step, so Linux is not a `local` target.
Standalone workflows are narrower: `unity-build-{android,webgl,linux}.yml` and
`unity-test.yml` are Linux + docker only; `unity-build-ios.yml`,
`unity-test-ios.yml` and `unity-release-ios.yml` are macOS + local only.

A policy that names an incompatible target **for a specific platform** (iOS →
a Linux runner, iOS → `github-hosted`, iOS with `build-engine: docker`,
Windows64 docker on a Windows runner) fails the run at stage 01 with a policy
error. It is never silently rerouted. A generic `default` list is filtered
instead: entries a platform cannot use are dropped with a reason.

## 4. Runner groups

Runner groups are an organization feature: a group decides which repositories
may use its runners. A pool or runner may name one:

```json
"pools": { "mac-pool": { "labels": ["self-hosted", "macOS", "unity"], "group": "unity-mac" } }
```

When selected, `runs-on` becomes `{ "group": "unity-mac", "labels": [...] }`,
so GitHub only considers that group's runners.

**Membership is never guessed from labels.** A runner that carries a pool's labels
may sit outside the group, where GitHub will never send the job. The scheduler
reads group membership only from the runner-group endpoints, which need an
organization-scoped token with **Self-hosted runners: read**. Each group then
falls into one of three cases:

| Group membership | What the scheduler does |
|---|---|
| **confirmed** (group listed, members read) | Members are the group's runners that also carry the pool labels. Capability and availability are checked per member. If the scheduler pins a member, it only considers runners inside the group. |
| **not found** (group listing read, group absent) | The target is ineligible: *"runner group 'x' does not exist, or is not visible to RUNNER_STATUS_TOKEN"*. If nothing else qualifies, the run fails with that reason. |
| **unknown** (group endpoints unreadable, e.g. a repository-scoped token, or the API is down) | The target stays eligible on its declared capabilities. Availability is `unknown`. No member is claimed, no individual runner is pinned, and `runs-on` is the group plus the labels, which GitHub can schedule on any real member. The log and summary say membership was not confirmed. |

A runner target that names a group follows the same rules. If it is confirmed
outside the group it is ineligible; if membership is unknown its availability is
`unknown`.

Groups are **optional**. Nothing in the toolkit requires them; they are useful
when one organization shares machines between repositories — see
[SELF_HOSTED_ORG_RUNNER.md](SELF_HOSTED_ORG_RUNNER.md).

## 5. Execution modes and the default runner

| `mode` | Searches | GitHub-hosted | No eligible self-hosted target |
|---|---|---|---|
| `self-hosted-only` | self-hosted only | never; listing it in `fallback` is a policy error | **fails** with an actionable report |
| `self-hosted-preferred` *(default)* | self-hosted first | only if listed in `fallback`, only after self-hosted fails | GitHub-hosted if listed, else **fails** |
| `github-hosted` | nothing — no discovery, no token | selected directly (`ubuntu-latest`, docker) | n/a. A job that cannot run there (iOS) fails at stage 01 if its own section says `github-hosted`. If the mode is only inherited, the job keeps its legacy routing (below). |

`mode` can be set at the top level and overridden per platform.

**Default runner policy.** `default` applies to every job that has no
`platforms.<job>` section:

```
platforms.<job>  →  default  →  legacy RUNNER_* resolution  →  toolkit default
```

A job covered by neither keeps its legacy routing. Inheritance is per field
(`mode`, `build-engine`, `require`, `on-busy`, `on-unavailable`), except the
target lists: a platform that writes its own `priority` does **not** inherit the
default's `fallback` (so iOS cannot quietly pick up a Linux fallback).

**When `default` cannot serve a job safely.** A `default` is generic, so it may
have no target a particular job can use. The usual case is a Linux-only `default`
and an iOS build. For a job with **no section of its own**:

1. Default targets the job can never use are dropped, with the reason recorded.
2. If nothing usable is left, or an inherited `mode: github-hosted` cannot run the
   job, the job falls back to its **legacy routing**:
   - iOS in the pipeline: `RUNNER_MACOS_LABEL`, or `macos-latest`
   - the standalone iOS workflows: `macos-unity-xcode`

   The fallback is logged with a warning, and the dropped candidates are listed.
3. The legacy answer is itself checked. If its labels imply an OS the job can
   never use (iOS legacy labels naming Linux or Windows, for example), the run
   fails with *"No safe runner for iOS"* instead of routing it there.

An inherited `build-engine` does not apply to jobs with a fixed engine: iOS
always uses `local`, Unity tests and Addressables use the run's `BUILD_ENGINE`,
and the standalone lanes keep theirs.

A job **with** its own `platforms.<job>` section never falls back silently. If
that section, or the lists it inherits, names nothing the job can use, the run
fails with a report.

## 6. Platform-specific runner pools

Two ways to say "any of these machines":

- **Priority list** (*mode B*): `"priority": ["mac-01", "mac-02", "mac-03"]` —
  the scheduler picks the best available one by name.
- **Capability pool** (*mode C*): `"pools": { "mac-pool": { "labels": [...] } }` —
  every runner carrying all the labels is a member.

Pool selection is done member by member:

```
discover members (labels ⊇ pool labels, ∩ CONFIRMED group membership — see §4)
  → capability-filter EACH member (actual OS, actual labels incl. required ones, declared caps)
  → availability of ELIGIBLE members only
  → pool state: idle (an eligible member is idle) | busy (eligible members online, all busy)
                | offline (eligible members exist, none online) | ineligible (no eligible member)
```

Example: `mac-a` is online but lacks `xcode-16`; `mac-b` has it but is busy. For
an iOS job requiring `xcode-16`, the pool is **busy**, not idle.

`runs-on` for a pool stays elastic — the pool's labels plus required labels —
whenever every runner those labels match is eligible. If they would also match an
ineligible runner (one the API shows lacking a capability that is not a label,
such as a Unity version), the scheduler pins the chosen member through a label
unique to it, or warns if there is none. For a group pool, only runners
confirmed in the group count here, because no other runner can take the job.
When membership is unknown, the scheduler never pins.

## 7. Priority

For each job, in the policy's order:

```
for target in priority:
    if it can build the job (capability)        # stage 3
    and it is online and not busy               # stage 4
        select it; stop
```

`ios-build-01` offline and `ios-build-02` idle → `ios-build-02`, with the reason
*"highest-priority eligible idle runner; passed over: ios-build-01 offline"*.
A single-entry list is a **fixed runner** (*mode A*).

## 8. Fallback

`fallback` is evaluated only after the priority list, and only among
**capability-eligible** targets — fallback is about *where* to run once
eligibility is established, never a way around it. The order is deterministic:

| `on-busy: wait` (default) | `on-busy: next` |
|---|---|
| 1. idle priority target | 1. idle priority target |
| 2. unknown priority target | 2. unknown priority target |
| 3. busy priority target (queue for it) | 3. idle fallback target |
| 4. idle fallback target | 4. unknown fallback target |
| 5. unknown fallback target | 5. GitHub-hosted fallback (`managed`) |
| 6. GitHub-hosted fallback (`managed`) | 6. busy priority target |
| 7. busy fallback target | 7. busy fallback target |

Rules behind the table:
- **`unknown`** means availability could not be read: the API is unavailable, or
  runner-group membership is unconfirmed. It ranks right after `idle` in the same
  list, because it is *possibly* idle but never proven. With
  `on-unavailable: fail`, `unknown` targets are not selectable at all.
- **Offline** and **not-registered** targets are never selected.
- **The GitHub-hosted provider** (`"fallback": ["github-hosted"]`, mode
  `self-hosted-preferred` only) has availability `managed`. It ranks after every
  idle or unknown self-hosted fallback target, so an unreadable API can never by
  itself move a job to GitHub-hosted.
- Under `wait`, a busy preferred Mac still wins over GitHub-hosted; under `next`
  it does not.

When nothing qualifies, stage 01 fails with the requirements, every candidate's
status and the reason it was excluded, and suggested actions:

```
No eligible runner found for iOS.

Required capabilities:
  os=macos
  platform=iOS
  build-engine=local
  unity=6000.0.26f1
  xcode=16

Candidates:
  ios-build-01  [runner, primary]
    status=offline
  ios-build-02  [runner, primary]
    status=busy  (ineligible)
    xcode=15 does not include 16
  ios-build-03  [runner, primary]
    status=idle  (ineligible)
    missing=xcode-16

Suggested actions:
  - bring ios-build-01 online (the runner service is not connected)
  - install Xcode 16 on ios-build-02 and declare it in its "xcode" list
  - add label(s) xcode-16 to ios-build-03, or install what they stand for
  - register another runner with labels xcode-16 on macos and add it to the priority list
```

Failing in stage 01 is deliberate. GitHub does not fail a job whose labels match
no runner straight away; it leaves it queued.

## 9. Availability detection

With a token, the scheduler reads runner state from the documented GitHub REST
API (`scripts/common/runner_inventory.py`):

| Scope (`availability.scope`) | Endpoint |
|---|---|
| `repo` (default) | `GET /repos/{owner}/{repo}/actions/runners` |
| `org` | `GET /orgs/{org}/actions/runners` |
| groups | `GET /orgs/{org}/actions/runner-groups`, `GET /orgs/{org}/actions/runner-groups/{id}/runners` |

Each runner reports `name`, `os`, `status` (online/offline), `busy` and its
labels. One discovery serves every job of the run.

### Authentication

`GITHUB_TOKEN` **cannot** read runner status (its permissions have no
`administration` scope). Create a read-only token and store it as the secret
**`RUNNER_STATUS_TOKEN`** — never in a repository variable:

| Runners you schedule | Fine-grained PAT / GitHub App permission | Classic PAT scope |
|---|---|---|
| Repository runners | Repository → **Administration: Read-only** | `repo` |
| Organization runners, runner groups | Organization → **Self-hosted runners: Read-only** | `admin:org` (read) |

The consumer templates call the pipeline with `secrets: inherit`, so the secret
reaches stage 01 with no further wiring. If you call `unity-pipeline.yml` (or a
standalone workflow) with explicit secrets, add
`RUNNER_STATUS_TOKEN: ${{ secrets.RUNNER_STATUS_TOKEN }}`. The token is sent
only as an `Authorization` header and is redacted from every message.

### When availability cannot be read

No token, a 401/403/404, a network error or a timeout makes availability
**`unknown`** — deterministically. Requests time out after
`availability.timeout-seconds` (default 10); the whole discovery has a 30 s
budget; a 5xx or timeout is retried once; nothing waits indefinitely. Then
`on-unavailable` decides:

- `first` (default): the first capability-eligible **self-hosted** target, in
  priority-then-fallback order (the `unknown` rows of the table in §8), with a
  warning. It does **not** jump to GitHub-hosted, because nothing proved the
  self-hosted runners unavailable.
- `fail`: stage 01 fails.

**Capability is still enforced** from what the policy declares: OS and platform
safety, engines, platforms, Unity, Xcode and declared labels. An unknown status
affects ranking only, never eligibility. Runner-group membership is never assumed
(§4). Set `"availability": { "source": "none" }` to schedule on declarations
alone, without ever calling the API.

> **API-unavailable selection does not reserve a runner.** The selected runner may
> already be busy or offline when GitHub attempts to start the job, in which case
> GitHub may queue the job.

## 10. Race conditions

Availability detection is **not a reservation**:

```
scheduler sees ios-build-02 idle  →  another workflow's job takes it  →  this build job queues
```

GitHub Actions remains responsible for final scheduling. The design accepts
that and limits the damage:

- prefer **pools / groups** for elastic capacity: a pool target is matched by
  labels, so whichever eligible member frees up first takes the job;
- `on-busy: wait` keeps a job on its preferred machine rather than bouncing
  between machines on a momentary snapshot;
- every build job starts with a **Runner identity** step that prints the target
  that was selected next to the runner that was actually assigned
  (`runner.name`), so a mismatch is visible;
- **How long a queued job waits is GitHub's decision, not the scheduler's.** If
  a selected runner goes offline after selection, the job waits in GitHub's queue;
  cancel and re-run to reschedule. A build job's `timeout-minutes` covers running
  time, not time spent queued.
- The stage-01 summary and the final report record the **selected target**, never
  the executing runner. The executing runner appears only in each build job's
  *Runner identity* step (`runner.name`).

## 11. GitHub-hosted vs self-hosted

| | Self-hosted | GitHub-hosted |
|---|---|---|
| Role | primary execution path | managed provider, opt-in |
| Inventory / state | discovered: idle, busy, offline, not-found | none — availability `managed` |
| Engines | `docker`, `local` (per the platform table) | `docker` only, Linux images only |
| iOS | macOS + local | never |
| Configured by | `runners`, `pools`, `priority`, `fallback` | `mode: github-hosted`, or `fallback: ["github-hosted"]`; image via `"github-hosted": {"labels": ["ubuntu-24.04"]}` |

`RUNNER_TYPE` / `BUILD_ENGINE` keep their legacy meaning for jobs no policy
covers. A policy does not need them.

## 12. Matrix behaviour

Every job is scheduled **independently** — one decision per platform row, plus
Unity tests and Addressables. A run building Android, iOS and WebGL can send
Android to a Linux Docker pool, iOS to a Mac with local Unity, and WebGL to
GitHub-hosted, in the same matrix. Each matrix row carries its own `runs-on`,
`build-engine`, `activation-strategy` and selected target.

Because a policy may put one platform on `local` while the run's
`BUILD_ENGINE` is `docker`, engine and Unity activation are per row, and the
license gate (stage 01b) runs whenever **any** job uses the docker engine. Unity
tests and Addressables follow the run's `BUILD_ENGINE`: a policy may choose their
runner but not change their engine.

## 13. Examples: Android and iOS

### iOS on a priority list of Macs, Android on a Linux pool with a GitHub fallback

`.github/unity-runner-policy.json` (also in
[`examples/runner-policy/ios-priority-android-pool.json`](../examples/runner-policy/ios-priority-android-pool.json)):

```json
{
  "version": 1,
  "mode": "self-hosted-preferred",
  "availability": { "scope": "repo", "on-busy": "wait", "on-unavailable": "first" },
  "runners": {
    "ios-build-01": { "labels": ["self-hosted", "macOS", "ios-build-01"], "platforms": ["iOS"],
                      "build-engines": ["local"], "xcode": ["16"], "unity": ["6000.0.26f1"] },
    "ios-build-02": { "labels": ["self-hosted", "macOS", "ios-build-02"], "platforms": ["iOS"],
                      "build-engines": ["local"], "xcode": ["16"], "unity": ["6000.0.26f1"] },
    "android-build-01": { "labels": ["self-hosted", "linux", "android-build-01"],
                          "build-engines": ["docker"] }
  },
  "pools": {
    "linux-docker-pool": { "labels": ["self-hosted", "linux", "unity-docker"], "os": "linux" }
  },
  "platforms": {
    "iOS": {
      "mode": "self-hosted-only",
      "priority": ["ios-build-01", "ios-build-02"],
      "require": { "unity": "project", "xcode": "16" }
    },
    "Android": {
      "priority": ["android-build-01", "linux-docker-pool"],
      "fallback": ["github-hosted"]
    }
  }
}
```

| Situation | iOS | Android |
|---|---|---|
| everything idle | `ios-build-01` | `android-build-01` |
| `ios-build-01` offline | `ios-build-02` | — |
| both Macs offline | **fails** (self-hosted-only) with the report above | — |
| `android-build-01` busy, pool idle | — | `linux-docker-pool` |
| `android-build-01` busy, pool empty | — | queues for `android-build-01` (`on-busy: wait`) |
| `android-build-01` offline, pool empty | — | `github-hosted` (`ubuntu-latest`) |
| no `RUNNER_STATUS_TOKEN` | `ios-build-01` (`unknown`, warning) | `android-build-01` (`unknown`, warning) |

Other jobs (WebGL, Unity tests, …) have no section and no `default`, so they
keep their legacy routing.

### Everything on GitHub except iOS

```json
{
  "version": 1,
  "mode": "github-hosted",
  "runners": { "studio-mac": { "labels": ["self-hosted", "macOS", "studio-mac"] } },
  "default": {},
  "platforms": { "iOS": { "mode": "self-hosted-only", "priority": ["studio-mac"] } }
}
```

### Organization pools with runner groups

See [`examples/runner-policy/pools-and-groups.json`](../examples/runner-policy/pools-and-groups.json):
`availability.scope: org`, one pool per OS, each bound to a runner group,
`on-busy: next`.

## 14. Configuration reference

### Where the policy comes from

| Source | Precedence |
|---|---|
| `runner-labels` workflow input (non-empty) | pins **every** job; policy not consulted |
| `runner-policy: legacy` input of `unity-pipeline.yml` | policy ignored for this run |
| repository variable **`RUNNER_POLICY`** (inline JSON) | the policy |
| file at **`RUNNER_POLICY_FILE`** (default `.github/unity-runner-policy.json`) | the policy, if `RUNNER_POLICY` is unset |
| none | legacy `RUNNER_*` resolution, unchanged |

This order is the same in the pipeline and the standalone workflows. In the
standalone workflows, the explicit label input (`runner-label` /
`ios-runner-label`) takes the place of `runner-labels`. It is deterministic:
nothing depends on the runner's environment beyond these two variables and the
file in the checkout.

**Standalone workflows need a variable to opt in.** Their runner selection runs
in a separate `resolve-runner` job on GitHub-hosted `ubuntu-latest`, because
`runs-on` cannot be decided inside the job it schedules. So that projects without
a policy gain no GitHub-hosted job, that resolver runs **only** when
`RUNNER_POLICY` or `RUNNER_POLICY_FILE` is set as a repository or organization
variable *and* no explicit label was passed. Otherwise it is skipped, and the Unity
job uses the explicit label, else its pre-scheduler literal (`ubuntu-latest` or
`macos-unity-xcode`). To use a policy *file* with the standalone workflows, set
`RUNNER_POLICY_FILE`, even to the default path.

When the resolver does run, it needs a GitHub-hosted Linux runner. If GitHub-hosted
runners are disabled or out of minutes, it stays queued like any other job;
remove the variable to return to the literal labels. When it runs but cannot find
an eligible runner, it fails with the report from §8 instead of leaving the Unity
job queued. The pipeline (`unity-pipeline.yml`) is unaffected: its selection runs
in *Resolve Build Config*, which was already a GitHub-hosted job.

Validate the file in your editor with
[`schemas/unity-runner-policy.schema.json`](../schemas/unity-runner-policy.schema.json)
(`"$schema"` key). An invalid policy **fails stage 01**; it is never silently
ignored. This includes:
- malformed JSON
- unknown keys
- invalid modes, platforms or capabilities
- `github-hosted` in a priority list
- incompatible targets written for a platform
- **duplicate ids**: a runner id, pool id or platform section defined twice, the
  same target listed twice (or in both `priority` and `fallback`), or a repeated
  pool member

JSON itself keeps only the last of two duplicate keys, so the scheduler checks
for duplicates while parsing and names the collection and id. JSON Schema cannot
detect duplicate keys; editor validation will not catch them, but stage 01 will.
Duplicate labels within one runner are normalised away (labels are
case-insensitive), not rejected.

### Reviewing policy changes

The policy decides which machines run your build code. It sits in the same
trust position as the workflow files. A pull request can change
`.github/unity-runner-policy.json` just as it can change `.github/workflows/`, and
on `pull_request` runs the checked-out policy is the PR's. Protect it like the
workflows: add `.github/unity-runner-policy.json` to `CODEOWNERS`. Keep the
self-hosted public-repository precautions in
[SELF_HOSTED_ORG_RUNNER.md](SELF_HOSTED_ORG_RUNNER.md). Prefer the
`RUNNER_POLICY` variable when the policy must not be editable from a branch.
`RUNNER_STATUS_TOKEN` is not available to fork pull requests, so their runs
schedule with availability `unknown`.

### Policy keys

| Key | Purpose |
|---|---|
| `version` | `1` |
| `mode` | default execution mode (§5) |
| `availability` | `source` (`auto`\|`api`\|`none`), `scope` (`repo`\|`org`\|`both`), `on-busy`, `on-unavailable`, `timeout-seconds` |
| `runners.<name>` | one runner: `labels` (required), `group`, `os`, `platforms`, `build-engines`, `unity`, `xcode` |
| `pools.<id>` | a capability pool: `labels` (required), `group`, `members` (preference order), and the same capability keys |
| `github-hosted.labels` | the managed provider's image labels (Linux; default `ubuntu-latest`) |
| `default` | section for jobs without their own |
| `platforms.<job>` | `Android`, `WebGL`, `Linux64`, `LinuxServer`, `Windows64`, `iOS`, `UnityTests`, `Addressables` |
| *section keys* | `mode`, `build-engine`, `priority`, `fallback`, `require` (`labels`, `unity`, `xcode`), `on-busy`, `on-unavailable` |

### Workflow inputs

| Workflow | Input | Default | Meaning |
|---|---|---|---|
| `unity-pipeline.yml` | `runner-policy` | `auto` | `auto` applies the policy if one exists; `legacy` ignores it this run. Not on the dispatch forms, which are at GitHub's documented input limit; `runner-labels` on the form pins every job instead |
| `unity-build-ios.yml`, `unity-build.yml` | `ios-runner-label` | `''` | explicit label wins; empty = policy (when opted in by variable), else `macos-unity-xcode` (unchanged) |
| `unity-build-{android,webgl,linux}.yml`, `unity-test.yml` | `runner-label` | `''` | explicit label wins; empty = policy (when opted in by variable), else `ubuntu-latest` (unchanged) |
| `unity-test-ios.yml`, `unity-release-ios.yml` | `runner-label` | `''` | explicit label wins; empty = policy (when opted in by variable), else `macos-unity-xcode` (unchanged) |

## 15. What you see in a run

**Stage 01 log**, one block per job:

```
Runner selection — iOS
----------------------
Decided by:    platform-policy (file:.github/unity-runner-policy.json)
Mode:          self-hosted-preferred
Runner type:   self-hosted
Build engine:  local
Required:
  os in: macos
  unity: 6000.0.26f1
  xcode: 16
Candidates:
  1. ios-build-01           runner        primary   [offline]
  2. ios-build-02           runner        primary   [idle]
  3. ios-build-03           runner        primary   [busy]  ineligible: xcode=15 does not include 16
Selected:      ios-build-02 [idle]
runs-on:       self-hosted,macOS,ios-build-02
Reason:        highest-priority eligible idle runner in the primary list; passed over: ios-build-01 offline
```

**Stage 01 summary**: a *Runner selection* table (job, selected target, type,
availability, engine, `runs-on`, decided-by, reason) with a collapsible candidate
table per job. The final report repeats it. Each build job's summary shows
*Selected* vs *Assigned* runner.

**Outputs** of `resolve-config`: `runner-selection` (the full document, the single
source of truth), plus `uses-docker`, `runs-on-unitytests` and
`runs-on-addressables`, which are serializations of the same document.

## 16. Migration

Nothing to do: with no policy, behaviour is unchanged. To adopt scheduling:

1. **Label each runner uniquely** (its name) and with what it can build
   (`xcode-16`, `unity-docker`, …).
2. **Write `.github/unity-runner-policy.json`**, starting from one of
   `examples/runner-policy/`. Start with one platform (often iOS); every platform
   without a section stays on its current routing.
3. **Optionally add `RUNNER_STATUS_TOKEN`** for online/busy awareness. Without it,
   scheduling still works on declared capabilities with availability `unknown`.
4. **Dispatch a run** and read the *Runner selection* table in stage 01.
5. **Escape hatch:** dispatch with `runner-labels` to pin every job to one label
   set. A caller of `unity-pipeline.yml` can also pass `runner-policy: legacy`
   to ignore the policy for a run.

Mapping legacy settings to a policy:

| Legacy | Policy equivalent |
|---|---|
| `RUNNER_LABELS=self-hosted,build-box` (one machine for everything) | `"default": {"priority": ["build-box"]}` with `"runners": {"build-box": {"labels": ["self-hosted", "build-box"], "os": "windows"}}` |
| `RUNNER_MACOS_LABEL=self-hosted,macOS` | `"platforms": {"iOS": {"priority": ["mac-pool"]}}` with a `mac-pool` pool |
| `RUNNER_WINDOWS_LABEL` + `BUILD_ENGINE=local` | `"platforms": {"Windows64": {"build-engine": "local", "priority": ["win-pool"]}}` |
| `RUNNER_DEFAULT_MODE=self-hosted-macos` | `"mode": "self-hosted-only"` + a `default` pointing at the Mac |
| `ios-runner-label: macos-unity-xcode` | keep it (explicit input still wins), or drop it and add `platforms.iOS` |

The legacy variables are **not deprecated by this change**; they remain the
routing for every job a policy does not cover.

## 17. Known limitations

- **Selection is not a reservation** (§10). How long a queued job waits is
  GitHub's decision.
- **Organization runners and groups** are visible only with an org-scoped token
  and `availability.scope: org|both`. With a repository token, an org runner
  shows as `not-found`, and group membership is `unknown` (§4).
- **Enterprise-level runners** are not queried.
- **Environment-scoped variables**: the scheduler runs in jobs without a
  deployment environment, so `RUNNER_POLICY` must be a repository or
  organization variable (a policy *file* has no such constraint).
- **Standalone workflows** honour a policy only when the `RUNNER_POLICY` or
  `RUNNER_POLICY_FILE` variable is set, and their resolver then needs a
  GitHub-hosted Linux runner (§14). In submodule mode the resolver fetches the
  toolkit submodule only when a policy applies; it needs the same submodule access
  the build job has.
- **Installed software is declared, not detected**: Unity and Xcode versions are
  trusted as declared unless also expressed as labels (§3).
- **Non-Unity jobs** (validation, reports, release promotion, the TestFlight upload
  in `pipeline-ios-release.yml`) are not scheduled — they run on GitHub-hosted
  `ubuntu-latest` / `macos-latest` as before.
- **Legacy GitHub-hosted iOS** (no policy, `RUNNER_TYPE=github-hosted`) is still
  routed to `macos-latest` and reported *blocked*, as before. A policy cannot send
  iOS to GitHub-hosted.
