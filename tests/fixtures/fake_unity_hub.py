#!/usr/bin/env python3
"""
fake_unity_hub.py -- stateful stand-in for the Unity Hub headless CLI.

Invoked as `<hub> -- --headless <command> ...`, like the real Hub:

  editors -i -j                                 JSON list (preceded by a blank line)
  install --version V [--changeset C] [--childModules] [-m ids...]
  install-modules --version V [--childModules] -m ids...

Installed modules are recorded the way the Hub does it: in
<FAKE_UNITY_ROOT>/<version>/modules.json, one entry per offered module with
`selected: true` when installed.

Environment:
  FAKE_UNITY_STATE   JSON state file listing installed editors (required)
  FAKE_UNITY_ROOT    directory holding fake editor folders (required)
  FAKE_UNITY_LOG     file receiving one JSON argv list per invocation (optional)
  FAKE_UNITY_MODE    success (default) | install_fail | install_noop
                     install_noop exits 0 without installing, like a Hub that
                     swallowed an error.
"""
import json
import os
import sys
from pathlib import Path

OFFERED = [
    "android", "android-sdk-ndk-tools", "android-open-jdk-17.0.9+9",
    "ios", "webgl", "linux-il2cpp", "linux-mono", "linux-server",
    "windows-il2cpp", "windows-server", "documentation",
]
CHILDREN = {
    "android": ["android-sdk-ndk-tools", "android-open-jdk-17.0.9+9"],
}


def editor_root(version):
    return Path(os.environ["FAKE_UNITY_ROOT"], version)


def executable_for(version):
    name = "Unity.exe" if os.name == "nt" else "Unity"
    return editor_root(version) / "Editor" / name


def load_state():
    path = Path(os.environ["FAKE_UNITY_STATE"])
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"editors": {}}


def save_state(state):
    Path(os.environ["FAKE_UNITY_STATE"]).write_text(json.dumps(state), encoding="utf-8")


def option_values(args, *names):
    for i, arg in enumerate(args):
        if arg in names:
            values = []
            for value in args[i + 1:]:
                if value.startswith("-"):
                    break
                values.append(value)
            return values
    return []


def write_modules(version, installed):
    entries = [{"id": m, "name": m, "selected": m in installed, "preSelected": False,
                "preselected": False} for m in OFFERED]
    path = editor_root(version) / "modules.json"
    # The real file repeats keys with different case; keep that quirk.
    path.write_text(json.dumps(entries), encoding="utf-8")


def read_modules(version):
    path = editor_root(version) / "modules.json"
    if not path.is_file():
        return set()
    return {e["id"] for e in json.loads(path.read_text(encoding="utf-8")) if e.get("selected")}


def with_children(ids, child_modules):
    result = []
    for module_id in ids:
        extra = CHILDREN.get(module_id, []) if child_modules else []
        for item in [module_id] + extra:
            if item not in result:
                result.append(item)
    return result


def main(argv):
    log = os.environ.get("FAKE_UNITY_LOG")
    if log:
        with open(log, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(argv) + "\n")
    if argv[:2] != ["--", "--headless"]:
        print("fake_unity_hub: expected '-- --headless'", file=sys.stderr)
        return 1
    args = argv[2:]
    mode = os.environ.get("FAKE_UNITY_MODE", "success")
    # Electron noise, as printed by the real Hub.
    print("[1001/152022.457:ERROR:cache_util_win.cc(25)] Unable to move the cache", file=sys.stderr)

    if args[:2] == ["editors", "-i"]:
        state = load_state()
        print("")
        print(json.dumps([{
            "version": version,
            "architecture": "x86_64",
            "location": str(executable_for(version)),
        } for version in state.get("editors", {})], indent=2))
        return 0

    if args and args[0] in ("install", "install-modules"):
        version = option_values(args, "--version", "-v")[0]
        modules = option_values(args, "-m", "--module")
        child = "--childModules" in args or "--cm" in args
        if mode == "install_fail":
            print(f"Failed to install {version}: network error")
            return 1
        if mode == "install_noop":
            return 0
        state = load_state()
        if args[0] == "install":
            state.setdefault("editors", {})[version] = {}
            exe = executable_for(version)
            exe.parent.mkdir(parents=True, exist_ok=True)
            exe.write_text("fake editor\n", encoding="utf-8")
            installed = set()
        else:
            if version not in state.get("editors", {}):
                print(f"Editor {version} is not installed")
                return 1
            installed = read_modules(version)
        installed.update(with_children(modules, child))
        write_modules(version, installed)
        save_state(state)
        print(f"{args[0]} completed")
        return 0

    print("fake_unity_hub: unsupported command " + " ".join(args))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
