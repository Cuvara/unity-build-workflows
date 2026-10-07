#!/usr/bin/env bash
# resolve_build_matrix.sh -- stage 01's build, validate and sign matrices.
#
# unity-pipeline.yml's resolve-config "Resolve build matrix" step: from the
# platforms resolve_build_flow.sh selected (SEL_<PLATFORM>), the environment,
# the build number and retention, and the runner-selection document, emit one
# row per platform job -- target, artifact name and type, configuration,
# runs-on, build profile -- as build-matrix / validate-matrix / sign-matrix
# (JSON) plus the build number, on stdout and $GITHUB_OUTPUT. "Which
# platforms build?" is answered here, not by per-job `if:` expressions.
#
# It was ~300 lines of bash inline in the workflow; it is the same bash.
# Inputs are the step's env (see the step), never `${{ }}` interpolation.
set -Eeuo pipefail

case "${ENVIRONMENT}" in
  development) CONFIG="Development" ;;
  staging)     CONFIG="Staging" ;;
  production)  CONFIG="Production" ;;
  "")          CONFIG="Production" ;;
  *)           CONFIG="$(printf '%s' "${ENVIRONMENT:0:1}" | tr '[:lower:]' '[:upper:]')${ENVIRONMENT:1}" ;;
esac
ANDROID_ARTIFACT=$(printf '%s' "${ANDROID_TYPE:-apk}" | tr '[:lower:]' '[:upper:]')

# ── Build number ──────────────────────────────────────────────────
# The store-facing counter: Android bundleVersionCode, iOS
# CFBundleVersion. Both stores reject a build number that has already
# been uploaded, so it must be monotonic and it must be decided HERE —
# before the artifact is built — not discovered afterwards.
#
# This reuses the convention the Unity package already applies
# (IOSBuilder: BUILD_NUMBER -> GITHUB_RUN_NUMBER) rather than
# introducing a second scheme. github.run_number is per-workflow and
# monotonic, so a project whose store history predates this pipeline
# sets BUILD_NUMBER_OFFSET once to clear it.
#
# Android was the broken one: AndroidBuilder derived versionCode from
# the MAJOR version, so every 1.x.y release uploaded versionCode 1 and
# Play rejected the second one.
#
# One offset per build type. Development and release builds come
# from different workflows, each with its own run_number, so a shared
# offset let a development build outnumber the next release (a tester
# then cannot install that release over it). The per-type variable
# wins; the shared BUILD_NUMBER_OFFSET is the fallback for projects
# that set only that one. See docs/VERSIONING.md.
case "${BUILD_TYPE_IN:-}" in
  release) OFFSET_NAME="BUILD_NUMBER_OFFSET_RELEASE";     OFFSET="${BUILD_NUMBER_OFFSET_RELEASE:-}" ;;
  *)       OFFSET_NAME="BUILD_NUMBER_OFFSET_DEVELOPMENT"; OFFSET="${BUILD_NUMBER_OFFSET_DEVELOPMENT:-}" ;;
esac
if [ -z "${OFFSET}" ] && [ -n "${BUILD_NUMBER_OFFSET:-}" ]; then
  OFFSET_NAME="BUILD_NUMBER_OFFSET"; OFFSET="${BUILD_NUMBER_OFFSET}"
fi
if [ -z "${OFFSET}" ]; then
  OFFSET_NAME="default"; OFFSET=0
fi
case "${OFFSET}" in
  *[!0-9]*)
    echo "::error::${OFFSET_NAME} must be a non-negative integer, got '${OFFSET}'"
    exit 1
    ;;
esac
BUILD_NUMBER=$(( RUN_NUMBER + OFFSET ))
echo "[matrix] build number ${BUILD_NUMBER} = run ${RUN_NUMBER} + offset ${OFFSET} (${OFFSET_NAME})"
if [ "${BUILD_NUMBER}" -le 0 ]; then
  echo "::error::Resolved build number ${BUILD_NUMBER} is not positive"
  exit 1
fi

# Resolved once, by resolve_build_flow.sh, which also derives the
# Android output format from it. Deriving it here as well was one
# divergence away from a release build naming its artifacts
# `development-*` — or producing an APK nobody can publish.
BUILD_TYPE="${BUILD_TYPE_IN:-}"
if [ -z "${BUILD_TYPE}" ]; then
  # Deriving a fallback here is what created two copies of the rule.
  # If the resolver did not emit one, that is the bug to fix.
  echo "::error::resolve_build_flow.sh emitted no build-type; the artifact contract cannot be resolved."
  exit 1
