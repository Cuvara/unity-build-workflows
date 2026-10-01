#!/usr/bin/env python3
"""
runner_scheduler.py
Unity runner scheduling layer (ADR 004 stage 2c).

Answers one question per Unity job: *which runner does this job go to?*

Self-hosted runners are the primary execution path. The scheduler's job is to
find a self-hosted runner that can build the job, is online, and is preferred by
the policy. GitHub-hosted is a managed provider the policy may allow as a
fallback (or select outright) -- it is never modelled as a runner with state.

Stages (each a separate function so a stage can be replaced independently):

  1. resolve_requirements()   platform, engine, lane, allowed OS, Unity, Xcode, labels
  2. provider.discover()      self-hosted runner inventory (InventoryProvider)
  3. evaluate_capability()    hard constraints only -> eligible / ineligible(reasons)
  4. classify_availability()  idle | busy | offline | not-found | unknown
  5. candidate order          priority list, then fallback list
  6. choose()                 deterministic tiers; on-busy wait|next
  7. render_runs_on()         labels array or {group, labels}

Ownership boundaries:
  resolve_build_flow.sh         legacy modes and defaults -- consumed, never re-derived
  resolve_platform_executor.py  platform/engine/lane -> allowed runner OS
  runner_inventory.py           GitHub runner API
  matrix_runner_labels.py       serialization of one field into a matrix row

With no policy configured every job is a legacy passthrough: its runs-on is the
legacy resolver's answer, unchanged.

The CLI writes one structured `runner-selection` JSON document; matrix rows,
job inputs, the step summary and the final report all read that document.
"""

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import resolve_platform_executor as compat  # noqa: E402

SELECTION_VERSION = 1
POLICY_VERSION = 1

# ── Modes ──────────────────────────────────────────────────────────────────
MODE_SELF_HOSTED_ONLY = "self-hosted-only"
MODE_SELF_HOSTED_PREFERRED = "self-hosted-preferred"
MODE_GITHUB_HOSTED = "github-hosted"
MODES = (MODE_SELF_HOSTED_ONLY, MODE_SELF_HOSTED_PREFERRED, MODE_GITHUB_HOSTED)
DEFAULT_MODE = MODE_SELF_HOSTED_PREFERRED

ON_BUSY_WAIT = "wait"
ON_BUSY_NEXT = "next"
ON_BUSY_VALUES = (ON_BUSY_WAIT, ON_BUSY_NEXT)

ON_UNAVAILABLE_FIRST = "first"
ON_UNAVAILABLE_FAIL = "fail"
ON_UNAVAILABLE_VALUES = (ON_UNAVAILABLE_FIRST, ON_UNAVAILABLE_FAIL)

AVAILABILITY_SOURCES = ("auto", "api", "none")
INVENTORY_SCOPES = ("repo", "org", "both")

# The keyword a policy uses for the GitHub-hosted provider.
GITHUB_HOSTED = "github-hosted"
# The provider runs the docker engine on Linux and nothing else: GitHub's macOS
# and Windows images have no Unity install and no Linux-container Docker.
DEFAULT_GITHUB_HOSTED_LABELS = ("ubuntu-latest",)

# ── Availability values ────────────────────────────────────────────────────
IDLE = "idle"
BUSY = "busy"
OFFLINE = "offline"
NOT_FOUND = "not-found"
UNKNOWN = "unknown"
MANAGED = "managed"          # GitHub-hosted: schedulable, no inventory state
NOT_CHECKED = "not-checked"  # legacy passthrough: nothing was evaluated

# ── Runner-group membership ────────────────────────────────────────────────
GROUP_CONFIRMED = "confirmed"
GROUP_NOT_FOUND = "not-found"
GROUP_UNKNOWN = "unknown"

# ── Target kinds / tiers / decision sources ────────────────────────────────
KIND_RUNNER = "runner"
KIND_POOL = "pool"
KIND_PROVIDER = "github-hosted"

TIER_PRIMARY = "primary"
TIER_FALLBACK = "fallback"

TYPE_SELF_HOSTED = "self-hosted"
TYPE_GITHUB_HOSTED = "github-hosted"
TYPE_LEGACY = "legacy"

DECIDED_PLATFORM = "platform-policy"
DECIDED_DEFAULT = "default-policy"
DECIDED_DISPATCH = "dispatch-labels"
DECIDED_EXPLICIT = "explicit-input"
DECIDED_LEGACY = "legacy"
DECIDED_BYPASS = "policy-bypassed"
DECIDED_INACTIVE = "not-requested"


class PolicyError(Exception):
    """The policy is malformed or asks for something the toolkit cannot do."""


class SchedulingError(Exception):
    """No eligible target could be selected; the message is the full report."""


# ════════════════════════════════════════════════════════════════════════════
# Labels and OS helpers
# ════════════════════════════════════════════════════════════════════════════

def _fold(label: str) -> str:
    # GitHub matches runner labels case-insensitively.
    return label.strip().casefold()


def _dedupe(labels: Sequence[str]) -> Tuple[str, ...]:
    seen = set()
    out = []
    for label in labels:
        label = label.strip()
        if not label or _fold(label) in seen:
            continue
        seen.add(_fold(label))
        out.append(label)
    return tuple(out)


def _has_all(have: Sequence[str], need: Sequence[str]) -> bool:
    folded = {_fold(x) for x in have}
    return all(_fold(n) in folded for n in need)


def _missing(have: Sequence[str], need: Sequence[str]) -> List[str]:
    folded = {_fold(x) for x in have}
    return [n for n in need if _fold(n) not in folded]


def normalize_os(value: str) -> str:
    """Map GitHub's runner `os` field ("Linux", "macOS", "Windows") to ours."""
    folded = (value or "").strip().casefold()
    if folded in ("linux",):
        return compat.OS_LINUX
    if folded in ("macos", "osx", "darwin"):
        return compat.OS_MACOS
    if folded in ("windows", "win"):
        return compat.OS_WINDOWS
    return ""


def infer_os_from_labels(labels: Sequence[str]) -> str:
    """Infer the OS a label set implies. Empty when none or ambiguous."""
    found = set()
    for label in labels:
        f = _fold(label)
        if f == "linux" or f.startswith("ubuntu-") or f == "ubuntu":
            found.add(compat.OS_LINUX)
        elif f in ("macos", "osx") or f.startswith("macos-"):
            found.add(compat.OS_MACOS)
        elif f == "windows" or f.startswith("windows-"):
            found.add(compat.OS_WINDOWS)
    return found.pop() if len(found) == 1 else ""


def parse_labels(raw) -> Tuple[str, ...]:
    """Accept a JSON array, a JSON string of one, or a CSV/space list."""
    if isinstance(raw, (list, tuple)):
        return _dedupe([str(x) for x in raw])
    text = (raw or "").strip()
    if not text:
        return ()
    if text.startswith("["):
        try:
            value = json.loads(text)
            if isinstance(value, list):
                return _dedupe([str(x) for x in value])
        except json.JSONDecodeError:
            pass
    text = text.replace("[", " ").replace("]", " ").replace('"', " ").replace(",", " ")
    return _dedupe(text.split())


# ════════════════════════════════════════════════════════════════════════════
# Policy model + validation
# ════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Capabilities:
    os: str = ""
    platforms: Optional[Tuple[str, ...]] = None
    build_engines: Optional[Tuple[str, ...]] = None
    unity: Optional[Tuple[str, ...]] = None
    xcode: Optional[Tuple[str, ...]] = None


@dataclass(frozen=True)
class RunnerSpec:
    id: str
    labels: Tuple[str, ...]
    group: str
    caps: Capabilities


@dataclass(frozen=True)
class PoolSpec:
    id: str
    labels: Tuple[str, ...]
    group: str
    members: Tuple[str, ...]
    caps: Capabilities


@dataclass(frozen=True)
class Require:
    labels: Tuple[str, ...] = ()
    unity: str = ""     # "" | "project" | a literal version
    xcode: str = ""


@dataclass(frozen=True)
class Section:
    """A `default` or `platforms.<job>` block, as written (no inheritance yet)."""
    mode: str = ""
    build_engine: str = ""
    priority: Optional[Tuple[str, ...]] = None
    fallback: Optional[Tuple[str, ...]] = None
    require: Optional[Require] = None
    on_busy: str = ""
    on_unavailable: str = ""


@dataclass(frozen=True)
class Availability:
    source: str = "auto"
    scope: str = "repo"
    on_busy: str = ON_BUSY_WAIT
    on_unavailable: str = ON_UNAVAILABLE_FIRST
    timeout_seconds: int = 10


@dataclass(frozen=True)
class Policy:
    source: str
    mode: str
    availability: Availability
    runners: Dict[str, RunnerSpec]
    pools: Dict[str, PoolSpec]
    github_hosted_labels: Tuple[str, ...]
    default: Optional[Section]
    platforms: Dict[str, Section]


@dataclass(frozen=True)
class JobPolicy:
    """The effective policy for one job after section inheritance."""
    decided_by: str
    mode: str
    build_engine: str          # "" = not set by policy
    priority: Tuple[str, ...]
    fallback: Tuple[str, ...]
    require: Require
    on_busy: str
    on_unavailable: str
    strict: bool               # True: incompatible targets are policy errors
    inherited: bool = False    # True: no platforms.<job> section; everything came from `default`
    engine_explicit: bool = False  # True: build-engine written in platforms.<job>


_TOP_KEYS = {"$schema", "version", "mode", "availability", "runners", "pools",
             "github-hosted", "default", "platforms"}
