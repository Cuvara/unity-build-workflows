# Documentation index

Thirty-seven documents accumulated over several architectures. This page says
which describe the pipeline as it is today, which are reference you reach for
when something specific breaks, and which are kept for the reasoning they
record rather than as instructions.

If you are setting a project up, you need two of them:
**[CONSUMER_SETUP.md](CONSUMER_SETUP.md)** then
**[PIPELINE_ARCHITECTURE.md](PIPELINE_ARCHITECTURE.md)**.

## Start here

| Document | What it answers |
|---|---|
| [CONSUMER_SETUP.md](CONSUMER_SETUP.md) | How do I put this pipeline on my project? |
| [PIPELINE_ARCHITECTURE.md](PIPELINE_ARCHITECTURE.md) | How does it work, and why is it shaped this way? |
| [NEW_PROJECT_END_TO_END.md](NEW_PROJECT_END_TO_END.md) | A worked setup, start to first release |
| [../.github/pipeline-policy/invariants.md](../.github/pipeline-policy/invariants.md) | The seventeen rules CI enforces, and why each exists |
| [../.github/pipeline-policy/validation-status.md](../.github/pipeline-policy/validation-status.md) | What has actually been proven, and what is only tested |

## The contracts

Load-bearing: tests read values back out of these, so they and the code cannot
drift apart quietly.

| Document | Contract |
|---|---|
| [BRANCH_FLOW_CONTRACT.md](BRANCH_FLOW_CONTRACT.md) | Branch and event → what gets built; every resolver input and output |
| [REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md) | Every repository variable, its default, and the new → legacy migration |
| [RUNNER_AND_BUILD_ENGINE.md](RUNNER_AND_BUILD_ENGINE.md) | *Where* a job runs versus *how* Unity builds — two axes, three valid combinations |
| [PLATFORM_MATRIX.md](PLATFORM_MATRIX.md) | Which platform builds on which executor |
| [BUILD_CONFIG.md](BUILD_CONFIG.md) | `BuildConfig/*.json` schema for the explicit-build path |

## Shipping

| Document | Covers |
|---|---|
| [STEAM_DISTRIBUTION.md](STEAM_DISTRIBUTION.md) | Windows and Linux to Steam; configuration, and why staging cannot mutate the artifact |
| [IOS_RELEASE.md](IOS_RELEASE.md) · [IOS_SIGNING.md](IOS_SIGNING.md) · [IOS_VERIFICATION.md](IOS_VERIFICATION.md) | The iOS lane, signing before the immutable boundary, and verification |
| [GITHUB_ENVIRONMENTS.md](GITHUB_ENVIRONMENTS.md) | Environment protection, approvals, deployment hygiene |
| [DISCORD_NOTIFICATIONS.md](DISCORD_NOTIFICATIONS.md) | Build and release notifications |

## Per-platform notes

[ANDROID.md](ANDROID.md) · [IOS.md](IOS.md) · [WEBGL.md](WEBGL.md) ·
[LINUX.md](LINUX.md) · [PLATFORM_LIMITATIONS.md](PLATFORM_LIMITATIONS.md)

## Running and fixing it

| Document | Covers |
|---|---|
| [GITHUB_ACTIONS_BUILD_RUNBOOK.md](GITHUB_ACTIONS_BUILD_RUNBOOK.md) | Triggering builds, reading logs, downloading artifacts, common errors |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Symptom → cause |
| [UNITY_PERSONAL_DOCKER_LICENSE.md](UNITY_PERSONAL_DOCKER_LICENSE.md) | The licensing setup that trips everyone up |
| [UNITY_VERSION_UPGRADE.md](UNITY_VERSION_UPGRADE.md) | Moving the project to a new Unity version |
| [SECURITY.md](SECURITY.md) | Secret handling, container hardening, what never reaches a log |

## Infrastructure

| Document | Covers |
|---|---|
| [DOCKER_BUILD.md](DOCKER_BUILD.md) · [IMAGE_LIFECYCLE.md](IMAGE_LIFECYCLE.md) | The Docker lane and its images |
| [SELF_HOSTED_RUNNER.md](SELF_HOSTED_RUNNER.md) · [SELF_HOSTED_ORG_RUNNER.md](SELF_HOSTED_ORG_RUNNER.md) · [SELF_HOSTED_WINDOWS_RUNNER.md](SELF_HOSTED_WINDOWS_RUNNER.md) · [SELF_HOSTED_MACOS_RUNNER.md](SELF_HOSTED_MACOS_RUNNER.md) | Running builds on your own machines |
| [SUBMODULE_INTEGRATION.md](SUBMODULE_INTEGRATION.md) | Consuming the toolkit as a git submodule |
| [ADD_NEW_PROJECT.md](ADD_NEW_PROJECT.md) | Onboarding via the explicit-build path (`BuildConfig/*.json`) |

## Decisions

[adr/001-docker-mandatory-architecture.md](adr/001-docker-mandatory-architecture.md) ·
[adr/002-ios-native-exception.md](adr/002-ios-native-exception.md) ·
[adr/003-generic-consumer-integration.md](adr/003-generic-consumer-integration.md)

## Superseded

Kept for the reasoning they record. **Do not follow them as instructions** —
they describe architectures the pipeline has moved past, and a reader who
mistakes one for current guidance ends up with a setup that has no release
layer at all.

| Document | Superseded by |
|---|---|
| [EXPLICIT_PLATFORM_FLOW.md](EXPLICIT_PLATFORM_FLOW.md) | [PIPELINE_ARCHITECTURE.md](PIPELINE_ARCHITECTURE.md) — the matrix returned deliberately |
| [EXPLICIT_PLATFORM_FLOW_SPEC.md](EXPLICIT_PLATFORM_FLOW_SPEC.md) | same |
| [DISCORD_BUILD_DELIVERY_PLAN.md](DISCORD_BUILD_DELIVERY_PLAN.md) | [DISCORD_NOTIFICATIONS.md](DISCORD_NOTIFICATIONS.md) — a plan, now implemented |
| [RELEASE_FLOW.md](RELEASE_FLOW.md) | [PIPELINE_ARCHITECTURE.md](PIPELINE_ARCHITECTURE.md) §1a — predates promote-only release |
| [ARCHITECTURE.md](ARCHITECTURE.md) | [PIPELINE_ARCHITECTURE.md](PIPELINE_ARCHITECTURE.md) — still accurate on the toolkit's internals, silent on the release layer |
