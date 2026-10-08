#!/usr/bin/env bash
# run_unity_player.sh -- run one Unity build on a native lane.
#
# The single place that turns "platform X, this editor, this project" into a
# Unity command line, for every lane that runs the editor directly: the
# self-hosted macOS and Windows lanes (Git Bash on Windows) and the
# Addressables pre-step. Before this script each lane spelled the same rules
# in its own shell -- cmd, bash -- and a fix to one (the AAB flag, the build
# method fallback, the output folder) had to be made three times.
#
#   run_unity_player.sh --platform <P> --project <path> [options]
#
#   --platform P            Android | iOS | WebGL | Windows64 | Linux64 |
#                           LinuxServer | Addressables
#   --project PATH          Unity project, relative to the workspace
#   --editor PATH           Unity executable (default: $UNITY_EDITOR, which
#                           Unity preflight provides)
#   --build-method M        explicit -executeMethod (the build-method input)
#   --player-method M       fallback player method (toolkit package or project)
#   --addressables-method M Addressables method
#   --android-export-type T apk | aab
#   --addressables-only     build Addressables content, not a player
#   --log-file F            Unity log (default: Editor.log)
#   --host-os OS            darwin | windows | linux (default: detected)
#   --tests MODE            run the Unity Test Framework instead of a build:
#                           EditMode | PlayMode | All (both, in that order)
#   --results-dir DIR       test results root (default: test-results); each
#                           mode writes DIR/<mode>/results.xml and Editor.log
#   --dry-run               print the command instead of running it
#
# Unity writes to its -logFile, not to stdout, so the step log used to show
# only the "Unity binary" line until the build ended. The log file is now
# followed into the step output while Unity runs (UNITY_LOG_STREAM=0 turns
# that off); the file itself is still written for the steps that parse and
# upload it. GitHub masks registered secrets in the step output.
#
# Test runs always exit 0: a mode with no tests exits non-zero, so the job's
# verdict comes from the parsed results.xml (reusable-unity-tests.yml), and
# Unity's exit code is reported as a warning.
#
# Contract with PlayerBuilder (unity-package/.../Builders/PlayerBuilder.cs):
#   BUILD_OUTPUT_DIR=build    the upload step takes the workspace build/
#   ANDROID_APP_BUNDLE=1      for an .aab; absent means .apk
# Keystore passwords, BUILD_NUMBER, APP_VERSION and BUILD_PROFILE arrive in
# the calling step's env and pass through untouched; this script never reads
# or prints them.
#
# Exit code: Unity's (PlayerBuilder exits 1 on a failed build); 2 for a usage
# or lane error, with an ::error:: annotation.
set -Eeuo pipefail

die() { echo "::error::$*"; exit 2; }

PLATFORM="" PROJECT="" EDITOR="${UNITY_EDITOR:-}" BUILD_METHOD="" PLAYER_METHOD=""
ADDR_METHOD="" EXPORT_TYPE="apk" ADDR_ONLY=0 LOG_FILE="Editor.log" HOST_OS="" DRY_RUN=0
TEST_MODE="" RESULTS_DIR="test-results"
while [ $# -gt 0 ]; do
  case "$1" in
    --platform)            PLATFORM="${2:-}"; shift 2 ;;
    --project)             PROJECT="${2:-}"; shift 2 ;;
    --editor)              EDITOR="${2:-}"; shift 2 ;;
    --build-method)        BUILD_METHOD="${2:-}"; shift 2 ;;
    --player-method)       PLAYER_METHOD="${2:-}"; shift 2 ;;
    --addressables-method) ADDR_METHOD="${2:-}"; shift 2 ;;
    --android-export-type) EXPORT_TYPE="${2:-apk}"; shift 2 ;;
    --addressables-only)   ADDR_ONLY=1; shift ;;
    --log-file)            LOG_FILE="${2:-}"; shift 2 ;;
    --host-os)             HOST_OS="${2:-}"; shift 2 ;;
    --tests)               TEST_MODE="${2:-}"; shift 2 ;;
    --results-dir)         RESULTS_DIR="${2:-}"; shift 2 ;;
    --dry-run)             DRY_RUN=1; shift ;;
    *) die "run_unity_player.sh: unknown argument '$1'" ;;
  esac
done
# Tests run against the project's active target; no platform is needed.
[ -n "${PLATFORM}" ] || [ -n "${TEST_MODE}" ] || die "run_unity_player.sh: --platform is required"
[ -n "${PROJECT}" ] || die "run_unity_player.sh: --project is required"

if [ -z "${HOST_OS}" ]; then
  case "$(uname -s 2>/dev/null)" in
    Darwin) HOST_OS=darwin ;;
    MINGW*|MSYS*|CYGWIN*) HOST_OS=windows ;;
    *) HOST_OS=linux ;;
  esac
fi

# The editor Unity preflight provisioned for this project's exact version.
if [ -z "${EDITOR}" ]; then
  die "Unity preflight did not provide an editor executable (got '')."
fi
if command -v cygpath >/dev/null 2>&1; then
  EDITOR="$(cygpath -u "${EDITOR}")"   # Windows path from preflight
fi
[ -f "${EDITOR}" ] || die "Unity preflight did not provide an editor executable (got '${EDITOR}')."
echo "Unity binary: ${EDITOR}"

# MSYS rewrites arguments that look like POSIX paths before a Windows program
# sees them; Unity's arguments are its own, not paths to translate.
export MSYS2_ARG_CONV_EXCL='*'

