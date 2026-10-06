#!/usr/bin/env bash
# ensure_ruby.sh -- find (or install) a Ruby that can run fastlane, plus Bundler.
#
# A self-hosted runner service keeps the PATH it was configured with (the
# runner's .path file), so a Ruby installed afterwards is on disk but not on
# the job's PATH. Every candidate is therefore *run* and its version checked —
# never assumed from a file existing — and candidates come from asking the
# tools that installed Ruby, not from guessed directories:
#
#   1. `ruby` on PATH
#   2. the version managers' own answers: `brew --prefix ruby`,
#      `rbenv which ruby`, `asdf which ruby`, Windows `where ruby`
#   3. install — macOS: `brew install ruby`; Windows: winget, else (a runner
#      service has no winget) the RubyInstaller+DevKit download, silently into
#      the tool cache:
#      `winget install RubyInstallerTeam.RubyWithDevKit.3.4` — then ask again
#
# The first candidate that runs and reports Ruby >= RUBY_MIN wins (macOS's
# system Ruby 2.6 is too old for fastlane). Its bin directory and its gem bin
# directory are prepended to PATH for later steps ($GITHUB_PATH), and Bundler
# is installed when missing.
#
# Outputs (stdout, and $GITHUB_OUTPUT when set):
#   ruby-bin       absolute path of the Ruby executable
#   ruby-version   e.g. 3.4.1
#   ruby-source    path | brew | rbenv | asdf | where | toolcache | installed:<how>
#
# Env:
#   RUBY_MIN               minimum version (default 2.7)
#   ENSURE_RUBY_NO_INSTALL 1 = never install, only find
#   RUBY_WINDOWS_SERIES    RubyInstaller series on Windows (default 3.4)
#   RUBY_WINDOWS_INSTALLER_URL  exact installer to use instead of the newest
#
# Exit codes: 0 ok, 3 no usable Ruby (with the command to fix it on stderr).
set -Eeuo pipefail

readonly EXIT_PREREQ=3
RUBY_MIN="${RUBY_MIN:-2.7}"
# Windows installs: RubyInstaller+DevKit series (the newest release of it is
# looked up; the fallback URL is used when GitHub cannot be asked).
RUBY_WINDOWS_SERIES="${RUBY_WINDOWS_SERIES:-3.4}"
RUBY_WINDOWS_FALLBACK_URL="https://github.com/oneclick/rubyinstaller2/releases/download/RubyInstaller-3.4.11-1/rubyinstaller-devkit-3.4.11-1-x64.exe"

log()  { echo "[ensure_ruby] $*" >&2; }

# version_ge A B — true when dotted version A >= B
version_ge() {
  [ "$(printf '%s\n%s\n' "$2" "$1" | sort -t. -k1,1n -k2,2n -k3,3n | head -1)" = "$2" ]
}

# probe BIN — prints the version when BIN runs as a Ruby >= RUBY_MIN
probe() {
  local bin="$1" ver
  [ -n "${bin}" ] || return 1
  ver="$("${bin}" -e 'print RUBY_VERSION' 2>/dev/null)" || return 1
  [[ "${ver}" =~ ^[0-9]+\.[0-9]+ ]] || return 1
  if ! version_ge "${ver}" "${RUBY_MIN}"; then
    log "rejected ${bin}: Ruby ${ver} < ${RUBY_MIN}"
    return 1
  fi
  printf '%s' "${ver}"
}

# Homebrew lives at a documented prefix per architecture; it is still *run*
# (`brew --prefix`) before being trusted.
find_brew() {
  local b
  for b in "$(command -v brew 2>/dev/null || true)" /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -n "${b}" ] && "${b}" --prefix >/dev/null 2>&1 && { printf '%s' "${b}"; return 0; }
  done
  return 1
}

RUBY_BIN=""; RUBY_VERSION_FOUND=""; RUBY_SOURCE=""

try() {  # try SOURCE BIN
  local ver
  if ver="$(probe "$2")"; then
    RUBY_BIN="$2"; RUBY_VERSION_FOUND="${ver}"; RUBY_SOURCE="$1"
    return 0
  fi
  return 1
}

