"""
Fetch submodules over SSH with the runner machine's own credentials.

A Windows runner service has no HOME in Git Bash, so ssh looked in /.ssh (the
Git install), found no key, could not write known_hosts, and every private
submodule failed with "Permission denied (publickey)".
"""

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).parent.parent / ".github" / "workflows"


@pytest.mark.parametrize("workflow", ["reusable-build-platform.yml", "reusable-unity-tests.yml"])
def test_submodule_step_uses_the_machine_key_properly(workflow):
    jobs = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))["jobs"]
    steps = next(iter(jobs.values()))["steps"]
    run = next(s for s in steps if s.get("name") == "Fetch submodules over SSH")["run"]
    assert 'export HOME="$(cygpath -u "${USERPROFILE}"' in run, "HOME from USERPROFILE"
    assert "UserKnownHostsFile=${_known_hosts}" in run, "known_hosts in a writable temp file"
    assert "SUBMODULE_SSH_KEY_FILE" in run, "a key file on the runner, named in its .env"
    assert run.index("SUBMODULE_SSH_KEY:-") < run.index("SUBMODULE_SSH_KEY_FILE:-"), \
        "the secret wins over the machine key file"
