#!/usr/bin/env bash
# fake_unity.sh — Fake Unity executable for entrypoint testing.
#
# Behaviour is controlled by FAKE_UNITY_MODE environment variable:
#   success           (default) exit 0, create dummy artifact
#   compile_error     exit 1 with compile-style log output
#   license_error     exit 64 with license failure message
#   timeout           sleep indefinitely (caller must kill)
#   missing_artifact  exit 0 but do NOT create the artifact
#   crash             exit 139 (SIGSEGV simulation)
#
# Reads: FAKE_UNITY_OUTPUT_DIR — path where artifact should be written.
#        FAKE_UNITY_LOG_FILE   — path where Editor.log should be written.

MODE="${FAKE_UNITY_MODE:-success}"
OUTPUT_DIR="${FAKE_UNITY_OUTPUT_DIR:-/tmp/fake_unity_output}"
LOG_FILE="${FAKE_UNITY_LOG_FILE:-/tmp/fake_unity_editor.log}"

# Write a fake Editor.log regardless of outcome
cat > "$LOG_FILE" <<EOF
Fake Unity Editor Log
Mode: $MODE
Arguments: $*
Initialize engine version: 2022.3.45f1 (fake)
GfxDevice: creating device client; threaded=0
EOF

case "$MODE" in
  success)
    echo "[Fake Unity] Build succeeded." >> "$LOG_FILE"
    mkdir -p "$OUTPUT_DIR"
    echo "FAKE_BUILD_ARTIFACT" > "$OUTPUT_DIR/game.apk"
    exit 0
    ;;

  compile_error)
    cat >> "$LOG_FILE" <<EOF
Assets/Scripts/GameManager.cs(42,10): error CS0103: The name 'NonExistent' does not exist in the current context
Error building Player because scripts had compiler errors
EOF
    echo "[Fake Unity] Compilation FAILED." >> "$LOG_FILE"
    exit 1
    ;;

  license_error)
    cat >> "$LOG_FILE" <<EOF
[Unity] Activation - Error: No valid Unity license found. Please activate Unity first.
LICENSE SYSTEM [2026-01-01T00:00:00] Next license update check is after 2026-01-01T00:00:00
EOF
    echo "[Fake Unity] License activation FAILED." >> "$LOG_FILE"
    exit 64
    ;;

  timeout)
    echo "[Fake Unity] Sleeping indefinitely (timeout simulation)." >> "$LOG_FILE"
    sleep 9999
    ;;

  missing_artifact)
    echo "[Fake Unity] Build 'succeeded' but forgot to write artifact." >> "$LOG_FILE"
    # Intentionally do NOT create artifact
    exit 0
    ;;

  crash)
    echo "[Fake Unity] Simulating crash (SIGSEGV)." >> "$LOG_FILE"
    exit 139
    ;;

  *)
    echo "[Fake Unity] Unknown mode: $MODE" >&2
    exit 1
    ;;
esac
