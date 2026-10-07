#!/usr/bin/env bash
# fetch_submodules_ssh.sh -- fetch the project's submodules over SSH, exactly
# as .gitmodules names them.
#
# Run from the project checkout by the build and tests jobs when
# submodule-auth is `ssh` (reusable-build-platform.yml,
# reusable-unity-tests.yml). Both jobs carried this as the same 50 lines.
#
# Credentials, first that is set:
#   SUBMODULE_SSH_KEY        secret: the private key itself (written to a 600
#                            temp file, removed on exit)
#   SUBMODULE_SSH_KEY_FILE   a key file on the runner machine (runner .env)
#   otherwise                the runner account's own ~/.ssh keys
set -Eeuo pipefail

# actions/checkout rewrites submodule URLs to HTTPS and injects
# GITHUB_TOKEN, which cannot see a private submodule in another
# organization — git reports it as "not found". Fetch them here instead,
# so the URLs in .gitmodules are used exactly as written.
_key_file=""
cleanup() { rm -f "${_key_file:-}" "${_known_hosts:-}" 2>/dev/null || true; }
trap cleanup EXIT

# The runner's own SSH credentials need a home directory: a Windows
# runner service has no HOME in Git Bash, so ssh looked in /.ssh
# (the Git install), found no key and could not write known_hosts.
if [[ -z "${HOME:-}" || "${HOME}" == "/" ]] && [[ -n "${USERPROFILE:-}" ]]; then
  export HOME="$(cygpath -u "${USERPROFILE}" 2>/dev/null || printf '%s' "${USERPROFILE}")"
fi
_known_hosts="$(mktemp)"
_ssh_opts="-o StrictHostKeyChecking=accept-new -o BatchMode=yes -o UserKnownHostsFile=${_known_hosts}"
if [[ -n "${SUBMODULE_SSH_KEY:-}" ]]; then
  _key_file="$(mktemp)"
  printf '%s\n' "${SUBMODULE_SSH_KEY}" > "${_key_file}"
  chmod 600 "${_key_file}"
  _ssh_opts="-i ${_key_file} -o IdentitiesOnly=yes ${_ssh_opts}"
  echo "Authenticating submodules with SUBMODULE_SSH_KEY."
elif [[ -n "${SUBMODULE_SSH_KEY_FILE:-}" ]]; then
  # A key file on the runner machine, named in the runner's .env.
  _machine_key="$(cygpath -u "${SUBMODULE_SSH_KEY_FILE}" 2>/dev/null || printf '%s' "${SUBMODULE_SSH_KEY_FILE}")"
  if [[ ! -r "${_machine_key}" ]]; then
    echo "::error::SUBMODULE_SSH_KEY_FILE=${SUBMODULE_SSH_KEY_FILE} is not readable by $(whoami)."
    exit 1
  fi
  _ssh_opts="-i ${_machine_key} -o IdentitiesOnly=yes ${_ssh_opts}"
  echo "Authenticating submodules with the runner's key file ${SUBMODULE_SSH_KEY_FILE}."
else
  echo "Using the runner account's own SSH keys: $(whoami), ${HOME}/.ssh"
  ls "${HOME}/.ssh" 2>/dev/null | grep -E '^id_' | sed 's/^/  /' \
    || echo "::warning::No id_* key in ${HOME}/.ssh. Put a key with read access to the submodules there, or set SUBMODULE_SSH_KEY_FILE in the runner's .env, or the SUBMODULE_SSH_KEY secret."
fi

export GIT_SSH_COMMAND="ssh ${_ssh_opts}"
git submodule sync --recursive
# --force is not optional. actions/checkout runs `git clean -ffdx`, which
# empties a submodule's working tree while leaving its gitdir behind at the
# recorded commit. Without --force, `submodule update` sees the right SHA,
# concludes there is nothing to do, and leaves the directory empty — Unity
# then opens a project whose embedded packages have no package.json.
#
# core.longpaths matters on Windows: a runner workspace is already deep
# before the project path, and a Unity package that vendors a plugin tree
# goes past MAX_PATH. Git reports it per file as "unable to create file"
# and still exits non-zero, so the clone half-lands. -c propagates to the
# child git processes that clone each submodule; it is inert elsewhere.
git -c core.longpaths=true submodule update --init --recursive --force
