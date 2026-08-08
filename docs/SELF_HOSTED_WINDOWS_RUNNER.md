# Self-Hosted Windows Runner — Unity Personal / Free

This document covers setting up a self-hosted Windows GitHub Actions runner for
Unity builds when the Docker lane is unavailable or impractical for Unity
Personal / free licenses.

This lane corresponds to `RUNNER_TYPE=self-hosted` + `BUILD_ENGINE=local`
(execution strategy `selfhosted-local`) — see
[RUNNER_AND_BUILD_ENGINE.md](RUNNER_AND_BUILD_ENGINE.md) for how this fits
into the toolkit's Runner/Build-Engine model, and
[REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md#runner) for the variable
reference (including the legacy `RUNNER_DEFAULT_MODE=self-hosted-windows`
mapping).

Related docs:
- [RUNNER\_AND\_BUILD\_ENGINE.md](RUNNER_AND_BUILD_ENGINE.md) — Runner vs Build
  Engine architecture, all execution strategies, licensing per mode
- [EXPLICIT\_PLATFORM\_FLOW\_SPEC.md](EXPLICIT_PLATFORM_FLOW_SPEC.md) — reusable
  workflow interface, runner-mode semantics, lane selection
- [UNITY\_PERSONAL\_DOCKER\_LICENSE.md](UNITY_PERSONAL_DOCKER_LICENSE.md) — the
  alternative Docker activation strategy (requires all three secrets)

---

## 1 — Why Use This Lane?

Unity Personal (free) licenses require an online activation step that combines
a machine-bound `.ulf` file with account credentials. Inside an ephemeral Docker
container this is handled by the `personal-combined` strategy (see
[UNITY\_PERSONAL\_DOCKER\_LICENSE.md](UNITY_PERSONAL_DOCKER_LICENSE.md)).

**Use the `self-hosted-windows` lane when:**

| Condition | Reason |
|---|---|
| No `UNITY_LICENSE` secret available | `.ulf` export not possible / not permitted in your pipeline |
| Docker activation is blocked | Corporate network policy, firewall blocking Unity license servers from within Docker |
| Prefer local Unity installation | Faster builds, cached Library, no Docker overhead |
| Personal/free licence with no serial | `UNITY_SERIAL` does not exist for Personal; local Hub activation avoids the online activation step entirely |

**In this lane:**
- Unity Hub and Unity Editor are **pre-installed and pre-activated on the runner
  machine** — no activation occurs in CI.
- No `UNITY_LICENSE`, `UNITY_EMAIL`, or `UNITY_PASSWORD` secrets are required.
- No Docker is required on the runner.
- The `activation-strategy` input is **ignored** — the pre-activated local
  installation is used unconditionally.

---

## 2 — Required Runner Labels

The reusable workflow `reusable-build-platform.yml` routes the job to this lane
via:

```yaml
runs-on: [self-hosted, Windows, unity]
```

All three labels must be present on the runner. Apply them during registration
with the `--labels` flag:

```cmd
.\config.cmd --url https://github.com/<org-or-user>/<repo> ^
             --token <REGISTRATION_TOKEN> ^
             --name unity-windows-runner-01 ^
             --labels self-hosted,Windows,unity ^
             --runasservice
```

> **Note:** GitHub automatically adds the `self-hosted` and `Windows` labels to
> any Windows runner. You still need to pass them explicitly via `--labels` to
> ensure the `unity` label is included in the same registration command. Verify
> all three labels appear in
> **Repository / Organization Settings → Actions → Runners** after registration.

---

## 3 — Local Prerequisites

Install and configure the following on the Windows machine **before** registering
the runner. All items are required; missing modules cause build failures.

### 3.1 Unity Hub

Download from [unity.com/download](https://unity.com/download) and install Unity
Hub. Unity Hub manages Editor installations, modules, and license activation.

### 3.2 Unity Editor — `6000.0.26f1`

Install Unity Editor **6000.0.26f1** (the version SSOT in
`ProjectSettings/ProjectVersion.txt`) via Unity Hub:

1. Unity Hub → **Installs → Install Editor**
2. Select version **6000.0.26f1** (use "Archive" tab if not listed in recommended)
3. Select the following **modules** during installation:

| Module | Required for |
|---|---|
| **Android Build Support** | Android platform builds |
| **Android SDK & NDK Tools** | Android SDK / NDK bundled with Editor |
| **OpenJDK** | Android Gradle build toolchain |
| **WebGL Build Support** | WebGL platform builds |
| **Linux Build Support (IL2CPP)** | Linux64 / LinuxServer platform builds |
| **Dedicated Server Build Support** | `LinuxServer` builds (sub-target Server) |
| **Windows Build Support (IL2CPP)** | Windows IL2CPP scripting backend |

> The Linux and Dedicated Server modules allow cross-compilation from Windows.
> All modules listed above must be installed — missing a module causes Unity to
> exit with `Error: target platform not supported`.

### 3.3 Git

Install [Git for Windows](https://git-scm.com/download/win) (Git Bash bundled).
Ensure `git` is on the system `PATH`:

```cmd
git --version
```

### 3.4 Git LFS

Git LFS is **required** — the Unity project stores large binary assets (textures,
audio, etc.) in LFS. The reusable workflow checks out with `lfs: true`; if LFS
is not installed the checkout step fails.

```cmd
git lfs install --system
git lfs version
```

### 3.5 Hardware Recommendations

| Resource | Minimum | Recommended |
|---|---|---|
| RAM | 16 GB | 32 GB (IL2CPP + Android) |
| Storage | 100 GB SSD | 200 GB NVMe |
| CPU cores | 4 | 8+ |

---

## 4 — Unity Personal License Activation (Preactivated Strategy)

Activate Unity on the runner machine **once** via Unity Hub. CI jobs never
perform activation — they inherit the machine's existing license.

### Steps

1. **Open Unity Hub** on the runner machine (local desktop session or RDP).
2. Click the user icon (top-left) → **Sign in** → sign in with your Unity ID
   (the account that holds the Personal license).
3. Unity Hub → **Licenses** (or the license icon) → **Add** →
   **Get a free Personal license** → **Agree and get Personal Edition license**.
4. Unity Hub confirms the license is active. The `.ulf` file is written to:
   ```
   C:\ProgramData\Unity\Unity_lic.ulf
   ```
5. Launch Unity Editor **6000.0.26f1** from Unity Hub once to verify:
   - The Editor opens without a license error.
   - Help → About Unity shows "Personal" edition.

### What this means for CI

When the GitHub Actions runner service runs under the **same Windows user
account** that activated Unity Hub, it inherits the license. No secrets are
passed to the workflow. The reusable workflow step 4 (`resolve activation
strategy`) is skipped entirely for the `self-hosted-windows` lane.

> **Security note:** Do **not** store `UNITY_PASSWORD` or any Unity credentials
> in environment variables, runner secrets, or files on the machine. License
> activation is done once interactively via Unity Hub. After that, the `.ulf`
> file in `C:\ProgramData\Unity\` is all that is needed — it is a machine-bound
> signed file and is not a secret.

---

## 5 — Selecting `runner-mode: self-hosted-windows` in the Consumer Workflow

### Workflow Dispatch (manual trigger)

In the GitHub Actions UI, trigger the `unity-build.yml` workflow with:

| Input | Value |
|---|---|
| `runner-mode` | `self-hosted-windows` |
| `activation-strategy` | *(any value — ignored for this lane)* |

All `build-*` jobs (Android, WebGL, Linux64, LinuxServer, Addressables) will
route to `runs-on: [self-hosted, Windows, unity]`.

> **iOS is not supported on this lane.** `build-ios` hardcodes
> `runner-mode: self-hosted-macos` regardless of the dispatch input — see
> Section 3.3 of [EXPLICIT\_PLATFORM\_FLOW\_SPEC.md](EXPLICIT_PLATFORM_FLOW_SPEC.md).

### Programmatic / API trigger

```bash
gh workflow run unity-build.yml \
  --repo Cuvara/NDCUnityTemplate \
  --field runner-mode=self-hosted-windows \
  --field platform=All \
  --field environment=production
```

### How the reusable workflow uses the input

From the contract (`reusable-build-platform.yml` internal structure):

```yaml
# runner selection pseudo-logic (implemented in reusable-build-platform.yml)
runs-on: >-
  ${{
    inputs.runner-mode == 'self-hosted-windows' && fromJSON('["self-hosted","Windows","unity"]') ||
    inputs.runner-mode == 'self-hosted-macos'   && fromJSON('["self-hosted","macOS","unity"]')   ||
    'ubuntu-latest'
  }}
```

Build step shell selection:

```yaml
# docker lane
- uses: game-ci/unity-builder@v4
  ...

# self-hosted-windows lane
- name: Build (Windows)
  shell: cmd
  run: |
    "%UNITY_EXE%" -batchmode -buildTarget %BUILD_TARGET% ...
```

Unity Editor is located by the step at the default Hub installation path:
`C:\Program Files\Unity\Hub\Editor\6000.0.26f1\Editor\Unity.exe`.

---

## 6 — Registering the Runner as a Windows Service

Run the following from an **elevated (Administrator) PowerShell** in the runner
installation directory:

```powershell
# Download runner package from GitHub (Settings → Actions → Runners → New runner)
# Extract, then:

.\config.cmd --url https://github.com/<org-or-user>/<repo> `
             --token <REGISTRATION_TOKEN> `
             --name unity-windows-runner-01 `
             --labels self-hosted,Windows,unity `
             --runasservice

# Start the service
.\svc.cmd install
.\svc.cmd start
```

Verify the service is running:

```powershell
Get-Service -Name "actions.runner.*"
```

The service should show `Status: Running`. It will restart automatically after
reboots.

---

## 7 — Verification Steps

After registration and license activation, verify the runner end-to-end:

### 7.1 Runner visible in GitHub

**Settings → Actions → Runners** — the runner should show as **Idle** (green dot)
with labels `self-hosted`, `Windows`, `unity`.

### 7.2 Trigger a smoke build

```bash
gh workflow run unity-build.yml \
  --repo Cuvara/NDCUnityTemplate \
  --field runner-mode=self-hosted-windows \
  --field platform=Android \
  --field environment=development \
  --field clean-build=false
```

Watch the run:

```bash
gh run watch --repo Cuvara/NDCUnityTemplate
```

Expected: `build-android` job picks up on your runner, builds successfully,
uploads artifact `unity-build-Android`.

### 7.3 Confirm Unity path

In the GitHub Actions log for the build step, look for a line similar to:

```
Unity.exe path: C:\Program Files\Unity\Hub\Editor\6000.0.26f1\Editor\Unity.exe
```

If the path is missing or wrong, see Troubleshooting §8.3.

---

## 8 — Troubleshooting

### 8.1 Runner Offline / Not Picking Up Jobs

| Symptom | Cause | Fix |
|---|---|---|
| Runner shows **Offline** in GitHub | Service stopped or machine rebooted without service | `.\svc.cmd start` (run as Administrator in runner dir) |
| Runner shows **Idle** but job queues indefinitely | Label mismatch — runner lacks `unity` label | Re-register with `--labels self-hosted,Windows,unity`; check Settings → Actions → Runners |
| Job error: `No runner matching the required labels` | No Windows runner with `unity` label is registered | Register runner with all three labels (§2) |

### 8.2 Wrong Labels

Verify labels via GitHub CLI:

```bash
gh api repos/<owner>/<repo>/actions/runners --jq '.runners[] | {name: .name, labels: [.labels[].name]}'
```

If `unity` is missing, remove the runner and re-register with
`--labels self-hosted,Windows,unity`.

### 8.3 Unity Not Found / Wrong Version

| Symptom | Cause | Fix |
|---|---|---|
| `Unity.exe not found at expected path` | Editor not installed via Hub, or wrong version | Install Unity **6000.0.26f1** via Unity Hub |
| `Editor version mismatch` | A different Editor version is on PATH | Ensure only `6000.0.26f1` is installed, or set `UNITY_EXE` env var on the runner to the correct path |
| `No valid Unity license` at build | Unity not activated, or runner service runs as a different user than Hub activation | Sign in to Unity Hub under the **same Windows user** the runner service runs as; re-activate |

To find Unity installations:

```powershell
Get-ChildItem "C:\Program Files\Unity\Hub\Editor\" -Directory | Select-Object Name
```

### 8.4 Missing Module

```
Error: target platform not supported (Android / WebGL / ...)
```

Open Unity Hub → **Installs → 6000.0.26f1 → ⋮ (options) → Add modules** and
install the missing module from the prerequisites matrix in §3.2.

### 8.5 Git LFS Missing

```
Error: git-lfs filter-process died of signal 9
# or
Smudge error: Error downloading ...
```

Install Git LFS:

```powershell
# Download from https://git-lfs.com or via Chocolatey:
choco install git-lfs

git lfs install --system
git lfs version   # should print: git-lfs/X.Y.Z
```

Then re-run the workflow — the checkout step re-fetches LFS objects.

### 8.6 Library Cache Issues

The self-hosted-windows lane caches the Unity `Library/` directory between runs.
If the cache is corrupted:

```powershell
# Find and delete Library caches for this repo
Get-ChildItem "D:\actions-runner\_work\<repo>\<repo>\Library" -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
```

Or trigger the workflow with `clean-build: true` to delete the cache at the
start of the build step.

---

## 9 — Security Notes

- **Do not store Unity credentials on the runner.** License activation is done
  once interactively via Unity Hub. The resulting `.ulf` file at
  `C:\ProgramData\Unity\Unity_lic.ulf` is all that persists.
- **Do not set `UNITY_PASSWORD` as a runner environment variable or system
  variable.** This is the Docker-lane pattern and is not needed here.
- **Runner service user:** The runner service should run as a dedicated
  low-privilege Windows user (not Administrator or SYSTEM). That user must be
  the same account used to activate Unity Hub.
- **No secrets required in the workflow:** `UNITY_LICENSE`, `UNITY_EMAIL`, and
  `UNITY_PASSWORD` are not passed to jobs using `runner-mode: self-hosted-windows`.
  If they are present in the repository, they are simply ignored by this lane.
- **Repository access:** Restrict the self-hosted runner to trusted workflows —
  forks can trigger malicious code on self-hosted runners. Use
  **Settings → Actions → General → Fork pull request workflows from outside
  collaborators → Require approval** to prevent untrusted forks from accessing
  the runner.
