#!/usr/bin/env bash
# =============================================================================
# extract_project_metadata.sh — read build-relevant metadata from a Unity
# project's ProjectSettings/ProjectSettings.asset (text-serialized YAML).
#
# Emits KEY=value lines to stdout and, when GITHUB_OUTPUT is set, appends there.
# Best-effort: missing fields emit empty values, never fails the caller.
#
# Env:
#   PROJECT_PATH  Unity project root (default '.')
#
# Outputs:
#   product-name        Application.productName
#   app-version         bundleVersion (e.g. 0.4.0)
#   bundle-id-android   applicationIdentifier.Android (e.g. com.ondi.pack.adventure)
#   bundle-id-ios       applicationIdentifier.iPhone
#   bundle-id           bundle-id-android or Standalone fallback
#   scripting-backend   IL2CPP | Mono  (Android target; default Mono)
#   android-arch        ARMv7 | ARM64 | ARMv7+ARM64 | x86 | x86_64 | (raw)
#   orientation         Portrait | PortraitUpsideDown | LandscapeRight | LandscapeLeft | AutoRotation
#   store-link-android  Play Store URL derived from bundle-id-android (empty if no id)
#   define-symbols-count-ios  scriptingDefineSymbols.iPhone token count
# =============================================================================
set -Euo pipefail

PROJECT_PATH="${PROJECT_PATH:-.}"
PS="${PROJECT_PATH%/}/ProjectSettings/ProjectSettings.asset"

log() { echo "[extract_project_metadata] $*" >&2; }

emit() {
  local k="$1" v="$2"
  printf '%s=%s\n' "$k" "$v"
  [ -n "${GITHUB_OUTPUT:-}" ] && printf '%s=%s\n' "$k" "$v" >> "$GITHUB_OUTPUT"
  return 0
}

if [ ! -f "$PS" ]; then
  log "ProjectSettings.asset not found at $PS — emitting empty metadata"
  for k in product-name app-version bundle-id-android bundle-id-ios bundle-id scripting-backend android-arch orientation define-symbols-count define-symbols-count-ios store-link-android; do
    emit "$k" ""
  done
  exit 0
fi

# ── Simple top-level scalars ────────────────────────────────────────────────
product_name=$(grep -m1 -E '^\s*productName:' "$PS" | sed -E 's/^\s*productName:\s*//' | tr -d '\r' || true)
app_version=$(grep -m1 -E '^\s*bundleVersion:' "$PS" | sed -E 's/^\s*bundleVersion:\s*//' | tr -d '\r' || true)

# ── applicationIdentifier map (Android / Standalone) ────────────────────────
bundle_android=$(awk '/^\s*applicationIdentifier:/{f=1;next} f&&/^\s*Android:/{sub(/^\s*Android:\s*/,"");print;exit} f&&/^\s*[A-Za-z]/&&!/^\s+/{exit}' "$PS" | tr -d '\r' || true)
bundle_ios=$(awk '/^\s*applicationIdentifier:/{f=1;next} f&&/^\s*iPhone:/{sub(/^\s*iPhone:\s*/,"");print;exit} f&&/^\s*[A-Za-z]/&&!/^\s+/{exit}' "$PS" | tr -d '\r' || true)
bundle_standalone=$(awk '/^\s*applicationIdentifier:/{f=1;next} f&&/^\s*Standalone:/{sub(/^\s*Standalone:\s*/,"");print;exit} f&&/^\s*buildNumber:/{exit}' "$PS" | tr -d '\r' || true)
bundle_id="${bundle_android:-$bundle_standalone}"

# ── scriptingBackend (Android): 1 = IL2CPP, 0/absent = Mono ─────────────────
sb_android=$(awk '/^\s*scriptingBackend:/{f=1;next} f&&/^\s*Android:/{sub(/^\s*Android:\s*/,"");print;exit} f&&/^\s*[A-Za-z].*:/&&!/^\s+/{exit}' "$PS" | tr -d '\r ' || true)
case "$sb_android" in
  1) scripting_backend="IL2CPP" ;;
  0) scripting_backend="Mono" ;;
  *) scripting_backend="Mono" ;;   # default when unset/empty map
esac

# ── AndroidTargetArchitectures bitmask: 1 ARMv7, 2 ARM64, 4 x86, 8 x86_64 ───
arch_raw=$(grep -m1 -E '^\s*AndroidTargetArchitectures:' "$PS" | sed -E 's/[^0-9]//g' || true)
case "${arch_raw:-}" in
  1) android_arch="ARMv7" ;;
  2) android_arch="ARM64" ;;
  3) android_arch="ARMv7+ARM64" ;;
  4) android_arch="x86" ;;
  8) android_arch="x86_64" ;;
  "") android_arch="" ;;
  *) android_arch="arch:${arch_raw}" ;;
esac

# ── defaultScreenOrientation: 0 P,1 PUpsideDown,2 LRight,3 LLeft,4 AutoRotate ─
orient_raw=$(grep -m1 -E '^\s*defaultScreenOrientation:' "$PS" | sed -E 's/[^0-9]//g' || true)
case "${orient_raw:-}" in
  0) orientation="Portrait" ;;
  1) orientation="PortraitUpsideDown" ;;
  2) orientation="LandscapeRight" ;;
  3) orientation="LandscapeLeft" ;;
  4) orientation="AutoRotation" ;;
  *) orientation="" ;;
esac

# ── scriptingDefineSymbols (Android): count ';'-separated non-empty tokens ──
defines_android=$(awk '/^\s*scriptingDefineSymbols:/{f=1;next} f&&/^\s*Android:/{sub(/^\s*Android:\s*/,"");print;exit} f&&/^\s*[A-Za-z].*:/&&!/^\s+/{exit}' "$PS" | tr -d '\r' || true)
define_symbols_count=0
if [ -n "$defines_android" ]; then
  define_symbols_count=$(printf '%s' "$defines_android" | tr ';' '\n' | sed '/^[[:space:]]*$/d' | grep -c . || echo 0)
fi
# Same for iPhone: a project's iOS defines differ from its Android ones.
defines_ios=$(awk '/^\s*scriptingDefineSymbols:/{f=1;next} f&&/^\s*iPhone:/{sub(/^\s*iPhone:\s*/,"");print;exit} f&&/^\s*[A-Za-z].*:/&&!/^\s+/{exit}' "$PS" | tr -d '\r' || true)
define_symbols_count_ios=0
if [ -n "$defines_ios" ]; then
  define_symbols_count_ios=$(printf '%s' "$defines_ios" | tr ';' '\n' | sed '/^[[:space:]]*$/d' | grep -c . || echo 0)
fi

store_link_android=""
[ -n "$bundle_android" ] && store_link_android="https://play.google.com/store/apps/details?id=${bundle_android}"

emit "product-name"       "$product_name"
emit "app-version"        "$app_version"
emit "bundle-id-android"  "$bundle_android"
emit "bundle-id-ios"      "$bundle_ios"
emit "bundle-id"          "$bundle_id"
emit "scripting-backend"  "$scripting_backend"
emit "android-arch"       "$android_arch"
emit "orientation"        "$orientation"
emit "define-symbols-count" "$define_symbols_count"
emit "define-symbols-count-ios" "$define_symbols_count_ios"
emit "store-link-android" "$store_link_android"
