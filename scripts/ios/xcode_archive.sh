#!/usr/bin/env bash
# xcode_archive.sh
# Runs xcodebuild archive on the Unity-generated Xcode project.
# Required env vars:
#   XCODE_PROJECT_PATH  — path to the directory Unity generated (contains .xcodeproj or .xcworkspace)
#   DEVELOPMENT_TEAM    — Apple Team ID
#   KEYCHAIN_PATH       — path to the signing keychain
# Optional env vars:
#   SCHEME              — Xcode scheme (default: Unity-iPhone)
#   CONFIGURATION       — Xcode configuration (default: Release)
#   ARCHIVE_PATH        — output .xcarchive path (default: Builds/iOS/Archive/Unity.xcarchive)
#   LOG_PATH            — log file path (default: Logs/iOS/xcode-archive.log)
#   IOS_DEPLOYMENT_TARGET — minimum iOS for every target, Pods included
#                         (default: the Unity-iPhone project's own value)
set -euo pipefail

XCODE_PROJECT_PATH="${XCODE_PROJECT_PATH:?XCODE_PROJECT_PATH is required}"
DEVELOPMENT_TEAM="${DEVELOPMENT_TEAM:?DEVELOPMENT_TEAM is required}"
KEYCHAIN_PATH="${KEYCHAIN_PATH:?KEYCHAIN_PATH is required}"
SCHEME="${SCHEME:-Unity-iPhone}"
CONFIGURATION="${CONFIGURATION:-Release}"
ARCHIVE_PATH="${ARCHIVE_PATH:-Builds/iOS/Archive/Unity.xcarchive}"
LOG_PATH="${LOG_PATH:-Logs/iOS/xcode-archive.log}"

mkdir -p "$(dirname "${ARCHIVE_PATH}")"
mkdir -p "$(dirname "${LOG_PATH}")"

# ── Resolve .xcworkspace vs .xcodeproj ─────────────────────────────────────────
# An array, not a string: the path may contain spaces ("My Game/Unity-iPhone.xcworkspace").
PROJECT_ARGS=()
if [[ -d "${XCODE_PROJECT_PATH}" ]]; then
  WORKSPACE=$(find "${XCODE_PROJECT_PATH}" -maxdepth 1 -name "*.xcworkspace" 2>/dev/null | head -1 || true)
  XCPROJ=$(find "${XCODE_PROJECT_PATH}" -maxdepth 1 -name "*.xcodeproj" 2>/dev/null | head -1 || true)
  if [[ -d "${WORKSPACE}" ]]; then
    PROJECT_ARGS=(-workspace "${WORKSPACE}")
    echo "[xcode_archive] Using workspace: ${WORKSPACE}"
  elif [[ -d "${XCPROJ}" ]]; then
    PROJECT_ARGS=(-project "${XCPROJ}")
    echo "[xcode_archive] Using project: ${XCPROJ}"
  else
    echo "::error::No .xcworkspace or .xcodeproj found in ${XCODE_PROJECT_PATH}" >&2
    exit 1
  fi
elif [[ "${XCODE_PROJECT_PATH}" == *.xcworkspace ]]; then
  PROJECT_ARGS=(-workspace "${XCODE_PROJECT_PATH}")
elif [[ "${XCODE_PROJECT_PATH}" == *.xcodeproj ]]; then
  PROJECT_ARGS=(-project "${XCODE_PROJECT_PATH}")
else
  echo "::error::XCODE_PROJECT_PATH must be a directory, .xcworkspace, or .xcodeproj" >&2
  exit 1
fi

