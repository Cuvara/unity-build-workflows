# Discord Build Delivery — Design Plan

> **Status:** DESIGN — published by workflow-architect for implementation engineers
> **Branch:** `feature/explicit-platform-jobs`
> **Date:** 2026-06-30

Related docs:
- [EXPLICIT_PLATFORM_FLOW_SPEC.md](EXPLICIT_PLATFORM_FLOW_SPEC.md) — job graph + reusable contracts
- [BRANCH_FLOW_CONTRACT.md](BRANCH_FLOW_CONTRACT.md) — flow resolver outputs (flow-type, environment, etc.)

---

## 0 — Summary of Decisions

| Question | Decision |
|---|---|
| Extend `discord-notify` or new action? | **New action** `discord-upload-build` — multipart/form-data curl is fundamentally different from the JSON-only embed path |
| Thread support | `?thread_id=<snowflake>` query param (existing thread), plus optional `thread_name` for forum channels |
| DISCORD_THREAD_ID secret vs variable? | **Repository variable** (`vars.DISCORD_THREAD_ID`) — a thread ID is not sensitive |
| Where to hook in consumer? | **New `notify-discord` job** with `if: always()`, sibling to `final-report` |
| Oversize build files (Android ~35MB, Linux64 ~37MB, LinuxServer ~33MB)? | Zip artifact, check size, attach if ≤ threshold, otherwise post **run-artifacts link** |
| Configurable size threshold? | Yes — `attach-size-threshold-mb` input, default `24` (leaves 1 MB headroom below Discord 25 MB non-boosted limit) |
| Multi-platform: one message or many? | **One message** per run — embed table lists all platforms; files that fit are attached; oversized ones get a link field |
| Failure tolerance | The entire `notify-discord` job must NEVER cause pipeline failure — every step uses `|| true`; webhook missing = silent skip |

---

## 1 — Files Created / Changed

### Toolkit (`unity-build-workflows`)

| File | Status | Owner |
|---|---|---|
| `.github/actions/discord-upload-build/action.yml` | **CREATE** | discord-delivery-engineer |
| `docs/DISCORD_BUILD_DELIVERY_PLAN.md` | **CREATE** | workflow-architect (this file) |

### Consumer (`NDC-Unity-Template`)

| File | Status | Owner |
|---|---|---|
| `.github/workflows/unity-build.yml` | **ADD `notify-discord` job** | lead |

### Existing files — **do not modify**

| File | Notes |
|---|---|
| `.github/actions/discord-notify/action.yml` | Kept as-is — embed-only notifier; remains available for individual build-step notifications |

---

## 2 — Why a New Action (Not Extending `discord-notify`)

`discord-notify/action.yml` sends a JSON body via:
```bash
curl -H "Content-Type: application/json" --data-binary @- "${DISCORD_WEBHOOK_URL}"
```

Discord file upload requires **multipart/form-data**:
```bash
curl -F "payload_json=<json>" -F "files[0]=@file.zip" "${URL}?thread_id=<id>"
```

These are incompatible curl invocations — the Content-Type changes, flag set changes, and the JSON payload is a form field rather than the body. Adding branching logic to `discord-notify` for two fundamentally different curl modes would make it fragile and hard to test. The new action is purpose-built for post-build file delivery; `discord-notify` stays as the lightweight embed notifier.

---

## 3 — New Action: `discord-upload-build`

**Location:** `.github/actions/discord-upload-build/action.yml` (composite)

### 3.1 Inputs

