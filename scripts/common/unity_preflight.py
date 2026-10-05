#!/usr/bin/env python3
"""
unity_preflight.py
Ensure the Unity Editor a project needs -- and the build modules for the
requested platforms -- are installed on this machine, then print where the
editor executable is.

The project is the single source of truth for the editor version:
ProjectSettings/ProjectVersion.txt (m_EditorVersion, plus the changeset from
m_EditorVersionWithRevision when present). Matching is exact; a different
installed version is never substituted.

Installation goes through a real Unity CLI, chosen by --cli:
  unity  the standalone Unity CLI (`unity install`, `unity install-modules`)
  hub    the Unity Hub headless CLI (`Unity Hub -- --headless install ...`)
  auto   unity if found, else hub (default)

Editors are installed machine-wide (the CLI's install path), so every worktree
of a project reuses one installation. Installs are serialised by a per-user
machine lock outside the worktree; read-only runs never take it.

Usage (scripts/unity-preflight.sh is the entry point; it locates Python first):
  bash scripts/unity-preflight.sh --project "$WORKTREE" --platform Android
  eval "$(bash scripts/unity-preflight.sh --project "$WORKTREE" --platform Android)"
  "$UNITY_EDITOR" ...
  python3 scripts/common/unity_preflight.py ...   (same arguments, direct)

--project may be the Unity project root or a directory with exactly one Unity
project up to two levels below it (e.g. a worktree root).

Output: machine-readable result on stdout (--format env|json|github-actions),
human report on stderr.

Exit codes:
  0 READY                       3 prerequisite missing (CLI, Xcode, override mismatch,
  1 install or verify failed      module not offered for this editor)
  2 usage / project error       4 not ready (--check)
                                5 install lock timeout

See docs/UNITY_PREFLIGHT.md.
"""

import argparse
import json
import os
import platform as host_platform
import re
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Set, Tuple

# -- Constants ----------------------------------------------------------------

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_PREREQ = 3
EXIT_NOT_READY = 4
EXIT_LOCK_TIMEOUT = 5

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+[abfpx]\d+$")
REVISION_RE = re.compile(r"^(\S+)\s*\(([0-9a-f]{12})\)$")

# Toolkit platform names (resolve_build_flow.sh VALID_PLATFORMS, PLATFORM_MATRIX.md).
CANONICAL_PLATFORMS = ("Android", "iOS", "WebGL", "Linux64", "LinuxServer", "Windows64")
PLATFORM_ALIASES = {
    "android": "Android",
    "ios": "iOS",
    "webgl": "WebGL",
    "web": "WebGL",
    "linux64": "Linux64",
    "linux": "Linux64",
    "standalonelinux64": "Linux64",
    "linuxserver": "LinuxServer",
    "windows64": "Windows64",
    "windows": "Windows64",
    "standalonewindows64": "Windows64",
}

HOST_OSES = ("windows", "darwin", "linux")

# Unity always ships Mono support for the platform the editor itself runs on;
# the CLIs do not offer it as a module.
HOST_NATIVE_MONO_MODULE = {
    "windows": "windows-mono",
    "darwin": "mac-mono",
    "linux": "linux-mono",
}

UNITY_CLI_INSTALL_HINT = (
    "  Unity CLI (preferred):\n"
    "    macOS / Linux: curl -fsSL https://unity.com/install.sh | UNITY_CLI_CHANNEL=beta bash\n"
    "    Windows:       $env:UNITY_CLI_CHANNEL='beta'; irm https://unity.com/install.ps1 | iex\n"
    "    then open a new shell and check: unity --version\n"
    "  Unity Hub (fallback): install from https://unity.com/download and pass\n"
    "    --unity-hub <path> (or UNITY_HUB) if it is not in the default location.\n"
    "  To use an editor you installed yourself, set UNITY_EDITOR=<path to the executable>."
)

QUERY_TIMEOUT = 600  # seconds; installs have no timeout
LOCK_FILE_NAME = "unity-install.lock"
LOCK_OWNER_FILE_NAME = "unity-install.owner.json"
LOCK_LOG_INTERVAL = 30.0


# -- Errors and logging -------------------------------------------------------

class PreflightError(Exception):
    """A failure with an actionable message and a defined exit code."""

    def __init__(self, message: str, exit_code: int = EXIT_FAILED) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def _log(message: str) -> None:
    print(f"[unity_preflight] {message}", file=sys.stderr, flush=True)


def _warn(message: str, warnings: List[str]) -> None:
    warnings.append(message)
    print(f"WARNING: {message}", file=sys.stderr, flush=True)


# -- Project inspection -------------------------------------------------------

PROJECT_VERSION_FILE = Path("ProjectSettings", "ProjectVersion.txt")
PROJECT_SEARCH_DEPTH = 2
PROJECT_SEARCH_SKIP = {"Library", "Temp", "Logs", "obj", "Build", "Builds", "node_modules"}


def locate_project(start: Path) -> Path:
    """The Unity project at `start`, or the single one up to two levels below it.

    Repositories often keep the Unity project in a subfolder (repo/MyGame/
    ProjectSettings), and an agent starts at the worktree root. More than one
    candidate is an error: preflight never guesses which project is meant.
    """
    if (start / PROJECT_VERSION_FILE).is_file():
        return start
    found: List[Path] = []
    level = [start]
    for _ in range(PROJECT_SEARCH_DEPTH):
        next_level: List[Path] = []
        for directory in level:
            try:
                children = sorted(p for p in directory.iterdir() if p.is_dir())
            except OSError:
                continue
            for child in children:
                if child.name.startswith(".") or child.name in PROJECT_SEARCH_SKIP:
                    continue
                if (child / PROJECT_VERSION_FILE).is_file():
                    found.append(child)
                else:
                    next_level.append(child)
        level = next_level
    if len(found) == 1:
        _log(f"Detected Unity project: {found[0]}")
        return found[0]
    if len(found) > 1:
        raise PreflightError(
            f"Several Unity projects were found under\n  {start}\n\n"
            + "\n".join(f"  {p}" for p in found)
            + "\n\nPass --project with the one to prepare.",
            EXIT_USAGE,
        )
    return start


