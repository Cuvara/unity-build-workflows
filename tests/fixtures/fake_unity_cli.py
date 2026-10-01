#!/usr/bin/env python3
"""
fake_unity_cli.py -- stateful stand-in for the standalone Unity CLI (`unity`).

Implements the subset unity_preflight.py drives, with the --json envelope
shape of the real CLI ({success, command, data, errors, warnings}):

  editors -i                       installed editors
  install-modules -e V --list      module ids with status Installed/Available
  install V [--changeset C] [-m ids...]
  install-modules -e V -m ids...
  editors verify V

Environment:
  FAKE_UNITY_STATE   JSON state file (required). Shape:
                     {"editors": {"<version>": {"architecture": "x86_64", "modules": [ids]}},
                      "available": [ids offered per editor]}   (optional; DEFAULT_AVAILABLE)
  FAKE_UNITY_ROOT    directory where fake editor executables are created (required)
  FAKE_UNITY_LOG     file receiving one JSON argv list per invocation (optional)
  FAKE_UNITY_MODE    success (default) | install_fail | modules_fail | install_noop | noisy
  FAKE_UNITY_INSTALL_DELAY  seconds an install takes (default 0)
"""
import json
import os
import sys
import time
from pathlib import Path

DEFAULT_AVAILABLE = [
    "android", "android-sdk-ndk-tools", "android-open-jdk-17.0.9+9",
    "ios", "webgl", "linux-il2cpp", "linux-mono", "linux-server",
    "windows-il2cpp", "windows-server", "mac-mono", "documentation",
]
CHILDREN = {
    "android": ["android-sdk-ndk-tools", "android-open-jdk-17.0.9+9"],
}


def load_state():
    path = Path(os.environ["FAKE_UNITY_STATE"])
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"editors": {}}


def save_state(state):
    path = Path(os.environ["FAKE_UNITY_STATE"])
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(str(tmp), str(path))


def executable_for(version):
    name = "Unity.exe" if os.name == "nt" else "Unity"
    return Path(os.environ["FAKE_UNITY_ROOT"], version, "Editor", name)


def envelope(command, data=None, errors=None, code=0):
    print(json.dumps({
        "success": not errors,
        "command": command,
        "data": data,
        "errors": errors or [],
        "warnings": [],
    }, indent=2))
    return code


def fail(command, message, code=6, error_code="COMMAND_FAILED"):
    return envelope(command, None, [{"code": error_code, "message": message}], code)


def option_values(args, *names):
    """Values following the first of names, up to the next --option."""
    for i, arg in enumerate(args):
        if arg in names:
            values = []
            for value in args[i + 1:]:
                if value.startswith("-"):
                    break
                values.append(value)
            return values
    return []


def with_children(ids):
    result = []
    for module_id in ids:
        for item in [module_id] + CHILDREN.get(module_id, []):
            if item not in result:
                result.append(item)
    return result


def main(argv):
    log = os.environ.get("FAKE_UNITY_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(argv) + "\n")

    mode = os.environ.get("FAKE_UNITY_MODE", "success")
    args = [a for a in argv if a not in ("--no-banner", "--non-interactive", "--json")]
    state = load_state()
    editors = state.setdefault("editors", {})
    available = state.get("available", DEFAULT_AVAILABLE)

    if args[:2] == ["editors", "-i"]:
        data = [{
            "version": version,
            "alias": version,
            "architecture": info.get("architecture", "x86_64"),
            "location": str(executable_for(version)),
            "modules": "",
            "default": False,
        } for version, info in editors.items()]
        return envelope("editors", data)

    if args[:2] == ["editors", "verify"]:
        version = args[2]
        info = editors.get(version)
        if info is None:
            return fail("editors verify", f"No installed editor found for version {version}.")
        ok = executable_for(version).is_file()
        components = [{"component": "editor", "kind": "editor",
                       "status": "ok" if ok else "missing", "path": str(executable_for(version))}]
        components += [{"component": m, "kind": "module", "status": "ok", "path": ""}
                       for m in info.get("modules", [])]
        return envelope("editors verify", {"version": version, "ok": ok, "components": components},
                        None if ok else [{"code": "VERIFY_FAILED", "message": "missing files"}],
                        0 if ok else 6)

    if args and args[0] == "install-modules" and "--list" in args:
        version = option_values(args, "-e", "--editor-version")[0]
        info = editors.get(version)
        if info is None:
            return fail("install-modules", f"No installed editor found for version {version}.")
        data = [{"id": m, "name": m, "category": "Platforms",
                 "status": "Installed" if m in info.get("modules", []) else "Available"}
                for m in available]
        return envelope("install-modules", data)

    if args and args[0] == "install":
        version = args[1]
        modules = option_values(args, "-m", "--module")
        if mode == "install_fail":
            print("Downloading Unity " + version + " ...")
            return fail("install", "Simulated download failure: connection reset", 6, "DOWNLOAD_FAILED")
        if mode == "noisy":
            for module_id in modules:
                for child in CHILDREN.get(module_id, []):
                    print(f"Adding module {child} as dependency of {module_id}.")
        time.sleep(float(os.environ.get("FAKE_UNITY_INSTALL_DELAY", "0")))
        if mode != "install_noop":
            state = load_state()
            state.setdefault("editors", {})[version] = {
                "architecture": "x86_64",
                "modules": with_children(modules),
                "changeset": (option_values(args, "-c", "--changeset") or [""])[0],
            }
            exe = executable_for(version)
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_text("fake editor\n", encoding="utf-8")
            save_state(state)
        return envelope("install", {"alreadyInstalled": False, "editor": {"version": version},
                                    "modules": [{"id": m} for m in modules]})

    if args and args[0] == "install-modules":
        version = option_values(args, "-e", "--editor-version")[0]
        modules = option_values(args, "-m", "--module")
        if version not in editors:
            return fail("install-modules", f"No installed editor found for version {version}.")
        if mode == "modules_fail":
            return fail("install-modules", "Simulated module install failure", 6, "MODULE_INSTALL_FAILED")
        time.sleep(float(os.environ.get("FAKE_UNITY_INSTALL_DELAY", "0")))
        if mode != "install_noop":
            state = load_state()
            current = state["editors"][version].setdefault("modules", [])
            for module_id in with_children(modules):
                if module_id not in current:
                    current.append(module_id)
            save_state(state)
        return envelope("install-modules", [{"id": m, "status": "installed"} for m in modules])

    return fail("unknown", "fake_unity_cli: unsupported command " + " ".join(args), 2, "BAD_ARGUMENTS")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