```yaml
inputs:
  # ── Run context (required) ─────────────────────────────────────────────────
  status:
    description: Overall build status — success | failure | cancelled | partial
    required: true

  flow-type:
    description: Flow type from resolve-config (push-develop, push-release, manual, etc.)
    required: false
    default: unknown

  environment:
    description: Build environment (development | staging | production)
    required: false
    default: development

  run-url:
    description: Full GitHub Actions run URL (https://github.com/<repo>/actions/runs/<id>)
    required: false
    default: ''

  commit-sha:
    description: Full or short commit SHA
    required: false
    default: ''

  branch:
    description: Branch name
    required: false
    default: ''

  unity-version:
    description: Unity version used for this build
    required: false
    default: ''

  # ── Per-platform results (one input per platform) ──────────────────────────
  # All default to 'skipped' so caller only needs to pass platforms that ran.
  result-android:
    description: build-android job result (success|failure|skipped|cancelled|blocked)
    required: false
    default: skipped
  result-webgl:
    description: build-webgl job result
    required: false
    default: skipped
  result-linux64:
    description: build-linux64 job result
    required: false
    default: skipped
  result-linuxserver:
    description: build-linuxserver job result
    required: false
    default: skipped
  result-ios:
    description: build-ios job result
    required: false
    default: skipped
  result-addressables:
    description: build-addressables job result
    required: false
    default: skipped
  result-tests:
    description: unity-tests job result
    required: false
    default: skipped

  # ── Thread routing ─────────────────────────────────────────────────────────
  thread-id:
    description: |
      Discord thread ID (snowflake). When set, the webhook posts into this
      existing thread via ?thread_id=<id>. Not a secret — pass from
      vars.DISCORD_THREAD_ID. Takes precedence over thread-name.
    required: false
    default: ''

  thread-name:
    description: |
      Discord thread name. Used ONLY when thread-id is empty AND the webhook
      targets a forum channel — Discord will create a new thread with this name.
      If thread-id is set, this is ignored.
    required: false
    default: ''

  # ── File attachment ────────────────────────────────────────────────────────
  artifact-dir:
    description: |
      Local directory containing downloaded build artifacts.
      Expected layout: <artifact-dir>/unity-build-<Platform>/ (one subdir per artifact,
      as produced by actions/download-artifact@v4 with merge-multiple: false).
    required: false
    default: ./artifacts

  attach-size-threshold-mb:
    description: |
      Maximum size (MB) of a ZIPPED artifact before switching to link-only.
      Default 24 to stay safely under the Discord 25 MB non-boosted server limit.
      Multiple attachments are checked cumulatively — if adding a file would push
      the total over the threshold, that file and all subsequent ones become links.
    required: false
    default: '24'
```

### 3.2 Secrets

The action reads `DISCORD_WEBHOOK_URL` from the **environment** (not an input), consistent with `discord-notify`. The caller sets it via `env:` on the step or the job:

```yaml
env:
  DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
```

No additional secrets. `DISCORD_THREAD_ID` is passed as the `thread-id` input from `vars.DISCORD_THREAD_ID` — it is not a secret.

### 3.3 Outputs

None. The action is fire-and-forget; errors are warnings only.

### 3.4 Internal Logic (Composite Steps)

#### Step 1 — Guard: webhook present?
```bash
if [ -z "${DISCORD_WEBHOOK_URL:-}" ]; then
  echo "::notice::DISCORD_WEBHOOK_URL not set — Discord delivery skipped."
  exit 0
fi
echo "::add-mask::${DISCORD_WEBHOOK_URL}"
```

#### Step 2 — Build webhook URL with thread routing
```bash
WEBHOOK_URL="${DISCORD_WEBHOOK_URL}"
if [ -n "${THREAD_ID}" ]; then
  WEBHOOK_URL="${WEBHOOK_URL}?thread_id=${THREAD_ID}"
fi
# thread_name is injected into the JSON payload (Step 4), not the URL
```

#### Step 3 — Collect and size-check artifacts

For each platform in `[Android, WebGL, Linux64, LinuxServer, iOS, Addressables]`:

```bash
ARTIFACT_DIR="<artifact-dir>/unity-build-<Platform>"
if [ -d "${ARTIFACT_DIR}" ] && [ "${RESULT}" = "success" ]; then
  # Zip the artifact directory
  ZIP_PATH="/tmp/unity-build-<Platform>.zip"
  zip -qr "${ZIP_PATH}" "${ARTIFACT_DIR}" 2>/dev/null || true
  ZIP_SIZE_MB=$(du -m "${ZIP_PATH}" 2>/dev/null | awk '{print $1}')
  # Cumulative size check
  if [ $(( CUMULATIVE_MB + ZIP_SIZE_MB )) -le THRESHOLD_MB ]; then
    CUMULATIVE_MB=$(( CUMULATIVE_MB + ZIP_SIZE_MB ))
    # Mark as ATTACH → add to files[] list
  else
    # Mark as LINK → embed field includes run URL
    rm -f "${ZIP_PATH}"
  fi
else
  # Mark as LINK (no artifact downloaded) or N/A (skipped)
fi
```

