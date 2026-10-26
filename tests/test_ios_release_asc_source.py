"""Release / iOS can promote a build that is already on App Store Connect.

With ARTIFACT_STORAGE=firebase, Build / Release uploads the IPA straight to
TestFlight and leaves no GitHub artifact, so the github source of
pipeline-ios-release.yml had nothing to download. `binary-source:
app-store-connect` picks the build by version + build number instead. The
default stays `github`, and its job graph must not move.
"""
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pipeline-ios-release.yml"
ACTION = REPO_ROOT / ".github" / "actions" / "ios-asc-existing-build" / "action.yml"
TEMPLATE = REPO_ROOT / "templates" / "consumer-21-release-ios.yml"
FASTFILE = REPO_ROOT / "fastlane" / "Fastfile"

ASC = "app-store-connect"
PHASES = ["internal-testing", "external-testing", "production-release"]


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _jobs():
    return _load(WORKFLOW)["jobs"]


def _inputs():
    wf = _load(WORKFLOW)
    return (wf.get("on") or wf[True])["workflow_call"]["inputs"]


def _steps(job_id):
    return _jobs()[job_id]["steps"]


def _uses(step):
    return str(step.get("uses", ""))


# ── inputs ─────────────────────────────────────────────────────────────────

def test_the_new_inputs_are_optional_strings_and_the_default_is_github():
    inputs = _inputs()
    for name in ("binary-source", "build-number", "testflight-groups"):
        assert inputs[name]["type"] == "string", name
        assert inputs[name]["required"] is False, name
        assert inputs[name]["description"], name
    assert inputs["binary-source"]["default"] == "github"
    assert inputs["build-number"]["default"] == ""


def test_the_github_only_inputs_no_longer_block_the_other_source():
    inputs = _inputs()
    for name in ("source-run-id", "artifact-name"):
        assert inputs[name]["required"] is False, name
        assert inputs[name]["default"] == "", name


# ── job graph ──────────────────────────────────────────────────────────────

def test_exactly_one_verify_job_runs_per_source():
    jobs = _jobs()
    assert "inputs.binary-source != 'app-store-connect'" in jobs["verify-artifact"]["if"]
    assert jobs["verify-asc-build"]["if"] == f"inputs.binary-source == '{ASC}'"


def test_the_github_source_checks_its_inputs_and_rejects_unknown_sources():
    check = next(s for s in _steps("verify-artifact") if s.get("name") == "Check inputs")
    run = check["run"]
    assert "!= \"github\"" in run and "source-run-id" in run
    assert "exit 1" in run
    # The check precedes the download it protects.
    names = [s.get("name") for s in _steps("verify-artifact")]
    assert names.index("Check inputs") < names.index("Verify release artifact")


def test_the_verify_job_looks_the_build_up_by_version_and_number():
    verify = next(s for s in _steps("verify-asc-build") if "ios-asc-existing-build" in _uses(s))
    assert verify["with"]["mode"] == "verify"
    assert verify["with"]["version-string"] == "${{ inputs.version-string }}"
    assert verify["with"]["build-number"] == "${{ inputs.build-number }}"
    assert verify["with"]["app-id"] == "${{ inputs.app-id }}"


def test_every_downstream_job_accepts_either_verification():
    jobs = _jobs()
    for job_id in ["release-notes"] + PHASES:
        job = jobs[job_id]
        assert "verify-artifact" in job["needs"] and "verify-asc-build" in job["needs"], job_id
        condition = str(job["if"])
        assert "needs.verify-artifact.result == 'success'" in condition, job_id
        assert "needs.verify-asc-build.result == 'success'" in condition, job_id
    assert "verify-asc-build" not in jobs["validate-ipa"]["needs"], "no IPA to validate"


