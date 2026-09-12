#!/usr/bin/env bash
# =============================================================================
# resolve_build_flow.sh — Build flow resolver (branch-based CI automation)
#
# Reads trigger context + grouped configuration from env, writes KEY=value
# lines to stdout AND appends to $GITHUB_OUTPUT when that variable is set in
# the environment. All diagnostic/report output goes to stderr only — never
# to stdout (tests parse stdout as pure KEY=value pairs).
#
# ---------------------------------------------------------------------------
# Resolution priority (every setting), per CONFIG_CONTRACT.md:
#   workflow_dispatch input (IN_*)  >  new repo variable  >  legacy repo
#   variable  >  toolkit default
# For push/pull_request there is no dispatch layer: new > legacy > default.
#
# For every grouped setting the resolver accepts BOTH of these env-name
# styles for the "new" tier and BOTH of these for the "legacy" tier (first
# non-empty wins within its tier; new tier always beats legacy tier):
#   new tier:    NEW_<SETTING>            (preferred; from vars.<NEW_NAME>)
#                <SETTING>                (bare new-variable name)
#   legacy tier: LEG_<LEGACY_SETTING>     (preferred; from vars.<LEGACY_NAME>)
#                <LEGACY_SETTING>         (bare legacy-variable name)
#                VAR_<LEGACY_SETTING>     (original toolkit env name — kept
#                                           so existing consumers that only
#                                           set VAR_* resolve identically to
#                                           today)
# Dispatch inputs always use IN_<...>.
#
# See docs/BRANCH_FLOW_CONTRACT.md and CONFIG_CONTRACT.md for the full table
# of settings, env names, and defaults.
# =============================================================================
set -Eeuo pipefail

