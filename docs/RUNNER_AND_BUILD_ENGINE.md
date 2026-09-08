# Runner & Build Engine — Architecture

This document explains the toolkit's separation of **Runner** (WHERE a job
runs) from **Build Engine** (HOW Unity builds), the three supported execution
strategies, and Unity licensing per mode. For the variable reference and
resolution priority, see the [RUNNER section of
REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md#runner). For how these
settings flow through the resolver into every downstream job, see
[BRANCH_FLOW_CONTRACT.md](BRANCH_FLOW_CONTRACT.md).

## Runner vs Build Engine

These are two independent axes. Neither implies the other.

| Axis | Question it answers | Variable(s) | Values |
|---|---|---|---|
| **Runner** | *Where* does the GitHub Actions job execute? | `RUNNER_TYPE`, `RUNNER_LABELS` | `github-hosted` \| `self-hosted` |
| **Build Engine** | *How* does Unity produce the build? | `BUILD_ENGINE` | `docker` \| `local` |

Picking a runner doesn't dictate a build engine, and vice versa — you choose
both, and the resolver combines them into one of three supported **execution
strategies** (a fourth combination is invalid and rejected up front).

## The three execution strategies

### 1. `github-hosted` + `docker` (`github-docker`) — default

```
┌─────────────────────┐     ┌──────────────────────┐
│ GitHub-hosted runner │ ──▶ │  GameCI Docker image  │
│   (ubuntu-latest)    │     │  (Unity preinstalled) │
└─────────────────────┘     └──────────────────────┘
```

The job runs on an ephemeral GitHub-hosted Ubuntu runner. The build step pulls
a GameCI Unity Docker image matching the project's editor version and runs
the build inside the container. No infrastructure to provision — this is the
toolkit's zero-setup default and the only strategy CI verifies fully
end-to-end.

### 2. `self-hosted` + `local` (`selfhosted-local`)

```
┌───────────────────────┐     ┌───────────────────────────┐
│  Self-hosted runner    │ ──▶ │  Local Unity.exe / .app    │
│ (your Windows/macOS PC)│     │ (Unity Hub, pre-activated) │
└───────────────────────┘     └───────────────────────────┘
```

The job runs on a runner you registered on your own machine. The build step
invokes the Unity Editor installed via Unity Hub directly on that machine —
no Docker involved. Unity is activated once, interactively, via Unity Hub;
CI never performs activation.

### 3. `self-hosted` + `docker` (`selfhosted-docker`)

```
┌───────────────────────┐     ┌────────────────┐     ┌──────────────────────┐
│  Self-hosted runner    │ ──▶ │ Docker Desktop  │ ──▶ │  GameCI Docker image  │
│ (your own hardware)    │     │  on that host   │     │  (Unity preinstalled) │
└───────────────────────┘     └────────────────┘     └──────────────────────┘
```

The job runs on your own runner, but the build step still uses the same
GameCI Docker image as strategy 1. Useful when you want dedicated / faster
hardware, or private registry access, while keeping the Docker-based build
and its pluggable license activation. Requires Docker Desktop (or Docker
Engine) installed and running on the self-hosted host.

### Invalid: `github-hosted` + `local`

```
┌─────────────────────┐     ┌──────┐
│ GitHub-hosted runner │ ──▶ │  ✗   │  No local Unity install possible
└─────────────────────┘     └──────┘
```

GitHub-hosted runners are ephemeral, shared machines with no persistent Unity
Editor installation. This combination fails **fast, at Resolve Config**, with
a clear error telling you to use `BUILD_ENGINE=docker` or switch
`RUNNER_TYPE=self-hosted`. It never reaches a build job.

## Choosing a strategy

| You want... | Use |
|---|---|
| Zero setup, works out of the box | `github-hosted` + `docker` (default) |
| Unity Personal/Free, avoid Docker license friction | `self-hosted` + `local` (recommended, see below) |
| Dedicated/faster hardware, keep Docker's pluggable activation | `self-hosted` + `docker` |
| iOS builds | `self-hosted` (macOS) + `local` — the only supported iOS path |

## Unity licensing per mode

Licensing is a property of the **build engine**, not the runner:

- **`BUILD_ENGINE=local`** — activation strategy is `none`. Unity is activated
  once, interactively, in Unity Hub on the self-hosted machine. CI performs no
  activation step at all. `UNITY_LICENSE` / `UNITY_EMAIL` / `UNITY_PASSWORD`
  are optional and unused — if present in the repo, this lane simply ignores
  them.
- **`BUILD_ENGINE=docker`** — activation strategy is pluggable and resolved
  per run (`activation-strategy` dispatch input, or `auto`): `UNITY_LICENSE`
  (a `.ulf` file), or `UNITY_EMAIL` + `UNITY_PASSWORD` (Personal/Free
  combined activation), or a serial (Pro/Plus). The resolver does not
  hardcode GameCI specifics — the actual strategy is resolved downstream by
  `resolve_activation_strategy.sh`. This applies identically whether the
  Docker build runs on a GitHub-hosted or a self-hosted runner.

### Unity Personal recommendation

For **Unity Personal (free)** licenses, prefer:

```
RUNNER_TYPE=self-hosted
BUILD_ENGINE=local
```

This reuses your existing Unity Hub activation on the machine you already
have Unity installed on, needs no license secrets in the repo, and avoids
Unity Personal's Docker-activation friction entirely (see
[UNITY_PERSONAL_DOCKER_LICENSE.md](UNITY_PERSONAL_DOCKER_LICENSE.md) for what
that friction looks like). **Docker remains a fully-supported advanced
option** for Personal licenses too — it's just more setup.

