# Image Lifecycle

## Base Image Selection

**Decision:** Thin organization-owned image extending pinned GameCI base images.

See [ADR-001](adr/001-docker-mandatory-architecture.md) for the rationale.

### GameCI Base Images

We use [GameCI](https://game.ci/) `unityci/editor` images as the base:

```
unityci/editor:6000.0.26f1-android-3
unityci/editor:6000.0.26f1-webgl-3
unityci/editor:6000.0.26f1-linux-il2cpp-3
```

GameCI handles Unity Editor installation, module setup, and SDK toolchain configuration. Our organization layer adds:

- Standardized entrypoint (`entrypoint.sh`)
- License activation/return scripts
- Health check script
- System tools (python3, jq, curl)
- Non-root user configuration
- OCI labels and metadata

## Image Variants

| Variant | Base Image | Supported Targets | Modules |
|---|---|---|---|
| `android` | `unityci/editor:*-android-*` | Android | android, android-sdk-ndk-tools, android-open-jdk |
| `webgl` | `unityci/editor:*-webgl-*` | WebGL | webgl |
| `linux` | `unityci/editor:*-linux-il2cpp-*` | Linux64, LinuxServer | linux-il2cpp, linux-server |

Do not create a single image with all modules. Each variant contains only the modules required for its targets.

## Image Tags

### Human-Readable Tags

```
ghcr.io/<IMAGE_NAMESPACE>/unity-builder:6000.0.26f1-android-v2.0.0
ghcr.io/<IMAGE_NAMESPACE>/unity-builder:6000.0.26f1-webgl-v2.0.0
ghcr.io/<IMAGE_NAMESPACE>/unity-builder:6000.0.26f1-linux-v2.0.0
```

Format: `<unity-version>-<variant>-v<tooling-version>`

### Digest References (Production)

```
ghcr.io/<IMAGE_NAMESPACE>/unity-builder@sha256:abc123...
```

Production and release workflows resolve and use digest-pinned references.

### Forbidden Tags

Never use:
- `latest`
- `ubuntu`
- `unity6`
- `android-latest`
- Any mutable tag in release mode

## Bootstrap

**Before any consumer build can run, at least one compatible image must be published to the registry.** The workflow cannot pull an image that does not exist.

### Problem

A first-time setup (new toolkit installation or new Unity version) has no image in `ghcr.io/<IMAGE_NAMESPACE>/unity-builder`. Consumer builds will fail immediately with:

```
Error: manifest unknown: manifest unknown
```

or the image resolver will emit:

```
ERROR: No published image found for unity-version=6000.0.26f1 variant=android.
Run the build-unity-image.yml workflow manually to publish an image before triggering consumer builds.
```

### Dev Bootstrap Path (pre-release / first install)

1. In the toolkit repository, go to **Actions → Build Unity Image → Run workflow**.
2. Select the Unity version and variant (e.g. `6000.0.26f1`, `android`).
3. The workflow builds → runs entrypoint tests → scans for vulnerabilities → generates SBOM → pushes to `ghcr.io/<IMAGE_NAMESPACE>/unity-builder:<unity-version>-<variant>-<tooling-version>`.
4. The image digest is recorded in the image manifest artifact.
5. Consumer builds can now pull the image by tag (dev) or digest (production).

This path uses a human-readable tag (`<unity-version>-<variant>-<tooling-version>`). It is suitable for development but **not** for production — production requires a digest-pinned reference.

### Production Digest-Pinned Path

```
build-unity-image.yml dispatch
  │
  ├── 1. Build image (BuildKit)
  ├── 2. Entrypoint smoke test (fake Unity)
  ├── 3. Vulnerability scan (Trivy/Grype — blocks on CRITICAL)
  ├── 4. SBOM generation (SPDX format)
  ├── 5. Push to registry — immutable tag published
  ├── 6. Record image digest (sha256:...)
  └── 7. Publish image-manifest.json artifact
         └── digest recorded for consumer digest-pinning
```

Consumer production workflows then resolve the image by digest:

```
ghcr.io/<IMAGE_NAMESPACE>/unity-builder@sha256:<digest>
```

`scripts/docker/resolve_image_reference.py` reads the image manifest and enforces digest-only references with `--release-mode`.

### What Happens If No Image Exists

If a build runs before any image is published, `scripts/docker/resolve_image_reference.py` fails with an actionable error:

```
ERROR: No compatible image found in the registry.
  Unity version : 6000.0.26f1
  Variant       : android
  Registry      : ghcr.io/<IMAGE_NAMESPACE>/unity-builder

To fix:
  1. In the toolkit repo, run Actions → Build Unity Image.
  2. Select unity-version=6000.0.26f1, variant=android.
  3. Wait for the image to publish (~15 min).
  4. Re-trigger your consumer build.
```

---

## Image Build Workflow

Triggered by:
- Changes to `docker/**`
- Changes to image manifest or entrypoint
- Manual dispatch
- Unity version updates

**Not** triggered by game project changes.

### Build Steps

1. Validate image configuration
2. Build the requested variant (BuildKit)
3. Run entrypoint tests (fake Unity)
4. Verify Unity executable availability
5. Verify required modules
6. Verify SDK/NDK/JDK versions (Android)
7. Run minimal Unity smoke command (when license available)
8. Generate image metadata
9. Generate SBOM (`scripts/docker/generate_sbom.sh`)
10. Scan for vulnerabilities (`scripts/docker/scan_image.sh`)
11. Check for embedded secrets (history inspection)
12. Push to registry
13. Publish immutable tags
14. Record image digest
15. Publish image manifest artifact

Image publication is blocked if critical validation or security scanning fails.

## Scanning

### Vulnerability Scanning

Uses Trivy or Grype to scan for known CVEs:

```bash
./scripts/docker/scan_image.sh \
  ghcr.io/<IMAGE_NAMESPACE>/unity-builder:6000.0.26f1-android-v2.0.0 \
  --severity HIGH,CRITICAL \
  --exit-code 1
```

Weekly scheduled scans via `scan-unity-image.yml` catch newly disclosed vulnerabilities.

### SBOM Generation

```bash
./scripts/docker/generate_sbom.sh \
  ghcr.io/<IMAGE_NAMESPACE>/unity-builder:6000.0.26f1-android-v2.0.0
```

Generates SPDX-format Software Bill of Materials.

## Image Manifest

Each published image includes a JSON manifest (`image-manifest.json`) validated against `schemas/unity-image-manifest.schema.json`.

The manifest records:
- Unity Editor version and changeset
- Base image digest
- OS version
- Installed modules
- Supported build targets
- Supported scripting backends
- Android SDK/NDK/JDK versions (android variant)
- Tooling version
- Build timestamp
- Source commit
- Image digest
- Contract version

Workflows validate the manifest before running a build. Builds fail if:
- Target is unsupported by the image
- Unity version does not match
- Required modules are missing
- Image contract version is incompatible
- Image reference is mutable in release mode

## Deprecation

When deprecating an image:

1. Remove it from the active image manifest
2. Keep the image in the registry for the retention period
3. Add a deprecation notice to the manifest
4. Update consuming workflows to use the replacement
5. Delete after the retention period

## Secret Inspection

Before publication, images are inspected for accidentally embedded secrets:

```bash
docker history --no-trunc <image> | grep -iE 'license|password|secret|key|token'
```

Images containing secret material in any layer must not be published.
