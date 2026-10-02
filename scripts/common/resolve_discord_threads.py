#!/usr/bin/env python3
"""Which Discord thread each platform's build notification goes to.

Two sources, read by the pipeline's resolve-config job:

  the config file    `.github/discord.json` in the project repository (or the
                     path in DISCORD_CONFIG_FILE) — versioned, reviewed in PRs:

                       {
                         "threads": {
                           "development": { "Android": "<id>", "iOS": "<id>" },
                           "production":  { "default": "<id>" },
                           "*":           { "default": "<id>" }
                         }
                       }

                     Environment keys: development | staging | production | "*"
                     (every environment). Platform keys: Android, iOS, WebGL,
                     Linux64, LinuxServer, Windows64, or "default" for platforms
                     without their own thread. Keys are case-insensitive.

  repository variables (JSON object on stdin, `toJSON(vars)`) — override the
                     file without a commit:
                       DISCORD_THREAD_ID_<ENV>_<PLATFORM>, DISCORD_THREAD_ID_<PLATFORM>,
                       DISCORD_THREAD_ID_<ENV>, DISCORD_THREAD_ID

Lookup for platform P in environment E (first set wins):

  1. DISCORD_THREAD_ID_<E>_<P>     variable
  2. DISCORD_THREAD_ID_<P>         variable
  3. threads[E][P]                 file
  4. threads["*"][P]               file
  5. the default thread

Default thread: DISCORD_THREAD_ID_<E>, DISCORD_THREAD_ID, threads[E].default,
threads["*"].default — then the channel root. Variables always win over the file.

Prints GITHUB_OUTPUT entries for the discord-upload-build action:

  thread-id            the default thread
  platform-thread-ids  one `Platform=<thread>` line per platform with its own

A broken or invalid config file is reported as an ::error:: annotation and
ignored — a notification setting never fails a build. Thread IDs are not
secrets.
"""
import argparse
import json
import re
import sys
import uuid
from pathlib import Path

# (variable suffix, platform name the action uses)
PLATFORMS = [
    ("ANDROID", "Android"),
    ("WEBGL", "WebGL"),
    ("LINUX64", "Linux64"),
    ("LINUXSERVER", "LinuxServer"),
    ("WINDOWS64", "Windows64"),
    ("IOS", "iOS"),
]
ENVIRONMENTS = ("development", "staging", "production", "*")
SNOWFLAKE = re.compile(r"^[0-9]{17,20}$")


def _get(variables, name):
    value = variables.get(name, "")
    return str(value).strip() if value is not None else ""


def load_config(path):
    """Return {env: {platform-key-lower: thread}} and a list of problems."""
    problems = []
    if not path:
        return {}, problems
    p = Path(path)
    if not p.is_file():
        return {}, problems
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, [f"{path} is not valid JSON ({exc})"]
    if not isinstance(data, dict) or not isinstance(data.get("threads", {}), dict):
        return {}, [f'{path}: expected {{"threads": {{"<environment>": {{"<platform>": "<id>"}}}}}}']

    known_platforms = {name.lower() for _, name in PLATFORMS} | {"default"}
    threads = {}
    for env_key, entries in data.get("threads", {}).items():
        env = str(env_key).strip().lower()
        if env not in ENVIRONMENTS:
            problems.append(f"{path}: unknown environment '{env_key}' "
                            f"(allowed: {', '.join(ENVIRONMENTS)})")
            continue
        if not isinstance(entries, dict):
            problems.append(f"{path}: threads.{env_key} must be an object")
            continue
        for plat_key, thread in entries.items():
            plat = str(plat_key).strip().lower()
            value = str(thread).strip() if thread is not None else ""
            if plat not in known_platforms:
                problems.append(f"{path}: unknown platform '{plat_key}' in threads.{env_key} "
                                f"(allowed: {', '.join(n for _, n in PLATFORMS)}, default)")
                continue
            if not SNOWFLAKE.match(value):
                problems.append(f"{path}: threads.{env_key}.{plat_key} '{value}' is not a "
                                "Discord thread ID (17-20 digits)")
                continue
            threads.setdefault(env, {})[plat] = value
    return threads, problems


def resolve(variables, environment, threads=None):
    threads = threads or {}
    env = environment.strip().lower()
    ENV = env.upper()
    here = threads.get(env, {}) if env else {}
    everywhere = threads.get("*", {})

    default = ((_get(variables, f"DISCORD_THREAD_ID_{ENV}") if env else "")
               or _get(variables, "DISCORD_THREAD_ID")
               or here.get("default", "")
               or everywhere.get("default", ""))
    routes = []
    for suffix, name in PLATFORMS:
        key = name.lower()
        thread = ((_get(variables, f"DISCORD_THREAD_ID_{ENV}_{suffix}") if env else "")
                  or _get(variables, f"DISCORD_THREAD_ID_{suffix}")
                  or here.get(key, "")
                  or everywhere.get(key, ""))
        if thread and thread != default:
            routes.append(f"{name}={thread}")
    return default, routes


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--environment", default="",
                        help="Build environment: development | staging | production")
    parser.add_argument("--config", default="",
                        help="Path of the project's Discord config file (missing = none)")
    args = parser.parse_args()

    raw = sys.stdin.read().strip()
    try:
        variables = json.loads(raw) if raw else {}
    except ValueError:
        print("::warning::resolve_discord_threads: variables are not JSON; ignoring them.",
              file=sys.stderr)
        variables = {}
    if not isinstance(variables, dict):
        variables = {}

    threads, problems = load_config(args.config)
    for problem in problems:
        print(f"::error::Discord config: {problem} — the entry is ignored and the build "
              "continues; notifications fall back to the default thread.", file=sys.stderr)

    default, routes = resolve(variables, args.environment, threads)
    print(f"thread-id={default}")
    if routes:
        delim = f"EOF_{uuid.uuid4().hex}"
        print(f"platform-thread-ids<<{delim}")
        for line in routes:
            print(line)
        print(delim)
    else:
        print("platform-thread-ids=")
    for line in routes:
        print(f"[resolve_discord_threads] {line}", file=sys.stderr)
    print(f"[resolve_discord_threads] default thread: {default or '<channel root>'}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
