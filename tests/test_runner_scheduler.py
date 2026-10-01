"""The runner scheduler, as pure functions: policy + legacy + inventory → selection.

No workflow YAML is involved here. Every test builds a policy document, an
in-memory runner inventory, and asserts on the runner-selection document the
scheduler produces -- the same document the pipeline routes on.

Self-hosted is the primary path: the scheduler's job is to find a self-hosted
runner. GitHub-hosted is a managed provider the policy may allow; it is never a
runner with an `idle` state. See docs/MULTI_RUNNER_SCHEDULING.md.
"""
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "common"))

import runner_scheduler as rs  # noqa: E402
from runner_scheduler import (  # noqa: E402
    Inventory, InventoryProvider, InventoryRunner, LegacyConfig, PolicyError, SchedulingError,
)

UNITY = "6000.0.26f1"

LEGACY = LegacyConfig(
    labels_by_job={
        "Android": ("ubuntu-latest",), "WebGL": ("ubuntu-latest",), "Linux64": ("ubuntu-latest",),
        "LinuxServer": ("ubuntu-latest",), "Windows64": ("ubuntu-latest",), "iOS": ("macos-latest",),
        "UnityTests": ("ubuntu-latest",), "Addressables": ("ubuntu-latest",),
    },
    build_engine="docker", activation="auto",
    activation_by_engine={"docker": "auto", "local": "none"},
)


# ---------------------------------------------------------------------------
# Fixtures-as-functions
# ---------------------------------------------------------------------------

def runner(name, os="linux", online=True, busy=False, labels=()):
    os_label = {"linux": "linux", "macos": "macOS", "windows": "windows"}[os]
    return InventoryRunner(name=name, os=os, online=online, busy=busy,
                           labels=tuple(["self-hosted", os_label, name] + list(labels)))


def inventory(*runners, groups=None, scopes=("repo",)):
    return Inventory(provider="snapshot", status="ok", runners={r.name: r for r in runners},
                     groups={k: frozenset(v) for k, v in (groups or {}).items()}, scopes=scopes)


class FakeProvider(InventoryProvider):
    def __init__(self, inv):
        self.inv = inv
        self.calls = 0

    def discover(self, request):
        self.calls += 1
        return self.inv


class ExplodingProvider(InventoryProvider):
    def discover(self, request):
        raise AssertionError("the inventory must not be consulted")


UNAVAILABLE = Inventory(provider="github-api", status="unavailable", reason="HTTP 403")

IOS_RUNNERS = {
    name: {"labels": ["self-hosted", "macOS", name], "platforms": ["iOS"],
           "build-engines": ["local"], "xcode": ["16"]}
    for name in ("ios-mac-01", "ios-mac-02", "ios-mac-03")
}
LINUX_RUNNERS = {
    name: {"labels": ["self-hosted", "linux", name], "build-engines": ["docker"]}
    for name in ("linux-01", "linux-02")
}


def policy(**doc):
    doc.setdefault("version", 1)
    doc.setdefault("runners", {**IOS_RUNNERS, **LINUX_RUNNERS})
    return rs.parse_policy(doc, "test")


def select(pol, jobs, inv=None, provider=None, legacy=LEGACY, **kw):
    provider = provider or FakeProvider(inv if inv is not None else inventory())
    return rs.build_selection(jobs=jobs, inactive_jobs=[], lane=kw.pop("lane", "pipeline"),
                              legacy=legacy, policy=pol, provider=provider,
                              unity_version=UNITY, **kw)


def job(selection, name):
    return selection["jobs"][name]


IOS_PLATFORM = {"priority": ["ios-mac-01", "ios-mac-02", "ios-mac-03"], "require": {"xcode": "16"}}


def ios_inv(**states):
    """states: name -> 'idle' | 'busy' | 'offline' | 'absent'."""
    rows = []
    for name in ("ios-mac-01", "ios-mac-02", "ios-mac-03"):
        state = states.get(name, "idle")
        if state == "absent":
            continue
        rows.append(runner(name, "macos", online=state != "offline", busy=state == "busy",
                           labels=("xcode-16",)))
    return inventory(*rows)


# ---------------------------------------------------------------------------
# Execution modes
# ---------------------------------------------------------------------------

class TestSelfHostedOnly:
    def test_idle_runner_is_selected(self):
        pol = policy(mode="self-hosted-only", platforms={"iOS": IOS_PLATFORM})
        e = job(select(pol, ["iOS"], ios_inv()), "iOS")
        assert e["selectedTarget"] == "ios-mac-01"
        assert e["targetType"] == "self-hosted"
        assert e["availability"] == "idle"
        assert e["runsOn"] == ["self-hosted", "macOS", "ios-mac-01"]

    def test_busy_runner_is_queued_for_under_wait(self):
        pol = policy(mode="self-hosted-only",
                     platforms={"iOS": {"priority": ["ios-mac-01"], "on-busy": "wait"}})
        e = job(select(pol, ["iOS"], ios_inv(**{"ios-mac-01": "busy"})), "iOS")
        assert e["selectedTarget"] == "ios-mac-01"
        assert e["availability"] == "busy"
        assert "queued for the highest-priority busy runner" in e["selectionReason"]

    def test_busy_runner_under_next_still_beats_nothing(self):
        pol = policy(mode="self-hosted-only",
                     platforms={"iOS": {"priority": ["ios-mac-01"], "on-busy": "next"}})
        e = job(select(pol, ["iOS"], ios_inv(**{"ios-mac-01": "busy"})), "iOS")
        assert e["selectedTarget"] == "ios-mac-01" and e["availability"] == "busy"

    def test_no_eligible_runner_is_a_hard_failure(self):
        pol = policy(mode="self-hosted-only", platforms={"iOS": IOS_PLATFORM})
        with pytest.raises(SchedulingError) as err:
            select(pol, ["iOS"], ios_inv(**{n: "offline" for n in IOS_RUNNERS}))
        assert "No eligible runner found for iOS" in str(err.value)

    def test_github_hosted_fallback_is_a_policy_error(self):
        with pytest.raises(PolicyError, match="self-hosted-only"):
            select(policy(mode="self-hosted-only",
                          platforms={"Android": {"priority": ["linux-01"], "fallback": ["github-hosted"]}}),
                   ["Android"], inventory(runner("linux-01")))


class TestSelfHostedPreferred:
    ANDROID = {"priority": ["linux-01", "linux-02"], "fallback": ["github-hosted"]}

    def test_self_hosted_available_is_selected(self):
        pol = policy(mode="self-hosted-preferred", platforms={"Android": self.ANDROID})
        e = job(select(pol, ["Android"], inventory(runner("linux-01"), runner("linux-02"))), "Android")
        assert e["selectedTarget"] == "linux-01" and e["targetType"] == "self-hosted"

    def test_self_hosted_unavailable_falls_back_to_github_hosted(self):
        pol = policy(mode="self-hosted-preferred", platforms={"Android": self.ANDROID})
        inv = inventory(runner("linux-01", online=False), runner("linux-02", online=False))
        e = job(select(pol, ["Android"], inv), "Android")
        assert e["selectedTarget"] == "github-hosted"
        assert e["targetType"] == "github-hosted"
        assert e["availability"] == "managed"
        assert e["runsOn"] == ["ubuntu-latest"]
        assert e["buildEngine"] == "docker"

    def test_no_fallback_is_a_hard_failure(self):
        pol = policy(mode="self-hosted-preferred",
                     platforms={"Android": {"priority": ["linux-01", "linux-02"]}})
        inv = inventory(runner("linux-01", online=False), runner("linux-02", online=False))
        with pytest.raises(SchedulingError) as err:
            select(pol, ["Android"], inv)
        assert 'allow a GitHub-hosted fallback' in str(err.value)

    def test_is_the_default_mode(self):
        assert policy(platforms={"Android": self.ANDROID}).mode == "self-hosted-preferred"


