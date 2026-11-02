#!/usr/bin/env bash
# clean_workspace.sh -- reset the project checkout to the commit, keeping the
# Unity Library/ folder unless this is a clean build.
#
# Run from the repository root by the build and tests jobs, right after the
# project checkout (which runs with clean: false) and the toolkit checkout
# (reusable-build-platform.yml, reusable-unity-tests.yml).
#
# actions/checkout's default clean is `git clean -ffdx`, which on a self-hosted
# runner deletes <project>/Library/ every run: BUILD_CLEAN=false still paid a
# full reimport, script compile, shader, Burst and IL2CPP build. This does the
# same reset but spares Library/ and the toolkit checkout.
#
# Env:
#   CLEAN_BUILD    true deletes Library/ too (BUILD_CLEAN / clean-build input)
#   PROJECT_PATH   the Unity project, relative to the repository root ('.' ok)
#   TOOLKIT_PATH   the toolkit checkout to keep (default .toolkit)
set -Eeuo pipefail

clean_build="${CLEAN_BUILD:-false}"
project="${PROJECT_PATH:-.}"
toolkit="${TOOLKIT_PATH:-.toolkit}"

project="${project#./}"
project="${project%/}"
if [[ -z "${project}" || "${project}" == "." ]]; then
  library="Library"
else
  library="${project}/Library"
fi
toolkit="${toolkit#./}"
toolkit="${toolkit%/}"

git reset --hard --quiet HEAD

# Pathspec excludes, anchored at the repository root, so only this project's
# Library/ is spared. (`git clean -e` cannot do this: with -x an -e pattern
# only marks the path ignored, and -x then removes it.)
keep=(":(top,exclude)${toolkit}/")
if [[ "${clean_build}" == "true" ]]; then
  echo "Clean build: removing every untracked and ignored file, Library included."
else
  keep+=(":(top,exclude)${library}/")
  echo "Incremental build: keeping ${library}/ (BUILD_CLEAN=true or clean-build: true rebuilds it)."
fi

git clean -ffdxq -- ":/" "${keep[@]}"

if [[ "${clean_build}" != "true" && -d "${library}" ]]; then
  echo "Library kept: ${library}/"
fi
