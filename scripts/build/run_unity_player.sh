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
#   --dry-run               print the command instead of running it
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
    --dry-run)             DRY_RUN=1; shift ;;
    *) die "run_unity_player.sh: unknown argument '$1'" ;;
  esac
done
[ -n "${PLATFORM}" ] || die "run_unity_player.sh: --platform is required"
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
# MSYS rewrites arguments that look like POSIX paths before a Windows program
# sees them; Unity's arguments are its own, not paths to translate.
export MSYS2_ARG_CONV_EXCL='*'
exec "${EDITOR}" "${ARGS[@]}"
