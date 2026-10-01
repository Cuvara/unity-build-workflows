#!/usr/bin/env python3
"""
runner_inventory.py
Self-hosted runner inventory from the GitHub REST API (runner scheduling stage 2).

Implements runner_scheduler.InventoryProvider. Uses only documented endpoints:

  GET /repos/{owner}/{repo}/actions/runners                         scope=repo
  GET /orgs/{org}/actions/runners                                   scope=org
  GET /orgs/{org}/actions/runner-groups                             (group lookup)
  GET /orgs/{org}/actions/runner-groups/{id}/runners                (group members)

Each runner carries `name`, `os`, `status` (online|offline), `busy` and
`labels[].name` -- the identity, platform and availability facts the scheduler
needs. Runner status is NOT readable with GITHUB_TOKEN (it cannot be granted
`administration`), so a separate read-only token is required:

  repository runners          fine-grained: Administration (read)     classic: repo
  organization runners/groups fine-grained: Self-hosted runners (read) classic: admin:org

Determinism: every request has a timeout, the whole discovery has a budget,
transport failures are retried once, and any failure ends in an Inventory with
status `unavailable` and a reason -- never an exception, never a hang. The token
is only ever placed in the Authorization header and is redacted from every
message this module produces.
"""

import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from runner_scheduler import (  # noqa: E402
    Inventory, InventoryProvider, InventoryRequest, inventory_from_document,
)

API_VERSION = "2022-11-28"
PER_PAGE = 100
MAX_PAGES = 20            # 2000 runners per scope; far beyond any build farm
DEFAULT_BUDGET_SECONDS = 30
USER_AGENT = "unity-build-workflows-runner-scheduler"


class ApiError(Exception):
    def __init__(self, message: str, status: int = 0, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class BudgetExceeded(ApiError):
    pass


class GitHubApiInventory(InventoryProvider):
    def __init__(self, token: str, repository: str, api_url: str = "https://api.github.com",
                 opener: Optional[Callable] = None, budget_seconds: float = DEFAULT_BUDGET_SECONDS,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self._token = token
        self.repository = repository.strip()
        self.api_url = (api_url or "https://api.github.com").rstrip("/")
        self._open = opener or urllib.request.urlopen
        self.budget_seconds = budget_seconds
        self._clock = clock
        self._sleep = sleep
        self._deadline = 0.0

    # ── transport ──────────────────────────────────────────────────────────

    def _redact(self, text: str) -> str:
        text = str(text)
        if self._token:
            text = text.replace(self._token, "***")
        return text

    def _get_once(self, path: str, timeout: float) -> dict:
        request = urllib.request.Request(self.api_url + path, headers={
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": USER_AGENT,
        })
        try:
            with self._open(request, timeout=timeout) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            if status in (401, 403, 404):
                hint = {401: "token rejected", 403: "token lacks permission",
                        404: "not found or not visible to the token"}[status]
                raise ApiError(f"HTTP {status} ({hint})", status=status) from None
            raise ApiError(f"HTTP {status}", status=status, retryable=status >= 500 or status == 429) from None
        except (socket.timeout, TimeoutError):
            raise ApiError(f"timed out after {timeout:.0f}s", retryable=True) from None
        except urllib.error.URLError as exc:
            raise ApiError(f"network error: {self._redact(exc.reason)}", retryable=True) from None
        except OSError as exc:
            raise ApiError(f"network error: {self._redact(exc)}", retryable=True) from None
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ApiError("response was not JSON") from None

    def _get(self, path: str, per_request_timeout: float) -> dict:
        last = None
        for attempt in range(2):
            remaining = self._deadline - self._clock()
            if remaining <= 0:
                raise BudgetExceeded(f"discovery budget of {self.budget_seconds:.0f}s exhausted")
            try:
                return self._get_once(path, min(per_request_timeout, remaining))
            except ApiError as exc:
                last = exc
                if not exc.retryable or attempt == 1:
                    raise
                self._sleep(min(1.0, max(0.0, self._deadline - self._clock())))
        raise last  # pragma: no cover

    def _paginate(self, path: str, key: str, timeout: float) -> List[dict]:
        items: List[dict] = []
        for page in range(1, MAX_PAGES + 1):
            sep = "&" if "?" in path else "?"
            data = self._get(f"{path}{sep}per_page={PER_PAGE}&page={page}", timeout)
            batch = data.get(key) or []
            items.extend(batch)
            total = data.get("total_count")
            if len(batch) < PER_PAGE or (isinstance(total, int) and len(items) >= total):
                break
        return items

    # ── discovery ──────────────────────────────────────────────────────────

    def discover(self, request: InventoryRequest) -> Inventory:
        self._deadline = self._clock() + self.budget_seconds
        if not self._token:
            return Inventory(provider="github-api", status="unavailable",
                             reason="RUNNER_STATUS_TOKEN not set")
        if "/" not in self.repository:
            return Inventory(provider="github-api", status="unavailable",
                             reason="GITHUB_REPOSITORY not set (owner/repo)")
        owner = self.repository.split("/", 1)[0]
        quoted_repo = "/".join(urllib.parse.quote(p, safe="") for p in self.repository.split("/", 1))
        quoted_owner = urllib.parse.quote(owner, safe="")

        scopes: List[Tuple[str, str]] = []
        if request.scope in ("repo", "both"):
            scopes.append(("repo", f"/repos/{quoted_repo}/actions/runners"))
        if request.scope in ("org", "both"):
            scopes.append(("org", f"/orgs/{quoted_owner}/actions/runners"))

        runners: List[dict] = []
        ok_scopes: List[str] = []
        problems: List[str] = []
        for scope, path in scopes:
            try:
                runners.extend(self._paginate(path, "runners", request.timeout_seconds))
                ok_scopes.append(scope)
            except ApiError as exc:
                problems.append(f"{scope} runners: {self._redact(exc)}")

        if not ok_scopes:
            return Inventory(provider="github-api", status="unavailable",
                             reason="; ".join(problems) or "no scope queried")

        # Membership is recorded only from the group endpoints. A group the
        # listing returned is confirmed; one the listing did not contain is
        # not-found; if the listing itself failed, every group stays unknown.
        groups: Dict[str, List[str]] = {}
        not_found: List[str] = []
        if request.groups:
            try:
                listed = self._paginate(f"/orgs/{quoted_owner}/actions/runner-groups", "runner_groups",
                                        request.timeout_seconds)
                by_name = {g.get("name"): g.get("id") for g in listed}
                for name in request.groups:
                    gid = by_name.get(name)
                    if gid is None:
                        problems.append(f"runner group '{name}' not found")
                        not_found.append(name)
                        continue
                    members = self._paginate(f"/orgs/{quoted_owner}/actions/runner-groups/{int(gid)}/runners",
                                             "runners", request.timeout_seconds)
                    groups[name] = [m.get("name", "") for m in members]
                    # Group members are organization runners; include any the
                    # repository listing did not return.
                    known = {r.get("name") for r in runners}
                    runners.extend(m for m in members if m.get("name") not in known)
            except ApiError as exc:
                problems.append(f"runner groups: {self._redact(exc)}")

        inventory = inventory_from_document({"runners": runners, "groups": groups,
                                             "groupsNotFound": not_found,
                                             "scopes": ok_scopes}, provider="github-api")
        inventory.reason = "; ".join(problems)
        return inventory