# ---------------------------------------------------------------------------
# Logging — stderr only
# ---------------------------------------------------------------------------
_log_prefix="resolve-build-flow"
log_info()  { printf '[%s] [INFO]  %s: %s\n'  "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "${_log_prefix}" "$*" >&2; }
log_warn()  { printf '[%s] [WARN]  %s: %s\n'  "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "${_log_prefix}" "$*" >&2; }
log_error() { printf '[%s] [ERROR] %s: %s\n'  "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "${_log_prefix}" "$*" >&2; }

# ---------------------------------------------------------------------------
# emit KEY VALUE — write to stdout and (if set) append to $GITHUB_OUTPUT
# ---------------------------------------------------------------------------
emit() {
    local key="$1" val="$2"
    printf '%s=%s\n' "${key}" "${val}"
    if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
        printf '%s=%s\n' "${key}" "${val}" >> "${GITHUB_OUTPUT}"
    fi
}

# ---------------------------------------------------------------------------
# resolve_setting DEFAULT NEW_DISPLAY_NAME [TIER VALUE]...
#   Returns (via stdout) the first non-empty VALUE, scanning tiers in the
#   order given by the caller (caller is responsible for ordering tiers
#   dispatch > variable-new > variable-legacy). Sets the global
#   _resolved_source to the matching tier label, or "default" if none
#   matched. When the matched tier is "variable-legacy" and NEW_DISPLAY_NAME
#   is non-empty, logs a deprecation warning naming the new variable.
# ---------------------------------------------------------------------------
# NOTE: resolve_setting sets globals _resolved_value/_resolved_source directly
# (rather than "returning" via stdout/command substitution) because command
# substitution forks a subshell — any variable it sets would be lost the
# instant the subshell exits. Callers MUST invoke it as a plain statement
# (not inside $(...)) and then read _resolved_value / _resolved_source.
_resolved_source="default"
_resolved_value=""
resolve_setting() {
    local default_val="$1"; shift
    local new_display_name="$1"; shift
    while [[ $# -ge 2 ]]; do
        local tier="$1" val="$2"
        if [[ -n "${val}" ]]; then
            _resolved_source="${tier}"
            _resolved_value="${val}"
            if [[ "${tier}" == "variable-legacy" && -n "${new_display_name}" ]]; then
                log_warn "Legacy variable in use; please migrate to '${new_display_name}'."
            fi
            return 0
        fi
        shift 2
    done
    _resolved_source="default"
    _resolved_value="${default_val}"
}

# validate_bool NAME VALUE — fail fast on anything but true|false
validate_bool() {
    local name="$1" val="$2"
    if [[ "${val}" != "true" && "${val}" != "false" ]]; then
        log_error "Invalid ${name}='${val}'. Allowed: true false"
        exit 1
    fi
}

# validate_positive_int NAME VALUE
validate_positive_int() {
    local name="$1" val="$2"
    if ! [[ "${val}" =~ ^[0-9]+$ ]] || [[ "${val}" -le 0 ]]; then
        log_error "Invalid ${name}='${val}'. Must be a positive integer."
        exit 1
    fi
}

# ---------------------------------------------------------------------------
# Trigger inputs — read from env, never fail on missing (default to empty)
# ---------------------------------------------------------------------------
EVENT_NAME="${EVENT_NAME:-}"
REF_NAME="${REF_NAME:-}"
BASE_REF="${BASE_REF:-}"
IN_PLATFORM="${IN_PLATFORM:-}"

# ── Platform capabilities ──────────────────────────────────────────────────
# PLATFORMS declares which targets this project can build at all — a different
# question from *_BUILD_PLATFORMS, which says which of them a branch builds.
# Accepts the same CSV/space forms as the platform lists. Unset means all, so
# projects that predate the capability model are unaffected.
PLATFORM_CAPABILITIES="$(printf '%s' "${NEW_PLATFORMS:-${PLATFORMS:-}}" | tr ',' ' ' | tr -s ' ')"
PLATFORM_CAPABILITIES="${PLATFORM_CAPABILITIES# }"
PLATFORM_CAPABILITIES="${PLATFORM_CAPABILITIES% }"
IN_ENVIRONMENT="${IN_ENVIRONMENT:-production}"
IN_RUN_TESTS="${IN_RUN_TESTS:-false}"
IN_TEST_MODE="${IN_TEST_MODE:-All}"
IN_BUILD_ADDRESSABLES="${IN_BUILD_ADDRESSABLES:-false}"
IN_DEFINE_SYMBOLS="${IN_DEFINE_SYMBOLS:-}"   # manual-dispatch extra define symbols (optional)
IN_CLEAN_BUILD="${IN_CLEAN_BUILD:-auto}"     # auto | true | false

log_info "EVENT_NAME=${EVENT_NAME} REF_NAME=${REF_NAME} BASE_REF=${BASE_REF}"

# ---------------------------------------------------------------------------
# Branch classification helpers
# ---------------------------------------------------------------------------
_is_develop() { [[ "${1}" == "develop" ]]; }
_is_staging()  { [[ "${1}" == "staging" ]]; }
_is_release()  { [[ "${1}" == release-* || "${1}" == release/* ]]; }

# ---------------------------------------------------------------------------
# Platform validation and CSV parsing
# ---------------------------------------------------------------------------
VALID_PLATFORMS="Android WebGL Linux64 LinuxServer Windows64 iOS"
VALID_RUNNER_MODES="docker self-hosted-windows self-hosted-macos auto"

validate_platform() {
    local plat="$1"
    for valid in ${VALID_PLATFORMS}; do
        [[ "${plat}" == "${valid}" ]] && return 0
    done
    return 1
}

validate_runner_mode() {
    local mode="$1" name="$2"
    for valid in ${VALID_RUNNER_MODES}; do
        [[ "${mode}" == "${valid}" ]] && return 0
    done
    log_error "Invalid ${name}='${mode}'. Allowed: ${VALID_RUNNER_MODES}"
    exit 1
}

# parse_platforms CSV_STRING VAR_NAME
#   Parses comma-separated platform list, validates each entry.
#   Exits with error if any platform is invalid.
#   Prints validated platforms space-separated to stdout.
parse_platforms() {
    local csv="$1" var_name="$2"
    # Replace commas with spaces for safe iteration (IFS stays default)
    local normalized="${csv//,/ }"
    local platforms=()
    for plat in ${normalized}; do
        # Trim any remaining whitespace
        plat="$(echo "${plat}" | tr -d '[:space:]')"
        [[ -z "${plat}" ]] && continue
        if ! validate_platform "${plat}"; then
            log_error "Invalid platform '${plat}' in ${var_name}='${csv}'. Allowed: ${VALID_PLATFORMS}"
            exit 1
        fi
        platforms+=("${plat}")
    done
    echo "${platforms[*]}"
}

# set_platforms_from_list SPACE_SEPARATED_LIST
#   Sets build_android/webgl/linux64/linuxserver/ios from a validated platform list.
#   iOS is NEVER set from branch flows (only manual dispatch).
# ---------------------------------------------------------------------------
# platform_enabled PLATFORM
#   True when the project declares this platform as a capability.
#
#   PLATFORMS is the project's capability declaration: which targets it can
#   build AT ALL. That is a different question from *_BUILD_PLATFORMS, which
#   says which of them a given branch builds. A project that cannot build iOS
#   should never see an iOS job, whatever a branch variable says.
#
#   Unset means every platform, so projects that predate the capability model
#   keep their current behaviour. Declare PLATFORMS to narrow it.
# ---------------------------------------------------------------------------
platform_enabled() {
    local plat="$1"
    [[ -z "${PLATFORM_CAPABILITIES}" ]] && return 0
    local cap
    for cap in ${PLATFORM_CAPABILITIES}; do
        [[ "${cap}" == "${plat}" ]] && return 0
    done
    return 1
}

set_platforms_from_list() {
    local list="$1" allow_ios="${2:-false}"
    build_android="false"
    build_webgl="false"
    build_linux64="false"
    build_linuxserver="false"
    build_windows64="false"
    if [[ "${allow_ios}" == "true" ]]; then
        build_ios="false"
    fi
    for plat in ${list}; do
        # Capability wins over selection. Every platform path funnels through
        # here, so filtering once covers branch flow and manual dispatch alike.
        if ! platform_enabled "${plat}"; then
            log_warn "${plat} requested but not in PLATFORMS; skipping (project capability)"
            skipped_platforms+=("${plat}: not enabled in PLATFORMS")
            continue
        fi
        case "${plat}" in
            Android)     build_android="true" ;;
            WebGL)       build_webgl="true" ;;
            Linux64)     build_linux64="true" ;;
            LinuxServer) build_linuxserver="true" ;;
            Windows64)   build_windows64="true" ;;
            iOS)
                if [[ "${allow_ios}" == "true" ]]; then
                    build_ios="true"
                else
                    log_warn "iOS in platform list ignored for branch flow (manual dispatch only)"
                fi
                ;;
        esac
    done
}

# ---------------------------------------------------------------------------
# Output variable defaults (overridden per flow below)
# ---------------------------------------------------------------------------
flow_type="none"
environment="development"
run_tests="false"
test_mode="None"
build_addressables="false"
build_android="false"
build_webgl="false"
build_linux64="false"
build_linuxserver="false"
build_windows64="false"
build_ios="false"
signing="none"
platform_source="default"
build_type=""               # development | release — the lifecycle
android_export_type="apk"   # apk | aab, derived from the lifecycle (see below)
define_symbols=""           # extra Scripting Define Symbols (branch-scoped, additive)
skipped_platforms=()        # human-readable "PLATFORM: reason" notes for the report

# ---------------------------------------------------------------------------
# Default platform lists per branch (used when no repo variable is set)
# UNCHANGED from today — do not shrink these.
# ---------------------------------------------------------------------------
DEFAULT_DEVELOP_PLATFORMS="Android WebGL"
DEFAULT_STAGING_PLATFORMS="Android WebGL Linux64 LinuxServer Windows64"
DEFAULT_RELEASE_PLATFORMS="Android WebGL Linux64 LinuxServer Windows64"

# ---------------------------------------------------------------------------
# Group: UNITY
# ---------------------------------------------------------------------------
NEW_UNITY_VERSION="${NEW_UNITY_VERSION:-}"
NEW_UNITY_PROJECT_PATH="${NEW_UNITY_PROJECT_PATH:-}"
NEW_UNITY_BUILD_METHOD="${NEW_UNITY_BUILD_METHOD:-}"
TOOLKIT_DEFAULT_UNITY_VERSION="6000.0.26f1"

resolve_setting "." "" \
    variable-new "${NEW_UNITY_PROJECT_PATH}"
unity_project_path="${_resolved_value}"
unity_project_path_source="${_resolved_source}"

resolve_setting "" "" \
    variable-new "${NEW_UNITY_BUILD_METHOD}"
unity_build_method="${_resolved_value}"
unity_build_method_source="${_resolved_source}"

if [[ -n "${NEW_UNITY_VERSION}" ]]; then
    unity_version="${NEW_UNITY_VERSION}"
    unity_version_source="variable-new"
    log_info "Unity version from repo variable: ${unity_version}"
else
    _pv_file="${unity_project_path}/ProjectSettings/ProjectVersion.txt"
    _detected=""
    if [[ -f "${_pv_file}" ]]; then
        _detected="$(grep -m1 '^m_EditorVersion:' "${_pv_file}" 2>/dev/null | sed -E 's/^m_EditorVersion:[[:space:]]*//' | tr -d '[:space:]' || true)"
    fi
    if [[ -n "${_detected}" ]]; then
        unity_version="${_detected}"
        unity_version_source="project-version-file"
        log_info "Unity version detected from ProjectVersion.txt (${_pv_file}): ${unity_version}"
    else
        unity_version="${TOOLKIT_DEFAULT_UNITY_VERSION}"
        unity_version_source="default"
        log_info "Unity version not set and ProjectVersion.txt not found/parseable; using toolkit default: ${unity_version}"
    fi
fi

# ---------------------------------------------------------------------------
# resolve_branch_platforms BRANCH_TYPE
#   Resolves platforms for a push/PR event using new/legacy repo variables
#   or defaults. Sets build_* flags and platform_source.
#   BRANCH_TYPE: develop | staging | release
# ---------------------------------------------------------------------------
resolve_branch_platforms() {
    local branch_type="$1" branch_upper
    branch_upper="$(echo "${branch_type}" | tr '[:lower:]' '[:upper:]')"
    local new_a new_b leg_a leg_b leg_c default_list new_display
    new_display="BUILD_${branch_upper}_PLATFORMS"
    default_list="$(eval echo "\${DEFAULT_${branch_upper}_PLATFORMS}")"

    new_a="$(eval echo "\${NEW_BUILD_${branch_upper}_PLATFORMS:-}")"
    new_b="$(eval echo "\${BUILD_${branch_upper}_PLATFORMS:-}")"
    leg_a="$(eval echo "\${LEG_${branch_upper}_BUILD_PLATFORMS:-}")"
    leg_b="$(eval echo "\${${branch_upper}_BUILD_PLATFORMS:-}")"
    leg_c="$(eval echo "\${VAR_${branch_upper}_BUILD_PLATFORMS:-}")"

    local resolved
    resolve_setting "${default_list}" "${new_display}" \
        variable-new "${new_a}" \
        variable-new "${new_b}" \
        variable-legacy "${leg_a}" \
        variable-legacy "${leg_b}" \
        variable-legacy "${leg_c}"
    resolved="${_resolved_value}"
    platform_source="${_resolved_source}"

    local var_name_used="${new_display} (or legacy equivalent)"
    if [[ "${platform_source}" == "default" ]]; then
        log_info "Platforms from defaults: ${resolved}"
    else
        log_info "Platforms (${platform_source}) resolved to '${resolved}' for ${var_name_used}"
    fi
    local validated
    validated="$(parse_platforms "${resolved}" "${new_display}")"
    set_platforms_from_list "${validated}"
}

# ---------------------------------------------------------------------------
# resolve_branch_optional BRANCH_TYPE
#   Resolves per-branch run-tests and build-addressables toggles from new
#   or legacy repo variables. Only overrides if resolved value is non-empty
#   relative to the default (which callers apply beforehand).
# ---------------------------------------------------------------------------
resolve_branch_optional() {
    local branch_type="$1" branch_upper
    branch_upper="$(echo "${branch_type}" | tr '[:lower:]' '[:upper:]')"

    local rt_new_a rt_new_b rt_leg_a rt_leg_b rt_leg_c
    rt_new_a="$(eval echo "\${NEW_TEST_${branch_upper}_ENABLED:-}")"
    rt_new_b="$(eval echo "\${TEST_${branch_upper}_ENABLED:-}")"
    rt_leg_a="$(eval echo "\${LEG_${branch_upper}_RUN_TESTS:-}")"
    rt_leg_b="$(eval echo "\${${branch_upper}_RUN_TESTS:-}")"
    rt_leg_c="$(eval echo "\${VAR_${branch_upper}_RUN_TESTS:-}")"

    local rt_resolved
    resolve_setting "${run_tests}" "TEST_${branch_upper}_ENABLED" \
        variable-new "${rt_new_a}" \
        variable-new "${rt_new_b}" \
        variable-legacy "${rt_leg_a}" \
        variable-legacy "${rt_leg_b}" \
        variable-legacy "${rt_leg_c}"
    rt_resolved="${_resolved_value}"
    if [[ "${_resolved_source}" != "default" ]]; then
        validate_bool "TEST_${branch_upper}_ENABLED" "${rt_resolved}"
        run_tests="${rt_resolved}"
        run_tests_source="${_resolved_source}"
        log_info "run-tests (${_resolved_source}) = ${run_tests}"
    fi

    local addr_new_a addr_new_b addr_leg_a addr_leg_b addr_leg_c
    addr_new_a="$(eval echo "\${NEW_ADDRESSABLES_${branch_upper}_ENABLED:-}")"
    addr_new_b="$(eval echo "\${ADDRESSABLES_${branch_upper}_ENABLED:-}")"
    addr_leg_a="$(eval echo "\${LEG_${branch_upper}_BUILD_ADDRESSABLES:-}")"
    addr_leg_b="$(eval echo "\${${branch_upper}_BUILD_ADDRESSABLES:-}")"
    addr_leg_c="$(eval echo "\${VAR_${branch_upper}_BUILD_ADDRESSABLES:-}")"

    local addr_resolved
    resolve_setting "${build_addressables}" "ADDRESSABLES_${branch_upper}_ENABLED" \
        variable-new "${addr_new_a}" \
        variable-new "${addr_new_b}" \
        variable-legacy "${addr_leg_a}" \
        variable-legacy "${addr_leg_b}" \
        variable-legacy "${addr_leg_c}"
    addr_resolved="${_resolved_value}"
    if [[ "${_resolved_source}" != "default" ]]; then
        validate_bool "ADDRESSABLES_${branch_upper}_ENABLED" "${addr_resolved}"
        build_addressables="${addr_resolved}"
        addressables_source="${_resolved_source}"
        log_info "build-addressables (${_resolved_source}) = ${build_addressables}"
    fi
}

# ---------------------------------------------------------------------------
# resolve_branch_define_symbols BRANCH_TYPE
#   Resolves extra Scripting Define Symbols from new/legacy per-branch repo
#   variables. Additive: merged into the project's existing symbols at build
#   time (see apply_define_symbols.sh).
# ---------------------------------------------------------------------------
resolve_branch_define_symbols() {
    local branch_type="$1" branch_upper
    branch_upper="$(echo "${branch_type}" | tr '[:lower:]' '[:upper:]')"

    local new_val leg_a leg_b
    new_val="$(eval echo "\${NEW_${branch_upper}_DEFINE_SYMBOLS:-}")"
    leg_a="$(eval echo "\${LEG_${branch_upper}_DEFINE_SYMBOLS:-}")"
    leg_b="$(eval echo "\${VAR_${branch_upper}_DEFINE_SYMBOLS:-}")"

    local resolved
    resolve_setting "" "UNITY_${branch_upper}_DEFINE_SYMBOLS" \
        variable-new "${new_val}" \
        variable-legacy "${leg_a}" \
        variable-legacy "${leg_b}"
    resolved="${_resolved_value}"
    if [[ -n "${resolved}" ]]; then
        define_symbols="${resolved}"
        log_info "define-symbols (${_resolved_source}) = '${define_symbols}'"
    fi
}

# ---------------------------------------------------------------------------
# Group: TEST (global toggles) — editmode / playmode / fail-fast
# ---------------------------------------------------------------------------
resolve_setting "true" "" \
    variable-new "${NEW_TEST_EDITMODE_ENABLED:-}" \
    variable-new "${TEST_EDITMODE_ENABLED:-}"
test_editmode="${_resolved_value}"
validate_bool "TEST_EDITMODE_ENABLED" "${test_editmode}"

resolve_setting "true" "" \
    variable-new "${NEW_TEST_PLAYMODE_ENABLED:-}" \
    variable-new "${TEST_PLAYMODE_ENABLED:-}"
test_playmode="${_resolved_value}"
validate_bool "TEST_PLAYMODE_ENABLED" "${test_playmode}"

resolve_setting "false" "" \
    variable-new "${NEW_TEST_FAIL_FAST:-}" \
    variable-new "${TEST_FAIL_FAST:-}"
test_fail_fast="${_resolved_value}"
validate_bool "TEST_FAIL_FAST" "${test_fail_fast}"

if [[ "${test_editmode}" == "true" && "${test_playmode}" == "true" ]]; then
    derived_test_mode="All"
elif [[ "${test_editmode}" == "true" ]]; then
    derived_test_mode="EditMode"
elif [[ "${test_playmode}" == "true" ]]; then
    derived_test_mode="PlayMode"
else
    derived_test_mode="None"
fi

# ---------------------------------------------------------------------------
# Group: BUILD (global) — timeout minutes, clean build
# ---------------------------------------------------------------------------
resolve_setting "120" "" \
    variable-new "${NEW_BUILD_TIMEOUT_MINUTES:-}" \
    variable-new "${BUILD_TIMEOUT_MINUTES:-}"
build_timeout_minutes="${_resolved_value}"
validate_positive_int "BUILD_TIMEOUT_MINUTES" "${build_timeout_minutes}"

resolve_setting "false" "" \
    variable-new "${NEW_BUILD_CLEAN:-}" \
    variable-new "${BUILD_CLEAN:-}"
_build_clean_var="${_resolved_value}"
if [[ "${_resolved_source}" != "default" ]]; then
    validate_bool "BUILD_CLEAN" "${_build_clean_var}"
fi

if [[ "${IN_CLEAN_BUILD}" != "auto" ]]; then
    validate_bool "IN_CLEAN_BUILD" "${IN_CLEAN_BUILD}"
    clean_build="${IN_CLEAN_BUILD}"
    clean_build_source="workflow_dispatch"
else
    clean_build="${_build_clean_var}"
    clean_build_source="${_resolved_source}"
fi

# ---------------------------------------------------------------------------
# Group: RUNNER / BUILD ENGINE
#
# Runner  = WHERE the job runs   → RUNNER_TYPE (github-hosted|self-hosted) + RUNNER_LABELS
# Engine  = HOW Unity builds     → BUILD_ENGINE (docker|local)
# These are independent axes. Priority: workflow_dispatch (IN_*) > new repo
# variable (NEW_*/bare) > legacy repo variable (RUNNER_DEFAULT_MODE mapping)
# > toolkit default. Explicit new/dispatch settings always beat the legacy
# mapping, even if only one of the two axes is explicit.
# ---------------------------------------------------------------------------
IN_RUNNER_TYPE="${IN_RUNNER_TYPE:-}"
IN_BUILD_ENGINE="${IN_BUILD_ENGINE:-}"
IN_RUNNER_LABELS="${IN_RUNNER_LABELS:-}"
IN_ACTIVATION_STRATEGY="${IN_ACTIVATION_STRATEGY:-}"

VALID_RUNNER_TYPES="github-hosted self-hosted"
VALID_BUILD_ENGINES="docker local"

validate_runner_type() {
    local val="$1"
    local v
    for v in ${VALID_RUNNER_TYPES}; do [[ "${val}" == "${v}" ]] && return 0; done
    log_error "Invalid runner-type='${val}'. Allowed: ${VALID_RUNNER_TYPES}"
    exit 1
}

validate_build_engine() {
    local val="$1"
    local v
    for v in ${VALID_BUILD_ENGINES}; do [[ "${val}" == "${v}" ]] && return 0; done
    log_error "Invalid build-engine='${val}'. Allowed: ${VALID_BUILD_ENGINES}"
    exit 1
}

# normalize_runner_labels CSV — trim/split/dedupe, print one label per line
normalize_runner_labels() {
    local csv="$1" normalized label seen existing
    normalized="${csv//,/ }"
    local -a result=()
    for label in ${normalized}; do
        label="$(echo "${label}" | tr -d '[:space:]')"
        [[ -z "${label}" ]] && continue
        seen="false"
        if [[ "${#result[@]}" -gt 0 ]]; then
            for existing in "${result[@]}"; do
                [[ "${existing}" == "${label}" ]] && seen="true" && break
            done
        fi
        [[ "${seen}" == "true" ]] && continue
        result+=("${label}")
    done
    if [[ "${#result[@]}" -gt 0 ]]; then
        printf '%s\n' "${result[@]}"
    fi
}

# --- explicit (dispatch / new-variable) tiers, no default applied yet -------
resolve_setting "" "" \
    dispatch "${IN_RUNNER_TYPE}" \
    variable-new "${NEW_RUNNER_TYPE:-}" \
    variable-new "${RUNNER_TYPE:-}"
_rt_explicit="${_resolved_value}"
_rt_explicit_source="${_resolved_source}"

resolve_setting "" "" \
    dispatch "${IN_BUILD_ENGINE}" \
    variable-new "${NEW_BUILD_ENGINE:-}" \
    variable-new "${BUILD_ENGINE:-}"
_be_explicit="${_resolved_value}"
_be_explicit_source="${_resolved_source}"

# --- legacy RUNNER_DEFAULT_MODE raw value (no mapping/default applied yet) --
resolve_setting "" "" \
    variable-new "${NEW_RUNNER_DEFAULT_MODE:-}" \
    variable-new "${RUNNER_DEFAULT_MODE:-}" \
    variable-legacy "${LEG_DEFAULT_RUNNER_MODE:-}" \
    variable-legacy "${DEFAULT_RUNNER_MODE:-}" \
    variable-legacy "${VAR_DEFAULT_RUNNER_MODE:-}"
_legacy_mode_raw="${_resolved_value}"

_legacy_default_labels=""
if [[ -z "${_rt_explicit}" && -z "${_be_explicit}" && -n "${_legacy_mode_raw}" ]]; then
    # Legacy-only config: map RUNNER_DEFAULT_MODE to runner-type + build-engine.
    case "${_legacy_mode_raw}" in
        docker|auto)
            runner_type="github-hosted"; build_engine="docker"
            ;;
        self-hosted-windows)
            runner_type="self-hosted"; build_engine="local"
            _legacy_default_labels="self-hosted,windows"
            ;;
        self-hosted-macos)
            runner_type="self-hosted"; build_engine="local"
            _legacy_default_labels="self-hosted,macOS"
            ;;
        *)
            log_error "Invalid RUNNER_DEFAULT_MODE='${_legacy_mode_raw}'. Allowed: docker auto self-hosted-windows self-hosted-macos"
            exit 1
            ;;
    esac
    runner_type_source="variable-legacy"
    build_engine_source="variable-legacy"
    log_warn "Legacy variable RUNNER_DEFAULT_MODE='${_legacy_mode_raw}' in use; please migrate to repository variables RUNNER_TYPE/BUILD_ENGINE."
else
    if [[ -n "${_rt_explicit}" ]]; then
        runner_type="${_rt_explicit}"; runner_type_source="${_rt_explicit_source}"
    else
        runner_type="github-hosted"; runner_type_source="default"
    fi
    if [[ -n "${_be_explicit}" ]]; then
        build_engine="${_be_explicit}"; build_engine_source="${_be_explicit_source}"
    else
        build_engine="docker"; build_engine_source="default"
    fi
fi

validate_runner_type "${runner_type}"
validate_build_engine "${build_engine}"

if [[ "${runner_type}" == "github-hosted" && "${build_engine}" == "local" ]]; then
    log_error "GitHub-hosted runners have no local Unity install; use BUILD_ENGINE=docker or RUNNER_TYPE=self-hosted."
    exit 1
fi

# --- runner labels -----------------------------------------------------------
resolve_setting "" "" \
    dispatch "${IN_RUNNER_LABELS}" \
    variable-new "${NEW_RUNNER_LABELS:-}" \
    variable-new "${RUNNER_LABELS:-}"
_labels_explicit="${_resolved_value}"
_labels_explicit_source="${_resolved_source}"

if [[ -n "${_labels_explicit}" ]]; then
    runner_labels_raw="${_labels_explicit}"
    runner_labels_source="${_labels_explicit_source}"
elif [[ -n "${_legacy_default_labels}" ]]; then
    runner_labels_raw="${_legacy_default_labels}"
    runner_labels_source="variable-legacy"
elif [[ "${runner_type}" == "self-hosted" ]]; then
    runner_labels_raw="self-hosted,windows"
    runner_labels_source="default"
else
    runner_labels_raw="ubuntu-latest"
    runner_labels_source="default"
fi

mapfile -t runner_labels_arr < <(normalize_runner_labels "${runner_labels_raw}")
if [[ "${#runner_labels_arr[@]}" -eq 0 ]]; then
    log_error "RUNNER_LABELS resolved to no usable labels (raw='${runner_labels_raw}')."
    exit 1
fi

runner_labels_json="["
_first_label="true"
for _label in "${runner_labels_arr[@]}"; do
    if [[ "${_first_label}" == "true" ]]; then
        _first_label="false"
    else
        runner_labels_json+=","
    fi
    runner_labels_json+="\"${_label}\""
done
runner_labels_json+="]"
runner_labels_csv="$(IFS=,; echo "${runner_labels_arr[*]}")"

# --- execution-strategy -------------------------------------------------------
case "${runner_type}:${build_engine}" in
    github-hosted:docker) execution_strategy="github-docker" ;;
    self-hosted:local)    execution_strategy="selfhosted-local" ;;
    self-hosted:docker)   execution_strategy="selfhosted-docker" ;;
esac

# --- activation-strategy -------------------------------------------------------
if [[ "${build_engine}" == "local" ]]; then
    activation_strategy="none"
elif [[ -n "${IN_ACTIVATION_STRATEGY}" ]]; then
    activation_strategy="${IN_ACTIVATION_STRATEGY}"
else
    activation_strategy="auto"
fi

# --- backward bridge: derive legacy runner-mode for unmigrated consumers ------
case "${runner_type}:${build_engine}" in
    github-hosted:docker) runner_mode="docker" ;;
    self-hosted:local)    runner_mode="self-hosted-windows" ;;
    self-hosted:docker)   runner_mode="docker" ;;
