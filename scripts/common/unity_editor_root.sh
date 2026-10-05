#!/usr/bin/env bash
# unity_editor_root.sh — sourced by the Unity preflight steps of
# reusable-build-platform.yml and reusable-unity-tests.yml.
#
# Maps the per-OS repository variables
#
#   UNITY_EDITOR_ROOT_WINDOWS   e.g. D:\GameDev\UnityEditor
#   UNITY_EDITOR_ROOT_MACOS     e.g. /Volumes/Data/Unity
#
# onto what unity_preflight.py reads, for the OS of the runner running the job
# (a repository variable reaches every runner, so a Windows path must never be
# applied on a Mac and vice versa):
#
#   * the folder IS one editor (<root>\Editor\Unity.exe, <root>/Unity.app)
#       -> UNITY_EDITOR=<that executable>   (preflight still verifies its version)
#   * otherwise it is an editors root (<root>\<version>\Editor\Unity.exe, the
#     Unity Hub layout)
#       -> UNITY_PREFLIGHT_INSTALL_ROOT=<root>   (searched first; a missing
#          version is installed there)
#
# A UNITY_EDITOR already set on the runner (its .env) is machine-specific and
# wins. The variable overrides a UNITY_PREFLIGHT_INSTALL_ROOT from the runner's
# .env, and says so. Nothing is printed for a runner whose OS has no variable.

_uer_log() { printf '[editor-root] %s\n' "$*" >&2; }

_uer_probe_path() {
    # Existence checks need a POSIX path under Git Bash; the value exported
    # keeps the native form the runner's tools expect.
    if command -v cygpath >/dev/null 2>&1; then
        cygpath -u "$1"
    else
        printf '%s' "$1"
    fi
}

_uer_native_path() {
    if command -v cygpath >/dev/null 2>&1; then
        cygpath -w "$1"
    else
        printf '%s' "$1"
    fi
}

apply_unity_editor_root() {
    local os="${RUNNER_OS:-}" root="" probe candidate exe=""
    case "${os}" in
        Windows) root="${UNITY_EDITOR_ROOT_WINDOWS:-}" ;;
        macOS)   root="${UNITY_EDITOR_ROOT_MACOS:-}" ;;
        *)       return 0 ;;
    esac
    # Trim surrounding whitespace and a trailing separator.
    root="$(printf '%s' "${root}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's#[\\/]*$##')"
    if [[ -z "${root}" ]]; then
        return 0
    fi

    if [[ -n "${UNITY_EDITOR:-}" ]]; then
        _uer_log "UNITY_EDITOR is already set on this runner (${UNITY_EDITOR}); it wins over the ${os} editor root '${root}'."
        return 0
    fi

    probe="$(_uer_probe_path "${root}")"
    if [[ "${os}" == "Windows" ]]; then
        for candidate in "${probe}/Editor/Unity.exe" "${probe}/Unity.exe"; do
            if [[ -f "${candidate}" ]]; then
                exe="$(_uer_native_path "${candidate}")"
                break
            fi
        done
    else
        for candidate in "${probe}/Unity.app/Contents/MacOS/Unity" "${probe}/Contents/MacOS/Unity"; do
            if [[ -f "${candidate}" ]]; then
                exe="${candidate}"
                break
            fi
        done
    fi

    if [[ -n "${exe}" ]]; then
        export UNITY_EDITOR="${exe}"
        _uer_log "${os} editor root '${root}' is a single editor: UNITY_EDITOR=${exe} (preflight verifies it is the project's version)."
        return 0
    fi

    if [[ -n "${UNITY_PREFLIGHT_INSTALL_ROOT:-}" && "${UNITY_PREFLIGHT_INSTALL_ROOT}" != "${root}" ]]; then
        _uer_log "Overriding the runner's UNITY_PREFLIGHT_INSTALL_ROOT (${UNITY_PREFLIGHT_INSTALL_ROOT}) with the repository's ${os} editor root."
    fi
    if [[ ! -d "${probe}" ]]; then
        _uer_log "Warning: ${os} editor root '${root}' does not exist on this runner; preflight will try to install the editor there."
    fi
    export UNITY_PREFLIGHT_INSTALL_ROOT="${root}"
    _uer_log "${os} editor root '${root}': searched first for <root>/<version>; a missing version is installed there."
    return 0
}

apply_unity_editor_root
