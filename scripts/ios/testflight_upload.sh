#!/usr/bin/env bash
# testflight_upload.sh
# Uploads an IPA to TestFlight using App Store Connect API (xcrun altool).
# GATED — only called when explicitly enabled via workflow input.
# Required env vars (all masked):
#   IPA_PATH                       — path to the .ipa file
#   APP_STORE_CONNECT_KEY_ID       — ASC API key ID
#   APP_STORE_CONNECT_ISSUER_ID    — ASC API issuer ID
#   APP_STORE_CONNECT_PRIVATE_KEY  — ASC API private key (.p8 content)
# Optional:
#   REPORT_PATH            — directory for upload status reports (default: BuildReports/iOS)
#   WAIT_FOR_PROCESSING    — if "true", poll ASC API until build finishes processing
#   ASC_APP_ID             — App Store Connect app ID (required when WAIT_FOR_PROCESSING=true)
set -euo pipefail

IPA_PATH="${IPA_PATH:?IPA_PATH is required}"
KEY_ID="${APP_STORE_CONNECT_KEY_ID:?APP_STORE_CONNECT_KEY_ID is required}"
ISSUER_ID="${APP_STORE_CONNECT_ISSUER_ID:?APP_STORE_CONNECT_ISSUER_ID is required}"
PRIVATE_KEY="${APP_STORE_CONNECT_PRIVATE_KEY:?APP_STORE_CONNECT_PRIVATE_KEY is required}"
REPORT_PATH="${REPORT_PATH:-BuildReports/iOS}"

# Mask secrets before any output
echo "::add-mask::${KEY_ID}"
echo "::add-mask::${ISSUER_ID}"
echo "::add-mask::${PRIVATE_KEY}"

if [[ ! -f "${IPA_PATH}" ]]; then
  echo "::error::IPA not found: ${IPA_PATH}" >&2
  exit 1
fi

# Write API key to a protected temp directory
ASC_API_KEY_DIR="${RUNNER_TEMP:-/tmp}/asc-keys-$$"
mkdir -p "${ASC_API_KEY_DIR}"
chmod 700 "${ASC_API_KEY_DIR}"
export ASC_API_KEY_DIR  # exposed so cleanup_signing.sh can scrub it

ASC_KEY_FILE="${ASC_API_KEY_DIR}/AuthKey_${KEY_ID}.p8"
printf '%s\n' "${PRIVATE_KEY}" > "${ASC_KEY_FILE}"
chmod 600 "${ASC_KEY_FILE}"

mkdir -p "${REPORT_PATH}"

echo "[testflight_upload] Uploading: ${IPA_PATH}"
echo "[testflight_upload] Key ID: [REDACTED]"

UPLOAD_LOG="${RUNNER_TEMP:-/tmp}/testflight-upload-$$.log"

set +e
xcrun altool \
  --upload-app \
  --type ios \
  --file "${IPA_PATH}" \
  --apiKey "${KEY_ID}" \
  --apiIssuer "${ISSUER_ID}" \
  --output-format json \
  2>&1 | tee "${UPLOAD_LOG}"
UPLOAD_EXIT=${PIPESTATUS[0]}
set -e

# Scrub key ID / issuer from log before persisting
sed -i \
  -e "s/${KEY_ID}/[REDACTED_KEY_ID]/g" \
  -e "s/${ISSUER_ID}/[REDACTED_ISSUER_ID]/g" \
  "${UPLOAD_LOG}" 2>/dev/null || true

cp "${UPLOAD_LOG}" "${REPORT_PATH}/testflight-upload.log"

if [[ "${UPLOAD_EXIT}" -ne 0 ]]; then
  echo "[testflight_upload] Upload FAILED (exit ${UPLOAD_EXIT})" >&2
  cat > "${REPORT_PATH}/upload-metadata.json" << EOF
{
  "status": "failed",
  "exit_code": ${UPLOAD_EXIT},
  "ipa_path": "${IPA_PATH}",
  "note": "Upload failed — see testflight-upload.log for details"
}
EOF
  rm -rf "${ASC_API_KEY_DIR}" 2>/dev/null || true
  exit "${UPLOAD_EXIT}"
fi

echo "[testflight_upload] Upload accepted by Apple — processing may take several minutes"
echo "[testflight_upload] NOTE: 'accepted' does NOT mean TestFlight distribution is ready"

cat > "${REPORT_PATH}/upload-metadata.json" << EOF
{
  "status": "upload-accepted",
  "ipa_path": "${IPA_PATH}",
  "note": "Upload accepted by Apple. Check App Store Connect for processing and distribution status."
}
EOF

echo "[testflight_upload] Report written: ${REPORT_PATH}/upload-metadata.json"

# Scrub API key
rm -rf "${ASC_API_KEY_DIR}" 2>/dev/null || true
echo "[testflight_upload] ASC API key scrubbed"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  printf 'upload-status=upload-accepted\n' >> "${GITHUB_OUTPUT}"
fi

# Optional: wait for build processing via ASC API
WAIT_FOR_PROCESSING="${WAIT_FOR_PROCESSING:-false}"
if [[ "${WAIT_FOR_PROCESSING}" == "true" ]]; then
  ASC_APP_ID="${ASC_APP_ID:?ASC_APP_ID is required when WAIT_FOR_PROCESSING=true}"
  # Extract CFBundleVersion from IPA for build version filter
  BUILD_VERSION=""
  if command -v unzip &>/dev/null; then
    PLIST_CONTENT=$(unzip -p "${IPA_PATH}" "Payload/*.app/Info.plist" 2>/dev/null || true)
    if [[ -n "${PLIST_CONTENT}" ]] && command -v plutil &>/dev/null; then
      BUILD_VERSION=$(echo "${PLIST_CONTENT}" | plutil -extract CFBundleVersion raw - 2>/dev/null || true)
    fi
  fi
  if [[ -z "${BUILD_VERSION}" ]]; then
    echo "[testflight_upload] WARNING: Could not extract CFBundleVersion from IPA, using version from metadata" >&2
    BUILD_VERSION=$(python3 -c "import json; print(json.load(open('${REPORT_PATH}/upload-metadata.json')).get('build_version',''))" 2>/dev/null || true)
  fi

  if [[ -n "${BUILD_VERSION}" ]]; then
    echo "[testflight_upload] Waiting for build ${BUILD_VERSION} to finish processing..."
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    export ASC_KEY_ID="${KEY_ID}"
    export ASC_ISSUER_ID="${ISSUER_ID}"
    export ASC_PRIVATE_KEY="${PRIVATE_KEY}"
    python3 "${SCRIPT_DIR}/asc_api.py" wait-for-build \
      --app-id "${ASC_APP_ID}" \
      --build-version "${BUILD_VERSION}" \
      --timeout "${ASC_WAIT_TIMEOUT:-1800}"
  else
    echo "[testflight_upload] WARNING: No build version found, skipping processing wait" >&2
  fi
fi