class TestGithubHostedMode:
    def test_selected_without_consulting_the_inventory(self):
        pol = policy(mode="github-hosted", platforms={"Android": {}})
        e = job(select(pol, ["Android", "WebGL"], provider=ExplodingProvider()), "Android")
        assert e["targetType"] == "github-hosted"
        assert e["runsOn"] == ["ubuntu-latest"]
        assert e["availability"] == "managed"

    def test_ios_cannot_be_github_hosted(self):
        pol = policy(mode="github-hosted", platforms={"iOS": {}})
        with pytest.raises(PolicyError, match="cannot run this job"):
            select(pol, ["iOS"], provider=ExplodingProvider())

    def test_provider_label_is_configurable(self):
        pol = policy(mode="github-hosted", default={}, **{"github-hosted": {"labels": ["ubuntu-24.04"]}})
        assert job(select(pol, ["WebGL"], provider=ExplodingProvider()), "WebGL")["runsOn"] == ["ubuntu-24.04"]

    def test_provider_label_must_be_linux(self):
        with pytest.raises(PolicyError, match="Linux image"):
            policy(**{"github-hosted": {"labels": ["macos-latest"]}})


class TestManagedProviderSemantics:
    """GitHub-hosted is a managed provider, never an `idle` runner."""

    @pytest.mark.parametrize("pol_kwargs,inv", [
        (dict(mode="github-hosted", default={}), inventory()),
        (dict(default={"priority": ["linux-01"], "fallback": ["github-hosted"]}),
         inventory(runner("linux-01", online=False))),
        (dict(default={"priority": ["linux-01"], "fallback": ["github-hosted"], "on-busy": "next"}),
         inventory(runner("linux-01", busy=True))),
    ])
    def test_never_reported_idle(self, pol_kwargs, inv):
        sel = select(policy(**pol_kwargs), ["Android"], inv)
        for c in job(sel, "Android")["candidates"]:
            if c["kind"] == "github-hosted":
                assert c["availability"] == "managed"
        e = job(sel, "Android")
        if e["targetType"] == "github-hosted":
            assert e["availability"] == "managed"

    def test_github_hosted_in_priority_is_rejected(self):
        with pytest.raises(PolicyError, match="fallback provider"):
            policy(default={"priority": ["github-hosted"]})


# ---------------------------------------------------------------------------
# Priority
# ---------------------------------------------------------------------------

class TestPriority:
    def test_primary_online_is_selected(self):
        e = job(select(policy(platforms={"iOS": IOS_PLATFORM}), ["iOS"], ios_inv()), "iOS")
        assert e["selectedTarget"] == "ios-mac-01"

    def test_primary_offline_selects_secondary(self):
        e = job(select(policy(platforms={"iOS": IOS_PLATFORM}), ["iOS"],
                       ios_inv(**{"ios-mac-01": "offline"})), "iOS")
        assert e["selectedTarget"] == "ios-mac-02"
        assert "ios-mac-01 offline" in e["selectionReason"]

    def test_primary_busy_secondary_idle_selects_secondary(self):
        e = job(select(policy(platforms={"iOS": IOS_PLATFORM}), ["iOS"],
                       ios_inv(**{"ios-mac-01": "busy"})), "iOS")
        assert e["selectedTarget"] == "ios-mac-02" and e["availability"] == "idle"

    def test_not_registered_runner_is_never_selected(self):
        e = job(select(policy(platforms={"iOS": IOS_PLATFORM}), ["iOS"],
                       ios_inv(**{"ios-mac-01": "absent"})), "iOS")
        assert e["selectedTarget"] == "ios-mac-02"
        first = e["candidates"][0]
        assert first["availability"] == "not-found"

    def test_fixed_runner_mode_a(self):
        pol = policy(platforms={"iOS": {"priority": ["ios-mac-03"]}})
        assert job(select(pol, ["iOS"], ios_inv()), "iOS")["selectedTarget"] == "ios-mac-03"


# ---------------------------------------------------------------------------
# Fallback tiers — the full table, both on-busy settings
# ---------------------------------------------------------------------------

FALLBACK_POLICY_RUNNERS = {
    name: {"labels": ["self-hosted", "linux", name]} for name in ("p1", "p2", "f1", "f2")
}


@pytest.mark.parametrize("on_busy,states,expected", [
    # on-busy=wait: idle primary > busy primary > idle fallback > busy fallback
    ("wait", dict(p1="busy", p2="idle", f1="idle", f2="idle"), "p2"),
    ("wait", dict(p1="busy", p2="offline", f1="idle", f2="idle"), "p1"),
    ("wait", dict(p1="offline", p2="offline", f1="busy", f2="idle"), "f2"),
    ("wait", dict(p1="offline", p2="offline", f1="busy", f2="busy"), "f1"),
    # on-busy=next: idle primary > idle fallback > busy primary > busy fallback
    ("next", dict(p1="busy", p2="idle", f1="idle", f2="idle"), "p2"),
    ("next", dict(p1="busy", p2="offline", f1="busy", f2="idle"), "f2"),
    ("next", dict(p1="busy", p2="offline", f1="busy", f2="offline"), "p1"),
    ("next", dict(p1="offline", p2="offline", f1="busy", f2="offline"), "f1"),
])
def test_tier_order(on_busy, states, expected):
    pol = rs.parse_policy({"runners": FALLBACK_POLICY_RUNNERS, "platforms": {"Android": {
        "priority": ["p1", "p2"], "fallback": ["f1", "f2"], "on-busy": on_busy}}}, "test")
    inv = inventory(*[runner(n, online=s != "offline", busy=s == "busy") for n, s in states.items()])
    assert job(select(pol, ["Android"], inv), "Android")["selectedTarget"] == expected


def test_github_fallback_sits_in_the_idle_fallback_tier():
    """Under wait, a busy primary beats the GitHub fallback; under next, it does not."""
    runners = {"p1": {"labels": ["self-hosted", "linux", "p1"]}}
    inv = inventory(runner("p1", busy=True))
    for on_busy, expected in (("wait", "p1"), ("next", "github-hosted")):
        pol = rs.parse_policy({"runners": runners, "platforms": {"Android": {
            "priority": ["p1"], "fallback": ["github-hosted"], "on-busy": on_busy}}}, "test")
        assert job(select(pol, ["Android"], inv), "Android")["selectedTarget"] == expected


