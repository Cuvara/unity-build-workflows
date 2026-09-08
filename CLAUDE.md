# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

`unity-build-workflows` is a **CI/CD platform toolkit**, not a Unity game. It ships reusable
GitHub Actions workflows, composite actions, Python/bash resolver scripts, Docker image
definitions, JSON schemas, and a UPM Editor package (`com.company.build-pipeline`) that Unity
project repos consume as a caller workflow (and, here, as a git submodule of
`IndieRPGMMOAdventure`).

There is no Unity Editor in this repo and none is needed to develop it. Nothing in `unity-package/`
compiles here — it is only distributed. The testable surface is **Python + bash + workflow YAML**.

`docs/` is unusually load-bearing: the contracts (branch flow, repository variables, platform
matrix, executor lanes) are specified there and asserted by tests. Read the relevant doc before
changing a resolver — several tests read the docs' values back.

## Commands

```bash
pip install -r tests/requirements.txt

# Full suite — CI runs it from inside tests/ (conftest.py resolves REPO_ROOT itself,
# so `pytest tests/` from the root works too). ~1070 tests, ~85s.
cd tests && python -m pytest -q

# One file / class / test
cd tests && python -m pytest test_build_flow.py -q
cd tests && python -m pytest test_build_flow.py::TestPushDevelop -q
cd tests && python -m pytest -k "android_export" -q

# Bash-only test (not collected by pytest — run directly)
bash tests/test_discord_size_check.sh

# Lint — CI gate is `bash -n`, not shellcheck; shellcheck is advisory
find scripts docker -name '*.sh' -type f -exec bash -n {} \;
shellcheck docker/unity/*.sh scripts/docker/*.sh

# Local Docker-lane build / tests (needs Docker + a published image for that version+variant)
python3 scripts/docker/run_unity_container.py \
  --project-path . --unity-version 6000.0.26f1 --target-platform Android \
  --environment development --build-config-path BuildConfig
# add --dry-run to print the docker command without running it
```

CI (`.github/workflows/ci.yml`) is exactly two gates — pytest and `bash -n` — plus an
auto-merge of `develop` → `main` on push.

## Architecture

### Five layers, top to bottom

```
consumer repo workflow  → uses: Cuvara/unity-build-workflows/.github/workflows/unity-pipeline.yml@<ref>
.github/workflows/      → orchestration (unity-pipeline.yml is the drop-in entry; 1300 lines)
.github/actions/        → 15 composite actions (run-unity-container, resolve-unity-image, build-ios, discord-*, …)
scripts/                → all real logic: resolvers (bash+python), docker wrapper, ios/android/webgl steps
docker/unity/           → Dockerfile variants + entrypoint.sh + license scripts (runs inside the container)
unity-package/          → UPM Editor package shipped to consumers (BuildCommand, validators, PlatformBuilders, hooks)
```

Nesting depth budget: `caller → unity-pipeline → reusable-build-platform` = 3 of GitHub's max 4.
Adding another layer of `workflow_call` will break callers.

### Logic lives in scripts, not YAML

`scripts/common/resolve_build_flow.sh` is the single source of branch-flow logic: pure bash, reads
env (`EVENT_NAME`, `REF_NAME`, `IN_*` dispatch inputs, `NEW_*`/`LEG_*` repo variables), writes
`KEY=value` to stdout and `$GITHUB_OUTPUT`. Everything downstream (which platforms build, tests,
addressables, environment, signing, runner labels, caches, define symbols) is one of its outputs.
Its contract is `docs/BRANCH_FLOW_CONTRACT.md`, its variables `docs/REPOSITORY_VARIABLES.md`, and
`tests/test_build_flow.py` (75KB) pins it. **Change the resolver, not the workflow YAML**, when
build behaviour needs to change.

Other resolvers with the same shape: `resolve_unity_version.sh`, `resolve_activation_strategy.sh`,
`resolve_platform_executor.py`, `version_resolver.py`, `validate_build_config.py`.

Every setting resolves by one fixed priority: **`workflow_dispatch` input → new grouped repo
variable (`BUILD_*`/`TEST_*`/`UNITY_*`/…) → legacy ungrouped variable → hardcoded default**.
Legacy names are deprecated but must keep working; the resolver logs a migration note.

### Runner and build engine are independent axes

`RUNNER_TYPE` (`github-hosted` | `self-hosted`) answers *where* the job runs; `BUILD_ENGINE`
(`docker` | `local`) answers *how* Unity builds. Three combinations are supported and the fourth is
rejected up front — `docs/RUNNER_AND_BUILD_ENGINE.md`. Don't collapse them into one input.

### Two executor lanes, chosen by platform