esac
runner_mode_source="${runner_type_source}"

resolve_setting "self-hosted-windows" "" \
    variable-new "${NEW_RUNNER_WINDOWS_LABEL:-}" \
    variable-new "${RUNNER_WINDOWS_LABEL:-}"
runner_windows_label="${_resolved_value}"
resolve_setting "self-hosted-macos" "" \
    variable-new "${NEW_RUNNER_MACOS_LABEL:-}" \
    variable-new "${RUNNER_MACOS_LABEL:-}"
runner_macos_label="${_resolved_value}"
resolve_setting "ubuntu-latest" "" \
    variable-new "${NEW_RUNNER_LINUX_LABEL:-}" \
    variable-new "${RUNNER_LINUX_LABEL:-}"
runner_linux_label="${_resolved_value}"

# ---------------------------------------------------------------------------
# Group: CACHE (all default true)
# ---------------------------------------------------------------------------
resolve_setting "true" "" \
    variable-new "${NEW_CACHE_LIBRARY_ENABLED:-}" \
    variable-new "${CACHE_LIBRARY_ENABLED:-}"
cache_library="${_resolved_value}"
validate_bool "CACHE_LIBRARY_ENABLED" "${cache_library}"

resolve_setting "true" "" \
    variable-new "${NEW_CACHE_GRADLE_ENABLED:-}" \
    variable-new "${CACHE_GRADLE_ENABLED:-}"