def test_capability_ineligible_targets_are_never_fallback_candidates():
    runners = {"p1": {"labels": ["self-hosted", "linux", "p1"]},
               "f1": {"labels": ["self-hosted", "linux", "f1"], "build-engines": ["local"]}}
    pol = rs.parse_policy({"runners": runners, "platforms": {"Android": {
        "priority": ["p1"], "fallback": ["f1"]}}}, "test")
    with pytest.raises(SchedulingError) as err:
        select(pol, ["Android"], inventory(runner("p1", online=False), runner("f1")))
    assert "build-engines=local does not include docker" in str(err.value)


# ---------------------------------------------------------------------------
# Capability
# ---------------------------------------------------------------------------

class TestCapability:
    def test_online_runner_missing_ios_capability_is_skipped(self):
        runners = {**IOS_RUNNERS, "mac-generic": {"labels": ["self-hosted", "macOS", "mac-generic"],
                                                  "platforms": ["Android"]}}
        pol = rs.parse_policy({"runners": runners, "platforms": {"iOS": {
            "priority": ["mac-generic", "ios-mac-01"]}}}, "test")
        inv = inventory(runner("mac-generic", "macos"), runner("ios-mac-01", "macos"))
        e = job(select(pol, ["iOS"], inv), "iOS")
        assert e["selectedTarget"] == "ios-mac-01"
        assert "does not include iOS" in e["candidates"][0]["reasons"][0]

    def test_actual_labels_are_checked_against_requirements(self):
        """Declared capability is a claim; the inventory is evidence."""
        pol = policy(platforms={"iOS": {"priority": ["ios-mac-01", "ios-mac-02"],
                                        "require": {"labels": ["xcode-16"]}}})
        inv = inventory(runner("ios-mac-01", "macos"),                       # no xcode-16 label
                        runner("ios-mac-02", "macos", labels=("xcode-16",)))
        e = job(select(pol, ["iOS"], inv), "iOS")
        assert e["selectedTarget"] == "ios-mac-02"
        assert "missing=xcode-16" in e["candidates"][0]["reasons"]
        assert e["runsOn"] == ["self-hosted", "macOS", "ios-mac-02", "xcode-16"]

    def test_unity_version_mismatch_is_skipped(self):
        runners = {"ios-old": {"labels": ["self-hosted", "macOS", "ios-old"], "unity": ["2022.3.1f1"]},
                   "ios-new": {"labels": ["self-hosted", "macOS", "ios-new"], "unity": [UNITY]}}
        pol = rs.parse_policy({"runners": runners, "platforms": {"iOS": {
            "priority": ["ios-old", "ios-new"], "require": {"unity": "project"}}}}, "test")
        inv = inventory(runner("ios-old", "macos"), runner("ios-new", "macos"))
        assert job(select(pol, ["iOS"], inv), "iOS")["selectedTarget"] == "ios-new"

    def test_xcode_requirement_needs_a_declared_xcode(self):
        runners = {"m": {"labels": ["self-hosted", "macOS", "m"]}}
        pol = rs.parse_policy({"runners": runners, "platforms": {"iOS": {
            "priority": ["m"], "require": {"xcode": "16"}}}}, "test")
        with pytest.raises(SchedulingError, match="install Xcode 16 on m"):
            select(pol, ["iOS"], inventory(runner("m", "macos")))

    def test_runner_os_reported_by_the_api_overrides_the_label_guess(self):
        """A runner whose labels say macOS but which the API reports as Linux is Linux."""
        pol = policy(platforms={"iOS": {"priority": ["ios-mac-01", "ios-mac-02"]}})
        liar = InventoryRunner(name="ios-mac-01", os="linux", online=True, busy=False,
                               labels=("self-hosted", "macOS", "ios-mac-01"))
        inv = inventory(liar, runner("ios-mac-02", "macos"))
        e = job(select(pol, ["iOS"], inv), "iOS")
        assert e["selectedTarget"] == "ios-mac-02"
        assert "os=linux not allowed for iOS" in e["candidates"][0]["reasons"][0]


# ---------------------------------------------------------------------------
# Pools — capability filtering before availability
# ---------------------------------------------------------------------------

POOL_DOC = {
    "runners": {},
    "pools": {"ios-pool": {"labels": ["self-hosted", "macOS", "ios"], "os": "macos"}},
    "platforms": {"iOS": {"priority": ["ios-pool"], "require": {"labels": ["xcode-16"]}}},
}


class TestPools:
    def test_pool_state_comes_from_eligible_members_only(self):
        """A online but missing xcode-16; B has it but is busy → the pool is busy."""
        a = runner("mac-a", "macos", labels=("ios",))
        b = runner("mac-b", "macos", busy=True, labels=("ios", "xcode-16"))
        e = job(select(rs.parse_policy(POOL_DOC, "t"), ["iOS"], inventory(a, b)), "iOS")
        assert e["availability"] == "busy", e
        assert e["selectedTarget"] == "ios-pool/mac-b"
        members = {m["name"]: m for m in e["candidates"][0]["members"]}
        assert members["mac-a"]["reasons"] == ["missing=xcode-16"]

    def test_runs_on_stays_elastic_when_every_matching_runner_is_eligible(self):
        a = runner("mac-a", "macos", labels=("ios", "xcode-16"))
        b = runner("mac-b", "macos", labels=("ios", "xcode-16"))
        e = job(select(rs.parse_policy(POOL_DOC, "t"), ["iOS"], inventory(a, b)), "iOS")
        assert e["runsOn"] == ["self-hosted", "macOS", "ios", "xcode-16"]

    def test_pins_a_member_when_the_pool_labels_also_catch_an_ineligible_runner(self):
        """Required labels keep mac-a out already; a non-label capability needs the pin.

        The pool declares the project's Unity for its members; mac-a is also
        declared individually, with an older editor, and that wins for mac-a.
        """
        doc = json.loads(json.dumps(POOL_DOC))
        doc["pools"]["ios-pool"]["unity"] = [UNITY]
        doc["runners"] = {"mac-a": {"labels": ["self-hosted", "macOS", "mac-a"], "unity": ["2022.3.1f1"]}}
        doc["platforms"]["iOS"]["require"] = {"unity": "project"}
        a = runner("mac-a", "macos", labels=("ios",))
        b = runner("mac-b", "macos", labels=("ios",))
        e = job(select(rs.parse_policy(doc, "t"), ["iOS"], inventory(a, b)), "iOS")
        assert e["selectedTarget"] == "ios-pool/mac-b"
        assert e["runsOn"] == ["self-hosted", "macOS", "ios", "mac-b"]

    def test_pool_with_no_eligible_member_is_reported(self):
        a = runner("mac-a", "macos", labels=("ios",))
        with pytest.raises(SchedulingError) as err:
            select(rs.parse_policy(POOL_DOC, "t"), ["iOS"], inventory(a))
        assert "no member satisfies the requirements" in str(err.value)
        assert "mac-a: missing=xcode-16" in str(err.value)

    def test_empty_pool_is_an_actionable_failure(self):
        with pytest.raises(SchedulingError) as err:
            select(rs.parse_policy(POOL_DOC, "t"), ["iOS"], inventory())
        text = str(err.value)
        assert "no runner in the inventory carries labels self-hosted,macOS,ios" in text
        assert "Suggested actions:" in text

    def test_group_membership_is_intersected(self):
        doc = json.loads(json.dumps(POOL_DOC))
        doc["pools"]["ios-pool"]["group"] = "ios-runners"
        a = runner("mac-a", "macos", labels=("ios", "xcode-16"))
        b = runner("mac-b", "macos", labels=("ios", "xcode-16"))
        e = job(select(rs.parse_policy(doc, "t"), ["iOS"],
                       inventory(a, b, groups={"ios-runners": ["mac-b"]})), "iOS")
        assert e["selectedTarget"] == "ios-pool/mac-b"
        assert e["runsOn"] == {"group": "ios-runners", "labels": ["self-hosted", "macOS", "ios", "xcode-16"]}

    def test_capability_pool_without_inventory_targets_the_labels(self):
        """Mode C: GitHub picks any runner carrying every label."""
        e = job(select(rs.parse_policy(POOL_DOC, "t"), ["iOS"], UNAVAILABLE), "iOS")
        assert e["availability"] == "unknown"
        assert e["runsOn"] == ["self-hosted", "macOS", "ios", "xcode-16"]


