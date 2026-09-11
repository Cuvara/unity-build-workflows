#!/usr/bin/env bash
# validate_ipa.sh
# Stage 04 — ARTIFACT VALIDATION for iOS.
#
# Validates an .ipa file structure, code signature, embedded provisioning
# profile and Info.plist identity.
#
# Usage: validate_ipa.sh <ipa-path>   OR   set IPA_PATH
#
# Optional expectations (env) — empty means "do not check":
#   EXPECTED_BUNDLE_ID       fail unless CFBundleIdentifier matches
#   EXPECTED_VERSION         fail unless CFBundleShortVersionString matches
#   EXPECTED_BUILD_NUMBER    fail unless CFBundleVersion matches
#   MIN_SIZE_BYTES           floor for the .ipa size (default 1000000)
#   MAX_SIZE_MB              ceiling for the .ipa size (0 = disabled)
#   REQUIRE_SIGNED           "true" fails an unsigned/unverifiable bundle
#
# Portability: `codesign` and `defaults` exist only on macOS. On Linux the
# structural, plist and provisioning checks still run (Info.plist is read with
# python3 plistlib) and the signature check degrades to "embedded.mobileprovision
# is present", which is reported honestly rather than claimed as a codesign pass.
#
# Outputs (when $GITHUB_OUTPUT is set): bundle-id, bundle-version, build-number,
# signed, size-bytes.
set -euo pipefail

IPA_PATH="${1:-${IPA_PATH:-}}"
: "${IPA_PATH:?Usage: validate_ipa.sh <ipa-path>}"

MIN_SIZE_BYTES="${MIN_SIZE_BYTES:-1000000}"
MAX_SIZE_MB="${MAX_SIZE_MB:-0}"
REQUIRE_SIGNED="${REQUIRE_SIGNED:-false}"

FAILURES=0
fail() {
  echo "::error::[validate_ipa] $*" >&2
  FAILURES=$((FAILURES + 1))
}
pass() { echo "[validate_ipa] OK   — $*"; }
warn() { echo "[validate_ipa] WARN — $*"; }

if [[ ! -f "${IPA_PATH}" ]]; then
  echo "::error::IPA not found: ${IPA_PATH}" >&2
  exit 1
fi

echo "[validate_ipa] Validating: ${IPA_PATH}"

# ── Size ────────────────────────────────────────────────────────────────────
SIZE_BYTES=$(stat -c%s "${IPA_PATH}" 2>/dev/null || stat -f%z "${IPA_PATH}")
SIZE_MB=$(( SIZE_BYTES / 1024 / 1024 ))
if [[ "${SIZE_BYTES}" -lt "${MIN_SIZE_BYTES}" ]]; then
  fail "IPA is ${SIZE_BYTES} bytes, below the ${MIN_SIZE_BYTES}-byte floor — truncated build"
elif [[ "${MAX_SIZE_MB}" != "0" && "${SIZE_MB}" -gt "${MAX_SIZE_MB}" ]]; then
  fail "IPA is ${SIZE_MB} MB, over the ${MAX_SIZE_MB} MB ceiling"
else
  pass "size ${SIZE_MB} MB (${SIZE_BYTES} bytes)"
fi

# ── Archive ─────────────────────────────────────────────────────────────────
if ! unzip -t "${IPA_PATH}" > /dev/null 2>&1; then
  echo "::error::IPA is not a valid zip archive" >&2
  exit 1
fi
pass "readable zip archive"

INSPECT_DIR="${RUNNER_TEMP:-/tmp}/ipa-inspect-$$"
mkdir -p "${INSPECT_DIR}"
# shellcheck disable=SC2064
trap "rm -rf '${INSPECT_DIR}'" EXIT

unzip -q "${IPA_PATH}" -d "${INSPECT_DIR}"

APP_PATH=$(find "${INSPECT_DIR}/Payload" -maxdepth 1 -name "*.app" 2>/dev/null | head -1 || true)
if [[ -z "${APP_PATH}" ]]; then
  echo "::error::No .app bundle found inside IPA Payload/" >&2
  exit 1
fi
pass "app bundle: $(basename "${APP_PATH}")"

# ── Code signature ──────────────────────────────────────────────────────────
SIGNED=false
if command -v codesign > /dev/null 2>&1; then
  if codesign --verify --verbose=2 "${APP_PATH}" 2>&1; then
    SIGNED=true
    pass "codesign verification passed"
  else
    fail "codesign verification failed for: ${APP_PATH}"
  fi
elif [[ -f "${APP_PATH}/embedded.mobileprovision" ]]; then
  # No codesign on this host — report what can actually be established.
  SIGNED=true
  warn "codesign unavailable (non-macOS host); embedded.mobileprovision present, signature not cryptographically verified"
else
  if [[ "${REQUIRE_SIGNED}" == "true" ]]; then
    fail "no embedded.mobileprovision and codesign unavailable — bundle is not signed"
  else
    warn "no embedded.mobileprovision and codesign unavailable — signing state unknown"
  fi
fi