cache_gradle="${_resolved_value}"
validate_bool "CACHE_GRADLE_ENABLED" "${cache_gradle}"

resolve_setting "true" "" \
    variable-new "${NEW_CACHE_ADDRESSABLES_ENABLED:-}" \
    variable-new "${CACHE_ADDRESSABLES_ENABLED:-}"
cache_addressables="${_resolved_value}"
validate_bool "CACHE_ADDRESSABLES_ENABLED" "${cache_addressables}"

resolve_setting "true" "" \
    variable-new "${NEW_CACHE_NUGET_ENABLED:-}" \
    variable-new "${CACHE_NUGET_ENABLED:-}"
cache_nuget="${_resolved_value}"
validate_bool "CACHE_NUGET_ENABLED" "${cache_nuget}"

# ---------------------------------------------------------------------------
# Group: ARTIFACT
# ---------------------------------------------------------------------------
resolve_setting "30" "" \
    variable-new "${NEW_ARTIFACT_RETENTION_DAYS:-}" \
    variable-new "${ARTIFACT_RETENTION_DAYS:-}"
artifact_retention_days="${_resolved_value}"
# Whether the project actually asked for this number or just inherited the
# default. A release artifact that expires cannot be promoted, so the pipeline
# lengthens the default for release builds — but a number the project chose
# deliberately must not be silently overruled.
artifact_retention_source="${_resolved_source}"
validate_positive_int "ARTIFACT_RETENTION_DAYS" "${artifact_retention_days}"