# run_unity LOG ARGS... -- run the editor, following LOG into the step output.
# Returns Unity's exit code.
run_unity() {
  local log="$1"; shift
  local rc=0 tail_pid=""
  if [ "${UNITY_LOG_STREAM:-1}" != "0" ] && command -v tail >/dev/null 2>&1; then
    # A log left by an earlier run would be replayed; -F waits for the new one.
    rm -f "${log}"
    tail -n +1 -F "${log}" 2>/dev/null &
    tail_pid=$!
  fi
  "${EDITOR}" "$@" || rc=$?
  if [ -n "${tail_pid}" ]; then
    sleep 2   # let tail print the last lines Unity wrote
    kill "${tail_pid}" 2>/dev/null || true
    wait "${tail_pid}" 2>/dev/null || true
  fi
  return "${rc}"
}

if [ -n "${TEST_MODE}" ]; then
  case "${TEST_MODE}" in
    EditMode|PlayMode) MODES=("${TEST_MODE}") ;;
    All)               MODES=(EditMode PlayMode) ;;
    *) die "run_unity_player.sh: --tests takes EditMode, PlayMode or All (got '${TEST_MODE}')" ;;
  esac
  # Absolute: Unity resolves a relative -testResults against the project, not
  # the workspace. On Windows the editor is a native program and argument
  # conversion is off (above), so it gets a C:/ path, not /c/.
  case "${RESULTS_DIR}" in /*|[A-Za-z]:*) ;; *) RESULTS_DIR="${PWD}/${RESULTS_DIR}" ;; esac
  for MODE in "${MODES[@]}"; do
    OUT="${RESULTS_DIR}/${MODE}"
    OUT_ARG="${OUT}"
    if command -v cygpath >/dev/null 2>&1; then OUT_ARG="$(cygpath -m "${OUT}")"; fi
    # No -quit: -runTests exits by itself once the run is done.
    TEST_ARGS=(-batchmode -nographics -projectPath "${PROJECT}" -runTests
               -testPlatform "${MODE}" -testResults "${OUT_ARG}/results.xml" -logFile "${OUT_ARG}/Editor.log")
    if [ "${DRY_RUN}" -eq 1 ]; then
      printf '%s' "${EDITOR}"; printf ' %q' "${TEST_ARGS[@]}"; printf '\n'
      continue
    fi
    mkdir -p "${OUT}"
    echo "Running Unity ${MODE} tests..."
    RC=0
    run_unity "${OUT}/Editor.log" "${TEST_ARGS[@]}" || RC=$?
    [ "${RC}" -eq 0 ] || echo "::warning::${MODE} tests exited with code ${RC}"
  done
  exit 0
fi

ARGS=(-batchmode -nographics -quit -projectPath "${PROJECT}")

if [ "${PLATFORM}" = "Addressables" ] || [ "${ADDR_ONLY}" -eq 1 ]; then
  # Content only. The player build that may follow is a separate invocation.
  ARGS+=(-executeMethod "${ADDR_METHOD:-AddressableBuilder.Build}" -logFile "${LOG_FILE}")
else
  # One mapping for every native lane -- the docker lanes resolve the same
  # names in the "Map platform to targetPlatform" step.
  EXTRA=()
  case "${PLATFORM}" in
    iOS)
      # Xcode exists only on macOS; the iOS guard already blocks other hosts,
      # this says so if a caller skips it.
      [ "${HOST_OS}" = darwin ] || die "iOS builds require build-engine=local on a macOS runner."
      TARGET=iOS ;;
    Android)     TARGET=Android ;;
    WebGL)       TARGET=WebGL ;;
    Windows64)   TARGET=StandaloneWindows64 ;;
    Linux64)     TARGET=StandaloneLinux64 ;;
    LinuxServer) TARGET=StandaloneLinux64; EXTRA=(-standaloneBuildSubtarget Server) ;;
    *) die "Unsupported platform for the ${HOST_OS} lane: ${PLATFORM}" ;;
  esac

  # -buildTarget only SWITCHES the active target; an -executeMethod that calls
  # BuildPipeline.BuildPlayer produces the build. The build-method input wins,
  # then the toolkit package's PlayerBuilder (or the project's own, see
  # install_build_package.sh).
  METHOD="${BUILD_METHOD:-${PLAYER_METHOD:-PlayerBuilder.Build}}"

  # Output folder PlayerBuilder writes to; the upload step takes build/.
  export BUILD_OUTPUT_DIR=build
  # APK or AAB. PlayerBuilder builds an APK when this is absent, which once
  # shipped APKs under a release-android-aab name without a word.
  if [ "${EXPORT_TYPE}" = "aab" ]; then
    export ANDROID_APP_BUNDLE=1
  else
    unset ANDROID_APP_BUNDLE
  fi

  ARGS+=(-buildTarget "${TARGET}" -executeMethod "${METHOD}" -logFile "${LOG_FILE}")
  ARGS+=(${EXTRA[@]+"${EXTRA[@]}"})
fi

if [ "${DRY_RUN}" -eq 1 ]; then
  printf '%s' "${EDITOR}"; printf ' %q' "${ARGS[@]}"; printf '\n'
  exit 0
fi
RC=0
run_unity "${LOG_FILE}" "${ARGS[@]}" || RC=$?
exit "${RC}"