# ---------------------------------------------------------------------------
# Platform isolation — policy can never weaken it
# ---------------------------------------------------------------------------

class TestPlatformIsolation:
    def test_ios_with_docker_is_rejected(self):
        with pytest.raises(PolicyError, match="no iOS path"):
            select(policy(platforms={"iOS": {"build-engine": "docker", "priority": ["ios-mac-01"]}}),
                   ["iOS"], ios_inv())

    def test_ios_on_a_linux_runner_is_rejected(self):
        with pytest.raises(PolicyError, match="can never run iOS"):
            select(policy(platforms={"iOS": {"priority": ["linux-01"]}}), ["iOS"],
                   inventory(runner("linux-01")))

    def test_ios_to_github_hosted_ubuntu_is_rejected(self):
        with pytest.raises(PolicyError, match="can never run iOS"):
            select(policy(platforms={"iOS": {"priority": ["ios-mac-01"], "fallback": ["github-hosted"]}}),
                   ["iOS"], ios_inv())

    def test_windows64_docker_on_native_windows_is_rejected(self):
        runners = {"win-01": {"labels": ["self-hosted", "windows", "win-01"]}}
        with pytest.raises(PolicyError, match="can never run Windows64"):
            select(rs.parse_policy({"runners": runners, "platforms": {"Windows64": {
                "build-engine": "docker", "priority": ["win-01"]}}}, "t"),
                ["Windows64"], inventory(runner("win-01", "windows")))

    def test_android_docker_on_windows_linux_containers_is_allowed(self):
        runners = {"win-01": {"labels": ["self-hosted", "windows", "win-01"]}}
        pol = rs.parse_policy({"runners": runners, "platforms": {"Android": {
            "build-engine": "docker", "priority": ["win-01"]}}}, "t")
        assert job(select(pol, ["Android"], inventory(runner("win-01", "windows"))),
                   "Android")["selectedTarget"] == "win-01"

    def test_local_engine_on_linux_is_rejected(self):
        """There is no Linux + local build step in the pipeline."""
        with pytest.raises(PolicyError, match="can never run Android"):
            select(policy(platforms={"Android": {"build-engine": "local", "priority": ["linux-01"]}}),
                   ["Android"], inventory(runner("linux-01")))

    def test_inherited_default_drops_incompatible_targets_instead_of_rerouting(self):
        """A generic default is not an error for iOS -- but it never sends iOS to Linux."""
        pol = policy(default={"priority": ["linux-01", "ios-mac-01"]})
        e = job(select(pol, ["iOS"], inventory(runner("linux-01"), runner("ios-mac-01", "macos"))), "iOS")
        assert e["selectedTarget"] == "ios-mac-01"
        dropped = e["candidates"][0]
        assert dropped["id"] == "linux-01" and not dropped["eligible"]

    def test_an_explicit_ios_section_with_nothing_compatible_still_fails(self):
        """Only an INHERITED default falls back to legacy (I-2); a section written
        for iOS that names nothing usable is a configuration error."""
        pol = policy(default={"priority": ["linux-01"]}, platforms={"iOS": {"require": {"xcode": "16"}}})
        with pytest.raises(SchedulingError, match="No eligible runner found for iOS"):
            select(pol, ["iOS"], inventory(runner("linux-01")))

    def test_os_contradicting_labels_is_a_policy_error(self):
        with pytest.raises(PolicyError, match="contradicts"):
            rs.parse_policy({"runners": {"x": {"labels": ["self-hosted", "linux", "x"], "os": "macos"}}}, "t")

    def test_tests_and_addressables_cannot_change_engine(self):
        with pytest.raises(PolicyError, match="always uses the run's build engine"):
            select(policy(platforms={"UnityTests": {"build-engine": "local", "priority": ["ios-mac-01"]}}),
                   ["UnityTests"], ios_inv())


# ---------------------------------------------------------------------------
# Matrix — every job resolves independently
# ---------------------------------------------------------------------------

def test_matrix_jobs_resolve_to_their_own_pools():
    doc = {
        "runners": {**IOS_RUNNERS},
        "pools": {"android-pool": {"labels": ["self-hosted", "linux", "android"]},
                  "webgl-pool": {"labels": ["self-hosted", "linux", "webgl"]}},
        "platforms": {"Android": {"priority": ["android-pool"]},
                      "WebGL": {"priority": ["webgl-pool"]},
                      "iOS": {"priority": ["ios-mac-01", "ios-mac-02"]}},
    }
    inv = inventory(runner("droid-1", labels=("android",)), runner("web-1", labels=("webgl",)),
                    runner("ios-mac-01", "macos", busy=True), runner("ios-mac-02", "macos"))
    provider = FakeProvider(inv)
    sel = select(rs.parse_policy(doc, "t"), ["Android", "iOS", "WebGL"], provider=provider)
    assert job(sel, "Android")["selectedTarget"] == "android-pool/droid-1"
    assert job(sel, "WebGL")["selectedTarget"] == "webgl-pool/web-1"
    assert job(sel, "iOS")["selectedTarget"] == "ios-mac-02"
    assert job(sel, "iOS")["buildEngine"] == "local"
    assert job(sel, "Android")["buildEngine"] == "docker"
    assert provider.calls == 1, "one inventory sweep per run, shared by every job"


def test_a_platform_without_a_section_or_default_stays_legacy():
    sel = select(policy(platforms={"iOS": IOS_PLATFORM}), ["Android", "iOS"], ios_inv())
    assert job(sel, "Android")["decidedBy"] == "legacy"
    assert job(sel, "Android")["runsOn"] == ["ubuntu-latest"]
    assert job(sel, "iOS")["decidedBy"] == "platform-policy"