resolve_setting "zip" "" \
    variable-new "${NEW_ARTIFACT_COMPRESSION:-}" \
    variable-new "${ARTIFACT_COMPRESSION:-}"
artifact_compression="${_resolved_value}"
if [[ "${artifact_compression}" != "zip" ]]; then
    log_error "Invalid ARTIFACT_COMPRESSION='${artifact_compression}'. Allowed: zip"
    exit 1
fi

# run-tests/addressables sources default (may be overridden per-branch below)
run_tests_source="default"
addressables_source="default"

# ---------------------------------------------------------------------------
# Flow resolution
# ---------------------------------------------------------------------------
case "${EVENT_NAME}" in

  pull_request)
    target="${BASE_REF}"
    log_info "pull_request: target=${target}"
    if _is_develop "${target}"; then
      flow_type="pr-develop"; environment="development"
      run_tests="true"; test_mode="${derived_test_mode}"
      # PR → develop: validation only, no binary builds
      resolve_branch_optional "develop"
      resolve_branch_define_symbols "develop"
    elif _is_staging "${target}"; then
      flow_type="pr-staging"; environment="staging"
      run_tests="true"; test_mode="${derived_test_mode}"
      # PR → staging: validation only, no binary builds
      resolve_branch_optional "staging"
      resolve_branch_define_symbols "staging"
    elif _is_release "${target}"; then
      flow_type="pr-release"; environment="production"
      run_tests="true"; test_mode="${derived_test_mode}"
      build_addressables="true"
      # PR → release-*: validation + addressables check, no binary builds
      resolve_branch_optional "release"
      resolve_branch_define_symbols "release"
    else
      log_warn "PR target branch '${target}' does not match develop/staging/release-*; flow=none"
    fi
    ;;

  push)
    branch="${REF_NAME}"
    log_info "push: branch=${branch}"
    if _is_develop "${branch}"; then
      flow_type="push-develop"; environment="development"
      run_tests="true"; test_mode="${derived_test_mode}"
      resolve_branch_platforms "develop"
      resolve_branch_optional "develop"
      resolve_branch_define_symbols "develop"
    elif _is_staging "${branch}"; then
      flow_type="push-staging"; environment="staging"
      run_tests="true"; test_mode="${derived_test_mode}"
      resolve_branch_platforms "staging"
      resolve_branch_optional "staging"
      resolve_branch_define_symbols "staging"
    elif _is_release "${branch}"; then
      flow_type="push-release"; environment="production"
      run_tests="true"; test_mode="${derived_test_mode}"
      build_addressables="true"
      resolve_branch_platforms "release"
      resolve_branch_optional "release"
      resolve_branch_define_symbols "release"
      signing="android-release"
    else
      log_warn "Push branch '${branch}' does not match develop/staging/release-*; flow=none"
    fi
    ;;

  workflow_dispatch)
    flow_type="manual"
    environment="${IN_ENVIRONMENT}"
    run_tests="${IN_RUN_TESTS}"
    test_mode="${IN_TEST_MODE}"
    build_addressables="${IN_BUILD_ADDRESSABLES}"
    platform_source="dispatch"
    define_symbols="${IN_DEFINE_SYMBOLS}"
    log_info "workflow_dispatch: platform=${IN_PLATFORM} environment=${environment} run-tests=${run_tests}"

    # Platform selection — iOS is only ever built via explicit manual dispatch.
    # Routed through set_platforms_from_list so a manual dispatch is filtered by
    # the project's PLATFORMS capability exactly like a branch build. Asking for
    # a platform the project cannot build must not produce a job.
    # `All` means the platforms this project configured for this environment,
    # NOT every platform the toolkit knows. It used to be a hardcoded list of
    # five, so a project with RELEASE_BUILD_PLATFORMS=Android that picked "All"
    # got five builds — fifty runner-minutes instead of nine — while the form
    # said "All uses RELEASE_BUILD_PLATFORMS". The form was right about the
    # intent and wrong about the behaviour.
    #
    # Anything else is a list: one name, "Android,WebGL", or a group alias.
    # set_platforms_from_list already loops and already applies the capability
    # filter, so a list costs nothing but the split.
    case "${IN_PLATFORM}" in
      All)
        case "${environment}" in
          development) dispatch_branch_type="develop" ;;
          staging)     dispatch_branch_type="staging" ;;
          *)           dispatch_branch_type="release" ;;
        esac
        resolve_branch_platforms "${dispatch_branch_type}"
        # iOS never joins an "All" build: it needs a macOS runner, so including
        # it would make the result depend on infrastructure rather than on the
        # request. Ask for it by name.
        if [[ "${build_ios}" == "true" ]]; then
          build_ios="false"
          skipped_platforms+=("iOS: manual-only, not included in 'All'")
        fi
        ;;
      "")
        log_error "platform is empty. Choose a platform, a comma-separated list, or All."
        exit 1
        ;;
      *)
        # Multi-word labels are folded before the split, or "Linux Server"
        # would tokenise into "Linux" and "Server" and quietly select the
        # desktop build as well as the dedicated server.
        requested="$(printf '%s' "${IN_PLATFORM}" \
          | sed -e 's/Linux Server/LinuxServer/g' -e 's/Linux server/LinuxServer/g' \
          | tr ',' ' ' | tr -s ' ')"
        # Group aliases, so the dropdown can offer a subset without a second
        # free-text field for a human to disagree with the dropdown in.
        # The form speaks human; the pipeline keeps its identifiers. Windows64
        # and Linux64 are Unity's target names and stay that way internally —
        # renaming a capability identifier for cosmetics would change the
        # meaning of every *_BUILD_PLATFORMS a project has already set.
        expanded=""
        for token in ${requested}; do
          case "${token}" in
            Desktop)              expanded="${expanded} Windows64 Linux64" ;;
            Windows)              expanded="${expanded} Windows64" ;;
            Linux)                expanded="${expanded} Linux64" ;;
            LinuxServer)          expanded="${expanded} LinuxServer" ;;
            *)                    expanded="${expanded} ${token}" ;;
          esac
        done
        # A name the toolkit does not know is a typo, and a typo must not
        # produce a green run with no artifacts — that is a build failure
        # wearing a success badge. A KNOWN platform the project has not
        # enabled is different: set_platforms_from_list skips it with a note,
        # which is I-016 working as intended.
        for token in ${expanded}; do
          case "${token}" in
            Android|iOS|WebGL|Windows64|Linux64|LinuxServer) ;;
            *)
              log_error "Unknown platform '${token}'. Valid: Android, iOS, WebGL, Windows, Linux, Linux Server, Desktop, All."
              exit 1
              ;;
          esac
        done
        # iOS is allowed only when asked for by name, which this is.
        if [[ " ${expanded} " == *" iOS "* ]]; then
          set_platforms_from_list "${expanded}" "true"
        else
          set_platforms_from_list "${expanded}"
        fi
        ;;
    esac
    ;;

  "")
    log_warn "EVENT_NAME is empty; flow=none"
    ;;

  *)
    log_warn "Unrecognised EVENT_NAME='${EVENT_NAME}'; flow=none"
    ;;
