#!/usr/bin/env python3
"""Which Discord thread each platform's build notification goes to.

Reads the repository variables as a JSON object on stdin (`toJSON(vars)` in a
workflow) and prints two GITHUB_OUTPUT entries for the discord-upload-build
action:

  thread-id            the default thread, for platforms without their own
  platform-thread-ids  one `Platform=<thread>` line per routed platform

Lookup for platform P in environment E (first non-empty wins):

  DISCORD_THREAD_ID_<E>_<P>   e.g. DISCORD_THREAD_ID_DEVELOPMENT_ANDROID
  DISCORD_THREAD_ID_<P>       e.g. DISCORD_THREAD_ID_IOS
  (the default thread)

Default thread:

  DISCORD_THREAD_ID_<E>       e.g. DISCORD_THREAD_ID_PRODUCTION
  DISCORD_THREAD_ID           the original single-thread variable

A platform whose thread equals the default thread is not listed, so a project
with only DISCORD_THREAD_ID set gets exactly one message, as before. Thread IDs
are not secrets. The action validates them.
"""
import argparse
import json
import sys
import uuid

# (variable suffix, platform name the action uses)
PLATFORMS = [
    ("ANDROID", "Android"),
    ("WEBGL", "WebGL"),
    ("LINUX64", "Linux64"),
    ("LINUXSERVER", "LinuxServer"),
    ("WINDOWS64", "Windows64"),
    ("IOS", "iOS"),
]


def _get(variables, name):
    value = variables.get(name, "")
    return str(value).strip() if value is not None else ""


def resolve(variables, environment):
    env = environment.strip().upper()
    default = (_get(variables, f"DISCORD_THREAD_ID_{env}") if env else "") \
        or _get(variables, "DISCORD_THREAD_ID")
    routes = []
    for suffix, name in PLATFORMS:
        thread = (_get(variables, f"DISCORD_THREAD_ID_{env}_{suffix}") if env else "") \
            or _get(variables, f"DISCORD_THREAD_ID_{suffix}")
        if thread and thread != default:
            routes.append(f"{name}={thread}")
    return default, routes


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--environment", default="",
                        help="Build environment: development | staging | production")
    args = parser.parse_args()

    raw = sys.stdin.read().strip()
    try:
        variables = json.loads(raw) if raw else {}
    except ValueError:
        print("::warning::resolve_discord_threads: variables are not JSON; "
              "posting to the default destination.", file=sys.stderr)
        variables = {}
    if not isinstance(variables, dict):
        variables = {}

    default, routes = resolve(variables, args.environment)
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