def test_default_policy_covers_platforms_without_a_section():
    sel = select(policy(default={"priority": ["linux-01"]}), ["Android", "WebGL"],
                 inventory(runner("linux-01")))
    for name in ("Android", "WebGL"):
        assert job(sel, name)["decidedBy"] == "default-policy"
        assert job(sel, name)["selectedTarget"] == "linux-01"


def test_platform_priority_does_not_inherit_default_fallback():
    pol = policy(default={"priority": ["linux-01"], "fallback": ["github-hosted"]},
                 platforms={"Android": {"priority": ["linux-02"]}})
    with pytest.raises(SchedulingError):
        select(pol, ["Android"], inventory(runner("linux-02", online=False)))


# ---------------------------------------------------------------------------
# Inventory unavailable
# ---------------------------------------------------------------------------

class TestInventoryUnavailable:
    def test_first_eligible_self_hosted_with_unknown_status(self):
        pol = policy(platforms={"Android": {"priority": ["linux-01"], "fallback": ["github-hosted"]}})
        e = job(select(pol, ["Android"], UNAVAILABLE), "Android")
        assert e["selectedTarget"] == "linux-01"
        assert e["availability"] == "unknown"
        assert any("availability unknown" in w for w in e["warnings"])

    def test_does_not_fall_back_to_github_hosted_without_proof(self):
        pol = policy(platforms={"Android": {"priority": ["linux-01"], "fallback": ["github-hosted"]}})
        assert job(select(pol, ["Android"], UNAVAILABLE), "Android")["targetType"] == "self-hosted"

    def test_fail_policy_fails(self):
        pol = policy(availability={"on-unavailable": "fail"},
                     platforms={"Android": {"priority": ["linux-01"]}})
        with pytest.raises(SchedulingError, match="availability is unknown"):
            select(pol, ["Android"], UNAVAILABLE)

    def test_static_capability_still_applies(self):
        runners = {"m": {"labels": ["self-hosted", "macOS", "m"], "xcode": ["15"]},
                   "n": {"labels": ["self-hosted", "macOS", "n"], "xcode": ["16"]}}
        pol = rs.parse_policy({"runners": runners, "platforms": {"iOS": {
            "priority": ["m", "n"], "require": {"xcode": "16"}}}}, "t")
        assert job(select(pol, ["iOS"], UNAVAILABLE), "iOS")["selectedTarget"] == "n"


# ---------------------------------------------------------------------------
# Failure messages
# ---------------------------------------------------------------------------

def test_failure_message_is_actionable():
    pol = policy(mode="self-hosted-only", platforms={"iOS": {
        "priority": ["ios-mac-01", "ios-mac-02", "ios-mac-03"], "require": {"labels": ["xcode-16"]}}})
    inv = inventory(runner("ios-mac-01", "macos", online=False, labels=("xcode-16",)),
                    runner("ios-mac-02", "macos", busy=True, online=False, labels=("xcode-16",)),
                    runner("ios-mac-03", "macos"))
    with pytest.raises(SchedulingError) as err:
        select(pol, ["iOS"], inv)
    text = str(err.value)
    for expected in ("No eligible runner found for iOS.", "Required capabilities:", "os=macos",
                     "platform=iOS", "labels=xcode-16", "ios-mac-01", "status=offline",
                     "missing=xcode-16", "Suggested actions:", "bring ios-mac-01 online",
                     "add label(s) xcode-16 to ios-mac-03"):
        assert expected in text, f"{expected!r} missing from:\n{text}"


# ---------------------------------------------------------------------------
# Overrides and legacy passthrough
# ---------------------------------------------------------------------------

class TestOverrides:
    def test_no_policy_is_a_pure_passthrough(self):
        sel = select(None, ["Android", "iOS"], provider=ExplodingProvider())
        assert job(sel, "Android")["runsOn"] == ["ubuntu-latest"]
        assert job(sel, "iOS")["runsOn"] == ["macos-latest"]
        assert job(sel, "iOS")["availability"] == "not-checked"
        assert sel["summary"]["usesDocker"] is True

    def test_dispatch_labels_bypass_the_policy(self):
        legacy = LegacyConfig(labels_by_job={"Android": ("self-hosted", "box")}, build_engine="docker",
                              activation="auto", activation_by_engine={}, labels_source="dispatch")
        sel = select(policy(default={"priority": ["linux-01"]}), ["Android"],
                     provider=ExplodingProvider(), legacy=legacy, dispatch_labels=True)
        assert job(sel, "Android")["runsOn"] == ["self-hosted", "box"]
        assert job(sel, "Android")["decidedBy"] == "dispatch-labels"

    def test_runner_policy_legacy_bypasses_the_policy(self):
        sel = select(policy(default={"priority": ["linux-01"]}), ["Android"],
                     provider=ExplodingProvider(), policy_mode="legacy")
        assert job(sel, "Android")["decidedBy"] == "policy-bypassed"
        assert job(sel, "Android")["runsOn"] == ["ubuntu-latest"]

    def test_explicit_standalone_label_wins(self):
        sel = rs.build_selection(["iOS"], [], "standalone-native",
                                 LegacyConfig({"iOS": ("macos-unity-xcode",)}, "local", "none", {}),
                                 policy(platforms={"iOS": IOS_PLATFORM}), ExplodingProvider(),
                                 explicit_labels=("my-mac",))
        assert job(sel, "iOS")["runsOn"] == ["my-mac"]

    def test_activation_follows_the_job_engine(self):
        """A local iOS row in a docker run must not ask for docker activation."""
        sel = select(policy(platforms={"iOS": IOS_PLATFORM}), ["Android", "iOS"], ios_inv())
        assert job(sel, "iOS")["activationStrategy"] == "none"
        assert job(sel, "Android")["activationStrategy"] == "auto"


# ---------------------------------------------------------------------------
# Policy validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("doc,match", [
    ({"version": 2}, "not supported"),
    ({"surprise": 1}, "unknown key"),
    ({"mode": "fastest"}, "policy.mode"),
    ({"platforms": {"Atari": {}}}, "unknown platform"),
    ({"default": {"priority": ["ghost"]}}, "neither a runner nor a pool"),
    ({"runners": {"r": {"labels": []}}}, "at least one label"),
    ({"runners": {"r": {"labels": ["self-hosted", "gpu"]}}}, "cannot tell which OS"),
    ({"availability": {"timeout-seconds": 600}}, "timeout-seconds"),
    ({"runners": {"github-hosted": {"labels": ["linux"]}}}, "reserved"),
])
def test_invalid_policies_fail_clearly(doc, match):
    with pytest.raises(PolicyError, match=match):
        rs.parse_policy(doc, "t")


def test_inline_variable_wins_over_the_file(tmp_path):
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"mode": "github-hosted"}), encoding="utf-8")
    assert rs.load_policy(json.dumps({"mode": "self-hosted-only"}), str(f)).mode == "self-hosted-only"
    assert rs.load_policy("", str(f)).mode == "github-hosted"
    assert rs.load_policy("", str(tmp_path / "absent.json")) is None