discover() {
  local p brew
  try path "$(command -v ruby 2>/dev/null || true)" && return 0
  if brew="$(find_brew)"; then
    p="$("${brew}" --prefix ruby 2>/dev/null || true)"
    [ -n "${p}" ] && try brew "${p}/bin/ruby" && return 0
  fi
  if command -v rbenv >/dev/null 2>&1; then
    try rbenv "$(rbenv which ruby 2>/dev/null || true)" && return 0
  fi
  if command -v asdf >/dev/null 2>&1; then
    try asdf "$(asdf which ruby 2>/dev/null || true)" && return 0
  fi
  if command -v where.exe >/dev/null 2>&1; then
    while IFS= read -r p; do
      p="${p%$'\r'}"
      try where "$(cygpath -u "${p}" 2>/dev/null || printf '%s' "${p}")" && return 0
    done < <(where.exe ruby 2>/dev/null || true)
    # The copy install_ruby_windows_toolcache put there on an earlier run.
    try toolcache "$(windows_ruby_dir)/bin/ruby.exe" && return 0
  fi
  return 1
}

# Where a winget-less Windows install goes: the runner's tool cache, kept
# across runs. Short on purpose -- MSYS2 nests headers deep enough to pass
# MAX_PATH under a long root.
windows_ruby_dir() {
  local base="${RUNNER_TOOL_CACHE:-${LOCALAPPDATA:-${HOME}}}"
  printf '%s/rb%s' "$(cygpath -u "${base}" 2>/dev/null || printf '%s' "${base}")" \
    "${RUBY_WINDOWS_SERIES//./}"
}

# The newest RubyInstaller+DevKit of RUBY_WINDOWS_SERIES, else a known one.
windows_installer_url() {
  local url
  if [ -n "${RUBY_WINDOWS_INSTALLER_URL:-}" ]; then
    printf '%s' "${RUBY_WINDOWS_INSTALLER_URL}"; return 0
  fi
  url="$(curl -fsSL --retry 2 --max-time 30 \
          "https://api.github.com/repos/oneclick/rubyinstaller2/releases?per_page=40" 2>/dev/null \
        | grep -oE "https://[^\"]*/rubyinstaller-devkit-${RUBY_WINDOWS_SERIES//./\\.}\.[0-9]+-[0-9]+-x64\.exe" \
        | head -1 || true)"
  printf '%s' "${url:-${RUBY_WINDOWS_FALLBACK_URL}}"
}

# No winget under a runner service (it is a per-user Store app): download the
# RubyInstaller+DevKit installer and run it silently, per user, into the tool
# cache. The DevKit build ships MSYS2 with the GCC toolchain, which fastlane's
# native gems need -- no `ridk install` and no network at gem-install time.
install_ruby_windows_toolcache() {
  local dir url exe winDir
  dir="$(windows_ruby_dir)"
  url="$(windows_installer_url)"
  exe="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/rubyinstaller-devkit.exe"
  winDir="$(cygpath -w "${dir}" 2>/dev/null || printf '%s' "${dir}")"
  if [ "${#winDir}" -gt 80 ]; then
    log "warning: ${winDir} is long; MSYS2 may exceed MAX_PATH. Set RUNNER_TOOL_CACHE to a short folder (e.g. C:\\t)."
  fi
  log "no usable Ruby and no winget — installing RubyInstaller+DevKit into ${winDir}"
  log "  ${url}"
  curl -fsSL --retry 3 -o "${exe}" "${url}" >&2 || { log "download failed"; return 1; }
  # MSYS would rewrite the /switches into paths without this.
  MSYS2_ARG_CONV_EXCL='*' "${exe}" /verysilent /suppressmsgboxes /norestart /currentuser \
    "/dir=${winDir}" /components=ruby,msys2 "/tasks=nomodpath,noassocfiles,noridkinstall" >&2 \
    || { log "installer failed (exit $?)"; rm -f "${exe}"; return 1; }
  rm -f "${exe}"
  INSTALLED_WITH="rubyinstaller"
  return 0
}

