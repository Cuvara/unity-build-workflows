# Unity Personal / Free License — Docker CI Activation

This document describes how Unity Personal (free) licenses are activated inside
ephemeral Docker containers in the unity-build-workflows CI system.

Related docs:
- [GITHUB\_ACTIONS\_BUILD\_RUNBOOK.md](GITHUB_ACTIONS_BUILD_RUNBOOK.md) — end-to-end build operations
- [UNITY\_VERSION\_UPGRADE.md](UNITY_VERSION_UPGRADE.md) — upgrading the Unity version

---

## Overview

Unity Personal licenses are machine-bound. A `.ulf` file issued on one machine
cannot be used standalone on another — attempting to do so fails with
`TimeStamp validation failed`. Conversely, providing only account credentials
(email + password) in a Docker container without a `.ulf` present results in
`0 entitlements`.

**The only reliable path for Unity Personal/free in ephemeral Docker is the
`personal-combined` strategy: provide `UNITY_LICENSE` (raw `.ulf` contents),
`UNITY_EMAIL`, and `UNITY_PASSWORD` together.**  This is the approach used by
[GameCI](https://game.ci/) and is implemented in this toolkit.

> ⚠️ **Correction (2026-07-13 investigation)** — this toolkit's
> `personal-combined` strategy (`docker/unity/activate-license.sh`) does
> **not actually mirror what GameCI does**, and this is the confirmed root
> cause of self-hosted Windows docker-run activation failures. See
> [GitHub-hosted vs Self-hosted Activation — Root Cause](#github-hosted-vs-self-hosted-activation--root-cause)
> below before debugging further activation failures.

---

## GitHub-hosted vs Self-hosted Activation — Root Cause

**Symptom:** the identical-looking `UNITY_LICENSE` + `UNITY_EMAIL` +
`UNITY_PASSWORD` combination activates successfully on GitHub-hosted +
`game-ci/unity-builder@v4` (Docker), but fails on the self-hosted Windows
runner's native `docker run` lane (both this toolkit's `activate-license.sh`
and the earlier hand-rolled inline script) with:

```
[Licensing::Client] Error: Code 500 while processing request (status: Unable to update
licenses. Errors: TimeStamp validation failed,No license activation found for this computer.)
[Licensing::Client] Error: Code 404 while processing request (status: Found 0 entitlement
groups and 0 free entitlements matching requested entitlement ids)
No valid Unity Editor license found. Please activate your license.
```
— from failing self-hosted run `29226308795`, job step "Build with Docker
(Windows, native docker run)".

### What GameCI actually does (verified from `game-ci/unity-builder` source, `dist/index.js`)

GameCI does **not** write the raw `.ulf` file into the license directory and
log in with `-username`/`-password` alone. It:

1. **Parses the serial number out of the `.ulf` XML itself**
   (`BuildParameters.getSerialFromLicenseFile`): extracts the
   `<DeveloperData Value="...">` node, base64-decodes it, and strips 4
   leading garbage bytes to recover the plaintext Unity serial (Personal
   serials start with `F`).
2. Passes that serial as `UNITY_SERIAL` (alongside `UNITY_EMAIL` /
   `UNITY_PASSWORD`) into the container — **not** `UNITY_LICENSE`.
3. The container `entrypoint.sh` **randomizes `/etc/machine-id`** before
   activation, specifically because the serial is a Personal license:
   ```bash
   if [[ "$UNITY_SERIAL" = F* ]]; then
     echo "Randomizing machine ID for personal license activation"
     dbus-uuidgen > /etc/machine-id && mkdir -p /var/lib/dbus/ && ln -sf /etc/machine-id /var/lib/dbus/machine-id
   fi
   ```
4. `activate.sh` then runs **SERIAL LICENSE MODE**:
   ```bash
   unity-editor -logFile /dev/stdout -quit \
     -serial "$UNITY_SERIAL" -username "$UNITY_EMAIL" -password "$UNITY_PASSWORD" \
     -projectPath "/BlankProject"
   ```
   with a 5-attempt exponential-backoff retry loop.

Confirmed from a live GitHub-hosted dispatch (run `29230778434`, job
`86754442045`):
```
Randomizing machine ID for personal license activation
Requesting activation
...
-serial
...
[Licensing::Client] Successfully activated ULF license
[Licensing::Module] Serial number assigned to (masked): "***"
Activation successful
```
(That run's *build* later failed on an unrelated Unity project asset issue —
`.meta` files missing in an immutable package folder — activation itself
fully succeeded.)

### What this toolkit's `activate-license.sh` (`personal-combined`) actually does

```bash
# ULF_DIR="${HOME}/.local/share/unity3d/Unity"
printf '%s' "${UNITY_LICENSE}" > "${ULF_DEST}"   # write the RAW .ulf as-is
...
"${UNITY_EDITOR}" -batchmode -nographics \
    -username "${UNITY_EMAIL}" -password "${UNITY_PASSWORD}" \
    -logFile "${UNITY_LOG_FILE}" -quit
```

This never extracts a serial and never touches `/etc/machine-id`. It writes
the `.ulf` — which is bound to whichever machine originally activated
it (e.g. a developer's Windows PC via Unity Hub) — directly into the fresh
container's license directory, then logs in with just email/password. Unity's
licensing client sees the pre-existing `.ulf`, tries to *update/refresh that
specific machine-bound entry* against the container's (unrelated, unrandomized)
machine id, and fails with exactly `TimeStamp validation failed / No license
activation found for this computer`. The subsequent entitlement lookup also
comes back empty (`0 entitlement groups`) because no `-serial` was ever passed
to request a fresh entitlement — this matches the self-hosted failure log
above line-for-line.

**This is true regardless of which of the two self-hosted implementations is
used** — the original hand-rolled inline PowerShell/bash script (pre
`9c79882`) and the toolkit's own `activate-license.sh` `personal-combined`
strategy (post `9c79882`, "reuse activate-license.sh for Windows docker-run
Unity activation") share the identical flaw: neither extracts the serial from
`UNITY_LICENSE` nor randomizes the container's machine id. Consolidating
self-hosted onto `activate-license.sh` was a valid de-duplication (one
implementation instead of two), but it does **not** fix activation, because
the toolkit's `activate-license.sh` was never actually equivalent to GameCI's
method — the "mirrors GameCI" description in this doc (see the correction
banner above) was inaccurate and is corrected here.

### Activation comparison table

| Dimension | GitHub-hosted + docker (`game-ci/unity-builder@v4`) | Self-hosted Windows + docker (`docker run`) |
|---|---|---|
| Unity version | 6000.0.26f1 | 6000.0.26f1 (same) |
| Docker image | `unityci/editor:ubuntu-6000.0.26f1-android-3` | `unityci/editor:ubuntu-6000.0.26f1-android-3` (same tag; digest `sha256:9213...8219` observed on both) |
| Container entrypoint | `game-ci`'s own `dist/platforms/ubuntu/entrypoint.sh` + `steps/activate.sh` (volume-mounted over the image, replacing its default) | `unityci/editor` image's own default entrypoint, invoked via `bash /tmp/build.sh` (a generated script) or, post-fix, `activate-license.sh` |
| Env vars passed | `UNITY_LICENSE`, `UNITY_EMAIL`, `UNITY_PASSWORD` **as GitHub Action inputs**, internally converted by the JS wrapper to `UNITY_SERIAL` (extracted from the `.ulf`), `UNITY_EMAIL`, `UNITY_PASSWORD` before `docker run` | `UNITY_LICENSE`, `UNITY_EMAIL`, `UNITY_PASSWORD` passed through unchanged via `-e` |
| Machine identity | `/etc/machine-id` **randomized** (`dbus-uuidgen`) before activation because `UNITY_SERIAL` starts with `F` | Container default machine id, **not** randomized/pinned |
| Activation command | `unity-editor -serial "$UNITY_SERIAL" -username "$UNITY_EMAIL" -password "$UNITY_PASSWORD" -projectPath /BlankProject` (retried up to 5x) | `unity-editor -username "$UNITY_EMAIL" -password "$UNITY_PASSWORD" -logFile ... -quit` (no `-serial`, single attempt) — both the pre-fix inline script and the post-fix `activate-license.sh personal-combined` path |
| `.ulf` handling | Never written to disk as-is; only its embedded serial is extracted in JS before the container runs | Written verbatim to `~/.local/share/unity3d/Unity/Unity_lic.ulf` inside the container before activation |
| Runner / GH context | `ubuntu-latest`, `runner.os == 'Linux'`, container action | `self-hosted, Windows, X64, self-hosted-windows, unity` labels, `runner.os == 'Windows'`, `docker run` invoked from PowerShell |
| Result | `Activation successful` (log-verified) | `No valid Unity Editor license found` (log-verified) |

### First point of divergence

The two flows diverge **before either container ever starts**: GameCI's
Node.js action code parses `UNITY_LICENSE` and converts it into
`UNITY_SERIAL` (`getSerialFromLicenseFile`), which then changes *which code
path* the shared `unityci/editor`-family entrypoint takes (serial+machine-id
reset, vs. plain email/password). The self-hosted lane never performs this
extraction step, so it can only ever reach the weaker email/password-with-
stale-`.ulf` path — which GameCI's own entrypoint script only offers as a
side door (`SERIAL LICENSE MODE` requires `UNITY_SERIAL` to be non-empty;
there is no email/password-only branch in `game-ci/docker`'s `activate.sh`
at all).

### Recommended fix (proposed to `docker-android`, not applied here)

Reuse GameCI's actual method instead of inventing a new one:

1. In `activate-license.sh`'s `personal-combined` case, extract the serial
   from `UNITY_LICENSE` the same way GameCI does (locate
   `<DeveloperData Value="...">`, base64-decode, drop the first 4 bytes) and
   call `unity-editor -serial "$SERIAL" -username ... -password ...` instead
   of `-username`/`-password` alone.
2. Before that call, randomize the container's machine identity the same way
   `game-ci/docker`'s `entrypoint.sh` does (`dbus-uuidgen > /etc/machine-id`,
   symlink into `/var/lib/dbus/machine-id`) — this needs `docker run` to
   allow writing `/etc/machine-id` (default container filesystem is
   writable, no extra mount needed) or, if read-only, pass
   `--hostname`/pre-seed a machine-id file via bind mount as
   `unity-generate-license.yml` already does (`-v ".../machine-id:/etc/machine-id:ro"`)
   with a **fixed, pre-committed machine id** matched to a `.ulf` that was
   generated against that same fixed id (see that workflow's comment: "MUST
   match run_unity_container.py").
3. Do **not** pre-place `UNITY_LICENSE`'s raw content into
   `~/.local/share/unity3d/Unity/Unity_lic.ulf` before calling
   `unity-editor` for the Personal/free case — that step is what triggers
   the stale-machine-binding update path instead of a fresh activation.

This is a self-hosted-specific code change (the docker-run step is owned by
`docker-android`); it is proposed here, not applied, to avoid colliding with
that teammate's in-flight iteration on the same step.

---

## The `personal-combined` Strategy

### How it works

Implemented in:
- `docker/unity/activate-license.sh` — execution
- `scripts/common/resolve_activation_strategy.sh` — strategy selection

**Strategy selection** (in `activate-license.sh`, lines 103–151):

When `UNITY_LICENSE`, `UNITY_EMAIL`, and `UNITY_PASSWORD` are **all** set,
`personal-combined` is selected immediately, before any other logic runs.
No strategy resolver call is needed.

In `resolve_activation_strategy.sh` the same condition is evaluated as
Strategy 0 — the highest-priority auto strategy.

**Execution** (`personal-combined` case in `activate-license.sh`):

1. The `.ulf` content from `UNITY_LICENSE` is written to
   `~/.local/share/unity3d/Unity/Unity_lic.ulf` (permissions `600`).
   - If that path already contains a `.ulf` (e.g. a host-mounted pre-activated
     file), it is reused without overwriting.
   - Encoding is handled automatically (see [Raw vs Base64](#raw-vs-base64)).
2. Unity is invoked in batch mode with `-username` and `-password`:
   ```
   unity-editor -batchmode -nographics \
     -username <UNITY_EMAIL> \
     -password <UNITY_PASSWORD> \
     -logFile /tmp/unity-home/Editor.log \
     -quit
   ```
3. On success, Unity exits 0 and logs `Activation successful`.
4. On failure, the failure class is determined from `Editor.log` and a fallback
   to `manual-ulf` is attempted; if that also fails, the build exits 1.

### Why all three secrets are required

| Secrets provided | Result |
|---|---|
| `UNITY_LICENSE` only (`.ulf` alone) | `TimeStamp validation failed` — file is machine-bound |
| `UNITY_EMAIL` + `UNITY_PASSWORD` only | `0 entitlements` — no seat entitlement found |
| All three together (`personal-combined`) | `Activation successful` |

---

## Required Secrets

Set these three secrets in the **consumer** repository
(`Settings → Secrets and variables → Actions`):

| Secret | Description |
|---|---|
| `UNITY_LICENSE` | Raw `.ulf` file contents (see below for how to find it) |
| `UNITY_EMAIL` | Email address of the Unity account that owns the license |
| `UNITY_PASSWORD` | Password of the Unity account |

> **Security:** Never print, log, or commit secret values. The scripts
> deliberately redact all secret content from logs — only presence (set/unset)
> is ever logged.

---

## Finding Your Local `Unity_lic.ulf` File

Unity Hub writes the `.ulf` license file to a platform-specific path after
license activation. On a Windows host accessed from WSL:

```bash
# Search common Windows/WSL Unity license locations
find /mnt/c/ProgramData/Unity /mnt/c/Users ~/.local/share/unity3d \
  -iname "Unity_lic.ulf" -type f -size +0c 2>/dev/null
```

Typical paths:

| OS | Path |
|---|---|
| Windows | `C:\ProgramData\Unity\Unity_lic.ulf` |
| macOS | `/Library/Application Support/Unity/Unity_lic.ulf` |
| Linux | `~/.local/share/unity3d/Unity/Unity_lic.ulf` |

The file is a signed XML document beginning with `<?xml`. Do **not** modify it.

---

## Recommended Secret Setup Commands

```bash
# 1. Set UNITY_LICENSE — pipe the raw .ulf file contents directly
#    (replace the path with the actual location found above)
ULF="/mnt/c/ProgramData/Unity/Unity_lic.ulf"
gh secret set UNITY_LICENSE --repo Cuvara/NDCUnityTemplate < "$ULF"

# 2. Set UNITY_EMAIL — enter interactively when prompted
gh secret set UNITY_EMAIL --repo Cuvara/NDCUnityTemplate

# 3. Set UNITY_PASSWORD — enter interactively when prompted
gh secret set UNITY_PASSWORD --repo Cuvara/NDCUnityTemplate
```

> Pipe `UNITY_LICENSE` from the file rather than copy-pasting to avoid
> shell escaping issues and accidental whitespace changes.

---

## Verifying Secrets Are Set

Check that all three secrets are present (names only — values are never shown):

```bash
gh secret list --repo Cuvara/NDCUnityTemplate \
  | grep -E 'UNITY_LICENSE|UNITY_EMAIL|UNITY_PASSWORD'
```

Expected output (timestamps will vary):

```
UNITY_EMAIL      Updated 2026-06-29
UNITY_LICENSE    Updated 2026-06-29
UNITY_PASSWORD   Updated 2026-06-29
```

---

## Raw vs Base64 (`UNITY_LICENSE_ENCODING`)

| Value | Behavior |
|---|---|
| *(unset / default)* | `auto` — tries base64 decode; falls back to raw if decode fails |
| `raw` | Treat `UNITY_LICENSE` as-is (raw XML) |
| `base64` | Force base64 decode before writing the `.ulf` |

**Default is raw.** A standard `.ulf` file starts with `<?xml` which is not
valid base64, so `auto` falls through to raw automatically.

**Do NOT base64-encode the `.ulf` when setting the secret.** Pipe the raw file
directly (as shown above). Base64-encoding is NOT required and is incompatible
with the GameCI-style activation path.

```bash
# CORRECT — raw file contents
gh secret set UNITY_LICENSE --repo Cuvara/NDCUnityTemplate < Unity_lic.ulf

# WRONG — do not base64-encode
gh secret set UNITY_LICENSE --repo Cuvara/NDCUnityTemplate \
  <<< "$(base64 Unity_lic.ulf)"
```

---

## Troubleshooting

### Failure classification

`activate-license.sh` classifies failures by scanning `Editor.log` for known
patterns. The classified failure name appears in CI logs as:
```
[ERROR] activate-license: License activation failed (personal-combined): <CLASS>
```

### Troubleshooting table

| Symptom / Log String | Cause | Fix |
|---|---|---|
| `TimeStamp validation failed` | `UNITY_LICENSE` set but `UNITY_EMAIL`/`UNITY_PASSWORD` missing — `.ulf` alone is machine-bound | Set all three secrets; use `personal-combined` strategy |
| `0 entitlements` | `UNITY_EMAIL`/`UNITY_PASSWORD` set but `UNITY_LICENSE` missing — no seat entitlement without `.ulf` | Add `UNITY_LICENSE` secret (raw `.ulf` contents) |
| `LICENSE_FILE_INVALID` / `license.*invalid` in log | `UNITY_LICENSE` contains invalid XML, wrong file type, or empty value | Re-export the `.ulf` from Unity Hub; verify `find` command above finds a non-empty file |
| `UNITY_LICENSE contains UnityEntitlementLicense XML (not a .ulf)` | A `.xml` entitlement file was set instead of a `.ulf` | Use the `.ulf` file, **not** `UnityEntitlementLicense.xml` |
| `UNITY_LICENSE decoded to empty file` | Secret is set to an empty string or whitespace | Delete the secret and re-set it: `gh secret set UNITY_LICENSE --repo Cuvara/NDCUnityTemplate < Unity_lic.ulf` |
| `UNITY_LICENSE accidentally base64-encoded` | `UNITY_LICENSE_ENCODING` forced to `base64` but value is raw, or vice-versa | Remove `UNITY_LICENSE_ENCODING` override; set secret as raw `.ulf` file |
| `AUTH_FAILED` / `invalid.*password\|auth.*fail` in log | Wrong email or password in secrets | Re-set `UNITY_EMAIL` / `UNITY_PASSWORD`; confirm credentials work at id.unity.com |
| `MFA_OR_2FA_REQUIRED` / `two.factor\|2fa` in log | The Unity account has MFA/2FA enabled | Disable 2FA on the CI Unity account, or use a dedicated CI account without 2FA |
| `ACTIVATION_LIMIT_REACHED` / `activation.*limit` in log | Maximum number of activations for this license exceeded | Return a seat in Unity Hub (Manage License → Return License) then retry |
| `PERSONAL_LICENSE_ONLINE_ACTIVATION_UNSUPPORTED` | Certain Unity versions block online Personal activation | Use the `UNITY_LICENSE` `.ulf` (all three secrets) to avoid online-only activation |
| `UNITY_SERVICE_UNAVAILABLE` / `service.*unavailable\|connect.*fail\|timeout` | Unity license server temporarily unreachable | Retry the workflow; check status.unity.com |
| `No valid Unity license found` in log at build step | Activation succeeded but the license file was cleaned up, or activation was skipped | Verify all three secrets are set and non-empty; check activation step log |
| Wrong Unity version image | Docker image built for a different Unity version than `ProjectVersion.txt` | Rebuild images for the correct version — see [UNITY\_VERSION\_UPGRADE.md](UNITY_VERSION_UPGRADE.md) |
| `TimeStamp validation failed` **with all three secrets set**, on self-hosted Windows `docker run` only | This toolkit's `personal-combined` strategy writes the raw `.ulf` into the container's license dir and activates with email/password only — unlike GameCI, it never extracts the `.ulf`'s embedded serial or randomizes the container's machine id, so Unity tries to refresh the stale machine-bound `.ulf` instead of performing a fresh activation. See [GitHub-hosted vs Self-hosted Activation — Root Cause](#github-hosted-vs-self-hosted-activation--root-cause). | Not yet fixed in this toolkit; the same failure reproduces on GitHub-hosted docker if you bypass GameCI and use this script directly. Workaround: use `runner-type=github-hosted` with `build-engine=docker`, or a self-hosted **local** (non-Docker) preactivated Unity install. |

### What NOT to do

- Do **not** use `UnityEntitlementLicense.xml` as `UNITY_LICENSE`.
- Do **not** pass a `.alf` (activation request file) as `UNITY_LICENSE`.
- Do **not** print or log secret values when debugging.

> Note: earlier revisions of this document said "Personal licenses do not
> have a serial number" and told users not to set `UNITY_SERIAL`. That is
> misleading — a Unity Personal `.ulf` **does** embed a serial (prefixed
> `F`) in its `<DeveloperData>` node, and GameCI's actual activation method
> depends on extracting and using it (see the root-cause section above).
> `UNITY_SERIAL` as a *secret* is still unnecessary for Personal on the
> GitHub-hosted lane (GameCI derives it from `UNITY_LICENSE` automatically);
> this note is only to correct the "no serial exists" claim, not to suggest
> setting `UNITY_SERIAL` manually.

---

## Supported Activation Matrix

| Runner / engine | Activation method | Works? | Notes |
|---|---|:---:|---|
| GitHub-hosted + `docker` (`game-ci/unity-builder@v4`) | GameCI-internal: serial extracted from `.ulf` + machine-id randomization + `unity-editor -serial -username -password` | ✅ | Verified: run `29230778434` — `Activation successful`. This is the only path in this repo confirmed working for Unity Personal in Docker. **Do not modify** (per team convention; game-ci owns this internally). |
| Self-hosted Windows + `docker run` (native, `unityci/editor` image) | This toolkit's `personal-combined` (`activate-license.sh`): raw `.ulf` write + `-username`/`-password`, no serial, no machine-id reset | ❌ | Verified failing: run `29226308795` — `TimeStamp validation failed` / `0 entitlements`. Root cause above. Consolidating the self-hosted step onto `activate-license.sh` (commit `9c79882`) fixed code duplication but not the underlying activation logic — same failure mode expected until the fix in "Recommended fix" above is applied. |
| Self-hosted Windows + `local` (Unity Hub install, no Docker) | Host-machine Unity Hub license (pre-activated interactively, or `.ulf` copied to `C:\ProgramData\Unity\Unity_lic.ulf` matching *that specific* Windows machine's id) | ✅ (if pre-activated) | Not machine-bound-mismatch-prone because the `.ulf` and the runner are the *same, persistent* machine — no ephemeral container identity to reconcile. Requires one-time interactive `unity-editor -quit -batchmode -username ... -password ...` (or Unity Hub GUI) directly on `ndc-win-runner` once; subsequent CI runs reuse the resulting license via the `preactivated` strategy in `activate-license.sh` / `resolve_activation_strategy.sh`. |
