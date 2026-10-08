# WebGL Builds

WebGL builds run inside Docker containers using the `webgl` image variant.

---

## Image Variant

```
ghcr.io/<IMAGE_NAMESPACE>/unity-builder:6000.0.26f1-webgl-v2.0.0
```

Base: `unityci/editor:6000.0.26f1-webgl-3`

Includes: Unity Editor, Emscripten toolchain

---

## Workflow Usage

WebGL builds run in `unity-pipeline.yml`, called by the consumer's numbered entry
workflows (`templates/consumer-10-build-development.yml`, `consumer-11-build-release.yml`).
Put `WebGL` in the environment's `BUILD_PLATFORMS` repository variable, or pick it in
the dispatch form:

```bash
gh workflow run "Build / Development" --ref develop -f platform=WebGL
```

Setup: [CONSUMER_SETUP.md](CONSUMER_SETUP.md). Variables: [REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md).

---

## BuildConfig Fields

See [BUILD_CONFIG.md](BUILD_CONFIG.md#webgl-object).

| Field | Default | Notes |
|---|---|---|
| `compressionFormat` | `Brotli` | Smallest files; requires server `Content-Encoding` support |
| `decompressionFallback` | `true` | Enable for static hosts without header support |
| `dataCaching` | `true` | IndexedDB caching for repeat visits |
| `memorySize` | `256` | Initial WASM heap in MB |
| `template` | `Default` | `PWA` adds installability |

---

## Compression Format Trade-offs

| Format | File Size | Server Requirement |
|---|---|---|
| `Brotli` | Smallest | Must serve `Content-Encoding: br` |
| `Gzip` | Medium | Must serve `Content-Encoding: gzip` |
| `Disabled` | Largest | No special headers |

---

## Build Output

```
Builds/WebGL/
  index.html
  Build/
    MyGame.loader.js
    MyGame.data.br
    MyGame.framework.js.br
    MyGame.wasm.br
  TemplateData/
```

---

## Deployment

### Cloudflare Pages

The workflow supports automatic Cloudflare Pages deployment. Add secrets:
```
CLOUDFLARE_API_TOKEN
CLOUDFLARE_ACCOUNT_ID
```

### GitHub Pages / Netlify / S3

Post-container deployment steps run on the host, not inside the Unity container.

---

## Local WebGL Build

```bash
python3 scripts/docker/run_unity_container.py \
  --project-path . \
  --unity-version 6000.0.26f1 \
  --target-platform WebGL \
  --environment development \
  --build-config-path BuildConfig
```

---

## Memory Configuration

| Game Type | Recommended `memorySize` |
|---|---|
| Casual / Puzzle | 128–256 MB |
| Mid-core / RPG | 256–512 MB |
| Heavy 3D | 512–1024 MB |

Mobile browsers typically cap WASM at 512–1024 MB.

---

## Troubleshooting

**"RangeError: Out of memory"** — Increase `memorySize`.

**Build loads locally but errors in production** — Usually CORS. Check browser console.

**"Unable to parse Build/MyGame.wasm.br"** — Server not setting `Content-Encoding: br`. Enable `decompressionFallback: true`.