def test_broken_policy_json_is_an_error_not_a_silent_legacy_run(tmp_path):
    f = tmp_path / "p.json"
    f.write_text("{not json", encoding="utf-8")
    with pytest.raises(PolicyError, match="not valid JSON"):
        rs.load_policy("", str(f))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_writes_the_selection_and_a_summary(tmp_path, monkeypatch):
    pol = tmp_path / "policy.json"
    pol.write_text(json.dumps({"runners": IOS_RUNNERS, "platforms": {"iOS": IOS_PLATFORM}}), encoding="utf-8")
    snap = tmp_path / "inv.json"
    snap.write_text(json.dumps({"runners": [
        {"name": "ios-mac-01", "os": "macOS", "status": "offline", "busy": False,
         "labels": [{"name": "self-hosted"}, {"name": "macOS"}, {"name": "ios-mac-01"}]},
        {"name": "ios-mac-02", "os": "macOS", "status": "online", "busy": False,
         "labels": ["self-hosted", "macOS", "ios-mac-02"]}]}), encoding="utf-8")
    out, summary = tmp_path / "out", tmp_path / "summary"
    monkeypatch.delenv("RUNNER_POLICY", raising=False)
    rc = rs.main(["--jobs", "Android,iOS", "--inactive-jobs", "UnityTests,Addressables",
                  "--legacy-labels-by-platform", '{"Android":["ubuntu-latest"],"iOS":["macos-latest"]}',
                  "--legacy-labels-linux", '["ubuntu-latest"]', "--legacy-build-engine", "docker",
                  "--legacy-activation", "auto", "--policy-file", str(pol), "--inventory-file", str(snap),
                  "--github-output", str(out), "--step-summary", str(summary)])
    assert rc == 0
    outputs = dict(line.split("=", 1) for line in out.read_text(encoding="utf-8").splitlines())
    sel = json.loads(outputs["runner-selection"])
    assert sel["jobs"]["iOS"]["selectedTarget"] == "ios-mac-02"
    assert sel["jobs"]["UnityTests"]["decidedBy"] == "not-requested"
    # Serializations of the same document, for `if:` / `with:` consumers.
    assert outputs["uses-docker"] == "true"
    assert outputs["runs-on-unitytests"] == '["ubuntu-latest"]'
    assert outputs["runs-on-addressables"] == '["ubuntu-latest"]'
    assert "### Runner selection" in summary.read_text(encoding="utf-8")


def test_cli_failure_exits_nonzero_with_the_report(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RUNNER_POLICY", json.dumps(
        {"mode": "self-hosted-only", "runners": IOS_RUNNERS, "platforms": {"iOS": IOS_PLATFORM}}))
    snap = tmp_path / "inv.json"
    snap.write_text(json.dumps({"runners": []}), encoding="utf-8")
    rc = rs.main(["--jobs", "iOS", "--inventory-file", str(snap), "--github-output", "",
                  "--step-summary", ""])
    assert rc == 1
    err = capsys.readouterr().err
    assert "::error title=No eligible runner::No eligible runner found for iOS." in err
    assert "Suggested actions:" in err


# ===========================================================================
# Audit regressions (B-1, I-2, I-3, I-4)
# ===========================================================================

def _labels_of(runs_on):
    return runs_on["labels"] if isinstance(runs_on, dict) else runs_on


class TestRunnerGroupMembershipIsNeverGuessed:
    """B-1: group membership comes only from the group endpoints, never from labels."""

    GROUP_POOL = {"runners": {}, "pools": {"gp": {"labels": ["self-hosted", "macOS"], "group": "ios-grp"}},
                  "platforms": {"iOS": {"priority": ["gp"]}}}
    # The exact audit reproduction: an ineligible runner (`bad`) makes the
    # scheduler want to pin, and `outsider` carries the pool labels but is not
    # a confirmed member of the group.
    AUDIT_CASE = {"runners": {"bad": {"labels": ["self-hosted", "macOS", "bad"], "unity": ["2022.3.1f1"]}},
                  "pools": {"gp": {"labels": ["self-hosted", "macOS"], "group": "ios-grp", "unity": [UNITY]}},
                  "platforms": {"iOS": {"priority": ["gp"], "require": {"unity": "project"}}}}

    def test_audit_case_never_pins_an_unconfirmed_outsider(self):
        e = job(select(rs.parse_policy(self.AUDIT_CASE, "t"), ["iOS"],
                       inventory(runner("bad", "macos"), runner("outsider", "macos"))), "iOS")
        assert "outsider" not in _labels_of(e["runsOn"])
        assert e["runsOn"] == {"group": "ios-grp", "labels": ["self-hosted", "macOS"]}
        assert e["selectedTarget"] == "gp", "no member is claimed"
        assert e["availability"] == "unknown"
        assert e["candidates"][0]["groupMembership"] == "unknown"
        assert "members" not in e["candidates"][0]

    def test_outsider_with_matching_labels_is_not_a_member(self):
        """Group confirmed: the outsider carries the labels but is not listed."""
        inv = inventory(runner("outsider", "macos"), runner("member", "macos", busy=True),
                        groups={"ios-grp": ["member"]})
        e = job(select(rs.parse_policy(self.GROUP_POOL, "t"), ["iOS"], inv), "iOS")
        assert e["selectedTarget"] == "gp/member"
        assert e["availability"] == "busy", "the idle outsider must not make the pool idle"
        assert [m["name"] for m in e["candidates"][0]["members"]] == ["member"]

    def test_confirmed_member_selected(self):
        inv = inventory(runner("member", "macos"), groups={"ios-grp": ["member"]})
        e = job(select(rs.parse_policy(self.GROUP_POOL, "t"), ["iOS"], inv), "iOS")
        assert (e["selectedTarget"], e["availability"]) == ("gp/member", "idle")
        assert e["runsOn"] == {"group": "ios-grp", "labels": ["self-hosted", "macOS"]}

    def test_pin_inside_a_confirmed_group_ignores_runners_outside_it(self):
        """Outside runners cannot take a group job, so they never force a pin."""
        inv = inventory(runner("bad", "macos"), runner("member", "macos"), groups={"ios-grp": ["member"]})
        e = job(select(rs.parse_policy(self.AUDIT_CASE, "t"), ["iOS"], inv), "iOS")
        assert e["runsOn"] == {"group": "ios-grp", "labels": ["self-hosted", "macOS"]}

    def test_unique_label_cannot_bypass_membership(self):
        """A runner target in a group whose membership is unknown is never 'idle'."""
        doc = {"runners": {"mac-1": {"labels": ["self-hosted", "macOS", "mac-1"], "group": "ios-grp"}},
               "platforms": {"iOS": {"priority": ["mac-1"]}}}
        e = job(select(rs.parse_policy(doc, "t"), ["iOS"], inventory(runner("mac-1", "macos"))), "iOS")
        assert e["availability"] == "unknown"
        assert e["runsOn"] == {"group": "ios-grp", "labels": ["self-hosted", "macOS", "mac-1"]}

    def test_runner_target_not_in_its_confirmed_group_is_ineligible(self):
        doc = {"runners": {"mac-1": {"labels": ["self-hosted", "macOS", "mac-1"], "group": "ios-grp"}},
               "platforms": {"iOS": {"priority": ["mac-1"]}}}
        with pytest.raises(SchedulingError, match="not a member of runner group 'ios-grp'"):
            select(rs.parse_policy(doc, "t"), ["iOS"],
                   inventory(runner("mac-1", "macos"), groups={"ios-grp": ["someone-else"]}))

    def test_group_that_does_not_exist_is_an_actionable_failure(self):
        inv = inventory(runner("m", "macos"))
        inv.groups_not_found = frozenset({"ios-grp"})
        with pytest.raises(SchedulingError) as err:
            select(rs.parse_policy(self.GROUP_POOL, "t"), ["iOS"], inv)
        assert "runner group 'ios-grp' does not exist, or is not visible" in str(err.value)
        assert "Self-hosted runners: read" in str(err.value)

    def test_api_unavailable_group_pool(self):
        e = job(select(rs.parse_policy(self.GROUP_POOL, "t"), ["iOS"], UNAVAILABLE), "iOS")
        assert e["availability"] == "unknown"
        assert e["runsOn"] == {"group": "ios-grp", "labels": ["self-hosted", "macOS"]}

    def test_unknown_membership_with_on_unavailable_fail(self):
        doc = json.loads(json.dumps(self.GROUP_POOL))
        doc["availability"] = {"on-unavailable": "fail"}
        with pytest.raises(SchedulingError, match="on-unavailable=fail"):
            select(rs.parse_policy(doc, "t"), ["iOS"], inventory(runner("m", "macos")))

    def test_membership_note_is_in_the_log(self):
        sel = select(rs.parse_policy(self.GROUP_POOL, "t"), ["iOS"], inventory(runner("m", "macos")))
        assert "membership of runner group 'ios-grp' could not be confirmed" in rs.render_log(sel)