**Size decision table (known approximate sizes):**

| Platform | Raw size | Typical zip | Decision at 24MB threshold |
|---|---|---|---|
| Android | ~35 MB | ~34 MB (APK already compressed) | **Link** |
| WebGL | ~13 MB | ~4–6 MB | **Attach** |
| Linux64 | ~37 MB | ~22–28 MB | **Link** (zip may be borderline; check at runtime) |
| LinuxServer | ~33 MB | ~20–26 MB | **Link** (same) |
| iOS | N/A (blocked) | — | **Link / N/A** |
| Addressables | small | small | **Attach** |

The threshold check is runtime (after actual zipping) — the table above is informational. The cumulative cap means WebGL + Addressables together will likely fit; adding Linux64 zip would push over.

#### Step 4 — Build JSON payload (via python3)

```python
import json, os

# Status → color + emoji
color_map = {
  'success': 3066993,    # green
  'failure': 15158332,   # red
  'cancelled': 9807270,  # grey
  'partial': 16776960,   # yellow (some success, some failure)
}
emoji_map = {
  'success': '✅', 'failure': '❌',
  'cancelled': '🚫', 'partial': '⚠️', 'skipped': '⏭️', 'blocked': '⛔',
}

# Title
title = f"{emoji_map.get(status, '❓')} Unity Build {status.capitalize()} — {environment} / {flow_type}"

# Per-platform field rows (inline=False for table readability)
platform_rows = ""
for plat, result, delivery in PLATFORMS:
  e = emoji_map.get(result, '❓')
  delivery_str = f"📎 attached" if delivery == "attach" else f"[⬇ download]({run_url}) *(login required)*"
  if result in ('skipped', 'blocked', 'cancelled'):
    delivery_str = "—"
  platform_rows += f"{e} **{plat}**: `{result}` {delivery_str}\n"

fields = [
  {"name": "Branch",        "value": branch,        "inline": True},
  {"name": "Environment",   "value": environment,   "inline": True},
  {"name": "Flow",          "value": flow_type,     "inline": True},
  {"name": "Unity",         "value": unity_version, "inline": True},
  {"name": "Commit",        "value": short_sha,     "inline": True},
  {"name": "Platforms",     "value": platform_rows, "inline": False},
]

embed = {
  "title": title,
  "color": color_map.get(status, 9807270),
  "fields": fields,
  "url": run_url,
}

payload = {"embeds": [embed]}

# thread_name: only include when thread_id is absent (forum-channel new-thread path)
if thread_name and not thread_id:
  payload["thread_name"] = thread_name

# attachment metadata for files that will be attached
for idx, (plat, zip_path) in enumerate(files_to_attach):
  payload.setdefault("attachments", []).append({
    "id": idx,
    "filename": os.path.basename(zip_path),
    "description": f"Unity {plat} build",
  })

print(json.dumps(payload))
```

#### Step 5 — Send via curl (multipart or JSON)

**Case A — Files to attach (multipart/form-data):**
```bash
CURL_ARGS=(
  --silent --show-error
  --max-time 30 --retry 2 --retry-delay 3
  -F "payload_json=${PAYLOAD}"
)
for idx in $(seq 0 $((N_FILES - 1))); do
  CURL_ARGS+=(-F "files[${idx}]=@${ZIP_PATHS[$idx]}")
done

curl "${CURL_ARGS[@]}" "${WEBHOOK_URL}" \
  --output "${_RESP_FILE}" \
  --write-out "%{http_code}" > "${_CODE_FILE}" 2>"${_ERR_FILE}" || true
```

**Case B — No files (JSON only):**
```bash
echo "${PAYLOAD}" | \
  curl --silent --show-error \
    --max-time 30 --retry 2 --retry-delay 3 \
    -H "Content-Type: application/json" \
    --data-binary @- \
    --output "${_RESP_FILE}" \
    --write-out "%{http_code}" \
    "${WEBHOOK_URL}" > "${_CODE_FILE}" 2>"${_ERR_FILE}" || true
```

