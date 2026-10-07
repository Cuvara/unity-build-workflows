"""The toolkit's own automation gets its GitHub App token one way.

Six workflows (auto-merge, auto-release, version-bump, build-pr-comment,
license-rotation, sync-org-variables) each carried the same ~55 lines of
Node.js that signed a JWT and exchanged it for an installation token. They
now use actions/create-github-app-token, pinned by SHA: it needs no checkout
(five of the six mint the token before checking out, because the checkout
uses it), masks the token, and revokes it when the job ends.
"""
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
ACTION = "actions/create-github-app-token@"
USERS = ["auto-merge.yml", "auto-release.yml", "version-bump.yml",
         "build-pr-comment.yml", "license-rotation.yml", "sync-org-variables.yml",
         "unity-generate-license.yml"]


def _token_steps(name):
    doc = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    return [s for job in doc["jobs"].values() for s in job.get("steps") or []
            if str(s.get("uses", "")).startswith(ACTION)]


@pytest.mark.parametrize("name", USERS)
def test_the_token_comes_from_the_pinned_action(name):
    steps = _token_steps(name)
    assert len(steps) == 1, name
    step = steps[0]
    ref = step["uses"].split("@", 1)[1]
    assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), "pinned to a commit SHA"
    assert step["with"]["app-id"] == "${{ secrets.APP_ID }}"
    assert step["with"]["private-key"] == "${{ secrets.APP_PRIVATE_KEY }}"
    assert step["with"]["owner"] == "${{ github.repository_owner }}"
    # Later steps read steps.<id>.outputs.token, which the action provides.
    assert step["id"] in ("bot", "bot-token")


def test_no_workflow_signs_its_own_jwt():
    for path in sorted(WORKFLOWS.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        assert "/access_tokens" not in text, f"{path.name} hand-rolls the installation token"


def test_every_user_pins_the_same_version():
    refs = {_token_steps(name)[0]["uses"] for name in USERS}
    assert len(refs) == 1, refs