install_ruby() {
  local brew
  if [ "${ENSURE_RUBY_NO_INSTALL:-0}" = "1" ]; then
    return 1
  fi
  if brew="$(find_brew)"; then
    log "no usable Ruby — installing with Homebrew (brew install ruby)"
    "${brew}" install ruby >&2 || return 1
    INSTALLED_WITH="brew"
    return 0
  fi
  if command -v winget >/dev/null 2>&1 || command -v winget.exe >/dev/null 2>&1; then
    log "no usable Ruby — installing with winget (RubyInstaller with DevKit ${RUBY_WINDOWS_SERIES})"
    if ! winget install --id "RubyInstallerTeam.RubyWithDevKit.${RUBY_WINDOWS_SERIES}" --silent \
      --accept-package-agreements --accept-source-agreements >&2; then
      install_ruby_windows_toolcache
      return
    fi
    # winget updates the machine PATH, not this process's: re-read it.
    if command -v powershell.exe >/dev/null 2>&1; then
      local machine
      machine="$(powershell.exe -NoProfile -Command \
        "[Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')" \
        2>/dev/null | tr -d '\r')"
      PATH="$(cygpath -u -p "${machine}" 2>/dev/null || printf '%s' "${PATH}"):${PATH}"
    fi
    INSTALLED_WITH="winget"
    return 0
  fi
  if command -v cygpath >/dev/null 2>&1; then  # Windows (Git Bash), no winget
    install_ruby_windows_toolcache
    return
  fi
  return 1
}

INSTALLED_WITH=""
if ! discover; then
  if install_ruby && discover; then
    RUBY_SOURCE="installed:${INSTALLED_WITH}"
  else
    log "no Ruby >= ${RUBY_MIN} found and none could be installed."
    log "  macOS:   brew install ruby   (Homebrew: https://brew.sh)"
    log "  Windows: winget install RubyInstallerTeam.RubyWithDevKit.${RUBY_WINDOWS_SERIES}, or https://rubyinstaller.org (with DevKit)"
    log "  Linux:   install ruby >= ${RUBY_MIN} with the distribution's package manager"
    exit "${EXIT_PREREQ}"
  fi
fi

RUBY_DIR="$(dirname "${RUBY_BIN}")"
GEM_BIN_DIR="$("${RUBY_BIN}" -e 'print Gem.bindir' 2>/dev/null || true)"
export PATH="${RUBY_DIR}:${GEM_BIN_DIR:+${GEM_BIN_DIR}:}${PATH}"

# Bundler: present with this Ruby, or installed into its gem dir (no sudo for
# a Homebrew / rbenv / RubyInstaller Ruby), falling back to the user gem dir.
if ! "${RUBY_BIN}" -S bundle --version >/dev/null 2>&1; then
  log "installing Bundler for ${RUBY_BIN}"
  if ! "${RUBY_BIN}" -S gem install bundler --no-document >&2; then
    "${RUBY_BIN}" -S gem install bundler --no-document --user-install >&2
    USER_BIN="$("${RUBY_BIN}" -e 'print Gem.user_dir' 2>/dev/null)/bin"
    export PATH="${USER_BIN}:${PATH}"
    GEM_BIN_DIR="${USER_BIN}:${GEM_BIN_DIR}"
  fi
fi

if [ -n "${GITHUB_PATH:-}" ]; then
  # Later steps prepend in reverse order of writing.
  [ -n "${GEM_BIN_DIR}" ] && printf '%s\n' "${GEM_BIN_DIR//:/$'\n'}" >> "${GITHUB_PATH}"
  printf '%s\n' "${RUBY_DIR}" >> "${GITHUB_PATH}"
fi

# fastlane's dependencies compile C extensions (bigdecimal, json, ...). On
# macOS the compiler is Apple's clang, which refuses to run until the Xcode
# licence is accepted or the Command Line Tools are installed -- and then
# `bundle install` fails deep inside mkmf with "You have to install
# development tools first". Say what to run instead, before that.
if [ "$(uname -s 2>/dev/null)" = "Darwin" ]; then
  probe_dir="$(mktemp -d)"
  printf 'int main(void){return 0;}\n' > "${probe_dir}/probe.c"
  if ! cc "${probe_dir}/probe.c" -o "${probe_dir}/probe" >"${probe_dir}/cc.log" 2>&1; then
    log "the C compiler does not work for $(whoami): $(head -3 "${probe_dir}/cc.log" | tr '\n' ' ')"
    echo "::error title=No working C compiler on this Mac::fastlane's gems need it. As an administrator on the runner: sudo xcodebuild -license accept && xcode-select --install (then: clang --version)."
    rm -rf "${probe_dir}"
    exit "${EXIT_PREREQ}"
  fi
  rm -rf "${probe_dir}"
fi

log "using Ruby ${RUBY_VERSION_FOUND} at ${RUBY_BIN} (${RUBY_SOURCE})"
for kv in "ruby-bin=${RUBY_BIN}" "ruby-version=${RUBY_VERSION_FOUND}" "ruby-source=${RUBY_SOURCE}"; do
  echo "${kv}"
  [ -n "${GITHUB_OUTPUT:-}" ] && echo "${kv}" >> "${GITHUB_OUTPUT}"
done
exit 0