def read_project_version(project: Path) -> Tuple[str, str]:
    """Return (editor_version, changeset) from ProjectSettings/ProjectVersion.txt.

    changeset is '' when m_EditorVersionWithRevision is absent or inconsistent.
    """
    version_file = project / "ProjectSettings" / "ProjectVersion.txt"
    if not version_file.is_file():
        raise PreflightError(
            "Unity project version could not be determined.\n\n"
            f"Expected:\n  {version_file}\n\n"
            f"Project:\n  {project}\n\n"
            "No Unity project was found there or up to two folders below it.\n"
            "Pass --project with the Unity project root -- the folder that contains\n"
            "Assets/ and ProjectSettings/.",
            EXIT_USAGE,
        )
    version = ""
    revision = ""
    raw_version_line = ""
    with open(version_file, encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if line.startswith("m_EditorVersion:"):
                raw_version_line = line
                version = line.split(":", 1)[1].strip()
            elif line.startswith("m_EditorVersionWithRevision:"):
                revision = line.split(":", 1)[1].strip()
    if not VERSION_RE.match(version):
        found = raw_version_line or "(no m_EditorVersion line)"
        raise PreflightError(
            f"Unity project version in {version_file} is missing or malformed.\n"
            f"  Found:    {found}\n"
            "  Expected: m_EditorVersion: <major>.<minor>.<patch><a|b|f|p|x><n>, e.g. 6000.0.26f1",
            EXIT_USAGE,
        )
    changeset = ""
    match = REVISION_RE.match(revision)
    if match and match.group(1) == version:
        changeset = match.group(2)
    return version, changeset


def read_standalone_il2cpp(project: Path) -> bool:
    """True when ProjectSettings.asset selects IL2CPP for Standalone targets.

    Unity stores `scriptingBackend:` as a map of build-target group -> backend
    (0 = Mono, 1 = IL2CPP). An absent entry means Unity's default, Mono.
    """
    asset = project / "ProjectSettings" / "ProjectSettings.asset"
    if not asset.is_file():
        return False
    in_block = False
    block_indent = 0
    with open(asset, encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.rstrip("\r\n")
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            if not in_block:
                if stripped == "scriptingBackend:":
                    in_block = True
                    block_indent = indent
                continue
            if not stripped or indent <= block_indent:
                break
            if stripped.startswith("Standalone:"):
                return stripped.split(":", 1)[1].strip() == "1"
    return False


def resolve_platforms(values: Sequence[str]) -> List[str]:
    """Map --platform values (any case, common aliases) to toolkit names."""
    result: List[str] = []
    for value in values:
        for item in value.split(","):
            item = item.strip()
            if not item:
                continue
            canonical = PLATFORM_ALIASES.get(item.lower())
            if canonical is None:
                raise PreflightError(
                    f"Unknown platform '{item}'. Valid platforms: "
                    + ", ".join(CANONICAL_PLATFORMS)
                    + " (case-insensitive).",
                    EXIT_USAGE,
                )
            if canonical not in result:
                result.append(canonical)
    return result


# -- Platform -> module requirements ------------------------------------------

class Requirement(NamedTuple):
    platform: str
    module: str          # exact module id, or id prefix when is_prefix
    is_prefix: bool = False

    def satisfied_by(self, ids: Set[str]) -> bool:
        if self.is_prefix:
            return any(i.startswith(self.module) for i in ids)
        return self.module in ids

    def label(self) -> str:
        return f"{self.module}*" if self.is_prefix else self.module


def required_modules(platform: str, il2cpp: bool, host_os: str) -> List[Requirement]:
    """Unity CLI module ids a platform needs on this host."""
    if platform == "Android":
        # `android` + child modules pulls in the SDK/NDK tools and OpenJDK; the
        # OpenJDK id is versioned (e.g. android-open-jdk-17.0.9+9), hence a prefix.
        reqs = [
            Requirement(platform, "android"),
            Requirement(platform, "android-sdk-ndk-tools"),
            Requirement(platform, "android-open-jdk", True),
        ]
    elif platform == "iOS":
        reqs = [Requirement(platform, "ios")]
    elif platform == "WebGL":
        reqs = [Requirement(platform, "webgl")]
    elif platform == "Linux64":
        reqs = [Requirement(platform, "linux-il2cpp" if il2cpp else "linux-mono")]
    elif platform == "LinuxServer":
        reqs = [Requirement(platform, "linux-server")]
    elif platform == "Windows64":
        reqs = [Requirement(platform, "windows-il2cpp" if il2cpp else "windows-mono")]
    else:
        raise PreflightError(f"No module mapping for platform '{platform}'.", EXIT_USAGE)
    builtin = HOST_NATIVE_MONO_MODULE.get(host_os)
    return [r for r in reqs if r.module != builtin]


def missing_requirements(reqs: Sequence[Requirement], installed: Set[str]) -> List[Requirement]:
    return [r for r in reqs if not r.satisfied_by(installed)]


def resolve_module_ids(reqs: Sequence[Requirement], available: Optional[Set[str]],
                       version: str) -> List[str]:
    """Concrete module ids to request for missing requirements.

    Prefix requirements resolve to the real (versioned) id offered for this
    editor. Without an availability list, prefix requirements are left to the
    CLI's child-module resolution and checked again after install.
    """
    ids: List[str] = []
    for req in reqs:
        if req.is_prefix:
            if available is None:
                continue
            candidates = sorted(i for i in available if i.startswith(req.module))
            if not candidates:
                raise _module_unavailable(req, version)
            chosen = candidates[-1]
        else:
            if available is not None and req.module not in available:
                raise _module_unavailable(req, version)
            chosen = req.module
        if chosen not in ids:
            ids.append(chosen)
    return ids


def _module_unavailable(req: Requirement, version: str) -> PreflightError:
    return PreflightError(
        f"Unity:\n  {version}\n\nRequired platform:\n  {req.platform}\n\n"
        f"Required module:\n  {req.label()}\n\n"
        "The Unity CLI does not offer this module for this editor on this host,\n"
        "so it cannot be installed here. Use a host that supports the platform.",
        EXIT_PREREQ,
    )


# -- CLI invocation -----------------------------------------------------------

class CliResult(NamedTuple):
    argv: List[str]
    returncode: int
    stdout: str
    stderr: str
    payload: Any


def extract_json(text: str) -> Any:
    """First JSON document in text that starts at the beginning of a line.

    Both CLIs may print progress lines (e.g. "Adding module ...") or a blank
    line before the JSON, so the whole output is not parsed directly.
    """
    decoder = json.JSONDecoder()
    for match in re.finditer(r"(?m)^[ \t]*[\[{]", text):
        start = match.end() - 1
        try:
            value, _ = decoder.raw_decode(text, start)
            return value
        except ValueError:
            continue
    return None


HEARTBEAT_SECONDS = 60


def _kill_tree(proc: "subprocess.Popen") -> None:
    """Kill a CLI and everything it started.

    Killing only the direct child is not enough: on Windows the Unity CLI's
    helpers inherit its output pipes, keep them open, and the read never ends —
    a "600 s" query hung a CI job for two hours.
    """
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        try:
            os.killpg(proc.pid, 9)
        except (OSError, AttributeError):
            proc.kill()


def _run(argv: List[str], timeout: Optional[float], extra_env: Optional[Dict[str, str]] = None,
         stream: bool = False) -> CliResult:
    """Run a CLI command; never wait on the console and never outlive `timeout`.

    stdin is closed, so a CLI that would prompt (first-run terms, login) reads
    EOF instead of waiting forever. On timeout the whole process tree is
    killed. With `stream`, output is echoed as it arrives and a heartbeat is
    logged every HEARTBEAT_SECONDS, so a long install shows that it is alive.
    """
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    _log(f"$ {_format_argv(argv)}")
    popen_kwargs: Dict[str, Any] = {}
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True  # its own group, for _kill_tree
    try:
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            **popen_kwargs,
        )
    except FileNotFoundError as exc:
        raise PreflightError(f"Cannot execute {argv[0]}: {exc}", EXIT_PREREQ)

    out: List[str] = []
    err: List[str] = []

    def pump(pipe, sink: List[str]) -> None:
        for line in iter(pipe.readline, ""):
            sink.append(line)
            if stream:
                sys.stderr.write(f"  | {line}" if line.endswith("\n") else f"  | {line}\n")
                sys.stderr.flush()
        pipe.close()

    readers = [threading.Thread(target=pump, args=(proc.stdout, out), daemon=True),
               threading.Thread(target=pump, args=(proc.stderr, err), daemon=True)]
    for reader in readers:
        reader.start()

    started = time.monotonic()
    next_beat = started + HEARTBEAT_SECONDS
    while proc.poll() is None:
        now = time.monotonic()
        if timeout is not None and now - started > timeout:
            _kill_tree(proc)
            for reader in readers:
                reader.join(5)
            raise PreflightError(
                f"Command timed out after {int(timeout)}s and was stopped: {_format_argv(argv)}\n"
                f"Last output:\n{_tail(''.join(out + err), 20)}", EXIT_FAILED)
        if stream and now >= next_beat:
            _log(f"... still running ({int((now - started) // 60)} min)")
            next_beat = now + HEARTBEAT_SECONDS
        time.sleep(0.2)
    for reader in readers:
        reader.join(10)
    stdout, stderr = "".join(out), "".join(err)
    return CliResult(argv, proc.returncode, stdout, stderr, extract_json(stdout))


def _format_argv(argv: Sequence[str]) -> str:
    return " ".join(shlex.quote(a) for a in argv)


def _tail(text: str, lines: int = 40) -> str:
    parts = text.strip().splitlines()
    return "\n".join(parts[-lines:])


def _envelope_ok(result: CliResult) -> bool:
    payload = result.payload
    if isinstance(payload, dict) and "success" in payload:
        return result.returncode == 0 and payload.get("success") is True
    return result.returncode == 0


def describe_cli_failure(action: str, result: CliResult, context: List[str]) -> str:
    lines = [f"{action} failed.", ""]
    lines.extend(context)
    lines.append(f"Command:\n  {_format_argv(result.argv)}")
    lines.append(f"Exit code:\n  {result.returncode}")
    payload = result.payload
    if isinstance(payload, dict):
        for err in payload.get("errors") or []:
            if isinstance(err, dict):
                lines.append(f"CLI error [{err.get('code', '?')}]:\n  {err.get('message', '')}")
    out = _tail(result.stdout)
    err_text = _tail(result.stderr)
    if out:
        lines.append("CLI output:\n" + out)
    if err_text:
        lines.append("CLI stderr:\n" + err_text)
    return "\n".join(lines)


# -- Backends -----------------------------------------------------------------

class EditorInstall(NamedTuple):
    version: str
    architecture: str
    executable: str


def _editor_executable(location: str) -> str:
    """Executable path from a CLI-reported location (macOS reports the .app bundle)."""
    if location.endswith(".app"):
        return str(Path(location, "Contents", "MacOS", "Unity"))
    return location


class UnityCliBackend:
    """The standalone Unity CLI (`unity`), driven through its --json envelopes.

    Every argv for this CLI is built in this class; syntax and JSON shapes were
    checked against `unity` 1.0.0-beta.11. Update here if the CLI changes.
    """

    name = "unity"

    def __init__(self, executable: str) -> None:
        self.executable = executable

    def _argv(self, *args: str) -> List[str]:
        return [self.executable, "--no-banner", "--non-interactive", *args, "--json"]

    def _query(self, *args: str) -> CliResult:
        return _run(self._argv(*args), QUERY_TIMEOUT, {"UNITY_NO_PAGER": "1"})

    def installed_editors(self) -> List[EditorInstall]:
        result = self._query("editors", "-i")
        if not _envelope_ok(result) or not isinstance(result.payload, dict):
            raise PreflightError(
                describe_cli_failure("Listing installed editors", result, []), EXIT_FAILED
            )
        editors = []
        for item in result.payload.get("data") or []:
            if isinstance(item, dict) and item.get("version") and item.get("location"):
                editors.append(EditorInstall(
                    str(item["version"]), str(item.get("architecture") or ""),
                    _editor_executable(str(item["location"])),
                ))
        return editors

    def module_state(self, version: str) -> Tuple[Set[str], Optional[Set[str]]]:
        result = self._query("install-modules", "-e", version, "--list")
        if not _envelope_ok(result) or not isinstance(result.payload, dict):
            raise PreflightError(
                describe_cli_failure(f"Listing modules of Unity {version}", result, []), EXIT_FAILED
            )
        installed: Set[str] = set()
        available: Set[str] = set()
        for item in result.payload.get("data") or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            module_id = str(item["id"])
            available.add(module_id)
            if str(item.get("status", "")).lower() == "installed":
                installed.add(module_id)
        return installed, available

    def install_editor_argv(self, version: str, changeset: str, modules: Sequence[str]) -> List[str]:
        args = ["install", version]
        if changeset:
            args += ["--changeset", changeset]
        args += ["--child-modules", "--accept-eula", "--yes"]
        if modules:
            args += ["-m", *modules]
        return self._argv(*args)

    def install_modules_argv(self, version: str, modules: Sequence[str]) -> List[str]:
        return self._argv("install-modules", "-e", version, "--child-modules",
                          "--accept-eula", "--yes", "-m", *modules)

    def get_install_root(self) -> str:
        result = self._query("install-path", "--get")
        payload = result.payload if isinstance(result.payload, dict) else {}
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if not _envelope_ok(result) or not data.get("path"):
            raise PreflightError(describe_cli_failure("Reading the editor install path", result, []),
                                 EXIT_FAILED)
        return str(data["path"])

    def set_install_root(self, path: str) -> CliResult:
        return self._query("install-path", "--set", path)

    def register_editor(self, application: str) -> bool:
        """`editors add` an installed but unregistered editor (Unity.exe / Unity.app)."""
        result = self._query("editors", "add", application)
        if _envelope_ok(result):
            return True
        payload = result.payload if isinstance(result.payload, dict) else {}
        codes = {e.get("code") for e in payload.get("errors") or [] if isinstance(e, dict)}
        return codes == {"EDITORS_ADD_ALREADY_IN_LIST"}

    def verify(self, version: str) -> Tuple[bool, str]:
        result = self._query("editors", "verify", version)
        payload = result.payload if isinstance(result.payload, dict) else {}
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        if _envelope_ok(result) and data.get("ok") is True:
            return True, ""
        bad = [
            f"{c.get('component')}: {c.get('status')}"
            for c in data.get("components") or []
            if isinstance(c, dict) and c.get("status") not in ("ok", "skipped")
        ]
        detail = "; ".join(bad) or describe_cli_failure("Editor verification", result, [])
        return False, detail


class HubBackend:
    """The Unity Hub headless CLI (`Unity Hub -- --headless ...`).

    The Hub has no module listing or verify command; installed modules are
    read from the modules.json the Hub keeps in each editor folder
    (`selected: true` = installed). Its exit codes are not relied on: every
    install is followed by re-detection. Syntax checked against Unity Hub 3.21.3.
    """

    name = "hub"

    def __init__(self, executable: str) -> None:
        self.executable = executable

    def _argv(self, *args: str) -> List[str]:
        return [self.executable, "--", "--headless", *args]

    def installed_editors(self) -> List[EditorInstall]:
        result = _run(self._argv("editors", "-i", "-j"), QUERY_TIMEOUT)
        payload = result.payload
        if isinstance(payload, dict):
            payload = payload.get("data")
        if result.returncode != 0 or not isinstance(payload, list):
            raise PreflightError(
                describe_cli_failure("Listing installed editors (Unity Hub)", result, []), EXIT_FAILED
            )
        editors = []
        for item in payload:
            if isinstance(item, dict) and item.get("version") and item.get("location"):
                editors.append(EditorInstall(
                    str(item["version"]), str(item.get("architecture") or ""),
                    _editor_executable(str(item["location"])),
                ))
        return editors

    @staticmethod
    def _modules_file(executable: str) -> Optional[Path]:
        current = Path(executable).parent
        for _ in range(5):
            candidate = current / "modules.json"
            if candidate.is_file():
                return candidate
            if current.parent == current:
                break
            current = current.parent
        return None

    def module_state_for(self, editor: EditorInstall) -> Tuple[Set[str], Optional[Set[str]]]:
        modules_file = self._modules_file(editor.executable)
        if modules_file is None:
            return set(), None
        try:
            with open(modules_file, encoding="utf-8") as handle:
                entries = json.load(handle)
        except (OSError, ValueError):
            return set(), None
        installed: Set[str] = set()
        available: Set[str] = set()
        for item in entries if isinstance(entries, list) else []:
            if isinstance(item, dict) and item.get("id"):
                available.add(str(item["id"]))
                if item.get("selected") is True:
                    installed.add(str(item["id"]))
        return installed, available

    def module_state(self, version: str) -> Tuple[Set[str], Optional[Set[str]]]:
        for editor in self.installed_editors():
            if editor.version == version:
                return self.module_state_for(editor)
        return set(), None

    def install_editor_argv(self, version: str, changeset: str, modules: Sequence[str]) -> List[str]:
        args = ["install", "--version", version]
        if changeset:
            args += ["--changeset", changeset]
        args.append("--childModules")
        if modules:
            args += ["-m", *modules]
        return self._argv(*args)

    def install_modules_argv(self, version: str, modules: Sequence[str]) -> List[str]:
        return self._argv("install-modules", "--version", version, "--childModules", "-m", *modules)

    def get_install_root(self) -> str:
        result = _run(self._argv("ip", "-g"), QUERY_TIMEOUT)
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if result.returncode != 0 or not lines:
            raise PreflightError(
                describe_cli_failure("Reading the editor install path (Unity Hub)", result, []),
                EXIT_FAILED)
        return lines[-1]

    def set_install_root(self, path: str) -> CliResult:
        return _run(self._argv("ip", "-s", path), QUERY_TIMEOUT)

    def verify(self, version: str) -> Tuple[bool, str]:
        # No verify command in the Hub CLI; re-detection plus the executable
        # check in the caller is the verification.
        return True, ""


def _host_os() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def _host_arch() -> str:
    machine = host_platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("x86_64", "amd64", "x64"):
        return "x86_64"
    return machine


def _resolve_explicit(path: str, label: str) -> str:
    candidate = Path(path)
    if candidate.is_file():
        return str(candidate)
    found = shutil.which(path)
    if found:
        return found
    raise PreflightError(
        f"{label} '{path}' does not exist.\n\nInstall a Unity CLI:\n{UNITY_CLI_INSTALL_HINT}",
        EXIT_PREREQ,
    )


def _cli_in_home(cli_home: Optional[str], host_os: str) -> Path:
    name = "unity.exe" if host_os == "windows" else "unity"
    return Path(cli_home or "", "bin", name)


def find_unity_cli(explicit: Optional[str], host_os: str, cli_home: Optional[str] = None) -> Optional[str]:
    if explicit:
        return _resolve_explicit(explicit, "Unity CLI")
    if cli_home and _cli_in_home(cli_home, host_os).is_file():
        return str(_cli_in_home(cli_home, host_os))
    found = shutil.which("unity")
    if found:
        return found
    if host_os == "windows" and os.environ.get("LOCALAPPDATA"):
        candidate = Path(os.environ["LOCALAPPDATA"], "Unity", "bin", "unity.exe")
        if candidate.is_file():
            return str(candidate)
    return None


def find_unity_hub(explicit: Optional[str], host_os: str) -> Optional[str]:
    if explicit:
        return _resolve_explicit(explicit, "Unity Hub")
    candidates: List[Path] = []
    if host_os == "windows":
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        candidates.append(Path(program_files, "Unity Hub", "Unity Hub.exe"))
    elif host_os == "darwin":
        candidates.append(Path("/Applications", "Unity Hub.app", "Contents", "MacOS", "Unity Hub"))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which("unityhub")


def select_backend(choice: str, unity_cli: Optional[str], unity_hub: Optional[str], host_os: str,
                   cli_home: Optional[str] = None):
    """Return a backend, or None when no CLI is available."""
    if choice in ("auto", "unity"):
        path = find_unity_cli(unity_cli, host_os, cli_home)
        if path:
            return UnityCliBackend(path)
        if choice == "unity" or cli_home:
            # With a CLI home the caller bootstraps the Unity CLI rather than
            # falling back to whatever Hub happens to be installed.
            return None
    path = find_unity_hub(unity_hub, host_os)
    if path:
        return HubBackend(path)
    return None


# Unity's official installer for the standalone CLI (the commands its own
# documentation gives). UNITY_CLI_HOME makes the binary land in <home>/bin.
UNITY_CLI_INSTALLER = {
    "windows": ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-Command", "irm https://unity.com/install.ps1 | iex"],
    "posix": ["bash", "-c", "set -o pipefail; curl -fsSL https://unity.com/install.sh | bash"],
}


def bootstrap_unity_cli(cli_home: str, host_os: str) -> str:
    """Install the Unity CLI into cli_home with Unity's installer; return the binary."""
    argv = UNITY_CLI_INSTALLER["windows" if host_os == "windows" else "posix"]
    _log(f"Unity CLI not found; installing it into {cli_home} with Unity's installer ...")
    result = _run(argv, QUERY_TIMEOUT, {"UNITY_CLI_CHANNEL": "beta", "UNITY_CLI_HOME": cli_home})
    binary = _cli_in_home(cli_home, host_os)
    if result.returncode != 0 or not binary.is_file():
        raise PreflightError(
            describe_cli_failure("Installing the Unity CLI", result,
                                 [f"Expected binary:\n  {binary}"])
            + "\n\nInstall it on the runner manually:\n" + UNITY_CLI_INSTALL_HINT,
            EXIT_PREREQ,
        )
    _log(f"Unity CLI installed: {binary}")
    return str(binary)


def _nearest_existing(path: Path) -> Path:
    current = path
    while not current.exists() and current.parent != current:
        current = current.parent
    return current


def _write_error(root: str) -> Optional[str]:
    """Why this account cannot create files under root, or None if it can."""
    try:
        with tempfile.TemporaryFile(dir=str(_nearest_existing(Path(root)))):
            pass
    except OSError as exc:
        return str(exc)
    return None


def assert_install_root_writable(root: str, reason: str) -> None:
    """Fail before installing when the install root is not writable without elevation."""
    error = _write_error(root)
    if error:
        raise PreflightError(
            f"The editor install path is not writable by this account:\n  {root}\n"
            f"({error})\n\n{reason}\n"
            "Point installs at a directory this account owns: set UNITY_PREFLIGHT_INSTALL_ROOT\n"
            "(or pass --install-root), e.g. in the self-hosted runner's .env file.",
            EXIT_PREREQ,
        )


def editor_application(root: str, version: str, host_os: str) -> Path:
    """Where the CLI/Hub layout puts an editor of `version` under an install root.

    This is the application `editors add` accepts: Unity.app on macOS,
    Editor/Unity.exe on Windows, Editor/Unity on Linux.
    """
    if host_os == "darwin":
        return Path(root, version, "Unity.app")
    return Path(root, version, "Editor", "Unity.exe" if host_os == "windows" else "Unity")


def editor_home(executable: str, version: str) -> Path:
    """The per-version editor directory an executable belongs to (modules go there)."""
    path = Path(executable)
    for parent in path.parents:
        if parent.name == version:
            return parent
    return path.parent


def standard_editor_locations(version: str, host_os: str) -> List[Path]:
    """Where this toolkit's self-hosted lanes have always looked for an editor.

    These are the Unity Hub default install roots and the legacy per-version
    folders that reusable-build-platform.yml searched before preflight existed
    (and that SELF_HOSTED_WINDOWS_RUNNER.md / SELF_HOSTED_MACOS_RUNNER.md tell
    runner owners to install into). Built from parts: see the native-invocation
    guard test.
    """
    if host_os == "windows":
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        return [editor_application(str(Path(program_files, "Unity", "Hub", "Editor")), version, host_os),
                Path(program_files, f"Unity {version}", "Editor", "Unity.exe")]
    if host_os == "darwin":
        return [editor_application(str(Path("/Applications", "Unity", "Hub", "Editor")), version, host_os),
                Path("/Applications", f"Unity {version}", "Unity.app")]
    return []


def hub_config_dirs(host_os: str) -> List[Path]:
    """Unity Hub's per-account configuration directories on this machine.

    Every account, not only the one running preflight: a runner service has
    its own (empty) profile, while the editor was installed with Unity Hub by
    a person. UNITY_PREFLIGHT_HUB_CONFIG_DIRS (os.pathsep-separated) replaces
    the list.
    """
    override = os.environ.get("UNITY_PREFLIGHT_HUB_CONFIG_DIRS")
    if override is not None:
        return [Path(p) for p in override.split(os.pathsep) if p]
    dirs: List[Path] = []
    if host_os == "windows":
        if os.environ.get("APPDATA"):
            dirs.append(Path(os.environ["APPDATA"], "UnityHub"))
        users = Path(os.environ.get("SystemDrive", "C:") + "\\", "Users")
        try:
            dirs.extend(sorted(p / "AppData" / "Roaming" / "UnityHub" for p in users.iterdir()))
        except OSError:
            pass
    elif host_os == "darwin":
        dirs.append(Path.home() / "Library" / "Application Support" / "UnityHub")
        try:
            dirs.extend(sorted(p / "Library" / "Application Support" / "UnityHub"
                               for p in Path("/Users").iterdir()))
        except OSError:
            pass
    else:
        dirs.append(Path.home() / ".config" / "UnityHub")
    seen: List[Path] = []
    for d in dirs:
        if not any(_same_path(str(d), str(s)) for s in seen):
            seen.append(d)
    return seen


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None  # missing, unreadable by this account, or not JSON


def _locations_for(node: Any, version: str) -> List[str]:
    """Editor locations of `version` anywhere in a Hub editors file (any schema)."""
    found: List[str] = []
    if isinstance(node, dict):
        if str(node.get("version", "")) == version and node.get("location"):
            loc = node["location"]
            found.extend(str(x) for x in (loc if isinstance(loc, list) else [loc]))
        for value in node.values():
            found.extend(_locations_for(value, version))
    elif isinstance(node, list):
        for value in node:
            found.extend(_locations_for(value, version))
    return found


def hub_reported_editors(version: str, host_os: str) -> List[Path]:
    """Editor applications Unity Hub itself reports for `version`.

    Asks Hub's own records instead of guessing directories: its custom editor
    install location (secondaryInstallPath.json) and the editors it installed
    or was pointed at (editors-v2.json, editors.json). Each result is still
    registered and version-checked by the CLI before it is used.
    """
    apps: List[Path] = []
    for config in hub_config_dirs(host_os):
        root = _read_json(config / "secondaryInstallPath.json")
        if isinstance(root, str) and root.strip():
            apps.append(editor_application(root.strip(), version, host_os))
        for name in ("editors-v2.json", "editors.json"):
            for location in _locations_for(_read_json(config / name), version):
                apps.append(Path(_application_of(location)) if host_os == "darwin"
                             else Path(location))
    return apps


def editor_candidates(backend, version: str, host_os: str,
                      roots: Sequence[Optional[str]]) -> List[Path]:
    """Existing editor applications of `version` on disk, in discovery order.

    Order: the given roots (explicit install root, then the runner-managed
    fallback root), the CLI's configured install path, the editors Unity Hub
    reports for any account on the machine, then the standard
    locations above. Only exact `<root>/<version>` layouts are considered; the
    version is confirmed by the CLI after registration, never trusted from the
    folder name alone.
    """
    ordered: List[Path] = [editor_application(r, version, host_os) for r in roots if r]
    if isinstance(backend, UnityCliBackend):
        try:
            ordered.append(editor_application(backend.get_install_root(), version, host_os))
        except PreflightError:
            pass
    ordered.extend(hub_reported_editors(version, host_os))
    ordered.extend(standard_editor_locations(version, host_os))
    found: List[Path] = []
    for candidate in ordered:
        if candidate.exists() and not any(_same_path(str(candidate), str(f)) for f in found):
            found.append(candidate)
    return found


def _application_of(executable: str) -> str:
    """The application `editors add` accepts for an executable (the .app on macOS)."""
    for parent in Path(executable).parents:
        if parent.suffix == ".app":
            return str(parent)
    return executable


def discover_editor(backend, version: str, host_os: str, host_arch: str,
                    roots: Sequence[Optional[str]], register: bool):
    """Find an installed editor of exactly `version`, registering it if needed.

    Returns (editor or None, unregistered candidates found). The CLI's
    registry only lists editors registered to the account running it, so an
    editor installed by another account (an admin with Unity Hub, an earlier
    runner account) is invisible to it. Such an editor is registered with
    `editors add` and then re-read from the registry, which confirms its real
    version; a mismatched candidate is simply not selected.
    """
    editor = _find_editor(backend, version, host_arch)
    if editor is not None or not isinstance(backend, UnityCliBackend):
        return editor, []
    candidates = editor_candidates(backend, version, host_os, roots)
    if not register:
        return None, candidates
    for candidate in candidates:
        _log(f"Found Unity {version} on disk, not registered with the Unity CLI: {candidate}")
        if not backend.register_editor(str(candidate)):
            _log(f"  the Unity CLI did not accept {candidate}; skipping it")
            continue
        editor = _find_editor(backend, version, host_arch)
        if editor is not None:
            _log(f"  registered; reusing it")
            return editor, candidates
        _log(f"  registered, but it is not Unity {version}; skipping it")
    return None, candidates


# -- Machine-wide install lock ------------------------------------------------

def default_lock_dir(host_os: str) -> Path:
    if host_os == "windows":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base, "unity-build-workflows", "locks")
    if host_os == "darwin":
        return Path.home() / "Library" / "Caches" / "unity-build-workflows" / "locks"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base, "unity-build-workflows", "locks")