@pytest.mark.parametrize("on_busy,states,expected", [
    # `unknown` ranks right after idle in the same list; GitHub-hosted ranks after
    # every idle-or-unknown self-hosted fallback target.
    ("wait", dict(p1="unknown", p2="busy", f1="idle"), "p1"),
    ("wait", dict(p1="busy", p2="offline", f1="unknown"), "p1"),
    ("wait", dict(p1="offline", p2="offline", f1="unknown", gh=True), "f1"),
    ("next", dict(p1="busy", p2="unknown", f1="idle"), "p2"),
    ("next", dict(p1="busy", p2="offline", f1="unknown", gh=True), "f1"),
    ("next", dict(p1="busy", p2="offline", f1="offline", gh=True), "github-hosted"),
    ("wait", dict(p1="busy", p2="offline", f1="offline", gh=True), "p1"),
])
def test_unknown_availability_tier_order(on_busy, states, expected):
    states = dict(states)
    gh = states.pop("gh", False)
    unknown = {n for n, s in states.items() if s == "unknown"}
    # A runner in a group whose membership is not visible is the "unknown" case
    # with an otherwise readable inventory.
    runners = {n: {"labels": ["self-hosted", "linux", n], **({"group": "g-" + n} if n in unknown else {})}
               for n in states}
    fallback = ["f1"] + (["github-hosted"] if gh else [])
    pol = rs.parse_policy({"runners": runners, "platforms": {"Android": {
        "priority": ["p1", "p2"], "fallback": fallback, "on-busy": on_busy}}}, "test")
    inv = inventory(*[runner(n, online=s != "offline", busy=s == "busy") for n, s in states.items()])
    assert job(select(pol, ["Android"], inv), "Android")["selectedTarget"] == expected


def test_api_down_never_prefers_github_over_an_eligible_self_hosted_fallback():
    for on_busy in ("wait", "next"):
        pol = rs.parse_policy({"runners": {"f1": {"labels": ["self-hosted", "linux", "f1"]},
                                           "p1": {"labels": ["self-hosted", "linux", "p1"],
                                                  "build-engines": ["local"]}},
                               "platforms": {"Android": {"priority": ["p1"],
                                                         "fallback": ["github-hosted", "f1"],
                                                         "on-busy": on_busy}}}, "t")
        e = job(select(pol, ["Android"], UNAVAILABLE), "Android")
        assert e["selectedTarget"] == "f1", on_busy
        assert any("not a reservation" in w for w in e["warnings"])


class TestDefaultPolicyVersusLegacyFallback:
    """I-2: platform -> default -> legacy, without ever routing a job unsafely."""

    LINUX_DEFAULT = {"runners": {"lin1": {"labels": ["self-hosted", "linux", "lin1"]}},
                     "default": {"priority": ["lin1"], "fallback": ["github-hosted"]}}

    def test_linux_default_and_ios_falls_back_to_legacy_in_the_pipeline(self):
        sel = select(rs.parse_policy(self.LINUX_DEFAULT, "t"), ["Android", "iOS"], inventory(runner("lin1")))
        assert job(sel, "Android")["selectedTarget"] == "lin1"
        ios = job(sel, "iOS")
        assert ios["decidedBy"] == "legacy" and ios["runsOn"] == ["macos-latest"]
        assert "policy.default has no target that can run iOS safely" in ios["selectionReason"]
        assert ios["candidates"] and all(not c["eligible"] for c in ios["candidates"])

    def test_linux_default_and_ios_standalone_keeps_macos_unity_xcode(self):
        sel = rs.build_selection(["iOS"], [], "standalone-native",
                                 LegacyConfig({"iOS": ("macos-unity-xcode",)}, "local", "none", {}),
                                 rs.parse_policy(self.LINUX_DEFAULT, "t"),
                                 FakeProvider(inventory(runner("lin1"))), unity_version=UNITY)
        assert job(sel, "iOS")["runsOn"] == ["macos-unity-xcode"]

    def test_compatible_mac_in_default_is_used_for_ios(self):
        doc = json.loads(json.dumps(self.LINUX_DEFAULT))
        doc["runners"]["mac1"] = {"labels": ["self-hosted", "macOS", "mac1"]}
        doc["default"]["priority"] = ["lin1", "mac1"]
        sel = select(rs.parse_policy(doc, "t"), ["iOS"], inventory(runner("lin1"), runner("mac1", "macos")))
        assert job(sel, "iOS")["selectedTarget"] == "mac1"
        assert job(sel, "iOS")["buildEngine"] == "local"

    @pytest.mark.parametrize("legacy_labels", [("self-hosted", "linux"), ("windows-latest",), ("ubuntu-latest",)])
    def test_unsafe_legacy_answer_is_refused_not_used(self, legacy_labels):
        legacy = LegacyConfig({"iOS": legacy_labels}, "docker", "auto", {"docker": "auto", "local": "none"})
        with pytest.raises(SchedulingError, match="No safe runner for iOS"):
            select(rs.parse_policy(self.LINUX_DEFAULT, "t"), ["iOS"], inventory(runner("lin1")), legacy=legacy)

    def test_inherited_github_hosted_mode_does_not_break_ios(self):
        sel = select(rs.parse_policy({"mode": "github-hosted", "default": {}}, "t"), ["WebGL", "iOS"],
                     provider=ExplodingProvider())
        assert job(sel, "WebGL")["targetType"] == "github-hosted"
        assert job(sel, "iOS")["decidedBy"] == "legacy"
        assert job(sel, "iOS")["runsOn"] == ["macos-latest"]

    def test_inherited_docker_engine_does_not_apply_to_ios(self):
        doc = {"runners": {"mac1": {"labels": ["self-hosted", "macOS", "mac1"]}},
               "default": {"build-engine": "docker", "priority": ["mac1"]}}
        assert job(select(rs.parse_policy(doc, "t"), ["iOS"], inventory(runner("mac1", "macos"))),
                   "iOS")["buildEngine"] == "local"