Note: do NOT set `Content-Type: application/json` for multipart — curl sets it automatically with the correct `boundary`.

#### Step 6 — Result logging
```bash
HTTP_CODE=$(cat "${_CODE_FILE}" || echo "000")
case "${HTTP_CODE}" in
  2*)  echo "Discord build delivery sent (HTTP ${HTTP_CODE})." ;;
  000) echo "::warning::Discord delivery: curl failed — pipeline unaffected." ;;
  *)   echo "::warning::Discord delivery: HTTP ${HTTP_CODE} — pipeline unaffected." ;;
esac
```

Cleanup temp zip files and response files with `rm -f`.

---

## 4 — Consumer Job Wiring

### 4.1 `notify-discord` job (add to `unity-build.yml`)

```yaml
notify-discord:
  name: Notify Discord
  needs:
    - resolve-config
    - validate-project
    - unity-tests
    - build-addressables
    - build-android
    - build-webgl
    - build-linux64
    - build-linuxserver
    - build-ios
    - final-report
  if: always()
  runs-on: ubuntu-latest
  steps:
    # ── Download all build artifacts ────────────────────────────────────────
    # pattern matches: unity-build-Android, unity-build-WebGL, unity-build-Linux64,
    # unity-build-LinuxServer, unity-build-iOS, unity-build-Addressables.
    # Deliberately excludes unity-build-*-logs (too large, not useful in Discord).
    # Non-existent artifacts (skipped platforms) are silently ignored.
    - name: Download build artifacts
      uses: actions/download-artifact@v4
      continue-on-error: true        # missing artifacts must not fail the job
      with:
        pattern: 'unity-build-[!*-logs]*'   # exclude -logs variants
        path: ./artifacts
        merge-multiple: false        # each artifact in its own subdir

    # ── Determine overall status ────────────────────────────────────────────
    - name: Derive overall status
      id: status
      shell: bash
      env:
        R_ANDROID:     ${{ needs.build-android.result }}
        R_WEBGL:       ${{ needs.build-webgl.result }}
        R_LINUX64:     ${{ needs.build-linux64.result }}
        R_LINUXSERVER: ${{ needs.build-linuxserver.result }}
        R_IOS:         ${{ needs.build-ios.result }}
        R_TESTS:       ${{ needs.unity-tests.result }}
        R_ADDR:        ${{ needs.build-addressables.result }}
        R_VALIDATE:    ${{ needs.validate-project.result }}
      run: |
        OVERALL="success"
        ANY_SUCCESS=0
        for r in "${R_ANDROID}" "${R_WEBGL}" "${R_LINUX64}" "${R_LINUXSERVER}"; do
          [ "${r}" = "failure" ] && OVERALL="failure"
          [ "${r}" = "success" ] && ANY_SUCCESS=1
        done
        [ "${R_VALIDATE}" = "failure" ] && OVERALL="failure"
        # partial: at least one success and at least one failure
        if [ "${OVERALL}" = "failure" ] && [ "${ANY_SUCCESS}" = "1" ]; then
          OVERALL="partial"
        fi
        echo "overall=${OVERALL}" >> "$GITHUB_OUTPUT"

    # ── Post to Discord thread ───────────────────────────────────────────────
    - name: Post Discord build delivery
      uses: Cuvara/unity-build-workflows/.github/actions/discord-upload-build@main
      env:
        DISCORD_WEBHOOK_URL: ${{ secrets.DISCORD_WEBHOOK_URL }}
      with:
        status:          ${{ steps.status.outputs.overall }}
        flow-type:       ${{ needs.resolve-config.outputs.flow-type }}
        environment:     ${{ needs.resolve-config.outputs.environment }}
        run-url:         ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
        commit-sha:      ${{ github.sha }}
        branch:          ${{ github.ref_name }}
        unity-version:   ${{ needs.resolve-config.outputs.unity-version }}
        result-android:     ${{ needs.build-android.result }}
        result-webgl:       ${{ needs.build-webgl.result }}
        result-linux64:     ${{ needs.build-linux64.result }}
        result-linuxserver: ${{ needs.build-linuxserver.result }}
        result-ios:         ${{ needs.build-ios.result }}
        result-addressables: ${{ needs.build-addressables.result }}
        result-tests:       ${{ needs.unity-tests.result }}
        thread-id:       ${{ vars.DISCORD_THREAD_ID }}
        artifact-dir:    ./artifacts
        attach-size-threshold-mb: '24'
```