# ── Provisioning profile ────────────────────────────────────────────────────
if [[ -f "${APP_PATH}/embedded.mobileprovision" ]]; then
  pass "embedded provisioning profile present"
  # The profile is CMS-wrapped; the plist inside is plain text, so pull the
  # expiry without needing `security cms` (macOS-only).
  PROFILE_EXPIRY=$(strings "${APP_PATH}/embedded.mobileprovision" 2>/dev/null \
    | grep -A1 'ExpirationDate' | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]+Z' | head -1 || true)
  if [[ -n "${PROFILE_EXPIRY}" ]]; then
    NOW_EPOCH=$(date -u +%s)
    EXP_EPOCH=$(date -u -d "${PROFILE_EXPIRY}" +%s 2>/dev/null \
      || date -u -j -f "%Y-%m-%dT%H:%M:%SZ" "${PROFILE_EXPIRY}" +%s 2>/dev/null || echo 0)
    if [[ "${EXP_EPOCH}" -gt 0 && "${EXP_EPOCH}" -lt "${NOW_EPOCH}" ]]; then
      fail "provisioning profile expired on ${PROFILE_EXPIRY}"
    else
      pass "provisioning profile valid until ${PROFILE_EXPIRY}"
    fi
  fi
elif [[ "${REQUIRE_SIGNED}" == "true" ]]; then
  fail "embedded.mobileprovision missing — IPA is not distributable"
fi

# ── Info.plist identity ─────────────────────────────────────────────────────
INFO_PLIST="${APP_PATH}/Info.plist"
if [[ ! -f "${INFO_PLIST}" ]]; then
  echo "::error::Info.plist not found in .app bundle" >&2
  exit 1
fi

read_plist() { # key
  if command -v defaults > /dev/null 2>&1; then
    defaults read "${INFO_PLIST%.plist}" "$1" 2>/dev/null && return 0
  fi
  if command -v plutil > /dev/null 2>&1; then
    plutil -extract "$1" raw -o - "${INFO_PLIST}" 2>/dev/null && return 0
  fi
  python3 - "$1" "${INFO_PLIST}" <<'PY' 2>/dev/null || echo "unknown"
import plistlib, sys
key, path = sys.argv[1], sys.argv[2]
try:
    with open(path, "rb") as fh:
        print(plistlib.load(fh).get(key, "unknown"))
except Exception:
    print("unknown")
PY
}

BUNDLE_ID=$(read_plist CFBundleIdentifier | tail -1)
BUNDLE_VERSION=$(read_plist CFBundleShortVersionString | tail -1)
BUILD_NUMBER=$(read_plist CFBundleVersion | tail -1)

echo "[validate_ipa] Bundle ID:       ${BUNDLE_ID}"
echo "[validate_ipa] Version:         ${BUNDLE_VERSION}"
echo "[validate_ipa] Build number:    ${BUILD_NUMBER}"

check_expected() { # label actual expected
  local label="$1" actual="$2" expected="$3"
  [[ -z "${expected}" ]] && return 0
  if [[ "${actual}" == "unknown" ]]; then
    fail "${label} could not be read from Info.plist but ${expected} was expected"
  elif [[ "${actual}" != "${expected}" ]]; then
    fail "${label} is '${actual}', expected '${expected}'"
  else
    pass "${label} matches ${expected}"
  fi
}
check_expected "bundle id"    "${BUNDLE_ID}"      "${EXPECTED_BUNDLE_ID:-}"
check_expected "version"      "${BUNDLE_VERSION}" "${EXPECTED_VERSION:-}"
check_expected "build number" "${BUILD_NUMBER}"   "${EXPECTED_BUILD_NUMBER:-}"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  printf 'bundle-id=%s\n'      "${BUNDLE_ID}"      >> "${GITHUB_OUTPUT}"
  printf 'bundle-version=%s\n' "${BUNDLE_VERSION}" >> "${GITHUB_OUTPUT}"
  printf 'build-number=%s\n'   "${BUILD_NUMBER}"   >> "${GITHUB_OUTPUT}"
  printf 'signed=%s\n'         "${SIGNED}"         >> "${GITHUB_OUTPUT}"
  printf 'size-bytes=%s\n'     "${SIZE_BYTES}"     >> "${GITHUB_OUTPUT}"
fi

if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
  {
    echo "### iOS Artifact Validation"
    echo ""
    echo "| Field | Value |"
    echo "|---|---|"
    echo "| Bundle ID | \`${BUNDLE_ID}\` |"
    echo "| Version | \`${BUNDLE_VERSION}\` |"
    echo "| Build number | \`${BUILD_NUMBER}\` |"
    echo "| Signed | \`${SIGNED}\` |"
    echo "| Size | \`${SIZE_MB} MB\` |"
    echo "| Result | $([ "${FAILURES}" -eq 0 ] && echo '✅ passed' || echo "❌ ${FAILURES} failed check(s)") |"
    echo ""
  } >> "${GITHUB_STEP_SUMMARY}"
fi

if [[ "${FAILURES}" -gt 0 ]]; then
  echo "::error::[validate_ipa] ${FAILURES} check(s) failed" >&2
  exit 1
fi

echo "[validate_ipa] Validation passed"