fi
# ── Artifact retention, by what the artifact is for ─────────────
# A Release Set is the input to every later promotion: once its
# artifacts expire, `actions/download-artifact` cannot fetch them and
# the release can never be promoted again. Thirty days for everything
# made a storage default into a promotion deadline nobody had agreed
# to. Development artifacts are disposable by I-002 and can go sooner.
#
# A project that set ARTIFACT_RETENTION_DAYS chose a number; that is
# never overruled here.
# Defaulted rather than assumed: the step runs under `set -u`, and a
# caller that predates these two variables must not crash the matrix.
RETENTION_DAYS="${RETENTION_DAYS:-30}"
RETENTION_SOURCE="${RETENTION_SOURCE:-default}"
if [ "${RETENTION_SOURCE}" = "default" ]; then
  case "${BUILD_TYPE}" in
    release) RETENTION_DAYS=90 ;;
    *)
      case "${ENVIRONMENT}" in
        staging) RETENTION_DAYS=14 ;;
        *)       RETENTION_DAYS=7 ;;
      esac
      ;;
  esac
fi
# Logs, validation reports and per-platform result files are
# diagnostics for the run that produced them. Nothing downstream
# reads them, so they never need the artifact's lifetime.
LOG_RETENTION_DAYS=7
[ "${RETENTION_DAYS}" -lt "${LOG_RETENTION_DAYS}" ] && LOG_RETENTION_DAYS="${RETENTION_DAYS}"
echo "retention-days=${RETENTION_DAYS}"
echo "log-retention-days=${LOG_RETENTION_DAYS}"
{
  echo "retention-days=${RETENTION_DAYS}"
  echo "log-retention-days=${LOG_RETENTION_DAYS}"
} >> "${GITHUB_OUTPUT}"

case "${BUILD_TYPE}" in
  development) BUILD_TYPE_LABEL="Development" ;;
  release)     BUILD_TYPE_LABEL="Release" ;;
  *)
    echo "::error::Invalid build-type='${BUILD_TYPE}'. Allowed: development release"
    exit 1
    ;;
esac

BUILD_ROWS=""
VALIDATE_ROWS=""

# `platform: None` means "validate and test, build nothing" — the CI
# lane. It has to be honoured here rather than in the resolver,
# because on a push the resolver takes the platform set from the
# branch's *_BUILD_PLATFORMS variable and never looks at the input.
# An empty matrix leaves stages 03 and 04 with no node at all.
if [ "${IN_PLATFORM}" = "None" ]; then
  echo "[matrix] platform=None — validation and tests only, no player build"
  SEL_ANDROID=false; SEL_WEBGL=false; SEL_LINUX64=false
  SEL_LINUXSERVER=false; SEL_WINDOWS64=false; SEL_IOS=false
fi

# add <selected> <platform> <artifact-type> <node-suffix> <validator>
# node-suffix is appended after "03 / <platform>" in the graph, so it
# only carries what the platform name does not already say.
# validator is empty for platforms with no stage-04 checker.
# The artifact name encodes build type, platform and artifact type, so a
# development APK and a release AAB can never be confused in the
# artifact list: development-android-apk vs release-android-aab.
# Slugs are the platform as a person names it, not the internal
# Unity target id. The artifact type is appended only where it
# distinguishes two real outputs — Android ships APK or AAB, iOS an
# Xcode project or an IPA. Appending it everywhere produced
# "development-webgl-webgl".
slug_of() {
  case "$1" in
    Windows64)   echo "windows" ;;
    Linux64)     echo "linux" ;;
    LinuxServer) echo "linux-server" ;;
    *)           printf '%s' "$1" | tr '[:upper:]' '[:lower:]' ;;
  esac
}

# What a person calls the platform. Windows64 and Linux64 are Unity's
# target identifiers and stay that way in the config and the matrix;
# the graph is read by humans, so it says Windows and Linux.
label_of() {
  case "$1" in
    Windows64)   echo "Windows" ;;
    Linux64)     echo "Linux" ;;
    LinuxServer) echo "Linux Server" ;;
    *)           printf '%s' "$1" ;;
  esac
}

# One platform's runs-on, as an escaped JSON string: the row goes
# through fromJSON into a workflow input that is itself a JSON string,
# so the quotes have to survive one level more than they look like.
# The helper sits next to this script.
_rl_script="$(dirname "${BASH_SOURCE[0]}")/matrix_runner_labels.py"
[ -f "${_rl_script}" ] || {
  echo "::error::matrix_runner_labels.py not found; every matrix row would lose its runs-on"
  exit 1
}

