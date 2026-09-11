"""
The Google Play versionCode guard.

Play rejects a duplicate versionCode, but only after the upload has started —
a late failure that reads like a credentials problem. A code merely *lower*
than the current one is worse: it uploads cleanly and then cannot be promoted,
which surfaces days later mid-rollout.

These tests drive `check_build_number.py` against a fake Play API so the whole
decision table can be exercised without credentials, including the failure
modes that must fail closed.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts" / "android"))

import check_build_number as guard  # noqa: E402


# ---------------------------------------------------------------------------
# A Play API that answers from a dict instead of the network
# ---------------------------------------------------------------------------

class FakeTracks:
    def __init__(self, by_track, fail_with=None):
        self._by_track = by_track
        self._fail_with = fail_with

    def get(self, packageName, editId, track):
        class Call:
            def execute(_self):
                if self._fail_with:
                    raise self._fail_with
                if track not in self._by_track:
                    raise RuntimeError("404 track not found")
                return {"releases": [{"versionCodes": self._by_track[track]}]}
        return Call()


class FakeEdits:
    def __init__(self, by_track, fail_with=None):
        self._tracks = FakeTracks(by_track, fail_with)
        self.deleted = False

    def insert(self, packageName):
        class Call:
            def execute(_self):
                return {"id": "edit-1"}
        return Call()

    def tracks(self):
        return self._tracks

    def delete(self, packageName, editId):
        outer = self

        class Call:
            def execute(_self):
                outer.deleted = True
                return {}
        return Call()


class FakeService:
    def __init__(self, by_track, fail_with=None):
        self._edits = FakeEdits(by_track, fail_with)

    def edits(self):
        return self._edits


# ---------------------------------------------------------------------------
# Reading the store
# ---------------------------------------------------------------------------

def test_highest_is_taken_across_every_track():
    """A code used on internal cannot be reused for production, so the guard
    has to look at all of them — not just the track being published to."""
    service = FakeService({"internal": [1041], "alpha": [1039], "production": [1030]})
    assert guard.highest_version_code(service, "com.example.game") == 1041


def test_a_missing_track_is_normal():
    service = FakeService({"production": [7]})
    assert guard.highest_version_code(service, "com.example.game") == 7


def test_an_app_with_no_releases_returns_none():
    assert guard.highest_version_code(FakeService({}), "com.example.game") is None


def test_the_edit_is_always_cleaned_up():
    """A read-only check that leaves an edit open blocks every later one."""
    service = FakeService({"production": [5]})
    guard.highest_version_code(service, "com.example.game")
    assert service.edits().deleted


def test_a_real_api_error_is_not_swallowed_as_a_missing_track():
    service = FakeService({}, fail_with=RuntimeError("403 permission denied"))
    with pytest.raises(RuntimeError):
        guard.highest_version_code(service, "com.example.game")


def test_non_integer_version_codes_are_ignored():
    service = FakeService({"production": ["1041", None, "not-a-number"]})
    assert guard.highest_version_code(service, "com.example.game") == 1041


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------

@pytest.fixture
def store(monkeypatch):
    """Point the guard at a fake store and hand back its verdict."""
    def configure(highest, raises=None):
        def fake_highest(service, package_name):
            if raises:
                raise raises
            return highest
        monkeypatch.setattr(guard, "highest_version_code", fake_highest)
        monkeypatch.setattr(guard, "load_credentials", lambda raw: object())

        # The guard imports googleapiclient lazily; stub it so the test does
        # not need the real client installed.
        module = type(sys)("googleapiclient.discovery")
        module.build = lambda *a, **k: object()
        sys.modules["googleapiclient"] = type(sys)("googleapiclient")
        sys.modules["googleapiclient.discovery"] = module
    return configure


def run(argv, credentials='{"type":"service_account"}'):
    env = ["--service-account-json", credentials] if credentials else []
    return guard.main(argv + env)


@pytest.mark.parametrize("highest,candidate,expected", [
    (1041, 1042, 0),   # above the store — the only safe case
    (1041, 1041, 1),   # duplicate: Play rejects it, late and confusingly
    (1041, 1039, 1),   # below: uploads fine, then cannot be promoted
    (1041, 9999, 0),
])
def test_the_decision_table(store, highest, candidate, expected):
    store(highest)
    assert run(["--package-name", "com.example.game",
                "--build-number", str(candidate)]) == expected


def test_a_first_upload_is_allowed(store):
    """No existing code at all is not a collision."""
    store(None)
    assert run(["--package-name", "com.example.game", "--build-number", "1"]) == 0


def test_an_unreachable_store_fails_closed(store):
    """Whether the number is safe is unknown, and unknown is not yes."""
    store(None, raises=RuntimeError("connection reset"))
    assert run(["--package-name", "com.example.game", "--build-number", "5"]) == 1


def test_missing_credentials_fail_closed():
    assert guard.main(["--package-name", "com.example.game",
                       "--build-number", "5",
                       "--service-account-json", ""]) == 1


def test_the_escape_hatch_is_explicit(store):
    """--allow-unverified exists for a package the console does not have yet."""
    store(None, raises=RuntimeError("connection reset"))
    assert run(["--package-name", "com.example.game", "--build-number", "5",
                "--allow-unverified"]) == 0


@pytest.mark.parametrize("value", ["", "abc", "1.5", "-1", "0"])
def test_a_nonsense_build_number_is_a_usage_error(value):
    """Exit 2, not 1: this is a broken call, not a store verdict."""
    assert guard.main(["--package-name", "com.example.game",
                       "--build-number", value,
                       "--service-account-json", "{}"]) == 2


def test_the_verdict_reaches_the_job_summary(store, tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    store(1041)
    assert run(["--package-name", "com.example.game", "--build-number", "1041"]) == 1
    text = summary.read_text()
    assert "1041" in text and "already been used" in text


def test_no_credential_material_reaches_the_summary(store, tmp_path, monkeypatch):
    """A secret in a job summary is a secret in a public artifact."""
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON", "")
    store(1041)
    secret = '{"private_key":"-----BEGIN PRIVATE KEY-----super-secret"}'
    run(["--package-name", "com.example.game", "--build-number", "1042"],
        credentials=secret)
    assert "super-secret" not in summary.read_text()


def test_the_promotion_workflow_runs_the_guard_before_publishing():
    """The check is only worth anything if it happens before the upload."""
    workflow = (REPO_ROOT / ".github" / "workflows"
                / "pipeline-android-release.yml").read_text()
    assert "check_build_number.py" in workflow
    guard_at = workflow.index("check_build_number.py")
    for publish in ("internal-testing:", "external-testing:", "production-release:"):
        if publish in workflow:
            assert workflow.index(publish) > guard_at, (
                f"{publish} is defined before the versionCode guard runs"
            )


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_a_blank_secret_is_reported_as_missing_not_corrupt(blank, capsys):
    """An unset GitHub secret interpolates as an empty string. Sending that to
    the JSON parser produced "service account JSON is not valid JSON", which
    sends whoever reads the log looking for a broken key instead of an absent
    one."""
    assert guard.main(["--package-name", "com.example.game",
                       "--build-number", "5",
                       "--service-account-json", blank]) == 1
    err = capsys.readouterr().err
    assert "no Google Play service account provided" in err
    assert "not valid JSON" not in err
