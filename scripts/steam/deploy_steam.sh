#!/usr/bin/env bash
# =============================================================================
# deploy_steam.sh
# Upload an already-built, already-verified release artifact to a Steam branch.
#
# This is a PROMOTION step. It publishes bytes that Build / Release produced;
# it never produces any. SteamCMD wants a content directory of its own next to
# a pair of VDF scripts, which is the one thing that could tempt a promotion
# into rewriting the artifact — so the artifact is COPIED into a staging
# workspace and the copy is what steamcmd reads. The downloaded artifact is
# left exactly as it arrived, and the copy is checksummed against it before
# anything is uploaded (see verify_staging_matches_source).
#
# Secrets: STEAM_USERNAME and STEAM_CONFIG_VDF arrive through the environment
# and are never echoed, never written to a log, and never passed on a command
# line where `ps` could read them. config.vdf is written with mode 600 into a
# directory removed by the exit trap.
#
# Environment:
#   ARTIFACT_DIR        the verified artifact, read-only to this script
#   STAGING_DIR         where the content copy is assembled (default: RUNNER_TEMP)
#   STEAM_APP_ID        numeric app id            (resolve_steam_config.py)
#   STEAM_DEPOT_ID      numeric depot id          (resolve_steam_config.py)
#   STEAM_BRANCH        branch to set live        (resolve_steam_config.py)
#   STEAM_USERNAME      Steam account             (secret)
#   STEAM_CONFIG_VDF    base64 config.vdf with a valid Steam Guard session (secret)
#   BUILD_DESCRIPTION   shows up in Steamworks' build list
#   DRY_RUN             "true" prepares and validates everything, uploads nothing
# =============================================================================
set -Eeuo pipefail

ARTIFACT_DIR="${ARTIFACT_DIR:?ARTIFACT_DIR is required}"
STAGING_DIR="${STAGING_DIR:-${RUNNER_TEMP:-/tmp}/steam-staging}"
STEAM_APP_ID="${STEAM_APP_ID:?STEAM_APP_ID is required}"
STEAM_DEPOT_ID="${STEAM_DEPOT_ID:?STEAM_DEPOT_ID is required}"
STEAM_BRANCH="${STEAM_BRANCH:?STEAM_BRANCH is required}"
BUILD_DESCRIPTION="${BUILD_DESCRIPTION:-}"
DRY_RUN="${DRY_RUN:-false}"

WORK_DIR="$(mktemp -d)"
STEAM_HOME="${WORK_DIR}/steam-home"

cleanup() {
  # config.vdf holds a live Steam session. It must not outlive the job even if
  # steamcmd dies badly.
  rm -rf "${WORK_DIR}"
}
trap cleanup EXIT

log() { echo "[steam] $*"; }

# ── Fingerprint a directory tree: relative path + content, in a stable order ──
# Used to prove the staging copy is identical to the verified artifact. Paths
# are part of the digest so a renamed file changes it.
fingerprint() {
  local root="$1"
  ( cd "${root}" && find . -type f -print0 \
      | LC_ALL=C sort -z \
      | xargs -0 -r sha256sum \
      | sha256sum \
      | cut -d' ' -f1 )
}

stage_content() {
  log "Staging content for depot ${STEAM_DEPOT_ID}"
  rm -rf "${STAGING_DIR}"
  mkdir -p "${STAGING_DIR}"
  # -a preserves the executable bit, which a Linux player needs to start and
  # which a plain `cp` would drop.
  cp -a "${ARTIFACT_DIR}/." "${STAGING_DIR}/"

  # The artifact manifest travels with the artifact but is not game content;
  # shipping it to players would leak nothing sensitive, but it is not part of
  # the build and does not belong in a depot.
  find "${STAGING_DIR}" -maxdepth 2 -name 'artifact-manifest.json' -delete
}

verify_staging_matches_source() {
  # The whole point of the immutable boundary is that promotion changes
  # nothing. Copying is the one place this script could break that, so it is
  # checked rather than assumed.
  local source_print staged_print
  source_print="$(fingerprint "${ARTIFACT_DIR}")"

  local tmp_compare="${WORK_DIR}/compare"
  mkdir -p "${tmp_compare}"
  cp -a "${STAGING_DIR}/." "${tmp_compare}/"
  # Put the manifest back so the two trees are compared on equal terms.
  if [ -f "${ARTIFACT_DIR}/artifact-manifest.json" ]; then
    cp -a "${ARTIFACT_DIR}/artifact-manifest.json" "${tmp_compare}/"
  fi
  staged_print="$(fingerprint "${tmp_compare}")"

  if [ "${source_print}" != "${staged_print}" ]; then
    echo "::error::The staged content is not identical to the verified artifact." >&2
    echo "::error::source ${source_print} != staged ${staged_print}" >&2
    echo "::error::Promotion must publish the exact bytes Build / Release produced." >&2
    return 1
  fi
  log "Staged content matches the verified artifact (${source_print:0:16}…)"
}