esac

if [[ "${flow_type}" != "manual" && "${flow_type}" != "none" ]]; then
    [[ "${build_ios}" == "false" ]] && skipped_platforms+=("iOS: manual-only")
fi

# ---------------------------------------------------------------------------
# Normalise: if run-tests is false, test-mode must be None
# ---------------------------------------------------------------------------
if [[ "${run_tests}" == "false" ]]; then
    test_mode="None"
fi

# ---------------------------------------------------------------------------
# GitHub deployment environment (distinct from the Unity build `environment`).
# Only push flows (develop/staging/release) map to a real GitHub Environment /
# deployment. Pull-request flows are validation-only, and workflow_dispatch is an
# ad-hoc manual build — often from `main`, which branch-scoped environment
# protection rules block — so neither targets a protected GitHub Environment.
# ---------------------------------------------------------------------------
case "${flow_type}" in
    pr-develop|pr-staging|pr-release|manual|none) gh_environment="" ;;
    *)                                            gh_environment="${environment}" ;;
esac

log_info "gh-environment=${gh_environment} (deployment target; empty = none)"
# ---------------------------------------------------------------------------
# LIFECYCLE → ARTIFACT CONTRACT
# ---------------------------------------------------------------------------
# What a platform produces is a consequence of the lifecycle, not a question
# for whoever presses the button. A development Android build is an APK you can
# sideload; a release Android build is the App Bundle Google Play requires.
# Asking the user made `release + Android + apk` a configuration the pipeline
# accepted — an artifact that passes every gate and cannot be published.
#
# The build type is resolved once, here, and everything downstream reads it.
# The matrix step used to derive it a second time from the same two inputs,
# which is one divergence away from a release build that names its artifacts
# `development-*`.
build_type="${IN_BUILD_TYPE:-}"
if [[ -z "${build_type}" ]]; then
    case "${environment}" in
        production) build_type="release" ;;
        *)          build_type="development" ;;
    esac