class InstallLock:
    """Exclusive OS file lock serialising installs across processes.

    fcntl.flock (POSIX) / msvcrt.locking (Windows) locks are released by the
    kernel when the holder exits or is killed, so an interrupted install never
    leaves a stale lock behind.
    """

    def __init__(self, lock_dir: Path, timeout: float, owner: Dict[str, Any],
                 poll_interval: float = 1.0) -> None:
        self.lock_dir = lock_dir
        self.path = lock_dir / LOCK_FILE_NAME
        self.owner_path = lock_dir / LOCK_OWNER_FILE_NAME
        self.timeout = timeout
        self.owner = owner
        self.poll_interval = poll_interval
        self._handle = None

    def _try_acquire(self) -> bool:
        if os.name == "nt":
            import msvcrt
            self._handle.seek(0)
            try:
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                return False
        import fcntl
        try:
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _release(self) -> None:
        if os.name == "nt":
            import msvcrt
            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)

    def _read_owner(self) -> str:
        try:
            with open(self.owner_path, encoding="utf-8") as handle:
                info = json.load(handle)
            return (f"pid {info.get('pid')} on {info.get('host')} installing Unity "
                    f"{info.get('version')} for {info.get('project')} since {info.get('started')}")
        except (OSError, ValueError):
            return "unknown holder"

    def __enter__(self) -> "InstallLock":
        try:
            self.lock_dir.mkdir(parents=True, exist_ok=True)
            self._handle = open(self.path, "a+b")
        except OSError as exc:
            raise PreflightError(
                f"Cannot create the install lock at {self.path}: {exc}\n"
                "Set UNITY_PREFLIGHT_LOCK_DIR (or --lock-dir) to a writable directory.",
                EXIT_PREREQ,
            )
        deadline = time.monotonic() + self.timeout
        next_log = 0.0
        while not self._try_acquire():
            now = time.monotonic()
            if now >= deadline:
                holder = self._read_owner()
                self._handle.close()
                raise PreflightError(
                    f"Timed out after {self.timeout:.0f}s waiting for the Unity install lock\n"
                    f"  {self.path}\nheld by {holder}.\n"
                    "Another preflight is still installing. Wait and re-run, or raise --lock-timeout.\n"
                    "The lock is released automatically when the holding process exits.",
                    EXIT_LOCK_TIMEOUT,
                )
            if now >= next_log:
                _log(f"Waiting for the install lock ({self._read_owner()})")
                next_log = now + LOCK_LOG_INTERVAL
            time.sleep(self.poll_interval)
        try:
            with open(self.owner_path, "w", encoding="utf-8") as handle:
                json.dump(self.owner, handle)
        except OSError:
            pass
        return self

    def __exit__(self, *exc_info: Any) -> None:
        try:
            os.remove(self.owner_path)
        except OSError:
            pass
        try:
            self._release()
        finally:
            self._handle.close()


