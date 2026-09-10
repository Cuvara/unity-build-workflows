#!/usr/bin/env bash
# =============================================================================
# ensure_repo_variables.sh — create MISSING grouped Repository Variables
#
# Generic helper for consumers of the Unity build toolkit. Uses the `gh` CLI to
# create any grouped Repository Variable that is not already set, using the
# toolkit default. NEVER overwrites an existing value. Prints the final table.
#
# Requires: gh CLI authenticated with `repo` scope (gh auth status). If gh is
# missing or unauthenticated, exits 0 with a notice (non-fatal — this is a
# convenience helper, not a build dependency).
#
# Usage:
#   scripts/common/ensure_repo_variables.sh [--repo owner/name] [--dry-run]
#
# Legacy variables are intentionally NOT created — new consumers get the new
# grouped names; existing consumers keep their legacy vars (still honored by the
# resolver). See docs/REPOSITORY_VARIABLES.md.
# =============================================================================
set -Eeuo pipefail

REPO_FLAG=()
DRY_RUN=0
while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO_FLAG=(--repo "$2"); shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

if ! command -v gh >/dev/null 2>&1; then
  echo "::notice::ensure_repo_variables: gh CLI not found — skipping (non-fatal)."
  exit 0
fi
if ! gh auth status >/dev/null 2>&1; then
  echo "::notice::ensure_repo_variables: gh not authenticated — skipping (non-fatal)."
  exit 0
fi

# name<TAB>default — grouped Repository Variables and their toolkit defaults.
# Vars whose default is empty (e.g. *_DEFINE_SYMBOLS) are intentionally omitted:
# GitHub rejects empty variable values (HTTP 422), and "unset" is semantically
# identical to "empty" for the resolver. Set them by hand only when non-empty.
DEFAULTS=$(cat <<'TSV'
BUILD_DEVELOP_PLATFORMS	Android,WebGL
_UNITY_VERSION_PLACEHOLDER_
BUILD_STAGING_PLATFORMS	Android,WebGL,Linux64,LinuxServer,Windows64
BUILD_RELEASE_PLATFORMS	Android,WebGL,Linux64,LinuxServer,Windows64
BUILD_TIMEOUT_MINUTES	120
BUILD_CLEAN	false
TEST_DEVELOP_ENABLED	true
TEST_STAGING_ENABLED	true
TEST_RELEASE_ENABLED	true
TEST_EDITMODE_ENABLED	true
TEST_PLAYMODE_ENABLED	true
TEST_FAIL_FAST	false
ADDRESSABLES_DEVELOP_ENABLED	false
ADDRESSABLES_STAGING_ENABLED	false
ADDRESSABLES_RELEASE_ENABLED	true
RUNNER_DEFAULT_MODE	docker
RUNNER_WINDOWS_LABEL	self-hosted-windows
RUNNER_MACOS_LABEL	self-hosted-macos
RUNNER_LINUX_LABEL	ubuntu-latest
CACHE_LIBRARY_ENABLED	true
CACHE_GRADLE_ENABLED	true
CACHE_ADDRESSABLES_ENABLED	true
CACHE_NUGET_ENABLED	true
ARTIFACT_RETENTION_DAYS	30
ARTIFACT_COMPRESSION	zip
TSV
)

# UNITY_VERSION default is the project's editor version (SSOT: ProjectVersion.txt),
# not a hardcoded constant. Substitute the placeholder, or drop it if not found.
_uv="$(grep '^m_EditorVersion:' ProjectSettings/ProjectVersion.txt 2>/dev/null | awk '{print $2}' | tr -d '\r' || true)"
if [ -n "${_uv}" ]; then
  DEFAULTS="${DEFAULTS/_UNITY_VERSION_PLACEHOLDER_/UNITY_VERSION	${_uv}}"
else
  DEFAULTS="$(printf '%s\n' "${DEFAULTS}" | grep -v '_UNITY_VERSION_PLACEHOLDER_')"
fi

# Existing variable names (one per line).
existing="$(gh variable list "${REPO_FLAG[@]}" --json name --jq '.[].name' 2>/dev/null || true)"

printf '%-32s %-12s %s\n' "VARIABLE" "STATUS" "VALUE"
printf '%-32s %-12s %s\n' "--------" "------" "-----"

while IFS=$'\t' read -r name def; do
  [ -z "${name}" ] && continue
  if printf '%s\n' "${existing}" | grep -qx "${name}"; then
    printf '%-32s %-12s %s\n' "${name}" "exists" "(unchanged)"
    continue
  fi
  if [ "${DRY_RUN}" -eq 1 ]; then
    printf '%-32s %-12s %s\n' "${name}" "would-create" "${def}"
  elif gh variable set "${name}" --body "${def}" "${REPO_FLAG[@]}" >/dev/null 2>&1; then
    printf '%-32s %-12s %s\n' "${name}" "created" "${def}"
  else
    printf '%-32s %-12s %s\n' "${name}" "FAILED" "${def} (need repo admin?)"
  fi
done <<< "${DEFAULTS}"

echo
echo "Legacy variables (if present) are left untouched and still honored by the resolver."
