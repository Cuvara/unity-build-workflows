from pathlib import Path

import yaml


WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "sync-org-variables.yml"


def test_project_pinned_unity_versions_are_not_overwritten_by_org_sync():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    sync_run = workflow["jobs"]["sync"]["steps"][2]["run"]

    assert 'if [ "${VAR_NAME}" = "UNITY_VERSION" ]; then' in sync_run
    assert "Cuvara/IndieRPGMMOAdventure|Cuvara/NDCUnityTemplate)" in sync_run
    assert 'continue' in sync_run
    assert 'gh api "repos/${REPO_FULL}/actions/variables/${VAR_NAME}"' in sync_run