labels_of() {
  RL_PLATFORM="$1" python3 "${_rl_script}"
}
# Any other field of the platform's selection (empty when nothing
# selected one; the build job then falls back to the run-wide value).
field_of() {
  RL_PLATFORM="$1" RL_FIELD="$2" python3 "${_rl_script}"
}
# The Build Profile each platform builds with (empty = Player Settings).
# Validated once, so a typo fails here rather than in one leg's build.
_bp_script="$(dirname "${_rl_script}")/build_profile_for_platform.py"
if [ -f "${_bp_script}" ]; then
  python3 "${_bp_script}" --validate
elif [ -n "${BUILD_PROFILES:-}" ]; then
  echo "::error::build-profiles is set but build_profile_for_platform.py is not in this toolkit checkout"
  exit 1
fi
profile_of() {
  if [ -f "${_bp_script}" ]; then BP_PLATFORM="$1" python3 "${_bp_script}"; fi
}

# Sanitize a product name for use in artifact filenames:
# preserve original casing, strip spaces and filesystem-unsafe
# characters, collapse runs of underscores.
sanitize_name() {
  printf '%s' "$1" | sed 's/ //g; s/[^A-Za-z0-9._-]/_/g; s/__*/_/g; s/^_//; s/_$//'
}

PRODUCT_SLUG=$(sanitize_name "${PRODUCT_NAME:-Unknown}")
_APP_VER="${APP_VERSION:-0.0.0}"

add() {
  [ "$1" = "true" ] || return 0
  local platform="$2" artifact="$3" suffix="$4" validator="$5"
  local slug lower_artifact name label labels engine activation selected profile
  slug=$(slug_of "${platform}")
  label=$(label_of "${platform}")
  labels=$(labels_of "${platform}")
  engine=$(field_of "${platform}" buildEngine)
  activation=$(field_of "${platform}" activationStrategy)
  selected=$(field_of "${platform}" selectedTarget)
  profile=$(profile_of "${platform}")
  lower_artifact=$(printf '%s' "${artifact}" | tr '[:upper:]' '[:lower:]')
  # Artifact naming: {product}_{version}_{build}_{env}_{platform}_{type}
  # The type suffix is appended only where it distinguishes two real
  # outputs (Android: APK vs AAB, iOS: xcodeproj vs IPA).
  case "${platform}" in
    Android|iOS) name="${PRODUCT_SLUG}_${_APP_VER}_${BUILD_NUMBER}_${BUILD_TYPE}_${slug}_${lower_artifact}" ;;
    *)           name="${PRODUCT_SLUG}_${_APP_VER}_${BUILD_NUMBER}_${BUILD_TYPE}_${slug}" ;;
  esac
  BUILD_ROWS="${BUILD_ROWS}{\"platform\":\"${platform}\",\"label\":\"${label}\",\"artifact-type\":\"${artifact}\",\"artifact-name\":\"${name}\",\"node\":\"${suffix}\",\"runner-labels\":\"${labels}\",\"build-engine\":\"${engine}\",\"activation-strategy\":\"${activation}\",\"runner-selected\":\"${selected}\",\"build-profile\":\"${profile}\"},"
  if [ -n "${validator}" ]; then
    VALIDATE_ROWS="${VALIDATE_ROWS}{\"platform\":\"${platform}\",\"label\":\"${label}\",\"artifact-type\":\"${artifact}\",\"artifact-name\":\"${name}\",\"validator\":\"${validator}\",\"node\":\"${suffix}\"},"
  fi
}

add "${SEL_ANDROID}"     Android     "${ANDROID_ARTIFACT}" "${ANDROID_ARTIFACT}" android

# iOS is the one platform whose release artifact is not what Unity
# emits. Unity produces an Xcode project; the shippable artifact is a
# signed IPA. Signing therefore happens in THIS lane, before the
# immutable boundary — if the IPA were produced during promotion, the
# binary QA validated (a project) would not be the binary that ships.
#
# The Xcode project is still uploaded as an intermediate, but for a
# release build it is stage 03b's input, and stage 04 validates the
# IPA that comes out of it.
add "${SEL_IOS}"         iOS         XCODEPROJ             "Xcode Project"       ios
if [ "${SEL_IOS}" = "true" ] && [ "${BUILD_TYPE}" = "release" ]; then
  # Replace the iOS validation row: validate the IPA, not the project.
  VALIDATE_ROWS=$(printf '%s' "${VALIDATE_ROWS}" | sed 's/{"platform":"iOS"[^}]*},//')
  VALIDATE_ROWS="${VALIDATE_ROWS}{\"platform\":\"iOS\",\"label\":\"iOS\",\"artifact-type\":\"IPA\",\"artifact-name\":\"${PRODUCT_SLUG}_${_APP_VER}_${BUILD_NUMBER}_${BUILD_TYPE}_ios_ipa\",\"validator\":\"ipa\",\"node\":\"IPA\"},"
