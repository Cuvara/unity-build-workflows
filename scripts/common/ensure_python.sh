#!/usr/bin/env bash
# ensure_python.sh -- find (or install) Python >= 3.8 for the toolkit's scripts
# on a self-hosted runner, and make `python3` work in later bash steps.
#
# Same rules as ensure_ruby.sh: every candidate is *run* and its version
# checked — never trusted because a file exists — and candidates come from
# asking, not from guessed directories:
#
#   1. `python3`, then `python` on PATH
#   2. the Windows launcher: `py -3` reports its interpreter
#      (sys.executable); macOS: `brew --prefix python@3`
#   3. install — Windows: `winget install --id Python.Python.3.12 --scope user`
#      (no admin); macOS: `brew install python@3.12` — then ask again
#
# Windows' Microsoft Store "python3" placeholder exists on PATH but exits
# instead of running Python; the probe rejects it like any broken candidate.
#
# The chosen interpreter's directory goes on $GITHUB_PATH, and on Windows a
# small `python3` wrapper is added too — python.org installs only python.exe,
# while the toolkit's bash steps call `python3`.
#
# Outputs (stdout, and $GITHUB_OUTPUT when set): python-bin, python-version,
# python-source (path | py-launcher | brew | installed:<how>).
# Env: PYTHON_MIN (default 3.8), ENSURE_PYTHON_NO_INSTALL=1 (never install).
# Exit codes: 0 ok, 3 no usable Python (with the command to fix it).
set -Eeuo pipefail

readonly EXIT_PREREQ=3
PYTHON_MIN="${PYTHON_MIN:-3.8}"

log() { echo "[ensure_python] $*" >&2; }

version_ge() {
  [ "$(printf '%s\n%s\n' "$2" "$1" | sort -t. -k1,1n -k2,2n -k3,3n | head -1)" = "$2" ]
}

# probe BIN — prints "<version> <sys.executable>" when BIN is Python >= PYTHON_MIN
probe() {
  local bin="$1" out ver exe
  [ -n "${bin}" ] || return 1
  out="$("${bin}" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3], sys.executable)' 2>/dev/null)" || return 1
  out="${out%$'\r'}"
  ver="${out%% *}"; exe="${out#* }"
  [[ "${ver}" =~ ^[0-9]+\.[0-9]+ ]] || return 1
  if ! version_ge "${ver}" "${PYTHON_MIN}"; then
    log "rejected ${bin}: Python ${ver} < ${PYTHON_MIN}"
    return 1
  fi
  printf '%s %s' "${ver}" "${exe}"
}

PY_BIN=""; PY_VERSION=""; PY_SOURCE=""

try() {  # try SOURCE BIN
  local out
  if out="$(probe "$2")"; then
    PY_VERSION="${out%% *}"
    PY_BIN="${out#* }"
    PY_BIN="$(cygpath -u "${PY_BIN}" 2>/dev/null || printf '%s' "${PY_BIN}")"
    PY_SOURCE="$1"
    return 0
  fi
  return 1
}

discover() {
  local b p
  for b in python3 python; do
    try path "$(command -v "${b}" 2>/dev/null || true)" && return 0
  done
  if command -v py >/dev/null 2>&1; then
    # The launcher picks the newest installed Python 3; ask it which.
    p="$(py -3 -c 'import sys; print(sys.executable)' 2>/dev/null | tr -d '\r' || true)"
    [ -n "${p}" ] && try py-launcher "$(cygpath -u "${p}" 2>/dev/null || printf '%s' "${p}")" && return 0
  fi
  if command -v brew >/dev/null 2>&1; then
    p="$(brew --prefix python@3 2>/dev/null || true)"
    [ -n "${p}" ] && try brew "${p}/bin/python3" && return 0
  fi
  return 1
}

refresh_windows_path() {
  # winget updates the registry PATH, not this process's: re-read it.
  local reg
  command -v powershell.exe >/dev/null 2>&1 || return 0
  reg="$(powershell.exe -NoProfile -Command \
    "[Environment]::GetEnvironmentVariable('Path','User') + ';' + [Environment]::GetEnvironmentVariable('Path','Machine')" \
    2>/dev/null | tr -d '\r')"
  [ -n "${reg}" ] && PATH="$(cygpath -u -p "${reg}" 2>/dev/null || true):${PATH}"
}

INSTALLED_WITH=""
install_python() {
  [ "${ENSURE_PYTHON_NO_INSTALL:-0}" = "1" ] && return 1
  if command -v winget >/dev/null 2>&1 || command -v winget.exe >/dev/null 2>&1; then
    log "no usable Python — installing with winget (Python.Python.3.12, user scope, no admin)"
    winget install --id Python.Python.3.12 -e --scope user --silent \
      --accept-package-agreements --accept-source-agreements >&2 || return 1
    refresh_windows_path
    INSTALLED_WITH="winget"
    return 0
  fi
  if command -v brew >/dev/null 2>&1; then
    log "no usable Python — installing with Homebrew (brew install python@3.12)"
    brew install python@3.12 >&2 || return 1
    INSTALLED_WITH="brew"
    return 0
  fi
  return 1
}

if ! discover; then
  if install_python && discover; then
    PY_SOURCE="installed:${INSTALLED_WITH}"
  else
    log "no Python >= ${PYTHON_MIN} found and none could be installed."
    log "  Windows: winget install --id Python.Python.3.12 -e --scope user"
    log "  macOS:   brew install python@3.12"
    log "  Linux:   install python3 >= ${PYTHON_MIN} with the distribution's package manager"
    exit "${EXIT_PREREQ}"
  fi
fi

PY_DIR="$(dirname "${PY_BIN}")"
export PATH="${PY_DIR}:${PATH}"
SHIM_DIR=""
case "$(uname -s 2>/dev/null)" in
  MINGW*|MSYS*|CYGWIN*)
    # python.org's Windows installer ships python.exe only.
    SHIM_DIR="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/toolkit-python3"
    mkdir -p "${SHIM_DIR}"
    printf '#!/usr/bin/env bash\nexec "%s" "$@"\n' "${PY_BIN}" > "${SHIM_DIR}/python3"
    chmod +x "${SHIM_DIR}/python3"
    ;;
esac

if [ -n "${GITHUB_PATH:-}" ]; then
  # Later lines take precedence: the shim, if any, wins.
  printf '%s\n' "$(cygpath -w "${PY_DIR}" 2>/dev/null || printf '%s' "${PY_DIR}")" >> "${GITHUB_PATH}"
  [ -n "${SHIM_DIR}" ] && printf '%s\n' "$(cygpath -w "${SHIM_DIR}" 2>/dev/null || printf '%s' "${SHIM_DIR}")" >> "${GITHUB_PATH}"
fi

log "using Python ${PY_VERSION} at ${PY_BIN} (${PY_SOURCE})"
for kv in "python-bin=${PY_BIN}" "python-version=${PY_VERSION}" "python-source=${PY_SOURCE}"; do
  echo "${kv}"
  if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "${kv}" >> "${GITHUB_OUTPUT}"; fi
done
exit 0
