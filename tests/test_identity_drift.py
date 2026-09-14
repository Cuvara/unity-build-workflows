"""The gate that would have caught an app shipping under the template's identity.

A consumer shipped Android builds for weeks carrying
`com.UnityTechnologies.com.unity.template.urpblank` and version `0.4.2`, while
`BuildConfig/base.json` said `com.cuvara.indierpgmmo` and `0.5.0`. Every build
was green, because nothing compared the two.

What this gate does NOT do is worth stating: it catches DISAGREEMENT, not
wrongness. In the real case it would have caught the version and not the
identifier, because `production.json` and `ProjectSettings.asset` agreed with
each other on the template id — both wrong, identically. Two sources agreeing is
not evidence that either is right.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "common" / "check_identity_drift.py"

SETTINGS_TEMPLATE = """%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!129 &1
PlayerSettings:
  m_ObjectHideFlags: 0
  companyName: {company}
  productName: ExampleProject
  bundleVersion: {version}
  applicationIdentifier:
    Android: {app_id}
    Standalone: {app_id}
  buildNumber:
    Standalone: 0
"""


def _project(tmp_path, *, settings, config):
    (tmp_path / "ProjectSettings").mkdir()
    (tmp_path / "ProjectSettings" / "ProjectSettings.asset").write_text(
        SETTINGS_TEMPLATE.format(**settings), encoding="utf-8")
    (tmp_path / "BuildConfig").mkdir()
    (tmp_path / "BuildConfig" / "base.json").write_text(
        json.dumps(config), encoding="utf-8")
    return tmp_path


def _run(project, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--project-path", str(project), *args],
        capture_output=True, text=True)


MATCHING = dict(
    settings={"company": "Cuvara", "version": "0.5.0", "app_id": "com.cuvara.game"},
    config={"companyName": "Cuvara", "bundleVersion": "0.5.0",
            "android": {"applicationId": "com.cuvara.game"}},
)


def test_matching_identity_passes(tmp_path):
    result = _run(_project(tmp_path, **MATCHING))
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("field,settings_patch", [
    ("bundleVersion", {"version": "0.4.2"}),
    ("applicationIdentifier.Android", {"app_id": "com.UnityTechnologies.urpblank"}),
    ("companyName", {"company": "DefaultCompany"}),
])
def test_each_drifted_field_fails_the_build(tmp_path, field, settings_patch):
    settings = {**MATCHING["settings"], **settings_patch}
    result = _run(_project(tmp_path, settings=settings, config=MATCHING["config"]))
    assert result.returncode == 1, f"{field} drift did not fail: {result.stdout}"
    assert field in result.stdout
    # The message has to say which value ships, or the reader fixes the wrong file.
    assert "ProjectSettings value is the one that ships" in result.stdout


def test_the_environment_overlay_is_what_gets_compared(tmp_path):
    """base says one thing, the overlay another; the overlay is what builds.

    The divergence here is a *different* version, not a suffixed identifier —
    a suffix is a deliberate variant and would not fail, which would make this
    test pass for the wrong reason.
    """
    project = _project(tmp_path, **MATCHING)
    (project / "BuildConfig" / "development.json").write_text(json.dumps({
        "companyName": "Cuvara", "bundleVersion": "9.9.9",
        "android": {"applicationId": "com.cuvara.game"},
    }), encoding="utf-8")

    assert _run(project, "--environment", "development").returncode == 1
    assert _run(project, "--environment", "production").returncode == 0


def test_a_suffixed_identifier_is_a_variant_not_drift(tmp_path):
    """`com.acme.game.dev` against `com.acme.game` is how a QA build installs
    alongside production. A gate that reddens every development build is a gate
    that gets switched off."""
    settings = {**MATCHING["settings"], "app_id": "com.cuvara.game"}
    config = {"companyName": "Cuvara", "bundleVersion": "0.5.0",
              "android": {"applicationId": "com.cuvara.game.dev"}}
    result = _run(_project(tmp_path, settings=settings, config=config))
    assert result.returncode == 0, result.stdout
    # Reported, not failed -- on the Docker lane the suffix never reaches the
    # binary, so the reader should know the dev build installs as production.
    assert "::notice::" in result.stdout
    assert "the suffix is not applied" in result.stdout


def test_a_different_identifier_is_still_drift(tmp_path):
    """A variant extends the base id. A different id is a different app."""
    settings = {**MATCHING["settings"], "app_id": "com.cuvara.game"}
    config = {"companyName": "Cuvara", "bundleVersion": "0.5.0",
              "android": {"applicationId": "com.other.game.dev"}}
    result = _run(_project(tmp_path, settings=settings, config=config))
    assert result.returncode == 1, result.stdout


def test_warn_mode_reports_without_failing(tmp_path):
    settings = {**MATCHING["settings"], "version": "0.4.2"}
    result = _run(_project(tmp_path, settings=settings, config=MATCHING["config"]),
                  "--mode", "warn")
    assert result.returncode == 0
    assert "::warning::" in result.stdout


def test_off_mode_is_silent(tmp_path):
    settings = {**MATCHING["settings"], "version": "0.4.2"}
    result = _run(_project(tmp_path, settings=settings, config=MATCHING["config"]),
                  "--mode", "off")
    assert result.returncode == 0
    assert "::error::" not in result.stdout


def test_a_value_the_config_declines_to_set_is_not_drift(tmp_path):
    """An absent field is a config with no opinion, not a disagreement."""
    config = {"companyName": "Cuvara", "bundleVersion": "0.5.0"}  # no android block
    settings = {**MATCHING["settings"], "app_id": "com.something.else"}
    assert _run(_project(tmp_path, settings=settings, config=config)).returncode == 0


def test_a_missing_project_does_not_fail_the_build(tmp_path):
    """Not every consumer has BuildConfig at all; absence is not drift."""
    result = _run(tmp_path)
    assert result.returncode == 0