_AVAIL_KEYS = {"source", "scope", "on-busy", "on-unavailable", "timeout-seconds"}
_CAP_KEYS = {"os", "platforms", "build-engines", "unity", "xcode"}
_RUNNER_KEYS = {"labels", "group"} | _CAP_KEYS
_POOL_KEYS = {"labels", "group", "members"} | _CAP_KEYS
_SECTION_KEYS = {"mode", "build-engine", "priority", "fallback", "require",
                 "on-busy", "on-unavailable"}
_REQUIRE_KEYS = {"labels", "unity", "xcode"}


def _expect_type(value, kind, where):
    if not isinstance(value, kind):
        name = {dict: "an object", list: "an array", str: "a string", int: "an integer"}.get(kind, str(kind))
        raise PolicyError(f"{where} must be {name}.")
    return value


def _check_keys(obj: dict, allowed: set, where: str) -> None:
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise PolicyError(f"{where}: unknown key(s) {', '.join(unknown)}. "
                          f"Allowed: {', '.join(sorted(allowed))}.")


def _str_list(value, where) -> Tuple[str, ...]:
    _expect_type(value, list, where)
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise PolicyError(f"{where} must contain non-empty strings.")
    return tuple(item.strip() for item in value)


def _enum(value, allowed, where) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise PolicyError(f"{where}='{value}' is invalid. Allowed: {', '.join(allowed)}.")
    return value


def _parse_caps(obj: dict, where: str) -> Capabilities:
    os_value = obj.get("os", "")
    if os_value:
        os_value = _enum(os_value, compat.RUNNER_OSES, f"{where}.os")
    platforms = _str_list(obj["platforms"], f"{where}.platforms") if "platforms" in obj else None
    if platforms:
        for p in platforms:
            if p not in compat.SCHEDULABLE_JOBS:
                raise PolicyError(f"{where}.platforms: unknown platform '{p}'. "
                                  f"Allowed: {', '.join(compat.SCHEDULABLE_JOBS)}.")
    engines = None
    if "build-engines" in obj:
        engines = _str_list(obj["build-engines"], f"{where}.build-engines")
        for e in engines:
            _enum(e, compat.ENGINES, f"{where}.build-engines[]")
    unity = _str_list(obj["unity"], f"{where}.unity") if "unity" in obj else None
    xcode = _str_list(obj["xcode"], f"{where}.xcode") if "xcode" in obj else None
    return Capabilities(os=os_value, platforms=platforms, build_engines=engines,
                        unity=unity, xcode=xcode)


def _resolve_static_os(declared: str, labels: Sequence[str], where: str) -> str:
    inferred = infer_os_from_labels(labels)
    if declared and inferred and declared != inferred:
        raise PolicyError(f"{where}: os '{declared}' contradicts its labels, which imply '{inferred}'.")
    os_value = declared or inferred
    if not os_value:
        raise PolicyError(f"{where}: cannot tell which OS this target runs. Declare \"os\" "
                          f"({', '.join(compat.RUNNER_OSES)}) or give it an OS label.")
    return os_value


def _parse_section(obj, where: str) -> Section:
    _expect_type(obj, dict, where)
    _check_keys(obj, _SECTION_KEYS, where)
    require = None
    if "require" in obj:
        r = _expect_type(obj["require"], dict, f"{where}.require")
        _check_keys(r, _REQUIRE_KEYS, f"{where}.require")
        unity = r.get("unity", "")
        xcode = r.get("xcode", "")
        if not isinstance(unity, str) or not isinstance(xcode, str):
            raise PolicyError(f"{where}.require.unity / .xcode must be strings.")
        require = Require(
            labels=_str_list(r["labels"], f"{where}.require.labels") if "labels" in r else (),
            unity=unity.strip(), xcode=xcode.strip())
    return Section(
        mode=_enum(obj["mode"], MODES, f"{where}.mode") if "mode" in obj else "",
        build_engine=_enum(obj["build-engine"], compat.ENGINES, f"{where}.build-engine")
        if "build-engine" in obj else "",
        priority=_str_list(obj["priority"], f"{where}.priority") if "priority" in obj else None,
        fallback=_str_list(obj["fallback"], f"{where}.fallback") if "fallback" in obj else None,
        require=require,
        on_busy=_enum(obj["on-busy"], ON_BUSY_VALUES, f"{where}.on-busy") if "on-busy" in obj else "",
        on_unavailable=_enum(obj["on-unavailable"], ON_UNAVAILABLE_VALUES, f"{where}.on-unavailable")
        if "on-unavailable" in obj else "",
    )


def parse_policy(document, source: str) -> Policy:
    """Validate a policy document and return the model. Raises PolicyError."""
    doc = _expect_type(document, dict, "policy")
    _check_keys(doc, _TOP_KEYS, "policy")
    version = doc.get("version", POLICY_VERSION)
    if version != POLICY_VERSION:
        raise PolicyError(f"policy.version={version!r} is not supported (expected {POLICY_VERSION}).")

    mode = _enum(doc["mode"], MODES, "policy.mode") if "mode" in doc else DEFAULT_MODE

    avail_obj = _expect_type(doc.get("availability", {}), dict, "policy.availability")
    _check_keys(avail_obj, _AVAIL_KEYS, "policy.availability")
    timeout = avail_obj.get("timeout-seconds", 10)
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 60:
        raise PolicyError("policy.availability.timeout-seconds must be an integer from 1 to 60.")
    availability = Availability(
        source=_enum(avail_obj.get("source", "auto"), AVAILABILITY_SOURCES, "policy.availability.source"),
        scope=_enum(avail_obj.get("scope", "repo"), INVENTORY_SCOPES, "policy.availability.scope"),
        on_busy=_enum(avail_obj.get("on-busy", ON_BUSY_WAIT), ON_BUSY_VALUES, "policy.availability.on-busy"),
        on_unavailable=_enum(avail_obj.get("on-unavailable", ON_UNAVAILABLE_FIRST),
                             ON_UNAVAILABLE_VALUES, "policy.availability.on-unavailable"),
        timeout_seconds=timeout,
    )

    runners: Dict[str, RunnerSpec] = {}
    for rid, robj in _expect_type(doc.get("runners", {}), dict, "policy.runners").items():
        where = f"policy.runners.{rid}"
        _expect_type(robj, dict, where)
        _check_keys(robj, _RUNNER_KEYS, where)
        if rid == GITHUB_HOSTED:
            raise PolicyError(f"{where}: '{GITHUB_HOSTED}' is reserved for the GitHub-hosted provider.")
        labels = _dedupe(_str_list(robj.get("labels", []), f"{where}.labels"))
        if not labels:
            raise PolicyError(f"{where}.labels must name at least one label "
                              f"(GitHub cannot target a runner by name -- give it a unique label).")
        caps = _parse_caps(robj, where)
        caps = Capabilities(os=_resolve_static_os(caps.os, labels, where), platforms=caps.platforms,
                            build_engines=caps.build_engines, unity=caps.unity, xcode=caps.xcode)
        group = robj.get("group", "")
        if not isinstance(group, str):
            raise PolicyError(f"{where}.group must be a string.")
        runners[rid] = RunnerSpec(id=rid, labels=labels, group=group.strip(), caps=caps)

    pools: Dict[str, PoolSpec] = {}
    for pid, pobj in _expect_type(doc.get("pools", {}), dict, "policy.pools").items():
        where = f"policy.pools.{pid}"
        _expect_type(pobj, dict, where)
        _check_keys(pobj, _POOL_KEYS, where)
        if pid == GITHUB_HOSTED or pid in runners:
            raise PolicyError(f"{where}: id '{pid}' is already used by a runner or is reserved.")
        labels = _dedupe(_str_list(pobj.get("labels", []), f"{where}.labels"))
        group = pobj.get("group", "")
        if not isinstance(group, str):
            raise PolicyError(f"{where}.group must be a string.")
        if not labels:
            raise PolicyError(f"{where}.labels must name at least one label.")
        caps = _parse_caps(pobj, where)
        caps = Capabilities(os=_resolve_static_os(caps.os, labels, where), platforms=caps.platforms,
                            build_engines=caps.build_engines, unity=caps.unity, xcode=caps.xcode)
        members = _str_list(pobj["members"], f"{where}.members") if "members" in pobj else ()
        pools[pid] = PoolSpec(id=pid, labels=labels, group=group.strip(), members=members, caps=caps)

    gh_obj = _expect_type(doc.get("github-hosted", {}), dict, "policy.github-hosted")
    _check_keys(gh_obj, {"labels"}, "policy.github-hosted")
    gh_labels = _dedupe(_str_list(gh_obj["labels"], "policy.github-hosted.labels")) \
        if "labels" in gh_obj else DEFAULT_GITHUB_HOSTED_LABELS
    if not gh_labels:
        raise PolicyError("policy.github-hosted.labels must not be empty.")
    if any(_fold(label) == "self-hosted" for label in gh_labels):
        raise PolicyError("policy.github-hosted.labels must not contain 'self-hosted'.")
    if infer_os_from_labels(gh_labels) != compat.OS_LINUX:
        raise PolicyError("policy.github-hosted.labels must name a Linux image (e.g. ubuntu-latest): "
                          "the GitHub-hosted provider only runs the docker engine.")

    default = _parse_section(doc["default"], "policy.default") if "default" in doc else None

    platforms: Dict[str, Section] = {}
    for job, sobj in _expect_type(doc.get("platforms", {}), dict, "policy.platforms").items():
        if job not in compat.SCHEDULABLE_JOBS:
            raise PolicyError(f"policy.platforms.{job}: unknown platform/job. "
                              f"Allowed: {', '.join(compat.SCHEDULABLE_JOBS)}.")
        platforms[job] = _parse_section(sobj, f"policy.platforms.{job}")

    policy = Policy(source=source, mode=mode, availability=availability, runners=runners,
                    pools=pools, github_hosted_labels=gh_labels, default=default,
                    platforms=platforms)
    _check_references(policy)
    return policy