# -- Preflight ----------------------------------------------------------------

def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def _find_editor(backend, version: str, host_arch: str) -> Optional[EditorInstall]:
    matches = [e for e in backend.installed_editors() if e.version == version]
    if not matches:
        return None
    for editor in matches:
        if editor.architecture == host_arch:
            return editor
    return matches[0]


def _module_state(backend, editor: EditorInstall) -> Tuple[Set[str], Optional[Set[str]]]:
    if isinstance(backend, HubBackend):
        return backend.module_state_for(editor)
    return backend.module_state(editor.version)


def _xcode_status(host_os: str, platforms: Sequence[str], warnings: List[str]) -> str:
    if "iOS" not in platforms:
        return ""
    if host_os != "darwin":
        # The module lets the editor switch to and compile for the iOS target,
        # but an iOS player build (Xcode archive, signing, IPA) is impossible
        # here; the toolkit builds iOS only on its macOS lane.
        _warn("iOS player builds are NOT possible on this host: they need macOS with "
              "Xcode. Only iOS Build Support for the editor is ensured here.", warnings)
        return "unsupported-host"
    xcode_select = shutil.which("xcode-select")
    if xcode_select:
        result = subprocess.run([xcode_select, "-p"], capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip():
            return "present"
    raise PreflightError(
        "Required platform:\n  iOS\n\nXcode was not found (`xcode-select -p` failed).\n"
        "Install Xcode from the App Store, then run:\n"
        "  sudo xcode-select -s /Applications/Xcode.app/Contents/Developer\n"
        "Preflight installs Unity iOS Build Support but never installs Xcode.",
        EXIT_PREREQ,
    )


def run_preflight(args: argparse.Namespace) -> Dict[str, Any]:
    host_os = args.host_os or _host_os()
    host_arch = _host_arch()
    warnings: List[str] = []
    project = locate_project(Path(args.project).resolve())

    version, changeset = read_project_version(project)
    platforms = resolve_platforms(args.platform or [])
    il2cpp = read_standalone_il2cpp(project) if {"Linux64", "Windows64"} & set(platforms) else False
    requirements: List[Requirement] = []
    for plat in platforms:
        requirements.extend(required_modules(plat, il2cpp, host_os))

    result: Dict[str, Any] = {
        "UNITY_READY": "false",
        "UNITY_PROJECT_PATH": str(project),
        "UNITY_VERSION": version,
        "UNITY_CHANGESET": changeset,
        "UNITY_EDITOR": "",
        "UNITY_EDITOR_SOURCE": "",
        "UNITY_VERSION_VERIFIED": "true",
        "UNITY_PLATFORMS": ",".join(platforms),
        "UNITY_MODULES": ",".join(r.label() for r in requirements),
        "UNITY_INSTALLED": "",
        "UNITY_CLI_BACKEND": "",
        "UNITY_XCODE": "",
        "warnings": warnings,
    }

    print(f"Project:\n  {project}\n", file=sys.stderr)
    revision = f" (changeset {changeset})" if changeset else ""
    print(f"Unity version:\n  {version}{revision}\n", file=sys.stderr)
    if platforms:
        print("Platform:\n  " + ", ".join(platforms) + "\n", file=sys.stderr)

    result["UNITY_XCODE"] = _xcode_status(host_os, platforms, warnings)

    cli_home = args.install_cli_to or os.environ.get("UNITY_PREFLIGHT_CLI_HOME") or None
    install_root = args.install_root or os.environ.get("UNITY_PREFLIGHT_INSTALL_ROOT") or None
    lock_dir = Path(args.lock_dir or os.environ.get("UNITY_PREFLIGHT_LOCK_DIR") or default_lock_dir(host_os))
    owner = {
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "project": str(project),
        "version": version,
        "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    backend = select_backend(args.cli, args.unity_cli, args.unity_hub, host_os, cli_home)
    if backend is None and cli_home and args.cli != "hub" and not args.check:
        with InstallLock(lock_dir, args.lock_timeout, owner):
            # Re-check: a concurrent preflight may have installed it meanwhile.
            cli_path = find_unity_cli(args.unity_cli, host_os, cli_home) \
                or bootstrap_unity_cli(cli_home, host_os)
        backend = UnityCliBackend(cli_path)

    override = args.unity_editor or os.environ.get("UNITY_EDITOR") or ""
    if override:
        if not Path(override).is_file():
            raise PreflightError(
                f"UNITY_EDITOR points to\n  {override}\nwhich does not exist.\n"
                "Fix the path, or unset UNITY_EDITOR so preflight resolves the editor "
                "from ProjectVersion.txt.",
                EXIT_PREREQ,
            )
        def registered_as(path: str):
            for candidate in backend.installed_editors():
                if _same_path(candidate.executable, path):
                    return candidate
            return None

        known = registered_as(override) if backend is not None else None
        if known is None and isinstance(backend, UnityCliBackend) and not args.check:
            # Unknown to this account's CLI: register it so the CLI reports its
            # real version (and modules) instead of trusting the path.
            if backend.register_editor(_application_of(override)):
                known = registered_as(override)
        if known is not None and known.version != version:
            raise PreflightError(
                f"UNITY_EDITOR is Unity {known.version}:\n  {override}\n"
                f"but the project requires Unity {version} (ProjectSettings/ProjectVersion.txt).\n"
                "Preflight does not replace an explicit editor. Unset UNITY_EDITOR, or point it "
                f"at a {version} installation.",
                EXIT_PREREQ,
            )
        if known is None:
            _warn(f"UNITY_EDITOR={override} is not registered with a Unity CLI; its version "
                  f"cannot be verified against {version} and its modules are not checked. "
                  "Nothing is installed.", warnings)
            result.update({
                "UNITY_READY": "true",
                "UNITY_EDITOR": override,
                "UNITY_EDITOR_SOURCE": "override",
                "UNITY_VERSION_VERIFIED": "false",
                "UNITY_CLI_BACKEND": "override",
            })
            _report_ready(result)
            return result

    if backend is None:
        wanted = {"auto": "a Unity CLI", "unity": "the Unity CLI (`unity`)",
                  "hub": "the Unity Hub"}[args.cli]
        raise PreflightError(
            f"Unity CLI not found: preflight needs {wanted} to detect and install editors.\n\n"
            f"Install it:\n{UNITY_CLI_INSTALL_HINT}\n\n"
            "Or pass --install-cli-to <dir> (UNITY_PREFLIGHT_CLI_HOME) to let preflight\n"
            "install the Unity CLI there with Unity's installer.",
            EXIT_PREREQ,
        )
    result["UNITY_CLI_BACKEND"] = backend.name
    _log(f"Using {backend.name} CLI: {backend.executable}")

    editor, on_disk = discover_editor(backend, version, host_os, host_arch,
                                      [install_root, args.fallback_install_root],
                                      register=not args.check)
    plan = _plan(backend, editor, version, changeset, requirements)
    if editor is not None:
        print("Editor:\n  available\n", file=sys.stderr)
    elif on_disk and args.check:
        print("Editor:\n  found on disk, not registered with the Unity CLI:\n"
              + "\n".join(f"    {c}" for c in on_disk)
              + "\n  (a normal run registers and verifies it before installing anything)\n",
              file=sys.stderr)
    else:
        print("Editor:\n  MISSING\n", file=sys.stderr)
    _report_modules(requirements, plan["installed_modules"])

    if plan["argv"] is None:
        return _finish(backend, version, editor, requirements, override, result, installed=[])

    if args.check:
        print("Environment:\n  NOT READY\n", file=sys.stderr)
        print("Preflight would run:\n  " + _format_argv(plan["argv"]) + "\n", file=sys.stderr)
        result["UNITY_EDITOR"] = editor.executable if editor else ""
        result["UNITY_EDITOR_SOURCE"] = "override" if override else ("existing" if editor else "")
        raise NotReady(result)

    with InstallLock(lock_dir, args.lock_timeout, owner):
        # Another process may have installed while this one waited.
        editor = _find_editor(backend, version, host_arch)
        plan = _plan(backend, editor, version, changeset, requirements)
        installed: List[str] = []
        if plan["argv"] is not None:
            if editor is None:
                prepare_install_root(backend, install_root, args.fallback_install_root)
            elif os.environ.get("UNITY_NO_ELEVATE"):
                # Modules land inside the existing editor's own directory.
                assert_install_root_writable(
                    str(editor_home(editor.executable, version)),
                    f"Unity {version} is installed here, but its missing modules "
                    f"({', '.join(plan['modules'])}) cannot be added without elevation.\n"
                    "Add them as the account that installed the editor, or remove that "
                    "editor so preflight installs a complete one in a writable root.")
            context = [
                f"Requested Unity version:\n  {version}",
                "Platforms:\n  " + (", ".join(platforms) or "(editor only)"),
                "Modules:\n  " + (", ".join(plan["modules"]) or "(none)"),
            ]
            if editor is None:
                _log(f"Installing Unity {version}"
                     + (f" with modules {', '.join(plan['modules'])}" if plan["modules"] else "")
                     + " ...")
                action = f"Installing Unity {version}"
            else:
                _log(f"Installing required modules: {', '.join(plan['modules'])} ...")
                action = f"Installing modules into Unity {version}"
            outcome = _run(plan["argv"], args.install_timeout or None, {"UNITY_NO_PAGER": "1"},
                           stream=True)
            if not _envelope_ok(outcome):
                raise PreflightError(describe_cli_failure(action, outcome, context), EXIT_FAILED)
            _log("Installation complete")
            if editor is None:
                installed.append("editor")
            installed.extend(plan["modules"])
        else:
            _log("Another preflight finished the installation while this one waited")
        editor = _find_editor(backend, version, host_arch)
        return _finish(backend, version, editor, requirements, override, result,
                       installed=installed, verify=bool(installed))


def prepare_install_root(backend, install_root: Optional[str],
                         fallback_root: Optional[str] = None) -> None:
    """Make sure the next editor install lands somewhere this account can write.

    The install root is a persisted per-user CLI setting (`install-path`); it is
    only changed when the caller asks for a root explicitly, and only right
    before an install. Without elevation (UNITY_NO_ELEVATE, as on CI runners) an
    unwritable root falls back to `fallback_root` when one is given, and is
    reported here otherwise instead of failing inside the installer.
    """
    if not install_root and fallback_root and os.environ.get("UNITY_NO_ELEVATE"):
        current = backend.get_install_root()
        error = _write_error(current)
        if error:
            _log(f"Install path {current} is not writable by this account ({error}); "
                 f"using the fallback install root {fallback_root}")
            install_root = fallback_root
    if install_root:
        try:
            Path(install_root).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PreflightError(f"Cannot create the editor install root {install_root}: {exc}",
                                 EXIT_PREREQ)
        assert_install_root_writable(install_root, "Requested install root.")
        current = backend.get_install_root()
        if not _same_path(current, install_root):
            _log(f"Setting the {backend.name} editor install path: {current} -> {install_root}")
            outcome = backend.set_install_root(install_root)
            if not _envelope_ok(outcome) or not _same_path(backend.get_install_root(), install_root):
                raise PreflightError(
                    describe_cli_failure("Setting the editor install path", outcome,
                                         [f"Requested install root:\n  {install_root}"]),
                    EXIT_FAILED)
    elif os.environ.get("UNITY_NO_ELEVATE"):
        assert_install_root_writable(
            backend.get_install_root(),
            "UNITY_NO_ELEVATE is set, so the installer will not ask for elevation.")


class NotReady(Exception):
    def __init__(self, result: Dict[str, Any]) -> None:
        super().__init__("not ready")
        self.result = result


def _plan(backend, editor: Optional[EditorInstall], version: str, changeset: str,
          requirements: Sequence[Requirement]) -> Dict[str, Any]:
    """What must be installed: argv is None when nothing is missing."""
    if editor is None:
        modules = []
        for req in requirements:
            if not req.is_prefix and req.module not in modules:
                modules.append(req.module)
        return {"argv": backend.install_editor_argv(version, changeset, modules),
                "modules": modules, "installed_modules": set()}
    if not requirements:
        return {"argv": None, "modules": [], "installed_modules": set()}
    installed, available = _module_state(backend, editor)
    missing = missing_requirements(requirements, installed)
    if not missing:
        return {"argv": None, "modules": [], "installed_modules": installed}
    modules = resolve_module_ids(missing, available, version)
    if not modules:
        # Only prefix requirements are missing and the CLI cannot list what is
        # offered: re-request their parents so child-module resolution adds them.
        modules = sorted({r.module for r in requirements
                          if not r.is_prefix and r.platform in {m.platform for m in missing}})
    return {"argv": backend.install_modules_argv(version, modules),
            "modules": modules, "installed_modules": installed}


def _finish(backend, version: str, editor: Optional[EditorInstall],
            requirements: Sequence[Requirement], override: str, result: Dict[str, Any],
            installed: List[str], verify: bool = False) -> Dict[str, Any]:
    if editor is None:
        raise PreflightError(
            f"Verification failed: the {backend.name} CLI reported success, but Unity {version} "
            "is not among the installed editors.\nRe-run preflight; if it persists, install "
            "the editor manually and check the CLI's logs.",
            EXIT_FAILED,
        )
    if not Path(editor.executable).is_file():
        raise PreflightError(
            f"Verification failed: Unity {version} is registered at\n  {editor.executable}\n"
            "but that executable does not exist. Repair it with the CLI (for the Unity CLI: "
            f"unity install {version} --force) or remove the stale registration.",
            EXIT_FAILED,
        )
    still_missing: List[Requirement] = []
    if requirements:
        modules_now, _ = _module_state(backend, editor)
        still_missing = missing_requirements(requirements, modules_now)
    if still_missing:
        raise PreflightError(
            f"Unity:\n  {version}\n\nRequired platform:\n  "
            + ", ".join(sorted({r.platform for r in still_missing}))
            + "\n\nRequired module:\n  " + ", ".join(r.label() for r in still_missing)
            + "\n\nThe Editor exists but the required platform support is missing"
            + (" after installation (verification failed)." if installed else "."),
            EXIT_FAILED,
        )
    if verify:
        ok, detail = backend.verify(version)
        if not ok:
            raise PreflightError(f"Verification failed for Unity {version}: {detail}", EXIT_FAILED)
    if override:
        source = "override"
        executable = override
    else:
        source = "installed" if "editor" in installed else "existing"
        executable = editor.executable
    result.update({
        "UNITY_READY": "true",
        "UNITY_EDITOR": executable,
        "UNITY_EDITOR_SOURCE": source,
        "UNITY_INSTALLED": ",".join(installed),
    })
    _report_ready(result)
    return result


def _report_modules(requirements: Sequence[Requirement], installed: Set[str]) -> None:
    for plat in dict.fromkeys(r.platform for r in requirements):
        reqs = [r for r in requirements if r.platform == plat]
        missing = missing_requirements(reqs, installed)
        state = "available" if not missing else "MISSING (" + ", ".join(r.label() for r in missing) + ")"
        print(f"{plat} support:\n  {state}\n", file=sys.stderr)


def _report_ready(result: Dict[str, Any]) -> None:
    if result["UNITY_INSTALLED"]:
        print("Installed:\n  " + result["UNITY_INSTALLED"].replace(",", ", ") + "\n", file=sys.stderr)
    print("Environment:\n  READY\n", file=sys.stderr)
    if result.get("UNITY_XCODE") == "unsupported-host":
        print("iOS player build:\n  NOT POSSIBLE on this host (requires macOS + Xcode)\n",
              file=sys.stderr)
    print(f"Unity executable:\n  {result['UNITY_EDITOR']}\n", file=sys.stderr)


# -- Output -------------------------------------------------------------------

OUTPUT_KEYS = (
    "UNITY_READY", "UNITY_PROJECT_PATH", "UNITY_VERSION", "UNITY_CHANGESET",
    "UNITY_EDITOR", "UNITY_EDITOR_SOURCE", "UNITY_VERSION_VERIFIED", "UNITY_PLATFORMS",
    "UNITY_MODULES", "UNITY_INSTALLED", "UNITY_CLI_BACKEND", "UNITY_XCODE",
)


def emit(result: Dict[str, Any], fmt: str) -> None:
    if fmt == "json":
        payload = {key: result.get(key, "") for key in OUTPUT_KEYS}
        payload["warnings"] = list(result.get("warnings") or [])
        print(json.dumps(payload, indent=2))
        return
    if fmt == "github-actions":
        output_path = os.environ.get("GITHUB_OUTPUT")
        if not output_path:
            raise PreflightError("--format github-actions requires GITHUB_OUTPUT to be set.", EXIT_USAGE)
        lines = []
        for key in OUTPUT_KEYS:
            value = str(result.get(key, ""))
            # A newline would let a value (e.g. a crafted project path) inject
            # extra step outputs; refuse rather than write a forged line.
            if "\n" in value or "\r" in value:
                raise PreflightError(f"Refusing to write {key} to GITHUB_OUTPUT: value "
                                     "contains a line break.", EXIT_USAGE)
            lines.append(f"{key.lower()}={value}\n")
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.writelines(lines)
    # shlex.quote single-quotes every value, so `eval "$(...)"` assigns paths
    # and versions verbatim and never expands or executes them.
    for key in OUTPUT_KEYS:
        print(f"{key}={shlex.quote(str(result.get(key, '')))}")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ensure the project's exact Unity Editor and platform modules are installed.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 scripts/common/unity_preflight.py --project . --platform Android
  python3 scripts/common/unity_preflight.py --project "$WORKTREE" --platform iOS --platform WebGL
  python3 scripts/common/unity_preflight.py --project . --platform Android --check
  eval "$(python3 scripts/common/unity_preflight.py --project . --platform Android)"
  (PowerShell) $env = python scripts/common/unity_preflight.py --project . --format json | ConvertFrom-Json
""",
    )
    parser.add_argument("--project", default=".", help="Unity project root (contains ProjectSettings/). Default: .")
    parser.add_argument("--platform", action="append", default=[],
                        help="Build platform to prepare; repeatable or comma-separated. "
                             "One of: " + ", ".join(CANONICAL_PLATFORMS) + " (case-insensitive). "
                             "Omit to ensure only the editor.")
    parser.add_argument("--check", action="store_true",
                        help="Report readiness only; never install. Exit 4 when not ready.")
    parser.add_argument("--format", choices=("env", "json", "github-actions"), default="env",
                        help="Machine output on stdout. env: shell-quoted KEY=value (eval-able); "
                             "json: one object; github-actions: env + append to $GITHUB_OUTPUT.")
    parser.add_argument("--cli", choices=("auto", "unity", "hub"),
                        default=os.environ.get("UNITY_PREFLIGHT_CLI") or "auto",
                        help="Which Unity CLI to drive (env UNITY_PREFLIGHT_CLI). Default: auto.")
    parser.add_argument("--unity-cli", default=os.environ.get("UNITY_CLI"),
                        help="Path to the `unity` CLI (env UNITY_CLI). Default: PATH lookup.")
    parser.add_argument("--unity-hub", default=os.environ.get("UNITY_HUB"),
                        help="Path to the Unity Hub executable (env UNITY_HUB). Default: OS install location.")
    parser.add_argument("--unity-editor", default=None,
                        help="Use this editor executable instead of resolving one (env UNITY_EDITOR). "
                             "Must match the project version when the CLI knows it.")
    parser.add_argument("--install-root", default=None,
                        help="Install editors under this directory (env UNITY_PREFLIGHT_INSTALL_ROOT). "
                             "Sets the CLI's persisted install path, only when something must be "
                             "installed. Use a runner-writable directory on CI. Default: the CLI's "
                             "configured install path.")
    parser.add_argument("--fallback-install-root", default=None,
                        help="With UNITY_NO_ELEVATE set, install a missing editor here when the "
                             "configured install path is not writable by this account (CI uses "
                             "a directory in the runner account's home). Ignored when "
                             "--install-root is given.")
    parser.add_argument("--install-cli-to", default=None,
                        help="If the Unity CLI is missing, install it into this directory with "
                             "Unity's installer and use <dir>/bin/unity (env UNITY_PREFLIGHT_CLI_HOME).")
    parser.add_argument("--lock-dir", default=None,
                        help="Directory for the machine-wide install lock (env UNITY_PREFLIGHT_LOCK_DIR).")
    parser.add_argument("--lock-timeout", type=float, default=7200.0,
                        help="Seconds to wait for another preflight's install. Default: 7200.")
    parser.add_argument("--install-timeout", type=float,
                        default=float(os.environ.get("UNITY_PREFLIGHT_INSTALL_TIMEOUT") or 3600),
                        help="Seconds an editor/module install may run before it is stopped "
                             "(0 = no limit). Default: 3600, or UNITY_PREFLIGHT_INSTALL_TIMEOUT.")
    # Test seam: evaluate host-specific rules (built-in modules, Xcode) for another OS.
    parser.add_argument("--host-os", choices=HOST_OSES, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    # argparse does not validate defaults, and this one can come from the environment.
    if args.cli not in ("auto", "unity", "hub"):
        parser.error("UNITY_PREFLIGHT_CLI must be one of: auto, unity, hub")
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    args = parse_args(argv)
    try:
        try:
            result = run_preflight(args)
            exit_code = EXIT_OK
        except NotReady as not_ready:
            result = not_ready.result
            exit_code = EXIT_NOT_READY
        emit(result, args.format)
        return exit_code
    except PreflightError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
