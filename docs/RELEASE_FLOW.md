# Release Flow

> ## ⚠️ Superseded
>
> This predates the promote-only release layer. Release is now three lifecycle
> layers with an immutable artifact boundary between build and publish —
> nothing in `20`–`24` can rebuild, re-export or re-sign.
>
> Kept for the reasoning it records, not as instructions. For how the pipeline
> works today read [PIPELINE_ARCHITECTURE.md](PIPELINE_ARCHITECTURE.md); to set
> a project up read [CONSUMER_SETUP.md](CONSUMER_SETUP.md).

This document describes the tag-based release process for the Docker-mandatory Unity CI/CD platform.

> For the **store** release pipelines — build → validate → publish → production
> approval, per platform — see
> [PIPELINE_ARCHITECTURE.md](PIPELINE_ARCHITECTURE.md) §4–§8. This document
> covers branch/tag flow, not the release pipeline graph.

---

## Overview

```
feature branch  →  develop (dev builds)  →  main (staging builds)  →  v* tag (production builds)
```

All builds run inside Docker containers. Production builds use digest-pinned images.

---

## Branch and Tag Conventions

| Branch/Pattern | Triggers | Environment | Image Requirement |
|---|---|---|---|
| Feature branch | `push` | development | Tagged image OK |
| `develop` | `push` | development | Tagged image OK |
| `main` | `push` | staging | Tagged image OK |
| `v*` tag on `main` | Tag push | production | **Digest-pinned image required** |

---

## Creating a Release

### 1. Merge to main

All features for the release merged via pull request. Staging builds run automatically.

### 2. Verify Staging Build

Wait for staging workflow to complete. Download and test the artifact.

### 3. Update BuildConfig Version

```json
"bundleVersion": "1.2.3"
```

### 4. Create Release Tag

```bash
git checkout main
git pull
git tag v1.2.3 -m "Release 1.2.3: [brief description]"
git push origin v1.2.3
```

### 5. Approve Production Deployment

Navigate to **Actions → [workflow run] → Review deployments → Approve**.

### 6. Monitor Production Build

Production build uses:
- Digest-pinned Docker image
- Clean build mode (no cache)
- Strict quality gates
- Protected GitHub Environment

---

## Production Build Requirements

| Requirement | Enforcement |
|---|---|
| Digest-pinned image | `resolve-unity-image` rejects mutable tags in release mode |
| Clean build | `clean-build: true` forces fresh Library import |
| Image scan passed | Image manifest records scan status |
| Strict gates | `failOnWarnings: true`, all validation rules |
| Environment approval | GitHub Environment requires reviewer |

---

## Build → Sign → Deploy Separation

```
Docker Container (Unity build)
  └─ Unsigned artifact
       └─ Host signing step (Android keystore / not in container)
            └─ Host deployment step (Google Play / Cloudflare)
```

Signing and deployment credentials are never inside the Unity container.

---

## Rollback

1. Do not delete the version tag
2. Create hotfix branch: `git checkout -b hotfix/1.2.4 v1.2.3`
3. Fix, bump version to `1.2.4`, merge to `main`
4. Tag `v1.2.4` and follow normal release flow

---

## Versioning

Consumer repositories reference the major version:

```yaml
uses: <WORKFLOW_OWNER>/unity-build-workflows/.github/workflows/unity-build.yml@<ref>
```

- `@main` — development / pre-release; tracks the latest commit
- `@<commit-sha>` — pinned to a specific commit for reproducibility
- `@vX.Y.Z` — exact published tag (stable release) — **no tags have been published yet**; check the repository Releases page
- `@vMAJOR` (e.g. `@v2`) — floating major-version tag; **does not exist yet** — will be created alongside the first stable release of that major version
- Breaking changes increment the major version (e.g. `v3`)

> To create release tags (run when ready to ship — these commands are NOT executed yet):
> ```bash
> git tag vX.Y.Z && git push origin vX.Y.Z           # e.g. git tag v2.0.0
> git tag -f vMAJOR && git push -f origin vMAJOR     # e.g. git tag -f v2 — only after first vMAJOR.x.x tag
> ```
