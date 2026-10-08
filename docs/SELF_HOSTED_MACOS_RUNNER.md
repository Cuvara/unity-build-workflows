# Self-Hosted macOS Runner — iOS Builds

This document covers provisioning a self-hosted macOS GitHub Actions runner for
Unity **iOS** builds. iOS is the only platform with **no Docker path** — Xcode is
macOS-only, so iOS builds run natively on a macOS host with Unity + Xcode
pre-installed and pre-activated.

This lane corresponds to `BUILD_ENGINE=local` on a macOS runner (execution
strategy `selfhosted-local`, macOS variant). See
[RUNNER_AND_BUILD_ENGINE.md](RUNNER_AND_BUILD_ENGINE.md) for how it fits the
Runner/Build-Engine model, and [IOS.md](IOS.md) for the build pipeline itself
(Unity → Xcode project → archive → sign → IPA → TestFlight).

Related docs:
- [IOS.md](IOS.md) — full iOS build pipeline and BuildConfig `iOS` block
- [IOS_SIGNING.md](IOS_SIGNING.md) — certificates, provisioning profiles, secrets
- [IOS_VERIFICATION.md](IOS_VERIFICATION.md) — end-to-end verification runbook
- [SELF_HOSTED_WINDOWS_RUNNER.md](SELF_HOSTED_WINDOWS_RUNNER.md) — the equivalent
  Windows (Android/WebGL/Linux) local lane

---

## 1 — The Runner Label

`unity-pipeline.yml` routes its iOS job (stage 01's runner selection, then
`reusable-build-platform.yml` with `build-engine: local`) to:

- the runner picked by name in the dispatch form's `runner` dropdown, when the entry
  workflows have one (`templates/consumer-30-sync-runners.yml` keeps it filled);
- else the runner policy's choice, if `RUNNER_POLICY` / `RUNNER_POLICY_FILE` is set;
- else `RUNNER_MACOS_LABEL` — default `self-hosted,macOS` with `RUNNER_TYPE=self-hosted`,
  `macos-latest` otherwise ([REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md)).

Register the runner with GitHub's automatic `self-hosted` and `macOS` labels (plus its own
name, so it can be picked by name). A non-macOS runner never gets the job: the build job
reports `blocked` for iOS anywhere else.

**Several Macs?** List them in a runner policy rather than juggling labels:

```json
"runners": {
  "ios-build-01": { "labels": ["self-hosted", "macOS", "ios-build-01"], "xcode": ["16"] },
  "ios-build-02": { "labels": ["self-hosted", "macOS", "ios-build-02"], "xcode": ["16"] }
},
"platforms": { "iOS": { "mode": "self-hosted-only", "priority": ["ios-build-01", "ios-build-02"],
                        "require": { "xcode": "16" } } }
```

