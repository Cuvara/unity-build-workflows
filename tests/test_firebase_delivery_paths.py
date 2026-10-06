"""Firebase delivery on a Windows runner: fastlane runs under a native
Windows Ruby, which cannot open the Git Bash path mktemp returns (/tmp/...).
Seen on a self-hosted Windows runner: "Service credentials file does not exist"."""
from pathlib import Path

import yaml

REUSABLE = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "reusable-build-platform.yml"


def test_service_account_path_is_converted_for_windows_ruby():
    steps = yaml.safe_load(REUSABLE.read_text(encoding="utf-8"))["jobs"]["build"]["steps"]
    run = next(s for s in steps if s.get("name") == "Publish the build for download")["run"]
    assert 'SA_PATH="$(cygpath -w "${SA_FILE}")"' in run
    assert 'service_account_path:"${SA_PATH}"' in run
    assert 'service_account_path:"${SA_FILE}"' not in run