fi
case "${build_type}" in
    development|release) ;;
    *)
        log_error "Unknown build type '${build_type}'. Valid: development, release."
        exit 1
        ;;
esac

case "${build_type}" in
    release)     android_export_type="aab" ;;
    development) android_export_type="apk" ;;
esac

# An explicit format is still accepted from a workflow_call caller — the
# reusable interface stays technical — but only when it agrees with the
# lifecycle. Silently ignoring it would leave the caller believing something
# untrue about the artifact it is about to promote.
if [[ -n "${IN_ANDROID_EXPORT:-}" ]]; then
    if [[ "${IN_ANDROID_EXPORT}" != "${android_export_type}" ]]; then
        log_error "android-export='${IN_ANDROID_EXPORT}' contradicts the ${build_type} lifecycle, which produces '${android_export_type}'. A release Android artifact is an App Bundle; an APK cannot be published to Google Play."
        exit 1
    fi
    if [[ "${build_android}" != "true" ]]; then
        log_error "android-export='${IN_ANDROID_EXPORT}' was given but no Android build was selected. An Android output format cannot apply to ${IN_PLATFORM:-the selected platforms}."
        exit 1
    fi
fi

log_info "build-type=${build_type} android-export-type=${android_export_type} (from the lifecycle)"
log_info "flow-type=${flow_type} environment=${environment} run-tests=${run_tests} test-mode=${test_mode}"
log_info "build-addressables=${build_addressables} signing=${signing}"
log_info "platforms: android=${build_android} webgl=${build_webgl} linux64=${build_linux64} linuxserver=${build_linuxserver} windows64=${build_windows64} ios=${build_ios}"
log_info "platform-source=${platform_source}"
log_info "define-symbols=${define_symbols:-<none>}"