class TestVersionCapabilities:
    """I-3: one model for Unity and Xcode -- declared lists match exactly;
    undeclared is unknown, and unknown never satisfies a requirement."""

    @staticmethod
    def _pol(field, declared, required):
        caps = {} if declared is None else {field: declared}
        return rs.parse_policy({"runners": {"m": {"labels": ["self-hosted", "macOS", "m"], **caps}},
                                "platforms": {"iOS": {"priority": ["m"], "require": {field: required}}}}, "t")

    @pytest.mark.parametrize("field,declared,required,ok", [
        ("unity", [UNITY], "project", True),
        ("unity", ["6000.3.9f1"], "project", False),     # never a prefix or minor match
        ("unity", None, "project", False),               # undeclared = unknown
        ("unity", [], "project", False),                 # declared empty = supports none
        ("unity", ["6000.0.26f1"], "6000.0.26f1", True),
        ("xcode", ["16"], "16", True),
        ("xcode", ["15"], "16", False),
        ("xcode", ["16.2"], "16", False),
        ("xcode", None, "16", False),
        ("xcode", [], "16", False),
    ])
    def test_matching(self, field, declared, required, ok):
        pol = self._pol(field, declared, required)
        if ok:
            assert job(select(pol, ["iOS"], inventory(runner("m", "macos"))), "iOS")["selectedTarget"] == "m"
        else:
            with pytest.raises(SchedulingError):
                select(pol, ["iOS"], inventory(runner("m", "macos")))

    def test_no_requirement_means_not_checked(self):
        pol = rs.parse_policy({"runners": {"m": {"labels": ["self-hosted", "macOS", "m"],
                                                 "unity": ["2021.3.1f1"]}},
                               "platforms": {"iOS": {"priority": ["m"]}}}, "t")
        assert job(select(pol, ["iOS"], inventory(runner("m", "macos"))), "iOS")["selectedTarget"] == "m"

    def test_project_requirement_with_unknown_project_version_is_an_error(self):
        pol = self._pol("unity", [UNITY], "project")
        with pytest.raises(PolicyError, match="Unity version is unknown"):
            rs.build_selection(["iOS"], [], "pipeline", LEGACY, pol, FakeProvider(inventory()), unity_version="")

    @pytest.mark.parametrize("required,ok", [("project", True), (UNITY, True), ("6000.3.9f1", False)])
    def test_github_hosted_builds_only_the_project_version(self, required, ok):
        pol = rs.parse_policy({"mode": "github-hosted",
                               "platforms": {"Android": {"require": {"unity": required}}}}, "t")
        if ok:
            assert job(select(pol, ["Android"], provider=ExplodingProvider()),
                       "Android")["targetType"] == "github-hosted"
        else:
            with pytest.raises(PolicyError, match="builds with the project's Unity"):
                select(pol, ["Android"], provider=ExplodingProvider())

    def test_github_hosted_never_satisfies_xcode(self):
        pol = rs.parse_policy({"runners": {"l": {"labels": ["self-hosted", "linux", "l"]}},
                               "platforms": {"Android": {"priority": ["l"], "fallback": ["github-hosted"],
                                                         "require": {"xcode": "16"}}}}, "t")
        with pytest.raises(PolicyError, match="no Xcode"):
            select(pol, ["Android"], inventory(runner("l")))


DUP_RUNNER = '{"runners":{"r":{"labels":["self-hosted","linux","a"]},"r":{"labels":["self-hosted","linux","b"]}}}'


class TestDuplicateIds:
    """I-4: duplicates are rejected, never resolved by 'last one wins'."""

    @pytest.mark.parametrize("text,match", [
        (DUP_RUNNER, "policy.runners: duplicate runner id 'r'"),
        ('{"pools":{"p":{"labels":["self-hosted","linux"]},"p":{"labels":["self-hosted","macOS"]}}}',
         "policy.pools: duplicate pool id 'p'"),
        ('{"pools":{"p":{"labels":["self-hosted","macOS"],"group":"g"},'
         '"p":{"labels":["self-hosted","macOS"],"group":"h"}}}',
         "policy.pools: duplicate pool id 'p'"),
        ('{"platforms":{"iOS":{},"iOS":{}}}', "policy.platforms: duplicate platform section 'iOS'"),
        ('{"runners":{"r":{"labels":["self-hosted","linux","r"],"os":"linux","os":"macos"}}}',
         "policy.runners.r: duplicate key 'os'"),
        ('{"mode":"github-hosted","mode":"self-hosted-only"}', "policy: duplicate key 'mode'"),
    ])
    def test_duplicate_keys_in_json(self, text, match):
        with pytest.raises(PolicyError, match=match):
            rs.parse_policy_text(text, "test")

    @pytest.mark.parametrize("section,match", [
        ({"priority": ["a", "a"]}, "duplicate target id 'a'"),
        ({"priority": ["a"], "fallback": ["a"]}, "duplicate target id 'a'.*already listed in .*priority"),
    ])
    def test_duplicate_targets(self, section, match):
        doc = {"runners": {"a": {"labels": ["self-hosted", "linux", "a"]}}, "platforms": {"Android": section}}
        with pytest.raises(PolicyError, match=match):
            rs.parse_policy(doc, "t")

    def test_duplicate_pool_members(self):
        with pytest.raises(PolicyError, match="policy.pools.p.members: duplicate runner id 'm'"):
            rs.parse_policy({"pools": {"p": {"labels": ["self-hosted", "linux"], "members": ["m", "m"]}}}, "t")

    def test_runner_and_pool_sharing_an_id(self):
        with pytest.raises(PolicyError, match="already used by a runner"):
            rs.parse_policy({"runners": {"x": {"labels": ["self-hosted", "linux", "x"]}},
                             "pools": {"x": {"labels": ["self-hosted", "linux"]}}}, "t")

    def test_duplicate_ids_via_the_variable_and_the_file(self, tmp_path):
        with pytest.raises(PolicyError, match="duplicate runner id 'r'"):
            rs.load_policy(DUP_RUNNER, "")
        f = tmp_path / "p.json"
        f.write_text(DUP_RUNNER, encoding="utf-8")
        with pytest.raises(PolicyError, match="duplicate runner id 'r'"):
            rs.load_policy("", str(f))

    def test_duplicate_labels_within_a_runner_are_normalised_not_errors(self):
        pol = rs.parse_policy({"runners": {"r": {"labels": ["self-hosted", "linux", "r", "Linux", "r"]}}}, "t")
        assert pol.runners["r"].labels == ("self-hosted", "linux", "r")