def _check_references(policy: Policy) -> None:
    sections = [("policy.default", policy.default)] if policy.default else []
    sections += [(f"policy.platforms.{k}", v) for k, v in policy.platforms.items()]
    for where, section in sections:
        for list_name, ids in (("priority", section.priority), ("fallback", section.fallback)):
            for target in ids or ():
                if target == GITHUB_HOSTED:
                    if list_name == "priority":
                        raise PolicyError(
                            f"{where}.priority lists '{GITHUB_HOSTED}'. GitHub-hosted is a fallback "
                            f"provider, not a priority runner: put it in `fallback` (mode "
                            f"{MODE_SELF_HOSTED_PREFERRED}) or use mode {MODE_GITHUB_HOSTED}.")
                    continue
                if target not in policy.runners and target not in policy.pools:
                    raise PolicyError(f"{where}.{list_name}: '{target}' is neither a runner nor a pool "
                                      f"declared in this policy.")
        # A target listed twice -- in one list, or in both -- has no single
        # position in the order, so the order would be a guess.
        seen: Dict[str, str] = {}
        for list_name, ids in (("priority", section.priority), ("fallback", section.fallback)):
            for target in ids or ():
                if target in seen:
                    raise PolicyError(f"{where}.{list_name}: duplicate target id '{target}' "
                                      f"(already listed in {where}.{seen[target]}).")
                seen[target] = list_name
    for pid, pool in policy.pools.items():
        dupes = sorted({m for m in pool.members if pool.members.count(m) > 1})
        if dupes:
            raise PolicyError(f"policy.pools.{pid}.members: duplicate runner id '{dupes[0]}'.")


# ── Duplicate keys ─────────────────────────────────────────────────────────
# JSON objects with a repeated key are legal JSON, and json.loads keeps the
# LAST value silently. For runner and pool ids that would drop a runner from the
# policy without a word, so duplicates are recorded while parsing and rejected
# with the collection they occurred in.

class _Obj(dict):
    duplicates: Tuple[str, ...] = ()


def _pairs_hook(pairs):
    obj = _Obj()
    dupes = []
    for key, value in pairs:
        if key in obj:
            dupes.append(key)
        obj[key] = value
    obj.duplicates = tuple(dupes)
    return obj


_COLLECTION_NAMES = {"policy.runners": "runner id", "policy.pools": "pool id",
                     "policy.platforms": "platform section"}