### 4.2 `needs` rationale

`notify-discord` needs `final-report` in addition to the build jobs because:
- `final-report` may run in parallel with notification delivery, which is fine.
- Including it ensures `notify-discord` is the **last job** in the graph and doesn't block anything else.
- If `final-report` fails (a core build failed), `notify-discord` still runs (the `if: always()` propagates).

### 4.3 Artifact download pattern note

`actions/download-artifact@v4` with `pattern: 'unity-build-[!*-logs]*'` (shell glob):
- Matches: `unity-build-Android`, `unity-build-WebGL`, `unity-build-Linux64`, `unity-build-LinuxServer`, `unity-build-iOS`, `unity-build-Addressables`
- Excludes: `unity-build-Android-logs`, `unity-build-WebGL-logs`, etc.

**Caveat:** GitHub Actions glob support in `download-artifact@v4` uses minimatch, not bash glob. Use `pattern: 'unity-build-*'` and let the action script filter out `-logs` variants:
```bash
for dir in ./artifacts/unity-build-*; do
  [[ "${dir}" == *"-logs" ]] && continue
  # process...
done
```

---

## 5 — Thread Routing Details

### Existing thread (`thread_id`)
```
POST https://discord.com/api/webhooks/<id>/<token>?thread_id=<snowflake>
```
The post appears inside the existing thread. The thread snowflake is visible in Discord by right-clicking a thread → "Copy Thread ID" (developer mode on).

**Configuration:** Set `vars.DISCORD_THREAD_ID` in the consumer repo (Settings → Variables → Actions). It is NOT a secret.

### New thread in forum channel (`thread_name`)
```json
{
  "thread_name": "Build #42 — release-1.0.0",
  "embeds": [...]
}
```
Works only when the webhook targets a Discord forum channel. If the webhook targets a text/announcement channel, `thread_name` is silently ignored by Discord (the post appears in the channel root).

### Priority
If both `thread-id` and `thread-name` are provided, `thread-id` wins (URL param routing takes precedence over JSON body).

### Missing thread ID
If `thread-id` is empty and `thread-name` is empty, the post goes to the webhook channel root (no thread routing). This is acceptable — delivery still works, just not threaded.

---

## 6 — Artifact Link Format

For oversized artifacts, the embed field shows:

```
⬇ [Download from GitHub Actions](https://github.com/<repo>/actions/runs/<run_id>)
⚠️ GitHub login required to download artifacts.
```

The run URL (`https://github.com/<GITHUB_REPOSITORY>/actions/runs/<GITHUB_RUN_ID>`) lists all artifacts on the Artifacts section of the run summary page. There is no public unauthenticated artifact download URL — **GitHub always requires login**. The embed must state this limitation explicitly to set expectations.

Do **not** use the internal artifact download API URL (`https://api.github.com/repos/.../artifacts/<id>/zip`) — it also requires a GitHub token and changes per run. The run summary URL is stable and human-friendly.

---

## 7 — `discord-upload-build` Full Step-by-Step Specification

