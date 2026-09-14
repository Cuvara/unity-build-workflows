# Build diagnostics

Finding out why a build failed, without downloading anything.

## The problem

A failed Unity build reports `Process completed with exit code 1`. The reason
is one line — usually `Assets/Scripts/Player.cs(42,17): error CS0103` — inside
roughly fifteen thousand lines of Unity output. Nobody reads that while a
release is blocked; they re-run the build, then ask someone.

Two attempts at fixing this failed, and the reasons are worth knowing because
they constrain any future attempt:

**Parsing `Editor.log` from the artifact.** On the game-ci lane Unity streams
its output to the job console and writes no `Editor.log` at all — the logs
artifact contains a shader-compiler log and nothing else. The summariser found
that, found no errors in it, and reported "no errors found", which is a
different statement from "I could not find the log" and is how a summary earns
the right to be ignored.

**Fetching the job log from inside the same run.** `GITHUB_TOKEN` cannot
download a job's log while the run is still in progress; the endpoint 404s
until the whole run completes. A build cannot summarise itself.

## What runs now

`templates/consumer-09-build-diagnostics.yml` triggers on `workflow_run` after
a build workflow completes, when the logs are readable. It reads the Unity
build jobs' logs and posts a **check run** on the build's commit.

```
Build / Development  ──completed──▶  Build Diagnostics
                                          │
                                          ├─ read stage 03 / 03b job logs
                                          ├─ extract errors and warnings
                                          └─ check run on the commit
```

A check run rather than console output, because that is what puts
`Player.cs:42  CS0103` on the line itself in the pull request. Console output
in a second workflow run is only marginally easier to find than console output
in the first one.

### Install it

```bash
curl -fsSL https://raw.githubusercontent.com/Cuvara/unity-build-workflows/main/templates/consumer-09-build-diagnostics.yml \
  -o .github/workflows/09-build-diagnostics.yml
```

Nothing else. It needs no secrets and no variables.

**If you rename a build workflow, update the `workflows:` list.** A
`workflow_run` trigger matches on the workflow's `name:`, and a trigger that
never fires produces no error — the diagnostics simply stop appearing. A test
in the toolkit pins the shipped names against this list for exactly that
reason.

## What it reports

- **Compiler diagnostics** — `error CS####` and `warning CS####`, with the file
  and line, so they can be attached to the diff.
- **Unity's own errors** — `[Error]`, unhandled exceptions.
- **Build-level failures** — `Build completed with a result of 'Failed'`,
  `BuildFailedException`, licence problems. These explain an exit code when the
  compiler was perfectly happy.

The matching is deliberately narrow. A Unity log contains the word "error" in
hundreds of harmless places — shader variants, package resolution, il2cpp
progress — and annotations that cry wolf are annotations people stop reading.
An explicit ignore list holds the noise that has already produced a false
positive, including obsolete-API warnings and unsupported shader passes.

Repeats are collapsed: Unity prints each compiler error once per assembly it
tried to build, and twelve copies of one typo is not twelve problems.

## Deliberate limits

**The check is `neutral`, never a failure.** The build already reported whether
it passed. A second red mark on the same commit for the same reason tells you
nothing new and trains people to ignore both.

**It never fails.** A missing token, an unreadable log, a rejected check run —
each produces a warning and exit 0. A diagnostics job that goes red is a
diagnostics job people disable.

**At most 50 annotations.** GitHub's limit per request, and well past the point
where a commit's diff becomes unreadable anyway. The full list is in the check
run's summary text.

**It runs after the build, so it is not instant.** Typically under a minute
after the build finishes.

## The other half

The build lane also summarises any Unity log it *can* find on disk —
`scripts/common/summarise_unity_log.py`, wired into stage 03. That covers the
native and self-hosted lanes, the Addressables pre-pass and licence activation,
where Unity does write a file. On the game-ci lane it correctly reports that no
Editor log exists rather than implying the build was clean.