def _reject_duplicate_keys(node, where: str = "policy") -> None:
    if isinstance(node, _Obj) and node.duplicates:
        what = _COLLECTION_NAMES.get(where, "key")
        raise PolicyError(f"{where}: duplicate {what} '{node.duplicates[0]}'. JSON keeps only the last "
                          f"occurrence, so the earlier definition would be silently lost.")
    if isinstance(node, dict):
        for key, value in node.items():
            _reject_duplicate_keys(value, f"{where}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _reject_duplicate_keys(value, f"{where}[{index}]")


def parse_policy_text(text: str, source: str) -> Policy:
    """Parse policy JSON text (duplicate keys rejected) and validate it."""
    try:
        document = json.loads(text, object_pairs_hook=_pairs_hook)
    except json.JSONDecodeError as exc:
        raise PolicyError(f"{source} is not valid JSON: {exc}") from None
    _reject_duplicate_keys(document)
    return parse_policy(document, source)


def load_policy(policy_json: str, policy_file: str) -> Optional[Policy]:
    """RUNNER_POLICY (inline JSON) wins over the policy file. None when neither exists."""
    if policy_json and policy_json.strip():
        return parse_policy_text(policy_json, "variable:RUNNER_POLICY")
    if policy_file and os.path.isfile(policy_file):
        with open(policy_file, encoding="utf-8") as fh:
            text = fh.read()
        return parse_policy_text(text, f"file:{policy_file}")
    return None


def effective_job_policy(policy: Policy, job: str) -> Optional[JobPolicy]:
    """Section inheritance. None when neither the platform nor `default` covers the job.

    Field-level inheritance runs platform -> default -> top level, EXCEPT the
    target lists: `priority` and `fallback` are inherited as a pair. A platform
    that names its own priority list does not silently pick up the default's
    fallback (which could otherwise hand an iOS job a Linux fallback).
    """
    platform = policy.platforms.get(job)
    default = policy.default
    if platform is None and default is None:
        return None

    def pick(attr, top):
        for section in (platform, default):
            if section is not None and getattr(section, attr):
                return getattr(section, attr)
        return top

    if platform is not None and platform.priority is not None:
        priority, fallback, lists_from_platform = platform.priority, platform.fallback or (), True
    elif default is not None and default.priority is not None:
        priority, fallback, lists_from_platform = default.priority, default.fallback or (), False
    else:
        priority, fallback, lists_from_platform = (), (), platform is not None

    require = None
    for section in (platform, default):
        if section is not None and section.require is not None:
            require = section.require
            break

    return JobPolicy(
        decided_by=DECIDED_PLATFORM if platform is not None else DECIDED_DEFAULT,
        mode=pick("mode", policy.mode),
        build_engine=pick("build_engine", ""),
        priority=tuple(priority),
        fallback=tuple(fallback),
        require=require or Require(),
        on_busy=pick("on_busy", policy.availability.on_busy),
        on_unavailable=pick("on_unavailable", policy.availability.on_unavailable),
        strict=lists_from_platform,
        inherited=platform is None,
        engine_explicit=bool(platform is not None and platform.build_engine),
    )


# ════════════════════════════════════════════════════════════════════════════
# Stage 1 — requirements
# ════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Requirements:
    job: str
    lane: str
    engine: str
    allowed_os: frozenset
    labels: Tuple[str, ...]
    unity: str
    xcode: str
    project_unity: str = ""    # the project's version (what a docker image is resolved from)

    def as_dict(self) -> dict:
        return {"os": sorted(self.allowed_os), "labels": list(self.labels),
                "unity": self.unity, "xcode": self.xcode, "buildEngine": self.engine,
                "lane": self.lane}


def resolve_requirements(job: str, jp: JobPolicy, lane: str, legacy_engine: str,
                         unity_version: str) -> Requirements:
    """What any runner must offer to run `job`. Raises PolicyError on an unsupported ask.

    An engine written for this job (`platforms.<job>.build-engine`) is taken
    literally and rejected if the job cannot use it. An engine inherited from
    `default` is a generic preference: jobs with a fixed engine (iOS, Unity
    tests, Addressables, the standalone lanes) keep theirs instead.
    """
    if job in (compat.JOB_UNITY_TESTS, compat.JOB_ADDRESSABLES) and lane == compat.LANE_PIPELINE:
        # Tests and Addressables follow the run's engine: their license gate,
        # activation and caches are wired to the global value.
        if jp.engine_explicit and jp.build_engine != legacy_engine:
            raise PolicyError(
                f"policy.platforms.{job}.build-engine={jp.build_engine} differs from the run's "
                f"BUILD_ENGINE={legacy_engine}. {job} always uses the run's build engine.")
        engine = legacy_engine
    elif lane == compat.LANE_STANDALONE_DOCKER or lane == compat.LANE_STANDALONE_NATIVE:
        engine = compat.ENGINE_DOCKER if lane == compat.LANE_STANDALONE_DOCKER else compat.ENGINE_LOCAL
        if jp.engine_explicit and jp.build_engine != engine:
            raise PolicyError(f"policy.platforms.{job}.build-engine={jp.build_engine} is not supported "
                              f"by the {lane} lane, which always uses build-engine={engine}.")
    elif job == "iOS" and not jp.engine_explicit:
        # iOS has exactly one engine. Inheriting the run's (or the default
        # section's) docker would be rejected below for a reason the policy
        # author never wrote for iOS.
        engine = compat.ENGINE_LOCAL
    else:
        engine = jp.build_engine or legacy_engine

    allowed = compat.allowed_runner_os(job, engine, lane)
    if not allowed:
        raise PolicyError(f"Unsupported combination for {job}: "
                          f"{compat.unsupported_combination_reason(job, engine, lane)}")

    unity = jp.require.unity
    if unity == "project":
        if not unity_version:
            # A requirement that cannot be evaluated must not quietly vanish.
            raise PolicyError(f"{job}: require.unity=\"project\" but the project's Unity version is "
                              f"unknown in this workflow; name the version explicitly.")
        unity = unity_version
    return Requirements(job=job, lane=lane, engine=engine, allowed_os=allowed,
                        labels=jp.require.labels, unity=unity, xcode=jp.require.xcode,
                        project_unity=unity_version or "")


# ════════════════════════════════════════════════════════════════════════════
# Stage 2 — inventory (provider interface)
# ════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class InventoryRunner:
    name: str
    os: str
    online: bool
    busy: bool
    labels: Tuple[str, ...]


@dataclass
class Inventory:
    provider: str                          # github-api | snapshot | none
    status: str                            # ok | unavailable | skipped
    runners: Dict[str, InventoryRunner] = field(default_factory=dict)
    # Runner-group membership is only ever taken from the group endpoints, never
    # inferred from labels. A group in `groups` is CONFIRMED (its members are
    # listed); one in `groups_not_found` was looked up and does not exist (or is
    # not visible to the token); any other group's membership is UNKNOWN.
    groups: Dict[str, frozenset] = field(default_factory=dict)  # group -> runner names
    groups_not_found: frozenset = frozenset()
    scopes: Tuple[str, ...] = ()
    reason: str = ""

    @property
    def available(self) -> bool:
        return self.status == "ok"

    def group_status(self, group: str) -> str:
        if self.available and group in self.groups:
            return GROUP_CONFIRMED
        if self.available and group in self.groups_not_found:
            return GROUP_NOT_FOUND
        return GROUP_UNKNOWN

    def find(self, name: str) -> Optional[InventoryRunner]:
        if name in self.runners:
            return self.runners[name]
        for key, runner in self.runners.items():
            if _fold(key) == _fold(name):
                return runner
        return None

    def as_dict(self) -> dict:
        return {"provider": self.provider, "status": self.status, "scopes": list(self.scopes),
                "reason": self.reason, "runnerCount": len(self.runners)}


@dataclass(frozen=True)
class InventoryRequest:
    groups: Tuple[str, ...]
    scope: str
    timeout_seconds: int


class InventoryProvider:
    """Discover self-hosted runners. Implementations must never raise for
    transport problems: they return an Inventory with status `unavailable`."""

    def discover(self, request: InventoryRequest) -> Inventory:  # pragma: no cover - interface
        raise NotImplementedError


class NullInventory(InventoryProvider):
    def __init__(self, reason: str, status: str = "unavailable"):
        self.reason = reason
        self.status = status

    def discover(self, request: InventoryRequest) -> Inventory:
        return Inventory(provider="none", status=self.status, reason=self.reason)


def inventory_from_document(document, provider: str = "snapshot") -> Inventory:
    """Build an Inventory from GitHub's runner list shape (also the snapshot format).

    { "runners": [ {"name", "os", "status": "online|offline", "busy", "labels": [str | {"name"}]} ],
      "groups":  { "<group>": ["runner-name", ...] },      # confirmed membership
      "groupsNotFound": ["<group>", ...] }                 # looked up, absent
    """
    runners: Dict[str, InventoryRunner] = {}
    for item in document.get("runners", []):
        labels = [l["name"] if isinstance(l, dict) else str(l) for l in item.get("labels", [])]
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        runners[name] = InventoryRunner(
            name=name, os=normalize_os(str(item.get("os", ""))) or infer_os_from_labels(labels),
            online=str(item.get("status", "")).casefold() == "online",
            busy=bool(item.get("busy", False)), labels=_dedupe(labels))
    groups = {g: frozenset(members) for g, members in (document.get("groups") or {}).items()}
    return Inventory(provider=provider, status="ok", runners=runners, groups=groups,
                     groups_not_found=frozenset(document.get("groupsNotFound") or ()),
                     scopes=tuple(document.get("scopes", ("snapshot",))))


class SnapshotInventory(InventoryProvider):
    def __init__(self, path: str):
        self.path = path

    def discover(self, request: InventoryRequest) -> Inventory:
        try:
            with open(self.path, encoding="utf-8") as fh:
                document = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            return Inventory(provider="snapshot", status="unavailable",
                             reason=f"cannot read inventory snapshot: {exc}")
        return inventory_from_document(document)


# ════════════════════════════════════════════════════════════════════════════
# Stages 3 + 4 — capability, then availability
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class Evaluation:
    target_id: str
    kind: str
    tier: str
    eligible: bool = True
    reasons: List[str] = field(default_factory=list)
    availability: str = UNKNOWN
    labels: Tuple[str, ...] = ()
    group: str = ""
    os: str = ""
    member: str = ""                       # pools: the member that decided the state
    member_evals: List[dict] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    # Runner-group membership could not be confirmed: availability is unknown,
    # no member is inferred from labels, and no individual runner is pinned.
    membership_unknown: bool = False

    def as_dict(self) -> dict:
        out = {"id": self.target_id, "kind": self.kind, "tier": self.tier,
               "eligible": self.eligible, "availability": self.availability,
               "reasons": list(self.reasons)}
        if self.group:
            out["group"] = self.group
        if self.membership_unknown:
            out["groupMembership"] = GROUP_UNKNOWN
        if self.member:
            out["member"] = self.member
        if self.member_evals:
            out["members"] = self.member_evals
        if self.notes:
            out["notes"] = list(self.notes)
        return out


def _version_reasons(name: str, declared: Optional[Tuple[str, ...]], required: str) -> List[str]:
    """One model for Unity and Xcode.

    No requirement -> not checked. A requirement is satisfied only by a declared
    list containing exactly that version string ("6000.0.26f1" never matches
    "6000.3.9f1"; "16" never matches "16.2"). A capability that is not declared
    -- or declared as [] -- is unknown, and unknown never satisfies a
    requirement: "nothing declared" does not mean "supports everything".
    """
    if not required:
        return []
    if declared is None:
        return [f"{name} not declared (requires {name} {required})"]
    if required not in declared:
        shown = ",".join(declared) if declared else "[]"
        return [f"{name}={shown} does not include {required}"]
    return []


def _declared_cap_reasons(caps: Capabilities, req: Requirements) -> List[str]:
    reasons = []
    if caps.os and caps.os not in req.allowed_os:
        reasons.append(f"os={caps.os} not allowed for {req.job} with build-engine={req.engine} "
                       f"(requires {'/'.join(sorted(req.allowed_os))})")
    if caps.platforms is not None and req.job in compat.PLAYER_JOBS and req.job not in caps.platforms:
        reasons.append(f"platforms={','.join(caps.platforms)} does not include {req.job}")
    if caps.build_engines is not None and req.engine not in caps.build_engines:
        reasons.append(f"build-engines={','.join(caps.build_engines)} does not include {req.engine}")
    reasons.extend(_version_reasons("unity", caps.unity, req.unity))
    reasons.extend(_version_reasons("xcode", caps.xcode, req.xcode))
    return reasons


def _runtime_reasons(actual: InventoryRunner, want_labels: Sequence[str],
                     req: Requirements) -> List[str]:
    reasons = []
    if actual.os and actual.os not in req.allowed_os:
        reasons.append(f"os={actual.os} not allowed for {req.job} "
                       f"(requires {'/'.join(sorted(req.allowed_os))})")
    elif not actual.os:
        reasons.append("runner reports no recognisable OS")
    missing = _missing(actual.labels, want_labels)
    if missing:
        reasons.append("missing=" + ",".join(missing))
    return reasons


def evaluate_capability(target_id: str, tier: str, policy: Policy, req: Requirements,
                        inventory: Inventory) -> Evaluation:
    """Stage 3: hard constraints. Never looks at online/busy state."""
    if target_id == GITHUB_HOSTED:
        ev = Evaluation(target_id=GITHUB_HOSTED, kind=KIND_PROVIDER, tier=tier,
                        labels=policy.github_hosted_labels, os=compat.OS_LINUX)
        if req.engine != compat.ENGINE_DOCKER:
            ev.eligible = False
            ev.reasons.append(f"GitHub-hosted runs build-engine=docker only (job needs {req.engine})")
        if compat.OS_LINUX not in req.allowed_os:
            ev.eligible = False
            ev.reasons.append(f"GitHub-hosted provides Linux only; {req.job} requires "
                              f"{'/'.join(sorted(req.allowed_os))}")
        if req.labels:
            ev.eligible = False
            ev.reasons.append("GitHub-hosted images cannot carry required labels "
                              + ",".join(req.labels))
        # Unity on GitHub-hosted is the docker image resolved from the project's
        # own version, so only that version can be required of it.
        if req.unity and req.unity != req.project_unity:
            ev.eligible = False
            ev.reasons.append(f"GitHub-hosted builds with the project's Unity "
                              f"{req.project_unity or '(unknown)'}, not {req.unity}")
        if req.xcode:
            ev.eligible = False
            ev.reasons.append(f"GitHub-hosted (Linux) has no Xcode (requires xcode {req.xcode})")
        return ev

    if target_id in policy.runners:
        spec = policy.runners[target_id]
        want = _dedupe(list(spec.labels) + list(req.labels))
        ev = Evaluation(target_id=target_id, kind=KIND_RUNNER, tier=tier, labels=want,
                        group=spec.group, os=spec.caps.os)
        ev.reasons.extend(_declared_cap_reasons(spec.caps, req))
        if spec.group:
            status = inventory.group_status(spec.group)
            if status == GROUP_NOT_FOUND:
                ev.reasons.append(_group_not_found(spec.group))
            elif status == GROUP_UNKNOWN:
                _mark_membership_unknown(ev, spec.group, inventory)
        if inventory.available:
            actual = inventory.find(target_id)
            if actual is not None:
                ev.os = actual.os or ev.os
                ev.reasons.extend(_runtime_reasons(actual, want, req))
                if spec.group and inventory.group_status(spec.group) == GROUP_CONFIRMED and \
                        actual.name not in inventory.groups[spec.group]:
                    ev.reasons.append(f"not a member of runner group '{spec.group}'")
        ev.eligible = not ev.reasons
        return ev

    pool = policy.pools[target_id]
    want = _dedupe(list(pool.labels) + list(req.labels))
    ev = Evaluation(target_id=target_id, kind=KIND_POOL, tier=tier, labels=want,
                    group=pool.group, os=pool.caps.os)
    ev.reasons.extend(_declared_cap_reasons(pool.caps, req))
    if pool.group:
        status = inventory.group_status(pool.group)
        if status == GROUP_NOT_FOUND:
            ev.reasons.append(_group_not_found(pool.group))
        elif status == GROUP_UNKNOWN:
            # Membership is never guessed from labels: a runner carrying the
            # pool's labels may be outside the group, where GitHub will never
            # send the job. The pool stays a target (runs-on = group + labels,
            # which GitHub can schedule), but nothing is claimed about members.
            _mark_membership_unknown(ev, pool.group, inventory)
    if ev.reasons or not inventory.available or ev.membership_unknown:
        ev.eligible = not ev.reasons
        return ev

    # Discover members, then filter EACH member on capability before any
    # availability is considered (stage 4 only ever sees eligible members).
    members = [r for r in inventory.runners.values() if _has_all(r.labels, pool.labels)]
    if pool.group:  # confirmed here
        members = [r for r in members if r.name in inventory.groups[pool.group]]
    order = {name: i for i, name in enumerate(pool.members)}
    members.sort(key=lambda r: (order.get(r.name, len(order)), r.name.casefold()))
    for member in members:
        reasons = _runtime_reasons(member, want, req)
        declared = policy.runners.get(member.name)
        if declared is not None:
            reasons.extend(_declared_cap_reasons(declared.caps, req))
        ev.member_evals.append({"name": member.name, "eligible": not reasons, "reasons": reasons,
                                "online": member.online, "busy": member.busy})
    if not members:
        ev.reasons.append("no runner in the inventory carries labels " + ",".join(pool.labels)
                          + (f" in group '{pool.group}'" if pool.group else ""))
    elif not any(m["eligible"] for m in ev.member_evals):
        ev.reasons.append("no member satisfies the requirements ("
                          + "; ".join(f"{m['name']}: {', '.join(m['reasons'])}"
                                      for m in ev.member_evals) + ")")
    ev.eligible = not ev.reasons
    return ev


def _group_not_found(group: str) -> str:
    return (f"runner group '{group}' does not exist, or is not visible to RUNNER_STATUS_TOKEN "
            f"(organization runner groups need Organization > Self-hosted runners: read)")


def _mark_membership_unknown(ev: Evaluation, group: str, inventory: Inventory) -> None:
    ev.membership_unknown = True
    why = inventory.reason if not inventory.available else \
        "the group endpoints were not readable (they need an organization-scoped token)"
    ev.notes.append(f"membership of runner group '{group}' could not be confirmed ({why}); "
                    f"availability is unknown, no runner is pinned, and runs-on targets the group")


def classify_availability(ev: Evaluation, inventory: Inventory) -> Evaluation:
    """Stage 4. Pools derive their state from capability-eligible members only."""
    if ev.kind == KIND_PROVIDER:
        ev.availability = MANAGED
        return ev
    if not inventory.available or ev.membership_unknown:
        ev.availability = UNKNOWN
        return ev
    if ev.kind == KIND_RUNNER:
        actual = inventory.find(ev.target_id)
        if actual is None:
            ev.availability = NOT_FOUND
            if inventory.scopes == ("repo",):
                ev.notes.append("not in the repository's runner list; an organization runner "
                                "needs availability.scope=org and an org-scoped token")
        elif not actual.online:
            ev.availability = OFFLINE
        elif actual.busy:
            ev.availability = BUSY
        else:
            ev.availability = IDLE
        return ev

    eligible = [m for m in ev.member_evals if m["eligible"]]
    idle = [m for m in eligible if m["online"] and not m["busy"]]
    busy = [m for m in eligible if m["online"] and m["busy"]]
    if idle:
        ev.availability, ev.member = IDLE, idle[0]["name"]
    elif busy:
        ev.availability, ev.member = BUSY, busy[0]["name"]
    elif eligible:
        ev.availability = OFFLINE
    else:
        ev.availability = NOT_FOUND
    return ev


# ════════════════════════════════════════════════════════════════════════════
# Stages 5 + 6 — priority and fallback
# ════════════════════════════════════════════════════════════════════════════

def _tiers(on_busy: str, allow_unknown: bool = True):
    """The deterministic tier order: a list of (list-tier, availability).

    `unknown` (availability could not be read, or group membership could not be
    confirmed) ranks right after `idle` in the same list: possibly idle, never
    proven. The GitHub-hosted provider (`managed`) ranks after every idle or
    unknown self-hosted fallback target, so an unreadable API can never by
    itself move a job to GitHub-hosted.
    """
    P, F = TIER_PRIMARY, TIER_FALLBACK
    if on_busy == ON_BUSY_NEXT:
        order = [(P, IDLE), (P, UNKNOWN), (F, IDLE), (F, UNKNOWN), (F, MANAGED),
                 (P, BUSY), (F, BUSY)]
    else:
        order = [(P, IDLE), (P, UNKNOWN), (P, BUSY), (F, IDLE), (F, UNKNOWN), (F, MANAGED),
                 (F, BUSY)]
    return [t for t in order if allow_unknown or t[1] != UNKNOWN]


def choose(evaluations: List[Evaluation], on_busy: str, on_unavailable: str,
           inventory: Inventory) -> Tuple[Optional[Evaluation], str]:
    """Stage 6. Returns (selected, reason). Only capability-eligible targets compete.

    Unknown availability only affects ranking; capability was settled in stage 3.
    With on-unavailable=fail, an unknown target is never selected.
    """
    eligible = [e for e in evaluations if e.eligible]
    allow_unknown = on_unavailable != ON_UNAVAILABLE_FAIL
    skipped = [f"{e.target_id} {e.availability}" for e in evaluations
               if e.eligible and e.availability not in (IDLE, MANAGED)]
    for tier, state in _tiers(on_busy, allow_unknown):
        for ev in eligible:
            if ev.tier == tier and ev.availability == state:
                return ev, _reason_for(ev, on_busy, skipped, inventory)
    if not allow_unknown and any(e.availability == UNKNOWN for e in eligible):
        return None, "runner availability is unknown and on-unavailable=fail"
    return None, "no eligible target is idle, busy or of unknown availability"


def _reason_for(ev: Evaluation, on_busy: str, skipped: List[str], inventory: Inventory) -> str:
    what = {IDLE: "idle", BUSY: "busy", MANAGED: "managed", UNKNOWN: "unknown"}[ev.availability]
    if ev.kind == KIND_PROVIDER:
        base = "GitHub-hosted fallback (no eligible self-hosted target could be selected)"
    elif ev.availability == BUSY:
        base = (f"no eligible target was idle; queued for the highest-priority busy "
                f"{ev.kind} in the {ev.tier} list (on-busy={on_busy})")
    elif ev.availability == UNKNOWN:
        why = (f"availability unknown ({inventory.reason or 'no inventory'})"
               if not inventory.available else "runner-group membership unconfirmed")
        base = (f"{why}; selected the highest-priority capability-eligible self-hosted "
                f"{ev.kind} in the {ev.tier} list without proof that it is free (on-unavailable=first)")
    else:
        base = f"highest-priority eligible {what} {ev.kind} in the {ev.tier} list"
    if ev.kind == KIND_POOL and ev.member:
        base += f" (member {ev.member} {what})"
    earlier = [s for s in skipped if not s.startswith(ev.target_id + " ")]
    if earlier:
        base += "; passed over: " + ", ".join(earlier)
    return base


# ════════════════════════════════════════════════════════════════════════════
# Stage 7 — runs-on
# ════════════════════════════════════════════════════════════════════════════

def _unique_label(member: str, inventory: Inventory) -> str:
    """A label carried by `member` and by no other inventory runner."""
    runner = inventory.find(member)
    if runner is None:
        return ""
    others = [r for r in inventory.runners.values() if r.name != runner.name]
    candidates = sorted(runner.labels, key=lambda l: (_fold(l) != _fold(runner.name), l))
    for label in candidates:
        if not any(_fold(label) in {_fold(x) for x in other.labels} for other in others):
            return label
    return ""


def render_runs_on(ev: Evaluation, inventory: Inventory, warnings: List[str]):
    """Stage 7. Returns a label list, or {"group", "labels"} when a group is set."""
    labels = list(ev.labels)

    def schedulable(runner: InventoryRunner) -> bool:
        # With a group in runs-on, GitHub only considers that group's runners.
        if ev.group:
            return runner.name in inventory.groups.get(ev.group, frozenset())
        return True

    if ev.membership_unknown:
        # Never pin (and never warn about specific runners) on guessed
        # membership: runs-on stays the group + the pool/runner labels, which
        # GitHub can schedule on any real member.
        pass
    elif ev.kind == KIND_POOL and inventory.available:
        matching = [r for r in inventory.runners.values()
                    if _has_all(r.labels, labels) and schedulable(r)]
        ineligible = {m["name"] for m in ev.member_evals if not m["eligible"]}
        catches = [r.name for r in matching if r.name in ineligible]
        if catches:
            unique = _unique_label(ev.member, inventory) if ev.member else ""
            if unique:
                labels.append(unique)
                ev.notes.append(f"pinned to member {ev.member} via label '{unique}': the pool labels "
                                f"also match capability-ineligible {', '.join(catches)}")
            else:
                warnings.append(f"pool {ev.target_id}: runs-on {','.join(labels)} also matches "
                                f"capability-ineligible runner(s) {', '.join(catches)} and "
                                f"{ev.member or 'the selected member'} has no unique label to pin "
                                f"to. Give each runner a unique label (its name).")
    elif ev.kind == KIND_RUNNER and inventory.available:
        matching = [r.name for r in inventory.runners.values()
                    if _has_all(r.labels, labels) and schedulable(r)]
        if len(matching) > 1:
            warnings.append(f"runner {ev.target_id}: runs-on {','.join(labels)} matches "
                            f"{len(matching)} runners ({', '.join(sorted(matching))}); GitHub may "
                            f"schedule on any of them. Add a label unique to {ev.target_id}.")
    labels = list(_dedupe(labels))
    if ev.group:
        return {"group": ev.group, "labels": labels}
    return labels


# ════════════════════════════════════════════════════════════════════════════
# Orchestration
# ════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class LegacyConfig:
    """The effective legacy configuration from resolve_build_flow.sh. Consumed, never re-derived."""
    labels_by_job: Dict[str, Tuple[str, ...]]
    build_engine: str
    activation: str
    activation_by_engine: Dict[str, str]
    labels_source: str = "default"


def _legacy_entry(job: str, legacy: LegacyConfig, decided_by: str, note: str = "") -> dict:
    labels = legacy.labels_by_job.get(job) or ()
    return {
        "runsOn": list(labels),
        "buildEngine": legacy.build_engine,
        "activationStrategy": legacy.activation,
        "lane": "",
        "mode": "",
        "selectedTarget": ",".join(labels),
        "targetType": TYPE_LEGACY,
        "availability": NOT_CHECKED,
        "selectionReason": note or "legacy runner resolution (no runner policy applies)",
        "decidedBy": decided_by,
        "requirements": {},
        "candidates": [],
        "warnings": [],
    }


def _activation_for(engine: str, legacy: LegacyConfig) -> str:
    if engine == legacy.build_engine:
        return legacy.activation
    return legacy.activation_by_engine.get(engine, legacy.activation)


def _static_filter(jp: JobPolicy, policy: Policy, req: Requirements, job: str,
                   mode: str) -> Tuple[List[Tuple[str, str]], List[Evaluation]]:
    """Validate and pre-filter target lists against platform safety.

    Lists written for this platform (`strict`) must be compatible: an
    incompatible entry is a policy error, never a silent reroute. Lists
    inherited from `default` are generic, so incompatible entries are dropped
    with a reason instead.
    """
    targets: List[Tuple[str, str]] = []
    dropped: List[Evaluation] = []
    lists = [(TIER_PRIMARY, jp.priority), (TIER_FALLBACK, jp.fallback)]
    for tier, ids in lists:
        for target in ids:
            if target == GITHUB_HOSTED and mode == MODE_SELF_HOSTED_ONLY:
                raise PolicyError(f"{job}: mode {MODE_SELF_HOSTED_ONLY} must not list '{GITHUB_HOSTED}' "
                                  f"in fallback. Use mode {MODE_SELF_HOSTED_PREFERRED} to allow it.")
            reasons = []
            if target == GITHUB_HOSTED:
                probe = evaluate_capability(target, tier, policy, req, Inventory("none", "skipped"))
                reasons = probe.reasons
            else:
                spec = policy.runners.get(target) or policy.pools.get(target)
                if spec.caps.os not in req.allowed_os:
                    reasons = [f"os={spec.caps.os} not allowed for {job} with build-engine="
                               f"{req.engine} (requires {'/'.join(sorted(req.allowed_os))})"]
            if reasons:
                if jp.strict:
                    raise PolicyError(f"policy.platforms.{job}.{'priority' if tier == TIER_PRIMARY else 'fallback'}"
                                      f" lists '{target}', which can never run {job}: {'; '.join(reasons)}.")
                kind = KIND_PROVIDER if target == GITHUB_HOSTED else (
                    KIND_RUNNER if target in policy.runners else KIND_POOL)
                dropped.append(Evaluation(target_id=target, kind=kind, tier=tier, eligible=False,
                                          reasons=reasons + ["(inherited from policy.default; "
                                                             "dropped for platform safety)"],
                                          availability=NOT_CHECKED))
                continue
            targets.append((target, tier))
    return targets, dropped


def _legacy_fallback(job: str, lane: str, legacy: LegacyConfig, why: str,
                     candidates: List[Evaluation], mode: str) -> dict:
    """The job's legacy routing, used when only an inherited `default` applies and
    none of it can run this job safely (I-2). The legacy answer is itself checked
    against platform safety: if its labels name an OS the job can never use, this
    fails instead of rerouting -- e.g. iOS is never sent to Linux or Windows."""
    labels = legacy.labels_by_job.get(job) or ()
    allowed = frozenset().union(*(compat.allowed_runner_os(job, e, lane) for e in compat.ENGINES))
    implied = infer_os_from_labels(labels)
    if implied and implied not in allowed:
        raise SchedulingError(
            f"No safe runner for {job}.\n\n{why}.\nThe legacy routing it would fall back to, "
            f"runs-on {','.join(labels)}, implies os={implied}, but {job} requires "
            f"{'/'.join(sorted(allowed))}.\n\nSuggested actions:\n"
            f"  - add policy.platforms.{job} with a {'/'.join(sorted(allowed))} runner or pool\n"
            f"  - or point the legacy labels (RUNNER_*_LABEL / RUNNER_LABELS / ios-runner-label) "
            f"at a {'/'.join(sorted(allowed))} runner")
    note = (f"{why}; legacy runner routing applies"
            + ("" if implied else " (its labels do not reveal an OS; used as configured)"))
    entry = _legacy_entry(job, legacy, DECIDED_LEGACY, note)
    entry["mode"] = mode
    entry["candidates"] = [e.as_dict() for e in candidates]
    entry["warnings"] = [f"{job}: {note}"]
    return entry


def schedule_job(job: str, policy: Policy, jp: JobPolicy, lane: str, legacy: LegacyConfig,
                 unity_version: str, get_inventory) -> dict:
    """Run stages 1-7 for one job. Raises PolicyError / SchedulingError."""
    req = resolve_requirements(job, jp, lane, legacy.build_engine, unity_version)
    mode = jp.mode
    warnings: List[str] = []

    if mode == MODE_GITHUB_HOSTED:
        ev = evaluate_capability(GITHUB_HOSTED, TIER_PRIMARY, policy, req, Inventory("none", "skipped"))
        if not ev.eligible:
            if jp.inherited:
                # Mode inherited from the top level / `default`, not written for
                # this job: a generic preference iOS (say) cannot take.
                ev.availability = NOT_CHECKED
                return _legacy_fallback(job, lane, legacy,
                                        f"inherited mode {MODE_GITHUB_HOSTED} cannot run {job} "
                                        f"({'; '.join(ev.reasons)})", [ev], mode)
            raise PolicyError(f"{job}: mode {MODE_GITHUB_HOSTED} cannot run this job: {'; '.join(ev.reasons)}.")
        classify_availability(ev, Inventory("none", "skipped"))
        selected, reason, evaluations = ev, f"mode {MODE_GITHUB_HOSTED}: GitHub-hosted provider selected directly", [ev]
        inventory = Inventory("none", "skipped", reason="not needed in mode github-hosted")
    else:
        targets, dropped = _static_filter(jp, policy, req, job, mode)
        if not targets and dropped and jp.inherited:
            return _legacy_fallback(job, lane, legacy,
                                    f"policy.default has no target that can run {job} safely",
                                    dropped, mode)
        if not targets and not dropped:
            raise PolicyError(f"{job}: the {jp.decided_by} names no runner, pool or fallback "
                              f"(mode {mode}). Add a `priority` list.")
        inventory = get_inventory()
        evaluations = list(dropped)
        for target, tier in targets:
            ev = evaluate_capability(target, tier, policy, req, inventory)
            classify_availability(ev, inventory)
            evaluations.append(ev)
        selected, reason = choose(evaluations, jp.on_busy, jp.on_unavailable, inventory)
        if selected is None:
            raise SchedulingError(format_failure(job, mode, req, evaluations, inventory, reason, jp))
        if selected.availability == UNKNOWN:
            why = inventory.reason if not inventory.available else "runner-group membership unconfirmed"
            warnings.append(f"{job}: runner availability unknown ({why}); selected "
                            f"{selected.target_id} without knowing whether it is online or free. "
                            f"Selection is not a reservation: if it is busy or offline, GitHub "
                            f"queues the job")

    runs_on = render_runs_on(selected, inventory, warnings)
    target_name = selected.target_id + (f"/{selected.member}" if selected.member else "")
    return {
        "runsOn": runs_on,
        "buildEngine": req.engine,
        "activationStrategy": _activation_for(req.engine, legacy),
        "lane": lane,
        "mode": mode,
        "selectedTarget": target_name,
        "targetType": TYPE_GITHUB_HOSTED if selected.kind == KIND_PROVIDER else TYPE_SELF_HOSTED,
        "availability": selected.availability,
        "selectionReason": reason,
        "decidedBy": jp.decided_by,
        "requirements": req.as_dict(),
        "candidates": [e.as_dict() for e in evaluations],
        "warnings": warnings,
    }


def _suggestions(job: str, req: Requirements, evaluations: List[Evaluation],
                 inventory: Inventory, mode: str, jp: JobPolicy) -> List[str]:
    tips = []
    for ev in evaluations:
        if ev.kind == KIND_PROVIDER:
            continue
        if ev.eligible and ev.availability == OFFLINE:
            tips.append(f"bring {ev.target_id} online (the runner service is not connected)")
        elif ev.eligible and ev.availability == NOT_FOUND:
            tips.append(f"register {ev.target_id} with GitHub, or check the token can see it "
                        f"(organization runners need availability.scope=org)")
        for reason in ev.reasons:
            if reason.startswith("missing="):
                tips.append(f"add label(s) {reason[len('missing='):]} to {ev.target_id}, "
                            f"or install what they stand for")
            elif reason.startswith("xcode"):
                tips.append(f"install Xcode {req.xcode} on {ev.target_id} and declare it in its \"xcode\" list")
            elif reason.startswith("unity="):
                tips.append(f"install Unity {req.unity} on {ev.target_id} and declare it in its \"unity\" list")
    if not inventory.available and jp.on_unavailable == ON_UNAVAILABLE_FAIL:
        tips.append("provide RUNNER_STATUS_TOKEN (see docs/MULTI_RUNNER_SCHEDULING.md) or set "
                    "availability.on-unavailable=first")
    tips.append(f"register another runner with labels {','.join(req.labels) or '<platform labels>'} "
                f"on {'/'.join(sorted(req.allowed_os))} and add it to the priority list")
    if mode == MODE_SELF_HOSTED_PREFERRED and not any(e.kind == KIND_PROVIDER for e in evaluations) \
            and compat.OS_LINUX in req.allowed_os and req.engine == compat.ENGINE_DOCKER:
        tips.append(f"allow a GitHub-hosted fallback: \"fallback\": [\"{GITHUB_HOSTED}\"]")
    out = []
    for tip in tips:
        if tip not in out:
            out.append(tip)
    return out


def format_failure(job: str, mode: str, req: Requirements, evaluations: List[Evaluation],
                   inventory: Inventory, why: str, jp: JobPolicy) -> str:
    lines = [f"No eligible runner found for {job}.", ""]
    lines.append(f"Mode: {mode}    Policy: {jp.decided_by}    Inventory: {inventory.provider} "
                 f"({inventory.status}{': ' + inventory.reason if inventory.reason else ''})")
    lines.append("")
    lines.append("Required capabilities:")
    lines.append(f"  os={'/'.join(sorted(req.allowed_os))}")
    lines.append(f"  platform={job}")
    lines.append(f"  build-engine={req.engine}")
    if req.unity:
        lines.append(f"  unity={req.unity}")
    if req.xcode:
        lines.append(f"  xcode={req.xcode}")
    if req.labels:
        lines.append(f"  labels={','.join(req.labels)}")
    lines.append("")
    lines.append("Candidates:")
    if not evaluations:
        lines.append("  (none)")
    for ev in evaluations:
        lines.append(f"  {ev.target_id}  [{ev.kind}, {ev.tier}]")
        lines.append(f"    status={ev.availability}" + ("" if ev.eligible else "  (ineligible)"))
        for reason in ev.reasons:
            lines.append(f"    {reason}")
        for member in ev.member_evals:
            state = "busy" if member["busy"] else ("online" if member["online"] else "offline")
            detail = ", ".join(member["reasons"]) if member["reasons"] else "eligible"
            lines.append(f"    member {member['name']}: {state}; {detail}")
        for note in ev.notes:
            lines.append(f"    note: {note}")
    lines.append("")
    lines.append(f"Why nothing was selected: {why}.")
    lines.append("")
    lines.append("Suggested actions:")
    for tip in _suggestions(job, req, evaluations, inventory, mode, jp):
        lines.append(f"  - {tip}")
    return "\n".join(lines)


def build_selection(jobs: Sequence[str], inactive_jobs: Sequence[str], lane: str,
                    legacy: LegacyConfig, policy: Optional[Policy], provider: InventoryProvider,
                    unity_version: str = "", policy_mode: str = "auto",
                    dispatch_labels: bool = False, explicit_labels: Tuple[str, ...] = ()) -> dict:
    """Schedule every job and return the runner-selection document."""
    inventory_cache: Dict[str, Inventory] = {}

    def get_inventory() -> Inventory:
        # One discovery per run, shared by every job -- and none at all when no
        # job needs self-hosted state (mode github-hosted, legacy passthrough).
        if "inv" not in inventory_cache:
            groups = tuple(sorted({p.group for p in policy.pools.values() if p.group}
                                  | {r.group for r in policy.runners.values() if r.group}))
            inventory_cache["inv"] = provider.discover(InventoryRequest(
                groups=groups, scope=policy.availability.scope,
                timeout_seconds=policy.availability.timeout_seconds))
        return inventory_cache["inv"]

    selection_jobs: Dict[str, dict] = {}
    for job in inactive_jobs:
        selection_jobs[job] = _legacy_entry(job, legacy, DECIDED_INACTIVE,
                                            "job not requested in this run")
    for job in jobs:
        if explicit_labels:
            entry = _legacy_entry(job, legacy, DECIDED_EXPLICIT,
                                  "explicit runner label input; policy not consulted")
            entry["runsOn"] = list(explicit_labels)
            entry["selectedTarget"] = ",".join(explicit_labels)
        elif dispatch_labels:
            entry = _legacy_entry(job, legacy, DECIDED_DISPATCH,
                                  "runner-labels workflow input pins every job; policy not consulted")
        elif policy is None:
            entry = _legacy_entry(job, legacy, DECIDED_LEGACY)
        elif policy_mode == "legacy":
            entry = _legacy_entry(job, legacy, DECIDED_BYPASS,
                                  "runner-policy=legacy: policy present but bypassed for this run")
        else:
            jp = effective_job_policy(policy, job)
            if jp is None:
                entry = _legacy_entry(job, legacy, DECIDED_LEGACY,
                                      "the runner policy has no section for this job and no default; "
                                      "legacy runner resolution applies")
            else:
                entry = schedule_job(job, policy, jp, lane, legacy, unity_version, get_inventory)
        entry["lane"] = lane
        selection_jobs[job] = entry

    inventory = inventory_cache.get("inv")
    if inventory is None:
        inventory = Inventory("none", "skipped", reason="no job needed self-hosted availability")
    uses_docker = legacy.build_engine == compat.ENGINE_DOCKER or any(
        selection_jobs[j]["buildEngine"] == compat.ENGINE_DOCKER for j in jobs)
    return {
        "version": SELECTION_VERSION,
        "policySource": policy.source if policy else "none",
        "policyMode": policy_mode,
        "mode": policy.mode if policy else "",
        "inventory": inventory.as_dict(),
        "summary": {"usesDocker": uses_docker, "jobs": list(jobs)},
        "jobs": selection_jobs,
    }


# ════════════════════════════════════════════════════════════════════════════
# Rendering
# ════════════════════════════════════════════════════════════════════════════

def _runs_on_text(runs_on) -> str:
    if isinstance(runs_on, dict):
        return f"group={runs_on.get('group')} labels={','.join(runs_on.get('labels', []))}"
    return ",".join(runs_on)


def render_log(selection: dict) -> str:
    out = []
    for job in selection["summary"]["jobs"]:
        e = selection["jobs"][job]
        title = f"Runner selection — {job}"
        out += [title, "-" * len(title)]
        out.append(f"Decided by:    {e['decidedBy']} ({selection['policySource']})")
        if e["mode"]:
            out.append(f"Mode:          {e['mode']}")
        out.append(f"Runner type:   {e['targetType']}")
        out.append(f"Build engine:  {e['buildEngine']}")
        req = e.get("requirements") or {}
        if req:
            out.append("Required:")
            out.append(f"  os in: {', '.join(req.get('os', []))}")
            if req.get("labels"):
                out.append(f"  labels: {', '.join(req['labels'])}")
            if req.get("unity"):
                out.append(f"  unity: {req['unity']}")
            if req.get("xcode"):
                out.append(f"  xcode: {req['xcode']}")
        if e["candidates"]:
            out.append("Candidates:")
            for i, c in enumerate(e["candidates"], start=1):
                flag = "" if c["eligible"] else "  ineligible: " + "; ".join(c["reasons"])
                member = f" via {c['member']}" if c.get("member") else ""
                out.append(f"  {i}. {c['id']:<22} {c['kind']:<13} {c['tier']:<9} "
                           f"[{c['availability']}{member}]{flag}")
                for note in c.get("notes", []):
                    out.append(f"       note: {note}")
        out.append(f"Selected:      {e['selectedTarget'] or '-'} [{e['availability']}]")
        out.append(f"runs-on:       {_runs_on_text(e['runsOn'])}")
        out.append(f"Reason:        {e['selectionReason']}")
        for w in e.get("warnings", []):
            out.append(f"Warning:       {w}")
        out.append("")
    inv = selection["inventory"]
    out.append(f"Inventory: provider={inv['provider']} status={inv['status']} "
               f"runners={inv['runnerCount']}" + (f" ({inv['reason']})" if inv["reason"] else ""))
    return "\n".join(out)


def _md(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(selection: dict) -> str:
    lines = ["### Runner selection", ""]
    lines.append(f"Policy: `{selection['policySource']}`"
                 + (f" · mode `{selection['mode']}`" if selection["mode"] else "")
                 + f" · inventory `{selection['inventory']['provider']}`/"
                 f"`{selection['inventory']['status']}`")
    lines.append("")
    jobs = selection["summary"]["jobs"]
    if not jobs:
        lines.append("_No Unity job scheduled in this run._")
        return "\n".join(lines) + "\n"
    lines.append("| Job | Selected | Type | Availability | Engine | runs-on | Decided by | Reason |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for job in jobs:
        e = selection["jobs"][job]
        lines.append(f"| {job} | `{_md(e['selectedTarget'] or '-')}` | {e['targetType']} | "
                     f"{e['availability']} | {e['buildEngine']} | `{_md(_runs_on_text(e['runsOn']))}` | "
                     f"{e['decidedBy']} | {_md(e['selectionReason'])} |")
    detailed = [j for j in jobs if selection["jobs"][j]["candidates"]]
    for job in detailed:
        e = selection["jobs"][job]
        lines += ["", f"<details><summary>{job} candidates</summary>", "",
                  "| # | Target | Kind | Tier | Availability | Eligible | Reasons |",
                  "|---|---|---|---|---|---|---|"]
        for i, c in enumerate(e["candidates"], start=1):
            lines.append(f"| {i} | `{_md(c['id'])}` | {c['kind']} | {c['tier']} | {c['availability']} | "
                         f"{'yes' if c['eligible'] else 'no'} | {_md('; '.join(c['reasons']) or '-')} |")
        lines += ["", "</details>"]
    return "\n".join(lines) + "\n"


# ════════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════════

def _csv(value: str) -> List[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def _legacy_from_args(args) -> LegacyConfig:
    labels_by_job: Dict[str, Tuple[str, ...]] = {}
    if args.legacy_labels_by_platform.strip():
        try:
            mapping = json.loads(args.legacy_labels_by_platform)
        except json.JSONDecodeError as exc:
            raise PolicyError(f"legacy runner-labels-by-platform is not JSON: {exc}") from None
        for job, labels in (mapping or {}).items():
            labels_by_job[job] = parse_labels(labels)
    tests_labels = parse_labels(args.legacy_labels_linux)
    for job in (compat.JOB_UNITY_TESTS, compat.JOB_ADDRESSABLES):
        labels_by_job.setdefault(job, tests_labels)
    if args.fallback_labels:
        fallback = parse_labels(args.fallback_labels)
        for job in compat.SCHEDULABLE_JOBS:
            labels_by_job.setdefault(job, fallback)
    return LegacyConfig(
        labels_by_job=labels_by_job,
        build_engine=args.legacy_build_engine,
        activation=args.legacy_activation,
        activation_by_engine={compat.ENGINE_DOCKER: args.activation_docker,
                              compat.ENGINE_LOCAL: args.activation_local},
        labels_source=args.legacy_labels_source,
    )


def _provider_from_args(args, policy: Optional[Policy]) -> InventoryProvider:
    if args.inventory_file:
        return SnapshotInventory(args.inventory_file)
    if policy is None:
        return NullInventory("no runner policy", status="skipped")
    if policy.availability.source == "none":
        return NullInventory("availability.source=none")
    token = os.environ.get("RUNNER_STATUS_TOKEN", "")
    if not token:
        if policy.availability.source == "api":
            return NullInventory("availability.source=api but RUNNER_STATUS_TOKEN is not set")
        return NullInventory("RUNNER_STATUS_TOKEN not set")
    import runner_inventory  # noqa: WPS433 - only needed when the API is used
    return runner_inventory.GitHubApiInventory(
        token=token, repository=args.repository or os.environ.get("GITHUB_REPOSITORY", ""),
        api_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"))


def _write_outputs(path: str, outputs: Dict[str, str]) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for key, value in outputs.items():
            fh.write(f"{key}={value}\n")


def _append(path: str, text: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)


def main(argv: Optional[Sequence[str]] = None) -> int:
    env = os.environ.get
    parser = argparse.ArgumentParser(
        description="Select a runner for every Unity job (ADR 004 stage 2c). "
                    "Writes the runner-selection JSON to $GITHUB_OUTPUT.")
    parser.add_argument("--jobs", default=env("RS_JOBS", ""),
                        help="Comma-separated jobs to schedule (Android,iOS,...,UnityTests,Addressables).")
    parser.add_argument("--inactive-jobs", default=env("RS_INACTIVE_JOBS", ""),
                        help="Jobs that will not run; recorded as legacy entries so expressions resolve.")
    parser.add_argument("--lane", default=env("RS_LANE", compat.LANE_PIPELINE), choices=compat.LANES)
    parser.add_argument("--legacy-labels-by-platform", default=env("RS_LEGACY_LABELS_BY_PLATFORM", ""))
    parser.add_argument("--legacy-labels-linux", default=env("RS_LEGACY_LABELS_LINUX", ""))
    parser.add_argument("--legacy-labels-source", default=env("RS_LEGACY_LABELS_SOURCE", "default"))
    parser.add_argument("--legacy-build-engine", default=env("RS_LEGACY_BUILD_ENGINE", "docker"),
                        choices=compat.ENGINES)
    parser.add_argument("--legacy-activation", default=env("RS_LEGACY_ACTIVATION", "auto"))
    parser.add_argument("--activation-docker", default=env("RS_ACTIVATION_DOCKER", ""))
    parser.add_argument("--activation-local", default=env("RS_ACTIVATION_LOCAL", "none"))
    parser.add_argument("--fallback-labels", default=env("RS_FALLBACK_LABELS", ""),
                        help="Standalone lanes: the label used when no policy applies.")
    parser.add_argument("--explicit-labels", default=env("RS_EXPLICIT_LABELS", ""),
                        help="Standalone lanes: an explicitly passed runner label; wins over policy.")
    parser.add_argument("--unity-version", default=env("RS_UNITY_VERSION", ""))
    parser.add_argument("--policy-mode", default=env("RS_POLICY_MODE", "auto") or "auto",
                        choices=("auto", "legacy"))
    parser.add_argument("--policy-file", default=env("RUNNER_POLICY_FILE", "") or
                        ".github/unity-runner-policy.json")
    parser.add_argument("--inventory-file", default=env("RS_INVENTORY_FILE", ""),
                        help="Read runner inventory from a JSON snapshot instead of the GitHub API.")
    parser.add_argument("--repository", default=env("GITHUB_REPOSITORY", ""))
    parser.add_argument("--github-output", default=env("GITHUB_OUTPUT", ""))
    parser.add_argument("--step-summary", default=env("GITHUB_STEP_SUMMARY", ""))
    parser.add_argument("--render", action="store_true",
                        help="Do not schedule: render the runner-selection JSON in RUNNER_SELECTION "
                             "as Markdown (for the final report).")
    args = parser.parse_args(argv)
    if args.render:
        try:
            selection = json.loads(env("RUNNER_SELECTION", "") or "{}")
            markdown = render_markdown(selection)
        except (json.JSONDecodeError, KeyError, TypeError):
            markdown = "### Runner selection\n\n_No runner selection was recorded for this run._\n"
        print(markdown)
        _append(args.step_summary, markdown)
        return 0
    if not args.activation_docker:
        args.activation_docker = args.legacy_activation

    jobs = _csv(args.jobs)
    for job in jobs + _csv(args.inactive_jobs):
        if job not in compat.SCHEDULABLE_JOBS:
            print(f"::error::Unknown job '{job}'.", file=sys.stderr)
            return 2

    try:
        legacy = _legacy_from_args(args)
        policy = load_policy(env("RUNNER_POLICY", ""), args.policy_file)
        provider = _provider_from_args(args, policy)
        selection = build_selection(
            jobs=jobs, inactive_jobs=[j for j in _csv(args.inactive_jobs) if j not in jobs],
            lane=args.lane, legacy=legacy, policy=policy, provider=provider,
            unity_version=args.unity_version, policy_mode=args.policy_mode,
            dispatch_labels=args.legacy_labels_source == "dispatch",
            explicit_labels=parse_labels(args.explicit_labels))
    except PolicyError as exc:
        print(f"::error title=Runner policy invalid::{exc}", file=sys.stderr)
        _append(args.step_summary, f"### Runner selection\n\n**Runner policy invalid:** {_md(exc)}\n")
        return 1
    except SchedulingError as exc:
        report = str(exc)
        first = report.splitlines()[0]
        print(f"::error title=No eligible runner::{first} See the log for candidates and actions.",
              file=sys.stderr)
        print(report, file=sys.stderr)
        _append(args.step_summary, "### Runner selection\n\n```text\n" + report + "\n```\n")
        return 1

    print(render_log(selection))
    for job in jobs:
        for warning in selection["jobs"][job].get("warnings", []):
            print(f"::warning title=Runner selection::{warning}")
    compact = json.dumps(selection, separators=(",", ":"), sort_keys=False)
    # Everything below is a serialization of the same document, for consumers
    # that cannot index JSON safely (a job-level `if:`, or a `with:` value that
    # must not depend on fromJSON() of an output that might be empty).
    outputs = {
        "runner-selection": compact,
        "uses-docker": "true" if selection["summary"]["usesDocker"] else "false",
    }
    for unity_job in (compat.JOB_UNITY_TESTS, compat.JOB_ADDRESSABLES):
        if unity_job in selection["jobs"]:
            outputs[f"runs-on-{unity_job.lower()}"] = json.dumps(
                selection["jobs"][unity_job]["runsOn"], separators=(",", ":"))
    if len(jobs) == 1:
        only = selection["jobs"][jobs[0]]
        outputs["runs-on"] = json.dumps(only["runsOn"], separators=(",", ":"))
        outputs["build-engine"] = only["buildEngine"]
        outputs["selected-target"] = only["selectedTarget"]
    _write_outputs(args.github_output, outputs)
    _append(args.step_summary, render_markdown(selection))
    return 0


if __name__ == "__main__":
    sys.exit(main())