```
Step 1:  Guard — DISCORD_WEBHOOK_URL set? No → notice + exit 0
Step 2:  add-mask the webhook URL
Step 3:  Build WEBHOOK_URL string (append ?thread_id= if thread-id input set)
Step 4:  Discover artifact directories under artifact-dir/
         For each platform [Android, WebGL, Linux64, LinuxServer, iOS, Addressables]:
           dir = artifact-dir/unity-build-<Platform>
           if dir exists AND result == 'success':
             zip -qr /tmp/unity-build-<Platform>.zip <dir>
             zip_mb = du -m | awk '{print $1}'
             if cumulative_mb + zip_mb <= threshold_mb:
               cumulative_mb += zip_mb
               push to FILES_TO_ATTACH list
             else:
               mark as LINK; rm -f zip
           else:
             mark as LINK or N/A depending on result
Step 5:  Build JSON payload via python3 (embed + attachments metadata + optional thread_name)
Step 6:  Validate JSON: python3 -c "import json,sys; json.load(sys.stdin)" || exit 0 (warning)
Step 7:  Send curl:
           if FILES_TO_ATTACH non-empty: multipart -F payload_json + -F files[N]
           else: -H Content-Type:application/json --data-binary @-
Step 8:  Log HTTP result (warning only, never fail)
Step 9:  Cleanup temp files (rm -f /tmp/unity-build-*.zip; rm -f resp/code/err tmp files)
```

All steps wrapped in `|| true` or `set +e` so no step can exit non-zero.

---

## 8 — Failure Tolerance Design

| Failure scenario | Behaviour |
|---|---|
| `DISCORD_WEBHOOK_URL` not set | `::notice::` + exit 0 (silent skip) |
| `thread-id` invalid / thread not found | Discord returns 404; logged as `::warning::` |
| Artifact dir missing (platform was skipped) | Skipped silently; platform marked LINK |
| Zip fails (disk full, permission) | `|| true`; platform marked LINK |
| JSON payload invalid | `::warning::` + exit 0 (skip send) |
| curl network timeout | `--retry 2` then `::warning::` + continue |
| Discord HTTP error (429 rate-limit, 5xx) | Logged as `::warning::` — no retry on 429 |
| `notify-discord` job itself crashes | Job exits 0 (all steps use `|| true`); annotated as warning |

**Critical:** The `notify-discord` job must NEVER be the reason a pipeline shows a red ❌. If the Discord delivery fails, the build is still valid. The job exit code must always be 0.

---

## 9 — GitHub Repository Variables

### Required (for Discord routing)

| Variable | Where to set | Value example | Notes |
|---|---|---|---|
| `DISCORD_THREAD_ID` | Repo Variables (`vars.*`) | `1234567890123456789` | Discord thread snowflake. Right-click thread → Copy Thread ID (developer mode). Not a secret. |

### Existing secret (unchanged)

| Secret | Where to set |
|---|---|
| `DISCORD_WEBHOOK_URL` | Repo Secrets (`secrets.*`) | Discord webhook URL — IS a secret |

### How to get thread ID
1. Enable Developer Mode in Discord: User Settings → Advanced → Developer Mode
2. Right-click the target thread → "Copy Thread ID"
3. Set: `gh variable set DISCORD_THREAD_ID --repo Cuvara/NDCUnityTemplate --body "<snowflake>"`

---

## 10 — Test Plan

### 10.1 Static unit tests — `tests/test_discord_upload_build.py`

All tests run without network access, without Discord credentials, without GitHub Actions context. Tests call the Python payload-building logic directly (extract it to a `build_discord_payload.py` helper that `action.yml` imports).

| ID | Test | Assertion |
|---|---|---|
| D1 | All platforms success, all fit under threshold | Payload has `attachments` with all fitting files; embed `Platforms` field shows `📎 attached` for each |
| D2 | Android fails, WebGL success, fits | Android shows `❌` + `—` delivery; WebGL shows `📎 attached` |
| D3 | All platforms skipped | No attachments; embed shows `⏭️` for all; status = `success` (nothing failed) |
| D4 | Android success (~35MB zip), threshold 24MB | Android marked LINK; embed field shows download URL with "login required" note |
| D5 | WebGL success (5MB zip), Linux64 success (25MB zip), threshold 24MB cumulative | WebGL attached (5MB); Linux64 triggers cumulative cap → LINK |
| D6 | `thread-id` set | `WEBHOOK_URL` ends with `?thread_id=<value>` |
| D7 | `thread-id` empty, `thread-name` set | Payload JSON contains `"thread_name": <value>`; URL has no query param |
| D8 | `thread-id` set AND `thread-name` set | `thread_id` in URL; `thread_name` NOT in payload |
| D9 | Invalid JSON from payload builder | Validator catches it; function raises / returns empty string |
| D10 | `status=failure`, some succeeded | Overall = `partial`; embed color = yellow |
| D11 | `status=success` | Embed color = green (3066993) |
| D12 | `run-url` empty | Embed has no `url` field; link fields fall back to `(see run)` |
| D13 | Long branch name / commit sha | Short SHA (first 7 chars) in embed; branch truncated if > 50 chars |
| D14 | Zip size exactly at threshold (24MB) | Attached (≤ threshold, inclusive) |
| D15 | Zip size = threshold + 1 byte | LINK (> threshold) |

