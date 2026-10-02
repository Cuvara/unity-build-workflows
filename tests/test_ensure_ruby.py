"""
scripts/common/ensure_ruby.sh — find or install a Ruby for fastlane.

Every candidate is run and its version checked; candidates come from PATH and
from asking brew / rbenv / asdf, never from a file merely existing. Fake
`ruby` and `brew` executables stand in for the real ones.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / "scripts" / "common" / "ensure_ruby.sh"
BASE_PATH = "/usr/bin:/bin"

pytestmark = pytest.mark.skipif(os.name == "nt", reason="bash fakes need a POSIX shell")


def _exe(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def fake_ruby(bin_dir, version, bundler=True):
    _exe(bin_dir / "ruby", f"""
case "$*" in
  "-e print RUBY_VERSION") printf '%s' "{version}" ;;
  "-e print Gem.bindir") printf '%s' "{bin_dir}" ;;
  "-S bundle --version") {"echo Bundler 2.5" if bundler else "exit 1"} ;;
  "-S gem install bundler --no-document") echo installed ;;
  *) exit 0 ;;
esac
""")


def fake_brew(bin_dir, prefix, install_creates=None):
    install = (f'mkdir -p "{install_creates}"; '
               f'cp "{prefix}.template/bin/ruby" "{install_creates}/ruby"'
               if install_creates else "exit 1")
    _exe(bin_dir / "brew", f"""
case "$*" in
  "--prefix") echo /fake ;;
  "--prefix ruby") [ -x "{prefix}/bin/ruby" ] && echo "{prefix}" || exit 1 ;;
  "install ruby") {install} ;;
  *) exit 0 ;;
esac
""")


def run(path, tmp_path, **env):
    full = {"PATH": path, "HOME": str(tmp_path), "GITHUB_PATH": str(tmp_path / "gh_path"),
            "GITHUB_OUTPUT": str(tmp_path / "gh_out")}
    full.update(env)
    (tmp_path / "gh_path").write_text("")
    (tmp_path / "gh_out").write_text("")
    r = subprocess.run(["bash", str(SCRIPT)], env=full, capture_output=True, text=True)
    out = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
    return r, out


def test_ruby_on_path_is_used(tmp_path):
    fake_ruby(tmp_path / "r/bin", "3.3.5")
    r, out = run(f"{tmp_path}/r/bin:{BASE_PATH}", tmp_path)
    assert r.returncode == 0, r.stderr
    assert out["ruby-version"] == "3.3.5" and out["ruby-source"] == "path"
    assert f"{tmp_path}/r/bin" in (tmp_path / "gh_path").read_text()


def test_old_system_ruby_is_rejected_and_brew_is_asked(tmp_path):
    fake_ruby(tmp_path / "sys", "2.6.10")
    fake_ruby(tmp_path / "brewruby/bin", "3.4.1")
    fake_brew(tmp_path / "brewbin", tmp_path / "brewruby")
    r, out = run(f"{tmp_path}/sys:{tmp_path}/brewbin:{BASE_PATH}", tmp_path)
    assert r.returncode == 0, r.stderr
    assert "rejected" in r.stderr and "2.6.10" in r.stderr
    assert out["ruby-source"] == "brew" and out["ruby-version"] == "3.4.1"


def test_installs_with_brew_when_nothing_qualifies(tmp_path):
    fake_ruby(tmp_path / "sys", "2.6.10")
    fake_ruby(tmp_path / "brewruby.template/bin", "3.4.1")
    fake_brew(tmp_path / "brewbin", tmp_path / "brewruby",
              install_creates=tmp_path / "brewruby/bin")
    r, out = run(f"{tmp_path}/sys:{tmp_path}/brewbin:{BASE_PATH}", tmp_path)
    assert r.returncode == 0, r.stderr
    assert out["ruby-source"] == "installed:brew"


def test_no_ruby_and_no_installer_exits_with_the_fix(tmp_path):
    fake_ruby(tmp_path / "sys", "2.6.10")
    r, _ = run(f"{tmp_path}/sys:{BASE_PATH}", tmp_path, ENSURE_RUBY_NO_INSTALL="1")
    assert r.returncode == 3
    assert "brew install ruby" in r.stderr and "winget install" in r.stderr


def test_a_broken_ruby_is_not_trusted(tmp_path):
    _exe(tmp_path / "broken/ruby", "exit 1\n")
    fake_ruby(tmp_path / "brewruby/bin", "3.2.0")
    fake_brew(tmp_path / "brewbin", tmp_path / "brewruby")
    r, out = run(f"{tmp_path}/broken:{tmp_path}/brewbin:{BASE_PATH}", tmp_path)
    assert r.returncode == 0, r.stderr
    assert out["ruby-source"] == "brew"


def test_bundler_is_installed_when_missing(tmp_path):
    fake_ruby(tmp_path / "r/bin", "3.3.5", bundler=False)
    r, _ = run(f"{tmp_path}/r/bin:{BASE_PATH}", tmp_path)
    assert r.returncode == 0, r.stderr
    assert "installing Bundler" in r.stderr
