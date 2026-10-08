# Docker Build Guide

This guide covers the toolkit's own Docker wrapper, `scripts/docker/run_unity_container.py`,
used for local Docker builds and tests. In CI, the Docker lane of `reusable-build-platform.yml`
runs `game-ci/unity-builder` instead (and the Windows-runner docker lane runs
`scripts/build/docker_windows_container.sh` in a `unityci/editor` container); the composite
actions that wrapped this script for the workflows removed in 7.0.0 are gone
([MIGRATION_V7.md](MIGRATION_V7.md)).

## Container Execution Flow

```
Developer Machine
  │
  └── scripts/docker/run_unity_container.py
              │
              ├── Validate inputs
              ├── Resolve image reference
              ├── Validate image manifest
              ├── Construct docker run command
              │
              └── docker run --rm --init \
                    --user "$(id -u):$(id -g)" \
                    --workdir /workspace \
                    --mount type=bind,source=<project>,target=/workspace \
                    --mount type=bind,source=<output>,target=/workspace/Builds \
                    --mount type=bind,source=<reports>,target=/workspace/BuildReports \
                    --mount type=bind,source=<logs>,target=/workspace/Logs \
                    --mount type=volume,source=<library-cache>,target=/workspace/Library \
                    <pinned-image-reference> \
                    build \
                    --project-path /workspace \
                    --build-config /workspace/BuildConfig/base.json \
                    --environment staging \
                    --target-platform Android
```

Logs, reports and artifacts land in the bound host directories.

## Bind Mounts

| Host Path | Container Path | Purpose |
|---|---|---|
| `<project-path>` | `/workspace` | Unity project (read-write for Library if not using volume) |
| `<output>/Builds` | `/workspace/Builds` | Build artifacts |
| `<output>/BuildReports` | `/workspace/BuildReports` | Build reports and metadata |
| `<output>/Logs` | `/workspace/Logs` | Editor.log and other logs |
| `<output>/TestResults` | `/workspace/TestResults` | Test result XML files |

## Volume Mounts

| Volume Name | Container Path | Purpose |
|---|---|---|
| `unity-lib-<hash>` | `/workspace/Library` | Unity Library cache |
| `unity-gradle-<hash>` | `/home/unity/.gradle` | Gradle cache (Android only) |

Cache volume names include project identity, Unity version, target platform, and cache schema version to prevent cross-contamination.

## Cache Modes

| Mode | Behavior |
|---|---|
| `off` | No cache volume. Fresh import every build. |
| `safe` | Named volume per project+version+platform. Exact match only. |
| `aggressive` | Named volume with broader reuse. Risk of stale cache. |

Default: `safe`

Clean release mode (`--clean-build`) skips cache restoration entirely.

## Licensing in Containers

Unity licensing is ephemeral. License material is injected at runtime and cleaned after execution.

### Flow

1. `UNITY_LICENSE` environment variable injected into container
2. `activate-license.sh` writes to `/tmp/unity-license.ulf` (mode 600)
3. Unity activated with `-manualLicenseFile`
4. Build executes
5. `return-license.sh` returns license (if serial activation)
6. Temporary license files deleted (cleanup trap)

### Security

- License files never baked into images
- License content never printed to logs
- Temporary files use restrictive permissions (600)
- Cleanup runs on all exit paths (trap EXIT)

## File Ownership

Containers run with `--user "$(id -u):$(id -g)"` to match host user.

- Build artifacts are owned by the CI runner user
- No root-owned files in the workspace
- No `chmod 777` used anywhere
- Writable home directory at `/tmp/unity-home`

If the container's Unity process needs a home directory:
```
HOME=/tmp/unity-home
XDG_CACHE_HOME=/tmp/unity-home/.cache
```

## Local Usage

```bash
# Build Android
python3 scripts/docker/run_unity_container.py \
  --project-path /path/to/unity-project \
  --unity-version 6000.0.26f1 \
  --target-platform Android \
  --environment development \
  --build-config-path BuildConfig

# Run EditMode tests
python3 scripts/docker/run_unity_container.py \
  --project-path /path/to/unity-project \
  --unity-version 6000.0.26f1 \
  --target-platform Android \
  --test-level editmode

# Dry run (print docker command without executing)
python3 scripts/docker/run_unity_container.py \
  --project-path /path/to/unity-project \
  --unity-version 6000.0.26f1 \
  --target-platform Android \
  --dry-run
```

## Debugging

### Enter the container interactively

```bash
docker run -it --rm \
  --workdir /workspace \
  --mount type=bind,source="$(pwd)",target=/workspace \
  ghcr.io/<IMAGE_NAMESPACE>/unity-builder:6000.0.26f1-android-v2.0.0 \
  /bin/bash
```

### Check Unity installation

```bash
docker run --rm \
  ghcr.io/<IMAGE_NAMESPACE>/unity-builder:6000.0.26f1-android-v2.0.0 \
  version
```

### Inspect installed modules

```bash
docker run --rm \
  ghcr.io/<IMAGE_NAMESPACE>/unity-builder:6000.0.26f1-android-v2.0.0 \
  inspect
```

### Common Failures

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for Docker-specific error resolution.
