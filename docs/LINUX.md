# Linux Builds

## Supported Targets

| Target | Unity Build Target | Description |
|---|---|---|
| Linux64 | `StandaloneLinux64` | Desktop Linux standalone player |
| LinuxServer | `LinuxServer` | Dedicated server (headless, no graphics) |

Both targets use the `linux` image variant.

## Image Variant

```
ghcr.io/<IMAGE_NAMESPACE>/unity-builder:6000.0.26f1-linux-v2.0.0
```

Base: `unityci/editor:6000.0.26f1-linux-il2cpp-3`

Modules: `linux-il2cpp`, `linux-server`

## Workflow Usage

Linux64 builds run in `unity-pipeline.yml`, called by the consumer's numbered entry
workflows (`templates/consumer-10-build-development.yml`, `consumer-11-build-release.yml`).
Put `Linux64` in the environment's `BUILD_PLATFORMS` repository variable, or pick it in
the dispatch form:

```bash
gh workflow run "Build / Development" --ref develop -f platform=Linux
```

`LinuxServer` is a separate platform value (`-f platform="Linux Server"` in the form).

Setup: [CONSUMER_SETUP.md](CONSUMER_SETUP.md). Variables: [REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md).

## Local Build

```bash
python3 scripts/docker/run_unity_container.py \
  --project-path . \
  --unity-version 6000.0.26f1 \
  --target-platform Linux64 \
  --environment development \
  --build-config-path BuildConfig
```

## Scripting Backends

| Backend | Support |
|---|---|
| IL2CPP | Recommended for production |
| Mono | Supported for development |

Set `scriptingBackend` in your BuildConfig JSON.

## Server Builds

For dedicated server builds:
- Set `target-platform: LinuxServer`
- Unity strips graphics code automatically
- Output is a headless executable
- Smaller build size than desktop Linux

## Output

Build artifacts are written to:
```
Builds/Linux64/<productName>    # or Builds/LinuxServer/<productName>
```

The output includes the executable and `_Data` directory.