# ── Deployment target for every target, Pods included ─────────────────────────
# Pods keep each pod's own minimum (10.0, 12.0, ...). Current Xcode rejects
# anything below its supported floor as an error, and the Podfile's
# post_install cannot fix it: Unity's resolver runs `pod install` before the
# project's later post-processors. A command-line build setting applies to
# every target in the workspace, so build them all at the app's own minimum,
# which is what each pod has to support anyway.
DEPLOYMENT_TARGET="${IOS_DEPLOYMENT_TARGET:-}"
TARGET_SOURCE="IOS_DEPLOYMENT_TARGET"
PROJECT_ROOT=$(dirname "${PROJECT_ARGS[1]}")
UNITY_XCODEPROJ="${PROJECT_ROOT}/Unity-iPhone.xcodeproj"
PBXPROJ="${UNITY_XCODEPROJ}/project.pbxproj"
# 1. The highest value written in the Unity project file. grep -o + awk:
#    the same on macOS (BSD) and Linux.
if [[ -z "${DEPLOYMENT_TARGET}" && -f "${PBXPROJ}" ]]; then
  DEPLOYMENT_TARGET=$( { grep -o 'IPHONEOS_DEPLOYMENT_TARGET = [^;]*' "${PBXPROJ}" || true; } \
    | tr -d '"' | awk '$3 ~ /^[0-9][0-9.]*$/ { print $3 }' | sort -t. -k1,1n -k2,2n | tail -1)
  TARGET_SOURCE="${PBXPROJ}"
fi
# 2. What Xcode resolves for the app target (covers an inherited value).
if [[ -z "${DEPLOYMENT_TARGET}" && -d "${UNITY_XCODEPROJ}" ]]; then
  DEPLOYMENT_TARGET=$( { xcodebuild -showBuildSettings -project "${UNITY_XCODEPROJ}" \
      -target Unity-iPhone -configuration "${CONFIGURATION}" 2>/dev/null || true; } \
    | awk '$1 == "IPHONEOS_DEPLOYMENT_TARGET" && $2 == "=" { print $3; exit }')
  TARGET_SOURCE="xcodebuild -showBuildSettings"
fi
BUILD_SETTINGS=()
if [[ -n "${DEPLOYMENT_TARGET}" ]]; then
  BUILD_SETTINGS+=("IPHONEOS_DEPLOYMENT_TARGET=${DEPLOYMENT_TARGET}")
  echo "[xcode_archive] Deployment target (all targets, Pods included): ${DEPLOYMENT_TARGET} (from ${TARGET_SOURCE})"
else
  echo "::warning::[xcode_archive] Could not read IPHONEOS_DEPLOYMENT_TARGET (project: ${UNITY_XCODEPROJ}, exists: $([[ -d "${UNITY_XCODEPROJ}" ]] && echo yes || echo no), mentions in project.pbxproj: $( { grep -c IPHONEOS_DEPLOYMENT_TARGET "${PBXPROJ}" 2>/dev/null || true; } )); Pods keep their own minimums. Set IOS_DEPLOYMENT_TARGET to force one."
fi

echo "[xcode_archive] Scheme: ${SCHEME} | Config: ${CONFIGURATION}"
echo "[xcode_archive] Archive: ${ARCHIVE_PATH}"
echo "[xcode_archive] Log: ${LOG_PATH}"

set +e
xcodebuild \
  "${PROJECT_ARGS[@]}" \
  -scheme "${SCHEME}" \
  -configuration "${CONFIGURATION}" \
  -archivePath "${ARCHIVE_PATH}" \
  -destination "generic/platform=iOS" \
  ${BUILD_SETTINGS[@]+"${BUILD_SETTINGS[@]}"} \
  CODE_SIGN_STYLE=Manual \
  DEVELOPMENT_TEAM="${DEVELOPMENT_TEAM}" \
  OTHER_CODE_SIGN_FLAGS="--keychain ${KEYCHAIN_PATH}" \
  archive 2>&1 | tee "${LOG_PATH}"
ARCHIVE_EXIT=${PIPESTATUS[0]}
set -e

if [[ "${ARCHIVE_EXIT}" -ne 0 ]]; then
  echo "::error::xcodebuild archive failed (exit ${ARCHIVE_EXIT}). See: ${LOG_PATH}" >&2
  exit "${ARCHIVE_EXIT}"
fi

if [[ ! -d "${ARCHIVE_PATH}" ]]; then
  echo "::error::Archive not found at expected path: ${ARCHIVE_PATH}" >&2
  exit 1
fi

echo "[xcode_archive] Archive created: ${ARCHIVE_PATH}"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  printf 'archive-path=%s\n'     "${ARCHIVE_PATH}" >> "${GITHUB_OUTPUT}"
  printf 'archive-log-path=%s\n' "${LOG_PATH}"     >> "${GITHUB_OUTPUT}"
fi
