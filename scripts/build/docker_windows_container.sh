#!/usr/bin/env bash
# docker_windows_container.sh -- activate Unity, then build, inside the
# unityci/editor container of the Windows-runner docker lane.
#
# Runs in the container that reusable-build-platform.yml's "Build with Docker
# (Windows, native docker run)" step starts, with the project at /project and
# the workspace (holding .toolkit) at /workspace. It used to be a PowerShell
# array of bash lines written to a temp file at run time; it is a script
# because it is one.
#
# Activation is game-ci/unity-builder's own Unity Personal path (root-caused
# in docs/UNITY_PERSONAL_DOCKER_LICENSE.md, "Recommended fix"): the
# toolkit's activate-license.sh personal-combined path (raw .ulf +
# -username/-password) fails with "TimeStamp validation failed" in a fresh
# container. game-ci instead (1) takes the Unity SERIAL from the .ulf's
# <DeveloperData Value="..."> (base64-decoded, first 4 bytes dropped),
# (2) randomizes /etc/machine-id when the serial starts with 'F' (Personal),
# (3) activates in serial mode, never pre-placing the .ulf. Activation and
# build run in one container so the activation persists for the build.
#
# Environment (passed by name by `docker run -e`, never by value):
#   UNITY_LICENSE UNITY_EMAIL UNITY_PASSWORD   activation; never echoed
#   PLATFORM BUILD_METHOD ANDROID_EXPORT_TYPE  the build
#   ANDROID_KEYSTORE_PASS ANDROID_KEY_PASS BUILD_NUMBER APP_VERSION BUILD_PROFILE
#                                              read by PlayerBuilder
#
# Exit code: the build's (Unity's), or activation's after 5 attempts.
set -eo pipefail

export HOME=/tmp/unity-home
mkdir -p "$HOME"
ACTIVATE_LOG=/workspace/Editor-activate.log

echo "[docker-windows] Extracting Unity serial from UNITY_LICENSE (game-ci getSerialFromLicenseFile equivalent)..."
DEV_DATA_B64=$(printf %s "$UNITY_LICENSE" | tr -d "\r\n" | sed -n "s/.*<DeveloperData Value=\"\([^\"]*\)\".*/\1/p")
if [ -z "$DEV_DATA_B64" ]; then
  echo "::error::Could not find <DeveloperData Value=\"...\"> in UNITY_LICENSE - cannot derive Unity serial for activation."
  exit 1
fi
SERIAL=$(printf %s "$DEV_DATA_B64" | base64 -d 2>/dev/null | tail -c +5)
if [ -z "$SERIAL" ]; then
  echo "::error::Failed to decode Unity serial from UNITY_LICENSE DeveloperData."
  exit 1
fi
echo "[docker-windows] Serial derived (masked): ${SERIAL:0:1}***${SERIAL: -4}"
if [ "${SERIAL:0:1}" = "F" ]; then
  echo "[docker-windows] Personal license serial (F-prefixed) - randomizing /etc/machine-id before activation."
  dbus-uuidgen > /etc/machine-id 2>/dev/null || cat /proc/sys/kernel/random/uuid | tr -d "-" > /etc/machine-id
  mkdir -p /var/lib/dbus
  ln -sf /etc/machine-id /var/lib/dbus/machine-id
fi

echo "[docker-windows] Activating Unity (serial license mode, game-ci-equivalent)..."
ATTEMPT=1
MAX_ATTEMPTS=5
# The real mounted project, not a scratch one: activation itself succeeds
# against a bare `mkdir -p` directory, but Unity then fails with "Couldn't set
# project path"; /project opens fine and the build uses it anyway.
until unity-editor -logFile "$ACTIVATE_LOG" -quit -serial "$SERIAL" -username "$UNITY_EMAIL" -password "$UNITY_PASSWORD" -projectPath /project; do
  RC=$?
  if [ "$ATTEMPT" -ge "$MAX_ATTEMPTS" ]; then
    echo "::error::Unity license activation failed after $MAX_ATTEMPTS attempts. See uploaded logs artifact (Editor-activate.log) for details."
    tail -c 4000 "$ACTIVATE_LOG" 2>/dev/null || true
    exit $RC
  fi
  echo "[docker-windows] Activation attempt $ATTEMPT failed (exit $RC), retrying..."
  ATTEMPT=$((ATTEMPT+1))
  sleep $((ATTEMPT*5))
done

echo "[docker-windows] Activation OK. Building..."
# Same script as the native lanes: target mapping, build method,
# BUILD_OUTPUT_DIR=build, ANDROID_APP_BUNDLE.
BUILD_RC=0
bash /workspace/.toolkit/scripts/build/run_unity_player.sh --host-os linux \
  --editor "$(command -v unity-editor)" --platform "$PLATFORM" --project /project \
  --build-method "$BUILD_METHOD" --android-export-type "$ANDROID_EXPORT_TYPE" \
  --log-file /workspace/Editor.log || BUILD_RC=$?

# The log artifact upload is not always there to rely on (a full artifact
# storage quota), so the tail of every plausible Editor.log goes to the
# console: a failure is diagnosable from the step log alone.
echo "[docker-windows] Build exited $BUILD_RC. Log file candidates:"
for f in /workspace/Editor.log /project/Library/Editor.log "$HOME/.config/unity3d/Editor.log" "$HOME/.local/share/unity3d/Editor.log"; do
  if [ -f "$f" ]; then
    echo "--- $f ($(wc -c < "$f") bytes) ---"
    tail -c 6000 "$f"
  else
    echo "--- $f: not found ---"
  fi
done
exit $BUILD_RC
