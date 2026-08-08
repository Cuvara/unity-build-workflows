# Unity Version Upgrade Checklist

Use this checklist whenever you upgrade the Unity editor version used by the
NDC-Unity-Template project. All steps must be completed in order.

Related docs:
- [UNITY\_PERSONAL\_DOCKER\_LICENSE.md](UNITY_PERSONAL_DOCKER_LICENSE.md) — license setup
- [GITHUB\_ACTIONS\_BUILD\_RUNBOOK.md](GITHUB_ACTIONS_BUILD_RUNBOOK.md) — triggering and monitoring builds

---

## Detecting a Version Mismatch

Before upgrading, or when diagnosing a broken build, check three sources:

| Source | Location | How to read |
|---|---|---|
| Project version (truth) | `ProjectSettings/ProjectVersion.txt` | `m_EditorVersion: <version>` |
| Toolkit default | `unity-build-workflows/config/unity-build-defaults.json` | `"unityVersion": "<version>"` |
| Docker image tag | `ghcr.io/dycuong03/unity-editor:<version>-<variant>` | Check GHCR packages page |

**How the build fails on mismatch:** The CI workflow resolves the Unity version
from the consumer's `build.yml` (`unity-version` input, currently hard-coded to
`6000.0.26f1`). If the Docker image for that version+variant does not exist in
GHCR, the `docker pull` step fails with:
```
Error response from daemon: manifest unknown
```
If a wrong-version image is pulled accidentally, Unity itself will fail
immediately with a version mismatch error in `Editor.log`.

---

## Upgrade Checklist

### Step 1 — Update the project in Unity Hub

1. Open Unity Hub.
2. Installs → Add the new Unity version.
3. Ensure required modules are installed for all build targets:
   - **Android:** Android Build Support, Android SDK & NDK Tools, OpenJDK
   - **WebGL:** WebGL Build Support
   - **Linux:** Linux Build Support (Mono) or Linux Dedicated Server Build Support
4. Open the project with the new version. Unity will re-import assets and update
   the project.
5. Fix any API upgrade errors reported by Unity.

### Step 2 — Commit `ProjectSettings/ProjectVersion.txt`

```bash
git add ProjectSettings/ProjectVersion.txt
git commit -m "chore: upgrade Unity to <NEW_VERSION>"
```

Verify the file contains the new version and changeset, e.g.:
```
m_EditorVersion: 6000.0.47f1
m_EditorVersionWithRevision: 6000.0.47f1 (abcdef123456)
```

### Step 3 — Review `Packages/manifest.json` and `packages-lock.json`

Unity may have updated package versions during the upgrade. Review and commit
any changes:

```bash
git diff Packages/manifest.json Packages/packages-lock.json
git add Packages/manifest.json Packages/packages-lock.json
git commit -m "chore: update package versions for Unity <NEW_VERSION>"
```

### Step 4 — Install required Unity modules

Verify each module is present in Unity Hub for the new version:

| Build Target | Required Module |
|---|---|
| Android | Android Build Support, Android SDK & NDK Tools, OpenJDK |
| WebGL | WebGL Build Support |
| Linux64 | Linux Build Support (Mono) |
| LinuxServer | Linux Dedicated Server Build Support |
| iOS | iOS Build Support + Xcode (macOS only — currently deferred) |

### Step 5 — Update `config/unity-build-defaults.json`

Update both `unityVersion` and `unityChangeset` in the toolkit config. The
changeset (revision hash) is found in `ProjectSettings/ProjectVersion.txt`
on the `m_EditorVersionWithRevision` line.

File: `unity-build-workflows/config/unity-build-defaults.json`

```json
{
  "unityVersion": "<NEW_VERSION>",
  "unityChangeset": "<NEW_CHANGESET>",
  "imageVariants": ["android", "webgl", "linux"],
  "registry": "ghcr.io",
  "imageNamespace": "dycuong03/unity-editor"
}
```

Example for a hypothetical upgrade to `6000.0.47f1`:
```json
{
  "unityVersion": "6000.0.47f1",
  "unityChangeset": "abcdef123456",
  "imageVariants": ["android", "webgl", "linux"],
  "registry": "ghcr.io",
  "imageNamespace": "dycuong03/unity-editor"
}
```