write_vdf_scripts() {
  # SteamCMD's app/depot scripts. Nothing project-specific is hardcoded: every
  # id comes from the resolved configuration.
  cat > "${WORK_DIR}/depot_${STEAM_DEPOT_ID}.vdf" <<EOF
"DepotBuildConfig"
{
  "DepotID" "${STEAM_DEPOT_ID}"
  "ContentRoot" "${STAGING_DIR}"
  "FileMapping"
  {
    "LocalPath" "*"
    "DepotPath" "."
    "recursive" "1"
  }
  "FileExclusion" "*.pdb"
}
EOF

  cat > "${WORK_DIR}/app_${STEAM_APP_ID}.vdf" <<EOF
"AppBuild"
{
  "AppID" "${STEAM_APP_ID}"
  "Desc" "${BUILD_DESCRIPTION}"
  "SetLive" "${STEAM_BRANCH}"
  "ContentRoot" "${STAGING_DIR}"
  "BuildOutput" "${WORK_DIR}/output"
  "Depots"
  {
    "${STEAM_DEPOT_ID}" "${WORK_DIR}/depot_${STEAM_DEPOT_ID}.vdf"
  }
}
EOF
  log "Wrote build scripts for app ${STEAM_APP_ID}, depot ${STEAM_DEPOT_ID}"
}

install_session() {
  # steamcmd reads a previously authorised session from config.vdf, which is
  # how a CI job logs in without a Steam Guard prompt. Written 600 into a
  # directory the trap removes.
  : "${STEAM_USERNAME:?STEAM_USERNAME is required}"
  : "${STEAM_CONFIG_VDF:?STEAM_CONFIG_VDF is required}"

  mkdir -p "${STEAM_HOME}/config"
  chmod 700 "${STEAM_HOME}" "${STEAM_HOME}/config"
  printf '%s' "${STEAM_CONFIG_VDF}" | base64 -d > "${STEAM_HOME}/config/config.vdf"
  chmod 600 "${STEAM_HOME}/config/config.vdf"

  if [ ! -s "${STEAM_HOME}/config/config.vdf" ]; then
    echo "::error::STEAM_CONFIG_VDF did not decode to anything. Regenerate it: base64 of a config.vdf from a machine where Steam Guard has already been satisfied." >&2
    return 1
  fi
  log "Steam session installed for the configured account"
}

upload() {
  local script="${WORK_DIR}/app_${STEAM_APP_ID}.vdf"
  log "Uploading to app ${STEAM_APP_ID}, branch '${STEAM_BRANCH}'"

  # +login takes the username only; the password/Guard code come from the
  # installed session, so no secret appears in the process list.
  HOME="${STEAM_HOME}" steamcmd \
    +@ShutdownOnFailedCommand 1 \
    +@NoPromptForPassword 1 \
    +login "${STEAM_USERNAME}" \
    +run_app_build "${script}" \
    +quit
}

main() {
  if [ ! -d "${ARTIFACT_DIR}" ]; then
    echo "::error::ARTIFACT_DIR ${ARTIFACT_DIR} does not exist. Nothing verified, nothing to publish." >&2
    exit 1
  fi

  stage_content
  verify_staging_matches_source
  write_vdf_scripts

  if [ "${DRY_RUN}" = "true" ]; then
    log "DRY RUN: content staged and build scripts written; nothing uploaded"
    if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
      {
        echo "### Steam upload (dry run)"
        echo
        echo "| Field | Value |"
        echo "|---|---|"
        echo "| App | \`${STEAM_APP_ID}\` |"
        echo "| Depot | \`${STEAM_DEPOT_ID}\` |"
        echo "| Branch | \`${STEAM_BRANCH}\` |"
        echo "| Content | \`$(du -sh "${STAGING_DIR}" | cut -f1)\` |"
        echo
        echo "Nothing was uploaded."
      } >> "${GITHUB_STEP_SUMMARY}"
    fi
    return 0
  fi

  install_session
  upload

  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    {
      echo "### Steam upload"
      echo
      echo "App \`${STEAM_APP_ID}\` depot \`${STEAM_DEPOT_ID}\` set live on branch \`${STEAM_BRANCH}\`."
    } >> "${GITHUB_STEP_SUMMARY}"
  fi
  log "Upload complete"
}

main "$@"
