# Submodule Integration

This guide explains how to consume `unity-build-workflows` as a **Git submodule** inside your Unity project repository, rather than referencing it remotely via the `uses:` URL alone.

## When to use submodule integration

| Scenario | Recommended approach |
|---|---|
| Team hosts a private fork of the toolkit | Submodule — gives you full control and local script access |
| Strictly reproducible builds pinned to an exact SHA | Submodule — the submodule SHA is tracked in your repo's git history |
| You want to run toolkit scripts locally without cloning a second repo | Submodule — scripts are available at `tools/unity-build-workflows/scripts/` |
| You want the simplest setup and are happy with remote `uses:` | Remote Git reference (see [CONSUMER_SETUP.md](CONSUMER_SETUP.md)) |

Submodule integration and remote `uses:` integration are **not mutually exclusive** — many teams use a submodule for the UPM package (local file path) while still calling the hosted reusable workflows via `uses: OWNER/unity-build-workflows/...@REF`.

---

## Step 1: Add the submodule

```bash
# From your Unity project repository root
git submodule add https://github.com/<WORKFLOW_OWNER>/unity-build-workflows.git \
  tools/unity-build-workflows

git submodule update --init --recursive

# Recommended: pin to a specific tag or commit SHA
git -C tools/unity-build-workflows checkout v1.0.0   # or a specific SHA
git add .gitmodules tools/unity-build-workflows
git commit -m "chore: add unity-build-workflows submodule at v1.0.0"
```

> **Recommendation:** Always pin the submodule to a release tag or commit SHA. Tracking `main` means your CI depends on an external repo's tip-of-branch, which can introduce unexpected breakage.

---

## Step 2: Add the UPM package — local file dependency

With the submodule at `tools/unity-build-workflows/`, the `com.company.build-pipeline` UPM package is available locally. Reference it with a `file:` path in your `Packages/manifest.json`:

```json
{
  "dependencies": {
    "com.company.build-pipeline": "file:../tools/unity-build-workflows/unity-package/Packages/com.company.build-pipeline",
    "com.unity.nuget.newtonsoft-json": "3.2.1"
  }
}
```

> **Path is relative to `Packages/manifest.json`.** The `Packages/` directory is one level below your project root, so `../tools/...` resolves to `<project-root>/tools/unity-build-workflows/unity-package/Packages/com.company.build-pipeline`.

Unity resolves `file:` dependencies at editor startup. No network access required; the package is served directly from the submodule on disk.

### Alternative: Git URL dependency (no submodule required)

If you do **not** use a submodule, reference the package directly via its Git URL:

```json
{
  "dependencies": {
    "com.company.build-pipeline": "https://github.com/<WORKFLOW_OWNER>/unity-build-workflows.git?path=/unity-package/Packages/com.company.build-pipeline#<VERSION>",
    "com.unity.nuget.newtonsoft-json": "3.2.1"
  }
}
```

Replace `<VERSION>` with a tag (e.g. `v1.0.0`) or a full commit SHA. Using `main` is not recommended for production.

---

## Step 3: CI workflow — still uses `uses:` with remote reference

GitHub Actions **reusable workflow `uses:` calls always require a remote reference** when the reusable workflow lives in a different repository. Even with a submodule, your caller workflow must still use:

```yaml
jobs:
  build:
    uses: <WORKFLOW_OWNER>/unity-build-workflows/.github/workflows/unity-pipeline.yml@<REF>
    with:
      # Pin toolkit-ref to the same tag/SHA as your submodule for reproducibility:
      # it is the ref the pipeline checks out for its scripts and actions.
      toolkit-repo: <WORKFLOW_OWNER>/unity-build-workflows
      toolkit-ref: v7.0.0   # ← match your submodule pin
    secrets: inherit
```

> **Why not a local path?** GitHub Actions evaluates `uses:` with a relative path (e.g. `uses: ./tools/unity-build-workflows/.github/workflows/unity-pipeline.yml`) only when the target workflow file exists in the **caller's own repository**. Because unity-build-workflows is a separate repo — even when submoduled — GitHub treats submodule contents as external and the local `uses:` path is not supported for cross-repo reusable workflows.
>
> The practical effect: the reusable workflows are fetched from GitHub at the `uses:` ref (and the toolkit at `toolkit-ref`), while your Unity project is checked out from your own repo. Both are available inside the same CI run.

