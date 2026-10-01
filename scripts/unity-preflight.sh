#!/usr/bin/env bash
# unity-preflight.sh -- entry point for the Unity environment preflight.
#
# Ensures the exact Unity Editor a project's ProjectSettings/ProjectVersion.txt
# names, plus the modules for the requested platforms, are installed, and
# prints UNITY_EDITOR=... for the caller. All logic lives in
# scripts/common/unity_preflight.py; this wrapper only finds a working
# Python 3.8+ (already a prerequisite of the toolkit's scripts) and explains
# how to get one when there is none.
#
# Usage (from any directory; Git Bash on Windows):
#   bash <toolkit>/scripts/unity-preflight.sh --project "$WORKTREE" --platform Android
#   eval "$(bash <toolkit>/scripts/unity-preflight.sh --project "$WORKTREE" --platform Android)"
#   bash <toolkit>/scripts/unity-preflight.sh --project "$WORKTREE" --check
#
# Interpreter override: UNITY_PREFLIGHT_PYTHON=/path/to/python
# See docs/UNITY_PREFLIGHT.md.
set -Eeuo pipefail

readonly EXIT_PREREQ=3
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly PREFLIGHT="${SCRIPT_DIR}/common/unity_preflight.py"

# True when "$@" is a real Python >= 3.8. Rejects the Windows Store
# "python3" placeholder, which exists on PATH but exits 49 instead of running.
python_ok() {
  "$@" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1
}

PYTHON=()
if [[ -n "${UNITY_PREFLIGHT_PYTHON:-}" ]]; then
  if ! python_ok "${UNITY_PREFLIGHT_PYTHON}"; then
    echo "ERROR: UNITY_PREFLIGHT_PYTHON=${UNITY_PREFLIGHT_PYTHON} is not a working Python 3.8+." >&2
    exit "${EXIT_PREREQ}"
  fi
  PYTHON=("${UNITY_PREFLIGHT_PYTHON}")
else
  for candidate in python3 python; do
    if command -v "${candidate}" >/dev/null 2>&1 && python_ok "${candidate}"; then
      PYTHON=("${candidate}")
      break
    fi
  done
  if [[ ${#PYTHON[@]} -eq 0 ]] && command -v py >/dev/null 2>&1 && python_ok py -3; then
    PYTHON=(py -3)
  fi
fi

if [[ ${#PYTHON[@]} -eq 0 ]]; then
  cat >&2 <<'EOF'
ERROR: Python 3.8+ was not found.

The Unity preflight -- like the toolkit's other scripts and the build job's
python3 steps -- needs Python 3.8 or newer. On Windows, a 'python3' that only
offers to open the Microsoft Store is a placeholder, not an interpreter.

Install Python, open a new shell, and re-run:
  Windows: winget install --id Python.Python.3.12 -e   (or https://www.python.org/downloads/)
  macOS:   brew install python@3.12                    (or https://www.python.org/downloads/)
  Linux:   sudo apt-get install -y python3             (or your distribution's package)

Or point UNITY_PREFLIGHT_PYTHON at an existing interpreter.
EOF
  exit "${EXIT_PREREQ}"
fi

exec "${PYTHON[@]}" "${PREFLIGHT}" "$@"