The scheduler takes the first one that is online and idle, skips one that is
offline or lacks Xcode 16, and fails with a report instead of queueing when
neither can run the job. It never sends iOS to Linux, Docker or GitHub-hosted.
See [MULTI_RUNNER_SCHEDULING.md](MULTI_RUNNER_SCHEDULING.md#13-examples-android-and-ios).

---

## 2 — Local Prerequisites

Install on the macOS host **before** registering the runner. All items are
required; a missing module or unaccepted Xcode license causes build failures.

| Requirement | Notes |
|---|---|
| macOS | 13 (Ventura) or newer |
| Xcode | Matching `xcodeVersion` in your BuildConfig; `xcode-select -p` set; `sudo xcodebuild -license accept` |
| Command-line tools | `xcodebuild`, `security`, `codesign`, `xcrun`, `curl` on `PATH` |
| Unity Hub + Editor | Project's Unity version (`6000.0.26f1`, the SSOT in `ProjectSettings/ProjectVersion.txt`) |
| Unity **iOS Build Support** | Module installed for that exact Editor version |
| Git + Git LFS | `git lfs install --system` — the project stores binary assets in LFS |

### 2.1 Xcode

Install Xcode from the App Store or Apple Developer downloads, then:

```bash
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
sudo xcodebuild -license accept
xcodebuild -version   # confirm the version matches BuildConfig xcodeVersion
```

### 2.2 Unity Hub + Editor + iOS module

1. Install Unity Hub from [unity.com/download](https://unity.com/download).
2. Unity Hub → **Installs → Install Editor** → select **6000.0.26f1**
   (use the "Archive" tab if not listed).
3. Add the **iOS Build Support** module during installation (or later via
   **⋮ → Add modules**).
4. Verify the iOS playback engine exists:
   ```bash
   ls "/Applications/Unity/Hub/Editor/6000.0.26f1/PlaybackEngines/iOSSupport"
   ```
   If this path is missing, the iOS module is not installed.

**Steps 2–4 are optional now.** Each native job runs Unity preflight before
building. It installs the editor version from the project's `ProjectVersion.txt`
plus iOS Build Support through the Unity CLI when they are missing, checks
Xcode, and the build runs the editor preflight reports. That needs Python 3.8+
and an install root the runner account can write without `sudo`, set in the
runner's `.env` as `UNITY_PREFLIGHT_INSTALL_ROOT=/Users/<runner>/unity-editors`.
An editor already installed by hand is reused. See
[UNITY_PREFLIGHT.md § In CI](UNITY_PREFLIGHT.md#in-ci-self-hosted-native-lanes).

### 2.3 Git LFS

```bash
brew install git-lfs      # or download from https://git-lfs.com
git lfs install --system
git lfs version
```

---

## 3 — Unity License Activation (Preactivated)

Activate Unity **once**, interactively, via Unity Hub. The macOS lane uses
`activation-strategy: preactivated` — CI performs **no** activation and passes
**no** Unity license secrets to the iOS build.

1. Open Unity Hub → sign in with your Unity ID.
2. Unity Hub → **Licenses → Add → Get a free Personal license**.
3. Launch Editor **6000.0.26f1** once to confirm it opens without a license error.

> The GitHub Actions runner service must run under the **same macOS user account**
> that activated Unity Hub, so it inherits the license. Do **not** store
> `UNITY_PASSWORD` or credentials on the machine.

---

## 4 — Registering the Runner

From the runner package (GitHub → **Settings → Actions → Runners → New
self-hosted runner → macOS**), extract it, then:

```bash
./config.sh --url https://github.com/<org-or-user>/<repo> \
            --token <REGISTRATION_TOKEN> \
            --name macos-ios-runner-01

# Run as a launchd service (starts on boot, restarts on crash)
./svc.sh install
./svc.sh start
```

Verify the service:

```bash
./svc.sh status
```

In GitHub, **Settings → Actions → Runners** — the runner shows **Idle** (green)
with the `self-hosted` and `macOS` labels.

---

## 5 — Verification

Trigger a smoke build (signing secrets must be set first — see
[IOS_VERIFICATION.md](IOS_VERIFICATION.md)):

```bash
gh workflow run "Build / Development" \
  --repo <ORG>/<CONSUMER_REPO> \
  --ref develop \
  -f platform=iOS
```

Watch it:

```bash
gh run watch --repo <ORG>/<CONSUMER_REPO>
```

Expected: the iOS build job picks up on your runner and uploads the Xcode project
artifact (`…_ios_xcodeproj`). For the full sign/archive/export/TestFlight checklist, follow
[IOS_VERIFICATION.md](IOS_VERIFICATION.md).

---

## 6 — Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| The iOS job queues indefinitely | No online runner matches `RUNNER_MACOS_LABEL` | Set `RUNNER_MACOS_LABEL` to your runner's labels, pick the runner by name in the dispatch form, or add a runner policy, which fails fast with a report instead of queueing |
| The iOS job shows `blocked` | Job landed on a non-macOS runner (guard step caught it) | Ensure only macOS runners carry the labels iOS routes to |
| `target platform not supported` / no `iOSSupport` | iOS Build Support module missing | Unity Hub → Editor **6000.0.26f1** → Add modules → iOS Build Support |
| `xcodebuild: error: ... license` | Xcode license not accepted | `sudo xcodebuild -license accept` |
| `No valid Unity license` at build | Runner service runs as a different user than Hub activation | Run the service as the same account that activated Unity Hub; re-activate |
| `git-lfs filter-process died` / smudge error | Git LFS not installed | `brew install git-lfs && git lfs install --system`, then re-run |

Verify the runner's registered labels:

```bash
gh api repos/<owner>/<repo>/actions/runners \
  --jq '.runners[] | {name: .name, labels: [.labels[].name]}'
```

---

## 7 — Security Notes

- **No Unity credentials on the runner.** Activation is interactive-once via Unity
  Hub; nothing else is stored.
- **Signing secrets** (`IOS_DISTRIBUTION_CERTIFICATE_BASE64`, provisioning profile,
  App Store Connect key) live in repository/environment secrets, not on the host —
  see [IOS_SIGNING.md](IOS_SIGNING.md).
- **Runner service user:** a dedicated low-privilege macOS user, matching the Unity
  Hub activation account.
- **Restrict fork PRs:** **Settings → Actions → General → Fork pull request
  workflows → Require approval** — untrusted forks must not reach a self-hosted
  runner.

---

## See also

- [RUNNER_AND_BUILD_ENGINE.md](RUNNER_AND_BUILD_ENGINE.md) — Runner vs Build Engine
  architecture and execution strategies.
- [EXPLICIT_PLATFORM_FLOW.md § 6](EXPLICIT_PLATFORM_FLOW.md#6-ios-build--special-requirements)
  — iOS job behaviour, blocking, and unblocking.
- [IOS.md](IOS.md) · [IOS_SIGNING.md](IOS_SIGNING.md) · [IOS_VERIFICATION.md](IOS_VERIFICATION.md).