### 10.2 Static unit tests — size-threshold shell logic

Extract the attach-vs-link decision to a testable bash function in `scripts/common/discord_size_check.sh`:

```bash
# Returns 0 (attach) or 1 (link)
discord_should_attach() {
  local zip_mb="$1" cumulative_mb_var="$2" threshold_mb="$3"
  local cumulative="${!cumulative_mb_var}"
  if (( cumulative + zip_mb <= threshold_mb )); then
    eval "${cumulative_mb_var}=$(( cumulative + zip_mb ))"
    return 0
  fi
  return 1
}
```

Test via `tests/test_discord_size_check.sh` (bash bats or simple assert):
- 5 + 10 ≤ 24 → attach both
- 5 + 20 ≤ 24 → attach both; 5 + 20 + 1 → third is link
- 0 ≤ 24 → attach (empty file edge case)
- 25 > 24 → link (first file already over)

### 10.3 Integration smoke test (manual, CI optional)

Add a `workflow_dispatch`-only workflow `tests/discord-delivery-smoke.yml` (toolkit):
- Creates a dummy 1KB file as a fake artifact
- Calls `discord-upload-build` action with `status=success`, `DISCORD_TEST_THREAD_ID` variable
- Checks that curl exits 0 (no network assertions — just verifies the action doesn't crash)

---

## 11 — Discord Message Format (Reference)

### Embed layout
```
┌─────────────────────────────────────────────────────────┐
│ ✅ Unity Build Success — production / push-release       │
│                                                         │
│ Branch      Environment   Flow                          │
│ release-1.0 production    push-release                  │
│                                                         │
│ Unity       Commit        (inline field)                │
│ 6000.0.26f1 a1b2c3d                                     │
│                                                         │
│ Platforms                                               │
│ ✅ Android: `success` ⬇ Download from GitHub Actions    │
│             ⚠️ GitHub login required                     │
│ ✅ WebGL: `success` 📎 attached                         │
│ ✅ Linux64: `success` ⬇ Download from GitHub Actions    │
│ ✅ LinuxServer: `success` ⬇ Download from GitHub Actions│
│ ⏭️ iOS: `skipped` —                                     │
│ ⏭️ Addressables: `skipped` —                            │
└─────────────────────────────────────────────────────────┘
📎 unity-build-WebGL.zip (5.2 MB)
```

### Status → color mapping
| Status | Color (decimal) | Hex |
|---|---|---|
| success | 3066993 | `#2ECC71` (green) |
| failure | 15158332 | `#E74C3C` (red) |
| partial | 16776960 | `#FFFF00` (yellow) |
| cancelled | 9807270 | `#95A5A6` (grey) |

---

## 12 — Implementation Checklist (for discord-delivery-engineer)

- [ ] Create `.github/actions/discord-upload-build/action.yml` per spec §3
- [ ] Extract Python payload logic to a testable helper (or inline with `python3 -c`)
- [ ] Extract size-check logic to `scripts/common/discord_size_check.sh` (optional but testable)
- [ ] Add `tests/test_discord_upload_build.py` (§10.1)
- [ ] Add `tests/test_discord_size_check.sh` or `.py` (§10.2)
- [ ] Add `notify-discord` job to consumer `unity-build.yml` per §4
- [ ] Set `vars.DISCORD_THREAD_ID` in the consumer repo
- [ ] Smoke test: trigger a manual dispatch, verify Discord thread receives the message
- [ ] Document: update `GITHUB_ACTIONS_BUILD_RUNBOOK.md` with the new variable + behaviour