## Registering a self-hosted Windows runner

> Registering at the **organization** level instead (one runner serving several
> repos, granted through a runner group) is covered in
> [SELF_HOSTED_ORG_RUNNER.md](SELF_HOSTED_ORG_RUNNER.md), together with the
> public-repository risks that setup carries.

1. Provision a Windows machine, install Unity Hub + the required Editor
   version/modules, Git, and Git LFS. Activate Unity once via Unity Hub. Full
   step-by-step in [SELF_HOSTED_WINDOWS_RUNNER.md](SELF_HOSTED_WINDOWS_RUNNER.md).
2. Register the runner with labels matching what you intend to set in
   `RUNNER_LABELS`:

   ```cmd
   .\config.cmd --url https://github.com/<org-or-user>/<repo> ^
                --token <REGISTRATION_TOKEN> ^
                --name unity-windows-runner-01 ^
                --labels self-hosted,windows ^
                --runasservice
   ```

3. Set the repo variables:

   ```
   RUNNER_TYPE=self-hosted
   BUILD_ENGINE=local
   RUNNER_LABELS=self-hosted,windows
   ```

   `RUNNER_LABELS` must match the runner's registered labels exactly (after
   comma-split/trim/dedup) or the job queues indefinitely with "no runner
   matching labels found."

## Known limitations

- **`self-hosted` + `docker`** needs Docker Desktop (or Docker Engine) running
  on the self-hosted host — the toolkit does not install or manage Docker for
  you.
- **iOS** requires a self-hosted macOS runner with `BUILD_ENGINE=local`
  (Unity.app via Unity Hub) — there is no Docker path for iOS and no
  automatic branch-flow trigger; use `workflow_dispatch` with `platform=iOS`.
- **GitHub-hosted runners are Docker-only** — `github-hosted` + `local` is
  rejected at Resolve Config; there is no way to install a persistent Unity
  Editor on an ephemeral GitHub-hosted machine.
- **CI verification coverage:** this toolkit's own CI verifies `github-hosted`
  + `docker` and `self-hosted` + `local` end-to-end. `self-hosted` + `docker`
  is verified at the Resolve Config / resolver-output level only (no Docker
  host available in this environment); provision and test it against your
  own self-hosted Docker host before relying on it in production.

## See also

- [REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md#runner) — variable
  reference, defaults, and the legacy `RUNNER_DEFAULT_MODE` migration table.
- [BRANCH_FLOW_CONTRACT.md](BRANCH_FLOW_CONTRACT.md) — full resolver
  input/output contract, including `runner-type`, `build-engine`,
  `execution-strategy`, `runner-labels`, and `activation-strategy` outputs.
- [SELF_HOSTED_WINDOWS_RUNNER.md](SELF_HOSTED_WINDOWS_RUNNER.md) — detailed
  Windows runner provisioning and Unity Hub activation steps.
- [SELF_HOSTED_MACOS_RUNNER.md](SELF_HOSTED_MACOS_RUNNER.md) — macOS (iOS) runner
  provisioning: Xcode, Unity iOS module, `macos-unity-xcode` label, activation.
- [UNITY_PERSONAL_DOCKER_LICENSE.md](UNITY_PERSONAL_DOCKER_LICENSE.md) —
  Docker-engine activation strategies for Unity Personal/Free.