---

## Step 4: Keep submodule in sync on CI

GitHub Actions **does not** automatically initialise submodules. Add `submodules: true` to your checkout step:

```yaml
- name: Checkout
  uses: actions/checkout@v4
  with:
    submodules: true     # initialises and updates all submodules
    fetch-depth: 0
```

This ensures `tools/unity-build-workflows/` is present for local script invocations and for the Unity package file-path dependency resolution if CI invokes Unity locally (e.g. for iOS on a self-hosted macOS runner without Docker).

---

## Step 5: Running toolkit scripts locally

With the submodule present you can run any toolkit script directly:

```bash
# Validate your BuildConfig
python3 tools/unity-build-workflows/scripts/common/validate_build_config.py \
  --config-path BuildConfig \
  --platform    Android \
  --environment development

# Generate build metadata locally
python3 tools/unity-build-workflows/scripts/common/generate_build_metadata.py \
  --project-path . \
  --platform     Android \
  --version      1.0.0 \
  --environment  development \
  --build-number 0 \
  --commit       "$(git rev-parse HEAD)"
```

---

## Updating the submodule

```bash
# Pull latest from the configured remote
git -C tools/unity-build-workflows fetch --tags

# Update to a new release tag
git -C tools/unity-build-workflows checkout v1.1.0

# Commit the pointer bump
git add tools/unity-build-workflows
git commit -m "chore: bump unity-build-workflows submodule to v1.1.0"
```

Also update `workflow-ref: v1.1.0` in your caller workflows and the `#v1.1.0` fragment in your `manifest.json` if you are using a Git URL dependency.

---

## Cloning a repo that contains the submodule

```bash
# Clone and initialise all submodules in one step
git clone --recurse-submodules https://github.com/YOUR_ORG/your-unity-project.git

# Or initialise after a plain clone
git clone https://github.com/YOUR_ORG/your-unity-project.git
cd your-unity-project
git submodule update --init --recursive
```

---

## File structure with submodule

```
your-unity-project/
├── .github/
│   └── workflows/
│       └── build.yml                      # Your caller workflow
├── Assets/                                # Unity project assets
├── BuildConfig/
│   ├── base.json
│   ├── development.json
│   └── production.json
├── Packages/
│   └── manifest.json                      # file: dependency → tools/unity-build-workflows/...
└── tools/
    └── unity-build-workflows/             # ← git submodule
        ├── .github/
        │   ├── actions/                   # Composite actions
        │   └── workflows/                 # Reusable workflows
        ├── schemas/
        │   └── unity-build-config.schema.json
        ├── scripts/                       # Python + shell scripts
        ├── templates/                     # BuildConfig example templates
        └── unity-package/
            └── Packages/
                └── com.company.build-pipeline/   # UPM package
```

---

## Comparison: submodule vs. remote Git reference

| | Submodule | Remote Git reference |
|---|---|---|
| UPM package install | `file:` path — instant, no network | Git URL — Unity fetches from GitHub |
| Reusable workflow `uses:` | Still remote (GitHub limitation) | Remote |
| Reproducibility | Submodule SHA tracked in git | Pinned via `@tag` or `@sha` in `uses:` |
| Local script access | Yes — `tools/unity-build-workflows/scripts/` | No — must clone separately |
| CI checkout | Needs `submodules: true` in checkout step | No extra setup |
| Upgrading | `git -C tools/unity-build-workflows checkout <tag>` | Change `@ref` in `uses:` line |
| Forking the toolkit | Natural — fork URL becomes submodule remote | Change `WORKFLOW_OWNER` everywhere |

---

## See also

- [CONSUMER_SETUP.md](CONSUMER_SETUP.md) — Remote reference integration (no submodule)
- [BUILD_CONFIG.md](BUILD_CONFIG.md) — Full BuildConfig field reference
- [docs/adr/003-generic-consumer-integration.md](adr/003-generic-consumer-integration.md) — Architecture decision record for consumer integration patterns
