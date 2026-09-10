#!/usr/bin/env bash
# fake_xcodebuild.sh — Fake xcodebuild executable for iOS pipeline testing.
#
# Behaviour is controlled by FAKE_XCODEBUILD_MODE environment variable:
#   success           (default) exit 0, create fake archive/IPA
#   archive_failure   exit 65 simulating xcodebuild archive failure
#   export_failure    exit 65 simulating xcodebuild -exportArchive failure
#   missing_ipa       exit 0 but do NOT create the IPA file
#   missing_archive   exit 0 but do NOT create the .xcarchive
#   signing_failure   exit 65 with signing error message
#   invalid_scheme    exit 66 with scheme not found error
#
# Reads:
#   FAKE_XCODE_ARCHIVE_PATH  — path where .xcarchive should be created
#   FAKE_XCODE_EXPORT_PATH   — path where IPA directory should be created
#   FAKE_XCODE_LOG_FILE      — path where xcodebuild log should be written

MODE="${FAKE_XCODEBUILD_MODE:-success}"
ARCHIVE_PATH="${FAKE_XCODE_ARCHIVE_PATH:-/tmp/fake_archive.xcarchive}"
EXPORT_PATH="${FAKE_XCODE_EXPORT_PATH:-/tmp/fake_export}"
LOG_FILE="${FAKE_XCODE_LOG_FILE:-/tmp/fake_xcodebuild.log}"

# Detect the action being performed
ACTION=""
for arg in "$@"; do
  case "$arg" in
    archive|-exportArchive) ACTION="$arg" ;;
  esac
done

# Write a fake log
cat > "$LOG_FILE" <<EOF
=== FAKE XCODEBUILD LOG ===
Mode: $MODE
Action: $ACTION
Arguments: $*
Build Settings: $(date)
EOF

case "$MODE" in
  success)
    if [[ "$ACTION" == "archive" ]]; then
      echo "** ARCHIVE SUCCEEDED **" >> "$LOG_FILE"
      mkdir -p "${ARCHIVE_PATH}/Products/Applications"
      echo "FAKE_ARCHIVE" > "${ARCHIVE_PATH}/Info.plist"
    elif [[ "$ACTION" == "-exportArchive" ]]; then
      echo "** EXPORT SUCCEEDED **" >> "$LOG_FILE"
      mkdir -p "$EXPORT_PATH"
      echo "FAKE_IPA_CONTENT" > "$EXPORT_PATH/MyApp.ipa"
    else
      echo "** BUILD SUCCEEDED **" >> "$LOG_FILE"
    fi
    exit 0
    ;;

  archive_failure)
    cat >> "$LOG_FILE" <<EOF
error: Code signing "MyApp" failed

** ARCHIVE FAILED **

The following build commands failed:
    CompileStoryboard
EOF
    echo "[Fake xcodebuild] Archive FAILED." >> "$LOG_FILE"
    exit 65
    ;;

  export_failure)
    cat >> "$LOG_FILE" <<EOF
error: exportArchive: No applicable devices found.
** EXPORT FAILED **
EOF
    echo "[Fake xcodebuild] Export FAILED." >> "$LOG_FILE"
    exit 65
    ;;

  missing_ipa)
    if [[ "$ACTION" == "archive" ]]; then
      mkdir -p "${ARCHIVE_PATH}/Products/Applications"
      echo "FAKE_ARCHIVE" > "${ARCHIVE_PATH}/Info.plist"
    fi
    # Intentionally skip IPA creation even though we exit 0
    echo "** EXPORT SUCCEEDED (but IPA missing — fake bug) **" >> "$LOG_FILE"
    exit 0
    ;;

  missing_archive)
    # Exit 0 but no .xcarchive created
    echo "** ARCHIVE SUCCEEDED (but archive dir missing — fake bug) **" >> "$LOG_FILE"
    exit 0
    ;;

  signing_failure)
    cat >> "$LOG_FILE" <<EOF
error: No signing certificate "iPhone Distribution" found
error: Code signing is required for product type 'Application' in SDK 'iOS 17.0'
** ARCHIVE FAILED **
EOF
    exit 65
    ;;

  invalid_scheme)
    cat >> "$LOG_FILE" <<EOF
error: The scheme "NonExistentScheme" does not exist.
** ARCHIVE FAILED **
EOF
    exit 66
    ;;

  *)
    echo "[Fake xcodebuild] Unknown mode: $MODE" >&2
    exit 1
    ;;
esac