def test_phase_gating_is_identical_for_both_sources():
    jobs = _jobs()
    assert jobs["internal-testing"]["environment"] == "internal-testing"
    assert jobs["external-testing"]["environment"] == "external-testing"
    assert jobs["production-release"]["environment"] == "production"
    for job_id, phase in [("internal-testing", "internal"), ("external-testing", "external"),
                          ("production-release", "production")]:
        condition = str(jobs[job_id]["if"])
        assert f"inputs.start-phase == '{phase}'" in condition
        assert "!inputs.dry-run" in condition


def test_the_report_covers_the_new_job():
    report = _jobs()["report"]
    assert "verify-asc-build" in report["needs"]
    phases = str(report["steps"][-1]["with"]["phases"])
    assert "04 Verify App Store Connect Build=${{ needs.verify-asc-build.result }}" in phases


# ── nothing is uploaded or downloaded for app-store-connect ────────────────

def test_github_only_steps_are_skipped_for_app_store_connect():
    for job_id in PHASES:
        for step in _steps(job_id):
            uses = _uses(step)
            github_only = (
                uses.endswith("/verify-release-artifact")
                or uses.endswith("/ios-testflight")
                or step.get("id") in ("find-ipa", "ipa")
            )
            if github_only:
                assert step["if"] == "inputs.binary-source != 'app-store-connect'", \
                    f"{job_id}: {step.get('name')}"


def test_the_asc_steps_run_only_for_app_store_connect():
    seen = 0
    for job_id in PHASES:
        for step in _steps(job_id):
            if "ios-asc-existing-build" in _uses(step):
                seen += 1
                assert step["if"] == f"inputs.binary-source == '{ASC}'", job_id
    assert seen == 3, "internal verify, external distribute, production verify"


def test_internal_uploads_nothing_for_app_store_connect():
    job = _jobs()["internal-testing"]
    assert "ubuntu-latest" in job["runs-on"] and "macos-latest" in job["runs-on"]
    asc_steps = [s for s in job["steps"] if "ios-asc-existing-build" in _uses(s)]
    assert [s["with"]["mode"] for s in asc_steps] == ["verify"]
    assert "ios-testflight" not in str([s for s in job["steps"]
                                         if s.get("if") == f"inputs.binary-source == '{ASC}'"])
    assert "inputs.build-number" in job["outputs"]["build-number"]


def test_external_distributes_the_existing_build_to_the_groups():
    step = next(s for s in _steps("external-testing") if "ios-asc-existing-build" in _uses(s))
    assert step["with"]["mode"] == "distribute"
    assert step["with"]["groups"] == "${{ inputs.testflight-groups }}"
    assert step["with"]["build-number"] == "${{ inputs.build-number }}"


def test_production_submits_the_existing_build_with_phased_release():
    steps = _steps("production-release")
    submits = [s for s in steps if "ios-asc-submit-review" in _uses(s)]
    assert len(submits) == 2
    asc = next(s for s in submits if s["if"] == f"inputs.binary-source == '{ASC}'")
    assert asc["with"]["build-number"] == "${{ inputs.build-number }}"
    assert asc["with"]["bundle-id"] == "${{ steps.asc.outputs.bundle-id }}"
    assert asc["with"]["phased-release"] == "${{ inputs.phased-release && 'true' || 'false' }}"
    names = [s.get("name") for s in steps]
    assert names.index("Verify build on App Store Connect") < names.index(asc["name"])


def test_secrets_reach_the_asc_steps_only_through_inputs():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets.APP_STORE_CONNECT_PRIVATE_KEY" not in "\n".join(
        l for l in text.splitlines() if l.strip().startswith("run:") or "echo" in l)
    action = ACTION.read_text(encoding="utf-8")
    run = action[action.index("run: |"):]
    assert "${{ inputs.asc-private-key }}" not in run
    assert "${{ secrets." not in action


# ── composite action and lanes ─────────────────────────────────────────────

def test_the_action_is_called_with_exactly_the_inputs_it_declares():
    declared = _load(ACTION)["inputs"]
    required = {k for k, v in declared.items() if v.get("required")}
    for job_id, job in _jobs().items():
        for step in job.get("steps", []):
            if "ios-asc-existing-build" not in _uses(step):
                continue
            passed = set(step["with"])
            assert passed <= set(declared), f"{job_id}: undeclared {passed - set(declared)}"
            assert required <= passed, f"{job_id}: missing {required - passed}"
    assert all(v.get("description") for v in declared.values())