`docker-unity` (ubuntu-latest, Android/WebGL/Linux64/LinuxServer) and `macos-unity-xcode`
(macos-13, iOS only) — mutually exclusive, selected by `resolve_platform_executor.py`. Windows64
has no Docker path: it works only on the self-hosted-windows lane. iOS never builds automatically;
it is dispatch-only.

### Unity version SSOT

The consumer's `ProjectSettings/ProjectVersion.txt` is authoritative. `config/unity-build-defaults.json`
(currently `6000.0.26f1`, namespace `cuvara/unity-editor`) is only the fallback. A workflow that
hardcodes a Unity version is a bug.

## Constraints the test suite enforces

These are guard-rail tests, and they are the usual reason an unrelated-looking change fails CI:

- **`test_no_native_unity_invocation.py`** — no `Unity -batchmode`, `-executeMethod`,
  `game-ci/unity-builder`, or `game-ci/unity-test-runner` anywhere except a hardcoded
  `ALLOWED_PATHS` set (the container entrypoint, the iOS macOS lane, `unity-build-gameci.yml`,
  `reusable-build-platform.yml`, `reusable-unity-tests.yml`, image/license workflows). Adding a
  Unity invocation means editing that allowlist deliberately — with a comment saying why.
- **`test_static_identifier_scan.py`** — no game- or studio-specific identifiers in `scripts/`,
  `templates/`, `examples/`, `unity-package/`, `schemas/`, `docker/metadata/`, `tests/`. Owner,
  namespace, and project name are always inputs or placeholders. `docs/` is allowlisted.
- **`test_secret_redaction.py`** — secret values must never appear in a docker command line, a
  Dockerfile `ENV`, or an artifact. Secrets go through `env:` blocks and runtime injection.
- **`test_toolkit_path_validation.py`** — `project-path` / `toolkit-path` reject `../` and absolute
  paths; the internal mount is always `.ci/unity-build-workflows`.
- **`test_workflow_contract.py`** — every workflow YAML parses, required inputs keep their types
  and defaults, no `@main` in internal reusable-workflow calls, `if: always()` on log/report
  uploads. Note PyYAML parses bare `on:` as boolean `True`.

## Known inconsistency — build entry point

The two iOS routes do not share an entry point, and this is deliberate, documented, unfixed:

| Lane | `-executeMethod` target | Overridable |
|---|---|---|
| Docker / game-ci — the default lane (Android, WebGL, Linux, pipeline's iOS job) | **game-ci's own default builder** — `build-method` defaults to `''` (`reusable-build-platform.yml:146`, passed through at `:574`); no consumer method needed | yes: `build-method` input / `UNITY_BUILD_METHOD` variable |
| Self-hosted (Windows `.bat` at `:843`, bash at `:903`) | `PlayerBuilder.Build` — **provided by the consuming project**, hardcoded fallback when `build-method` is empty | yes, same two knobs |
| iOS native (`unity-build-ios.yml`, `unity-release-ios.yml`) | `Company.BuildPipeline.Editor.BuildCommand.Execute` — from `unity-package/` | no: `readonly` in `scripts/ios/run_unity_ios.sh` |

So `UNITY_BUILD_METHOD` has no effect on the native iOS route. See `docs/ARCHITECTURE.md`
§Build Entry Points before "fixing" either side.

## Conventions

- Conventional Commits, scopes: `docker`, `android`, `webgl`, `linux`, `schema`, `scripts`, `docs`,
  `release`, `actions`, `workflows`.
- `CHANGELOG.md` entry under `[Unreleased]` is required per change (matches the workspace rule);
  behaviour, flag, or contract changes update `docs/` in the same change.
- Shell: `set -Eeuo pipefail`, `[[ ]]` over `[ ]`, quote every expansion, traps for cleanup,
  preserve exit codes. Python: 3.8-compatible, `argparse` CLI, follow `scripts/docker/` patterns.
- Actions YAML: pin third-party actions to SHA with a version comment; `description:` on every
  input; secrets via `env:`, not inline `${{ secrets.X }}` in `run:`.
- Container security is non-negotiable: no `--privileged`, no Docker socket mount, `--cap-drop=ALL`,
  `--security-opt=no-new-privileges`, non-root user, no secrets in image layers.
- Breaking changes to the workflow input interface need a major bump plus a migration guide.
- English for all code, comments, docs, changelog, and commit messages.

## Unity licensing (the part that trips people up)

Docker lane uses the `personal-combined` strategy: `UNITY_LICENSE` (raw `.ulf` XML, **not**
base64), `UNITY_EMAIL`, and `UNITY_PASSWORD` must all three be present. `.ulf` alone fails with
`TimeStamp validation failed`; credentials alone give `0 entitlements`. This is why
`unity-build-gameci.yml` is allowed to delegate to `game-ci/unity-builder` — it performs the online
activation Unity 6 accepts. Details: `docs/UNITY_PERSONAL_DOCKER_LICENSE.md`.
