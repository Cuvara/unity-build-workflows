"""The GitHub runner inventory provider: documented endpoints, bounded, never leaks.

Nothing here touches the network: `urlopen` is replaced by a fake that serves
canned GitHub responses (or failures) per URL, and time is a fake clock so the
budget can be tested without waiting.
"""
import io
import json
import socket
import sys
import urllib.error
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "common"))

import runner_inventory as ri  # noqa: E402
import runner_scheduler as rs  # noqa: E402

TOKEN = "ghp_SUPERSECRETTOKENVALUE0123456789"


def api_runner(name, os="Linux", status="online", busy=False, labels=("self-hosted", "linux")):
    return {"id": abs(hash(name)) % 10000, "name": name, "os": os, "status": status, "busy": busy,
            "labels": [{"id": i, "name": l, "type": "custom"} for i, l in enumerate(labels)]}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeApi:
    """routes: {url-substring: payload | Exception | [payload-or-exception, ...]}"""

    def __init__(self, routes, clock=None, cost=0.0):
        self.routes = routes
        self.calls = []
        self.headers = []
        self.clock = clock
        self.cost = cost

    def __call__(self, request, timeout):
        url = request.full_url
        self.calls.append(url)
        self.headers.append(dict(request.header_items()))
        if self.clock is not None:
            self.clock.now += self.cost
        for fragment, answer in self.routes.items():
            if fragment in url:
                if isinstance(answer, list):
                    answer = answer.pop(0) if len(answer) > 1 else answer[0]
                if isinstance(answer, Exception):
                    raise answer
                if callable(answer):
                    answer = answer(url)
                return FakeResponse(json.dumps(answer).encode())
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def provider(api, clock=None, repository="acme/game", budget=30):
    clock = clock or Clock()
    return ri.GitHubApiInventory(token=TOKEN, repository=repository, opener=api,
                                 budget_seconds=budget, clock=clock, sleep=clock.sleep)


def request(scope="repo", groups=(), timeout=10):
    return rs.InventoryRequest(groups=tuple(groups), scope=scope, timeout_seconds=timeout)


def http_error(code):
    return urllib.error.HTTPError("https://api.github.com/x", code, "err", {}, None)


# ---------------------------------------------------------------------------

def test_repo_runners_are_paginated():
    page1 = {"total_count": 150, "runners": [api_runner(f"r{i}") for i in range(100)]}
    page2 = {"total_count": 150, "runners": [api_runner(f"r{i}") for i in range(100, 150)]}

    def pages(url):
        return page2 if "page=2" in url else page1

    api = FakeApi({"/repos/acme/game/actions/runners": pages})
    inv = provider(api).discover(request())
    assert inv.status == "ok"
    assert len(inv.runners) == 150
    assert sum("page=" in c for c in api.calls) == 2


def test_runner_fields_are_normalized():
    api = FakeApi({"/repos/acme/game/actions/runners": {"total_count": 2, "runners": [
        api_runner("mac-1", os="macOS", busy=True, labels=("self-hosted", "macOS", "xcode-16")),
        api_runner("lin-1", status="offline")]}})
    inv = provider(api).discover(request())
    mac = inv.runners["mac-1"]
    assert (mac.os, mac.online, mac.busy) == ("macos", True, True)
    assert "xcode-16" in mac.labels
    assert inv.runners["lin-1"].online is False


def test_requests_use_documented_headers_and_bearer_auth():
    api = FakeApi({"/actions/runners": {"total_count": 0, "runners": []}})
    provider(api).discover(request())
    headers = {k.lower(): v for k, v in api.headers[0].items()}
    assert headers["authorization"] == f"Bearer {TOKEN}"
    assert headers["accept"] == "application/vnd.github+json"
    assert headers["x-github-api-version"] == "2022-11-28"
    assert TOKEN not in api.calls[0], "the token must never be in the URL"


def test_org_scope_and_runner_groups():
    api = FakeApi({
        "/orgs/acme/actions/runner-groups/7/runners": {"total_count": 1, "runners": [
            api_runner("mac-2", os="macOS", labels=("self-hosted", "macOS"))]},
        "/orgs/acme/actions/runner-groups": {"total_count": 1, "runner_groups": [{"id": 7, "name": "ios"}]},
        "/orgs/acme/actions/runners": {"total_count": 1, "runners": [
            api_runner("mac-2", os="macOS", labels=("self-hosted", "macOS"))]},
    })
    inv = provider(api).discover(request(scope="org", groups=["ios"]))
    assert inv.status == "ok" and inv.scopes == ("org",)
    assert inv.groups["ios"] == frozenset({"mac-2"})


@pytest.mark.parametrize("code,hint", [(401, "token rejected"), (403, "lacks permission"),
                                       (404, "not visible")])
def test_auth_failures_are_unavailable_without_retry(code, hint):
    api = FakeApi({"/actions/runners": http_error(code)})
    inv = provider(api).discover(request())
    assert inv.status == "unavailable"
    assert f"HTTP {code}" in inv.reason and hint in inv.reason
    assert len(api.calls) == 1, "a permission problem does not get better on retry"


def test_server_error_is_retried_once_then_succeeds():
    api = FakeApi({"/actions/runners": [http_error(502), {"total_count": 0, "runners": []}]})
    inv = provider(api).discover(request())
    assert inv.status == "ok"
    assert len(api.calls) == 2