Commit this change to the toolkit submodule:
```bash
cd unity-build-workflows
git add config/unity-build-defaults.json
git commit -m "chore: bump Unity default to <NEW_VERSION>"
cd ..
git add unity-build-workflows
git commit -m "chore: bump unity-build-workflows submodule for Unity <NEW_VERSION>"
```

### Step 6 — Rebuild GHCR Docker images

Trigger an image build for each variant. Run all three in parallel or
sequentially — each is independent.

```bash
NEW_VERSION="<NEW_VERSION>"   # e.g. 6000.0.47f1

# Android image
gh workflow run build-unity-image.yml \
  --repo Cuvara/unity-build-workflows \
  --ref main \
  -f unity-version="${NEW_VERSION}" \
  -f image-variant=android \
  -f push-image=true \
  -f run-vulnerability-scan=true

# WebGL image
gh workflow run build-unity-image.yml \
  --repo Cuvara/unity-build-workflows \
  --ref main \
  -f unity-version="${NEW_VERSION}" \
  -f image-variant=webgl \
  -f push-image=true \
  -f run-vulnerability-scan=true

# Linux image
gh workflow run build-unity-image.yml \
  --repo Cuvara/unity-build-workflows \
  --ref main \
  -f unity-version="${NEW_VERSION}" \
  -f image-variant=linux \
  -f push-image=true \
  -f run-vulnerability-scan=true
```

Monitor image builds:
```bash
gh run list --repo Cuvara/unity-build-workflows \
  --workflow build-unity-image.yml --limit 10
```

Wait for all three runs to complete with status `completed` / `success`.

### Step 7 — Verify image tags and digests

After the image builds succeed, confirm the images are present in GHCR:

```bash
NEW_VERSION="<NEW_VERSION>"

# List tags for the unity-editor package
gh api \
  /users/dycuong03/packages/container/unity-editor/versions \
  --jq '.[] | {tags: .metadata.container.tags, digest: .name}' \
  | grep -A2 "${NEW_VERSION}"
```

The manifest artifact (`image-manifest-<variant>-<version>.json`) uploaded by
the build workflow contains the image digest for pinned references:
```bash
gh run download --repo Cuvara/unity-build-workflows \
  --name "image-manifest-android-${NEW_VERSION}"
cat image-manifest-android.json
```

### Step 8 — Update the consumer `build.yml`

Update the `unity-version` input in `.github/workflows/build.yml`:

```yaml
unity-version: '<NEW_VERSION>'
```

Commit:
```bash
git add .github/workflows/build.yml
git commit -m "ci: update unity-version to <NEW_VERSION>"
```

### Step 9 — Trigger consumer builds and verify artifacts

Trigger builds for each Docker platform:

```bash
NEW_VERSION="<NEW_VERSION>"

gh workflow run build.yml \
  --repo Cuvara/NDCUnityTemplate \
  --ref main \
  -f platform=Android

gh workflow run build.yml \
  --repo Cuvara/NDCUnityTemplate \
  --ref main \
  -f platform=WebGL

gh workflow run build.yml \
  --repo Cuvara/NDCUnityTemplate \
  --ref main \
  -f platform=Linux64

gh workflow run build.yml \
  --repo Cuvara/NDCUnityTemplate \
  --ref main \
  -f platform=LinuxServer
```

Monitor:
```bash
gh run list --repo Cuvara/NDCUnityTemplate \
  --workflow build.yml --limit 10
```

Download and verify artifacts for a specific run:
```bash
RUN_ID=<run_id>
gh run download --repo Cuvara/NDCUnityTemplate "${RUN_ID}"
ls -lh
```

### Step 10 — Update changelog with verified run IDs

Add an entry to `CHANGELOG.md` (or the project changelog) recording:
- The new Unity version
- The changeset hash
- The GHCR image build run IDs (from Step 6)
- The consumer build run IDs (from Step 9)

Example entry:
```markdown
## Unity 6000.0.47f1 upgrade — 2026-06-29

- Unity version: 6000.0.47f1 (changeset: abcdef123456)
- Image builds: Cuvara/unity-build-workflows run #<android_run_id>, #<webgl_run_id>, #<linux_run_id>
- Consumer builds verified: Cuvara/NDCUnityTemplate run #<android_run_id>, #<webgl_run_id>, #<linux_run_id>
```