fi
add "${SEL_WEBGL}"       WebGL       WEBGL                 "WebGL"               webgl
# Desktop players had no stage-04 leg at all, so a Windows or Linux
# build missing its _Data directory — a player that cannot start —
# became an immutable release artifact unchallenged.
add "${SEL_LINUX64}"     Linux64     LINUX                 "Standalone"          desktop
add "${SEL_LINUXSERVER}" LinuxServer LINUX                 "Dedicated Server"    desktop
add "${SEL_WINDOWS64}"   Windows64   EXE                   "Standalone"          desktop

BUILD_MATRIX="[${BUILD_ROWS%,}]"
VALIDATE_MATRIX="[${VALIDATE_ROWS%,}]"

# ── Sign matrix: platform-specific post-build operations ───────────
# Only iOS builds are ever signed here. The matrix is empty for every
# other platform, so the sign-ios job is structurally incapable of
# scheduling iOS operations for a non-iOS build.
SIGN_ROWS=""
# Release builds always sign iOS; development builds sign when the
# project opts in (BUILD_IOS_SIGN_DEVELOPMENT), so testers get an IPA.
if [ "${SEL_IOS}" = "true" ] && { [ "${BUILD_TYPE}" = "release" ] || [ "${IOS_SIGN_DEV:-false}" = "true" ]; }; then
  _IOS_LABELS=$(labels_of "iOS")
  SIGN_ROWS="{\"platform\":\"iOS\",\"label\":\"iOS\",\"runner-labels\":\"${_IOS_LABELS}\",\"xcodeproj-artifact\":\"${PRODUCT_SLUG}_${_APP_VER}_${BUILD_NUMBER}_${BUILD_TYPE}_ios_xcodeproj\",\"ipa-artifact\":\"${PRODUCT_SLUG}_${_APP_VER}_${BUILD_NUMBER}_${BUILD_TYPE}_ios_ipa\"},"
fi
SIGN_MATRIX="[${SIGN_ROWS%,}]"

# The platforms this run set out to build, whether or not they got
# to run: a quality-gate failure still has to reach each platform's
# Discord thread, not the channel root.
PLANNED=""
for _sel in ANDROID:Android WEBGL:WebGL LINUX64:Linux64 LINUXSERVER:LinuxServer WINDOWS64:Windows64 IOS:iOS; do
  _var="SEL_${_sel%%:*}"
  [ "${!_var:-false}" = "true" ] && PLANNED="${PLANNED:+${PLANNED} }${_sel##*:}"
done

# A matrix with no vectors is a workflow error, so the jobs are also
# gated on these being non-empty.
{
  echo "configuration=${CONFIG}"
  echo "build-number=${BUILD_NUMBER}"
  echo "sign-ios=$([ "${SIGN_MATRIX}" = "[]" ] && echo false || echo true)"
  echo "ipa-artifact-name=$([ "${SIGN_MATRIX}" != "[]" ] && echo "${PRODUCT_SLUG}_${_APP_VER}_${BUILD_NUMBER}_${BUILD_TYPE}_ios_ipa" || echo "")"
  echo "build-type=${BUILD_TYPE}"
  echo "planned-platforms=${PLANNED}"
  echo "build-type-label=${BUILD_TYPE_LABEL}"
  echo "android-artifact-type=${ANDROID_ARTIFACT}"
  echo "label-addressables=${CONFIG}"
  echo "build-matrix=${BUILD_MATRIX}"
  echo "validate-matrix=${VALIDATE_MATRIX}"
  echo "sign-matrix=${SIGN_MATRIX}"
  echo "has-builds=$([ "${BUILD_MATRIX}" = "[]" ] && echo false || echo true)"
  echo "has-validations=$([ "${VALIDATE_MATRIX}" = "[]" ] && echo false || echo true)"
  echo "has-sign-ops=$([ "${SIGN_MATRIX}" = "[]" ] && echo false || echo true)"
} >> "$GITHUB_OUTPUT"

echo "[matrix] configuration=${CONFIG}"
echo "[matrix] build=${BUILD_MATRIX}"
echo "[matrix] validate=${VALIDATE_MATRIX}"
echo "[matrix] sign=${SIGN_MATRIX}"
