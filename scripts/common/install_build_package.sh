#!/usr/bin/env bash
# install_build_package.sh -- put the toolkit's Unity package into the project
# for one build, and take it out again.
#
#   install_build_package.sh install --project <unity-project> --package <toolkit>/unity-package/Packages/com.company.build-pipeline
#   install_build_package.sh remove  --project <unity-project>
#
# install copies the package to <project>/Packages/com.company.build-pipeline,
# where Unity loads it as an embedded package -- no manifest.json edit. The
# project then needs no build script of its own: the pipeline runs
# Company.BuildPipeline.Editor.PlayerBuilder.Build / AddressableBuilder.Build
# from the toolkit revision the run uses. remove deletes the copy and restores
# Packages/packages-lock.json, which Unity rewrites for the embedded package.
#
# A project that already provides the package (embedded, or referenced in
# manifest.json) keeps its own copy; nothing is installed.
#
# Build methods (stdout and $GITHUB_OUTPUT, read by the build steps):
#   player-method        PlayerBuilder.Build when the project still has its own
#                        global PlayerBuilder class (a project script wins, as
#                        before), else Company.BuildPipeline.Editor.PlayerBuilder.Build
#   addressables-method  same rule for AddressableBuilder
#   installed            true when this run copied the package in
# docs/TOOLKIT_BUILD_PACKAGE.md
set -Eeuo pipefail

PACKAGE_NAME="com.company.build-pipeline"
MARKER=".installed-by-unity-build-workflows"
LOCK_BACKUP_NAME="packages-lock.json.unity-build-workflows"

log() { echo "[install_build_package] $*" >&2; }
die() { echo "::error::[install_build_package] $*" >&2; exit 1; }

emit() {
    printf '%s=%s\n' "$1" "$2"
    if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
        printf '%s=%s\n' "$1" "$2" >> "${GITHUB_OUTPUT}"
    fi
}

usage() {
    sed -n '2,8p' "$0" >&2
    exit 2
}

[[ $# -ge 1 ]] || usage
action="$1"
shift
project=""
package=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --project) project="${2:-}"; shift 2 ;;
        --package) package="${2:-}"; shift 2 ;;
        *) usage ;;
    esac
done
[[ -n "${project}" ]] || usage
[[ -d "${project}" ]] || die "Unity project not found: ${project}"

target="${project%/}/Packages/${PACKAGE_NAME}"
lock_file="${project%/}/Packages/packages-lock.json"
backup_dir="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
lock_backup="${backup_dir%/}/${LOCK_BACKUP_NAME}"

# A project class in the global namespace wins over the package's, so a
# project keeps its own builder until it deletes it. The match is a type
# declaration in a file without a namespace block; a namespaced class of the
# same name cannot be called as "PlayerBuilder.Build" anyway.
project_has_global_class() {
    local class="$1" file
    [[ -d "${project%/}/Assets" ]] || return 1
    while IFS= read -r file; do
        if ! grep -Eq '^[[:space:]]*namespace[[:space:]]' "${file}"; then
            return 0
        fi
    done < <(grep -rlE --include='*.cs' "class[[:space:]]+${class}([^A-Za-z0-9_]|$)" "${project%/}/Assets" 2>/dev/null || true)
    return 1
}

emit_methods() {
    if project_has_global_class PlayerBuilder; then
        log "Project has its own PlayerBuilder: using it."
        emit "player-method" "PlayerBuilder.Build"
    else
        emit "player-method" "Company.BuildPipeline.Editor.PlayerBuilder.Build"
    fi
    if project_has_global_class AddressableBuilder; then
        log "Project has its own AddressableBuilder: using it."
        emit "addressables-method" "AddressableBuilder.Build"
    else
        emit "addressables-method" "Company.BuildPipeline.Editor.AddressableBuilder.Build"
    fi
}

remove_copy() {
    if [[ -f "${target}/${MARKER}" ]]; then
        rm -rf "${target}"
        log "Removed ${target}"
    fi
    if [[ -f "${lock_backup}" ]]; then
        mv -f "${lock_backup}" "${lock_file}"
        log "Restored ${lock_file}"
    fi
}

case "${action}" in
    install)
        [[ -n "${package}" ]] || usage
        [[ -f "${package%/}/package.json" ]] || die "Toolkit package not found: ${package}"

        # A copy left by an interrupted run on a persistent workspace.
        remove_copy

        if [[ -d "${target}" ]]; then
            log "Project embeds its own ${PACKAGE_NAME}: not installing."
            emit "installed" "false"
            emit_methods
            exit 0
        fi
        if [[ -f "${project%/}/Packages/manifest.json" ]] \
           && grep -q "\"${PACKAGE_NAME}\"" "${project%/}/Packages/manifest.json"; then
            log "Packages/manifest.json references ${PACKAGE_NAME}: not installing."
            emit "installed" "false"
            emit_methods
            exit 0
        fi

        if [[ -f "${lock_file}" ]]; then
            mkdir -p "${backup_dir}"
            cp -f "${lock_file}" "${lock_backup}"
        fi
        mkdir -p "${target}"
        # Tests stay out: they need the Test Framework and are the toolkit's own.
        (cd "${package%/}" && tar --exclude='./Tests' --exclude='./Tests.meta' -cf - .) \
            | (cd "${target}" && tar -xf -)
        printf 'Copied by unity-build-workflows for one build; removed afterwards.\n' \
            > "${target}/${MARKER}"
        version="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "${package%/}/package.json" | head -1)"
        log "Installed ${PACKAGE_NAME} ${version} into ${target}"
        emit "installed" "true"
        emit_methods
        ;;
    remove)
        remove_copy
        ;;
    *)
        usage
        ;;
esac