# ---------------------------------------------------------------------------
# Human-readable REPORT — stderr only. stdout stays pure KEY=value.
# ---------------------------------------------------------------------------
_source_label() {
    case "${1}" in
        variable-new)     echo "Repository Variable" ;;
        variable-legacy)  echo "Legacy Variable" ;;
        dispatch|workflow_dispatch) echo "Workflow Dispatch Input" ;;
        project-version-file) echo "ProjectVersion.txt" ;;
        default)          echo "Toolkit Default" ;;
        *)                echo "${1}" ;;
    esac
}

{
    echo ""
    echo "==================== Resolve Config: Build Flow ===================="
    echo "Flow:                ${flow_type}"
    echo "Event:                ${EVENT_NAME:-<none>}"
    echo "Branch:               ${REF_NAME:-<none>}"
    echo "Target branch:        ${BASE_REF:-<none>}"
    echo "Environment:          ${environment} (gh-environment: ${gh_environment:-<none>})"
    echo "Unity Version:        ${unity_version} (source: $(_source_label "${unity_version_source}"))"
    echo "Unity Project Path:   ${unity_project_path} (source: $(_source_label "${unity_project_path_source}"))"
    echo "Unity Build Method:   ${unity_build_method:-<game-ci default>} (source: $(_source_label "${unity_build_method_source}"))"
    echo "Runner:               mode=${runner_mode} (source: $(_source_label "${runner_mode_source}")) windows='${runner_windows_label}' macos='${runner_macos_label}' linux='${runner_linux_label}'"
    echo "Runner Type:          ${runner_type} (source: $(_source_label "${runner_type_source}"))"
    echo "Runner Labels:        ${runner_labels_csv} (source: $(_source_label "${runner_labels_source}"))"
    echo "Build Engine:         ${build_engine} (source: $(_source_label "${build_engine_source}"))"
    echo "Execution Strategy:   ${execution_strategy}"
    echo "Activation Strategy:  ${activation_strategy}"
    echo "Platforms:            android=${build_android} webgl=${build_webgl} linux64=${build_linux64} linuxserver=${build_linuxserver} windows64=${build_windows64} ios=${build_ios} (source: $(_source_label "${platform_source}"))"
    echo "Tests:                run-tests=${run_tests} (source: $(_source_label "${run_tests_source}")) test-mode=${test_mode} editmode=${test_editmode} playmode=${test_playmode} fail-fast=${test_fail_fast}"
    echo "Addressables:         build-addressables=${build_addressables} (source: $(_source_label "${addressables_source}"))"
    echo "Define Symbols:       ${define_symbols:-<none>}"
    echo "Timeout:              ${build_timeout_minutes} minutes"
    echo "Cache:                library=${cache_library} gradle=${cache_gradle} addressables=${cache_addressables} nuget=${cache_nuget}"
    echo "Artifact:             retention=${artifact_retention_days}d compression=${artifact_compression}"
    echo "Clean Build:          ${clean_build} (source: $(_source_label "${clean_build_source}"))"
    echo "Signing:              ${signing}"
    echo "Build Type:           ${build_type} (artifact contract)"
    echo "Android Export Type:  ${android_export_type} (from the lifecycle)"
    if [[ "${#skipped_platforms[@]}" -gt 0 ]]; then
        echo "Skipped Platforms:"
        for note in "${skipped_platforms[@]}"; do
            echo "  - ${note}"
        done
    else
        echo "Skipped Platforms:    <none>"
    fi
    echo "======================================================================"
    echo ""
} >&2

# ---------------------------------------------------------------------------
# Emit all outputs
# ---------------------------------------------------------------------------
emit "flow-type"               "${flow_type}"
emit "define-symbols"          "${define_symbols}"
emit "environment"             "${environment}"
emit "gh-environment"          "${gh_environment}"
emit "run-tests"                "${run_tests}"
emit "test-mode"               "${test_mode}"
emit "build-addressables"      "${build_addressables}"
emit "build-android"           "${build_android}"
emit "build-webgl"             "${build_webgl}"
emit "build-linux64"           "${build_linux64}"
emit "build-linuxserver"       "${build_linuxserver}"
emit "build-windows64"         "${build_windows64}"
emit "build-ios"               "${build_ios}"
emit "signing"                 "${signing}"
emit "build-type"              "${build_type}"
emit "android-export-type"     "${android_export_type}"
emit "platform-source"         "${platform_source}"

# New outputs
emit "unity-version"           "${unity_version}"
emit "project-path"            "${unity_project_path}"
emit "build-method"            "${unity_build_method}"
emit "build-timeout-minutes"   "${build_timeout_minutes}"
emit "clean-build"             "${clean_build}"
emit "clean-build-source"      "${clean_build_source}"
emit "test-editmode"           "${test_editmode}"
emit "test-playmode"           "${test_playmode}"
emit "test-fail-fast"          "${test_fail_fast}"
emit "runner-mode"             "${runner_mode}"
emit "runner-windows-label"    "${runner_windows_label}"
emit "runner-macos-label"      "${runner_macos_label}"
emit "runner-linux-label"      "${runner_linux_label}"
emit "runner-type"             "${runner_type}"
emit "build-engine"            "${build_engine}"
emit "execution-strategy"      "${execution_strategy}"
emit "runner-labels"           "${runner_labels_json}"
emit "runner-labels-csv"       "${runner_labels_csv}"
emit "activation-strategy"     "${activation_strategy}"
emit "runner-type-source"      "${runner_type_source}"
emit "build-engine-source"     "${build_engine_source}"
emit "runner-labels-source"    "${runner_labels_source}"
emit "cache-library"           "${cache_library}"
emit "cache-gradle"            "${cache_gradle}"
emit "cache-addressables"      "${cache_addressables}"
emit "cache-nuget"             "${cache_nuget}"
emit "artifact-retention-days" "${artifact_retention_days}"
emit "artifact-retention-source" "${artifact_retention_source}"
emit "artifact-compression"    "${artifact_compression}"