def test_timeouts_end_in_unavailable_within_the_budget():
    clock = Clock()
    api = FakeApi({"/actions/runners": socket.timeout("timed out")}, clock=clock, cost=10.0)
    inv = provider(api, clock=clock, budget=30).discover(request(timeout=10))
    assert inv.status == "unavailable"
    assert "timed out" in inv.reason
    assert len(api.calls) == 2, "one retry, no more"
    assert clock.now <= 30 + 1


def test_budget_exhaustion_stops_further_requests():
    clock = Clock()
    page = {"total_count": 10_000, "runners": [api_runner(f"r{i}") for i in range(100)]}
    api = FakeApi({"/actions/runners": page}, clock=clock, cost=8.0)
    inv = provider(api, clock=clock, budget=30).discover(request())
    assert inv.status == "unavailable"
    assert "budget" in inv.reason
    assert len(api.calls) <= 4


def test_one_scope_failing_keeps_the_other():
    api = FakeApi({"/repos/acme/game/actions/runners": {"total_count": 1, "runners": [api_runner("r1")]},
                   "/orgs/acme/actions/runners": http_error(403)})
    inv = provider(api).discover(request(scope="both"))
    assert inv.status == "ok" and inv.scopes == ("repo",)
    assert "org runners: HTTP 403" in inv.reason


def test_missing_token_or_repository_is_unavailable_without_a_request():
    api = FakeApi({})
    assert ri.GitHubApiInventory("", "acme/game", opener=api).discover(request()).status == "unavailable"
    assert ri.GitHubApiInventory(TOKEN, "", opener=api).discover(request()).reason.startswith("GITHUB_REPOSITORY")
    assert api.calls == []


def test_the_token_never_appears_in_any_reason():
    class Leaky(urllib.error.URLError):
        pass

    api = FakeApi({"/actions/runners": Leaky(f"proxy said: bad credentials {TOKEN}")})
    inv = provider(api).discover(request())
    assert inv.status == "unavailable"
    assert TOKEN not in inv.reason and "***" in inv.reason


def test_cli_never_prints_the_token(tmp_path, monkeypatch, capsys):
    """End to end: scheduler CLI → API provider failing → no token anywhere."""
    def failing(request_, timeout):
        raise urllib.error.URLError(f"connection reset ({TOKEN})")

    monkeypatch.setattr(ri.urllib.request, "urlopen", failing)
    monkeypatch.setenv("RUNNER_STATUS_TOKEN", TOKEN)
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/game")
    monkeypatch.setenv("RUNNER_POLICY", json.dumps({
        "runners": {"m": {"labels": ["self-hosted", "macOS", "m"]}},
        "platforms": {"iOS": {"priority": ["m"]}}}))
    out, summary = tmp_path / "out", tmp_path / "summary"
    assert rs.main(["--jobs", "iOS", "--github-output", str(out), "--step-summary", str(summary)]) == 0
    captured = capsys.readouterr()
    everything = captured.out + captured.err + out.read_text() + summary.read_text()
    assert TOKEN not in everything
    selection = json.loads(dict(l.split("=", 1) for l in out.read_text().splitlines())["runner-selection"])
    assert selection["inventory"]["status"] == "unavailable"
    assert selection["jobs"]["iOS"]["availability"] == "unknown"


def test_no_token_means_no_api_provider(monkeypatch):
    monkeypatch.delenv("RUNNER_STATUS_TOKEN", raising=False)
    pol = rs.parse_policy({"runners": {"m": {"labels": ["self-hosted", "macOS", "m"]}}}, "t")

    class Args:
        inventory_file = ""
        repository = "acme/game"

    p = rs._provider_from_args(Args(), pol)
    assert isinstance(p, rs.NullInventory)
    assert p.discover(request()).reason == "RUNNER_STATUS_TOKEN not set"


# ---------------------------------------------------------------------------
# Runner-group membership is confirmed, not-found or unknown -- never guessed
# ---------------------------------------------------------------------------

def test_group_listing_distinguishes_confirmed_and_not_found():
    api = FakeApi({
        "/orgs/acme/actions/runner-groups/7/runners": {"total_count": 1, "runners": [api_runner("mac-2", os="macOS")]},
        "/orgs/acme/actions/runner-groups": {"total_count": 1, "runner_groups": [{"id": 7, "name": "ios"}]},
        "/repos/acme/game/actions/runners": {"total_count": 0, "runners": []},
    })
    inv = provider(api).discover(request(groups=["ios", "ghost"]))
    assert inv.group_status("ios") == rs.GROUP_CONFIRMED
    assert inv.group_status("ghost") == rs.GROUP_NOT_FOUND
    assert "runner group 'ghost' not found" in inv.reason


def test_group_listing_failure_leaves_membership_unknown():
    """A repository-scoped token cannot read organization runner groups."""
    api = FakeApi({"/orgs/acme/actions/runner-groups": http_error(403),
                   "/repos/acme/game/actions/runners": {"total_count": 1, "runners": [api_runner("outsider")]}})
    inv = provider(api).discover(request(groups=["ios"]))
    assert inv.status == "ok"
    assert inv.group_status("ios") == rs.GROUP_UNKNOWN
    assert "runner groups: HTTP 403" in inv.reason
    assert TOKEN not in inv.reason