def test_the_action_runs_the_lanes_that_exist():
    fastfile = FASTFILE.read_text(encoding="utf-8")
    action = ACTION.read_text(encoding="utf-8")
    for lane in ("verify_asc_build", "distribute_existing_build"):
        assert f"lane :{lane} do" in fastfile
        assert f"fastlane ios {lane}" in action


def test_the_lanes_authenticate_through_the_one_key_lane():
    fastfile = FASTFILE.read_text(encoding="utf-8")
    assert fastfile.count("app_store_connect_api_key(") == 1
    for lane in ("verify_asc_build", "distribute_existing_build"):
        body = fastfile[fastfile.index(f"lane :{lane} do"):]
        body = body[:body.index("\n  end")]
        assert "configure_asc_api_key" in body, lane


def test_verify_names_the_exact_build_in_its_errors():
    fastfile = FASTFILE.read_text(encoding="utf-8")
    body = fastfile[fastfile.index("lane :verify_asc_build do"):]
    body = body[:body.index("\n  end")]
    assert 'wanted   = "version #{version} build #{number} (app #{app_id})"' in body
    for state in ("FAILED", "INVALID", "VALID", "expired"):
        assert state in body
    assert "Builds uploaded for version" in body, "a miss should list what does exist"


def test_distribute_uses_pilot_distribute_only_on_the_given_build():
    fastfile = FASTFILE.read_text(encoding="utf-8")
    body = fastfile[fastfile.index("lane :distribute_existing_build do"):]
    body = body[:body.index("\n  end")]
    for fragment in ("distribute_only:                true", "distribute_external:            true",
                     "build_number:", "app_version:", "needs groups"):
        assert fragment in body, fragment
    assert "ipa:" not in body, "an existing build is never re-uploaded"


# ── consumer template ──────────────────────────────────────────────────────

def test_the_template_defaults_binary_source_from_artifact_storage():
    template = _load(TEMPLATE)
    inputs = (template.get("on") or template[True])["workflow_dispatch"]["inputs"]
    assert inputs["binary-source"]["default"] == "auto"
    assert set(inputs["binary-source"]["options"]) == {"auto", "github", ASC}
    for name in ("build-number", "testflight-groups"):
        assert inputs[name]["required"] is False
    job = next(iter(template["jobs"].values()))
    expression = job["with"]["binary-source"]
    assert "vars.ARTIFACT_STORAGE == 'firebase'" in expression
    assert ASC in expression and "'github'" in expression
    assert job["with"]["build-number"] == "${{ inputs.build-number }}"


# ── pipeline invariant I-010 ───────────────────────────────────────────────

def test_the_invariant_checker_accepts_an_optional_run_id_only_with_a_guard(tmp_path):
    import os
    import shutil
    import subprocess
    import sys

    script = REPO_ROOT / "scripts" / "common" / "validate_pipeline_invariants.py"

    def run(root):
        return subprocess.run([sys.executable, str(script), "--repo-root", str(root)], env={**os.environ, "PYTHONUTF8": "1"},
                              capture_output=True, text=True, encoding="utf-8")

    assert "FAIL" not in run(REPO_ROOT).stdout

    # Without the guard step the optional input is the defect I-010 describes.
    root = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / ".github", root / ".github")
    for sub in ("scripts", "templates"):
        shutil.copytree(REPO_ROOT / sub, root / sub)
    wf = root / ".github" / "workflows" / "pipeline-ios-release.yml"
    wf.write_text(wf.read_text(encoding="utf-8").replace(
        '-z "${SOURCE_RUN_ID}"', '-n "${SOURCE_RUN_ID}"'), encoding="utf-8")
    result = run(root)
    assert "I-010" in result.stdout and "source-run-id is optional" in result.stdout
