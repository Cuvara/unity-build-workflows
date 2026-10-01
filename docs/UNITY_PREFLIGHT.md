# Unity environment preflight

`scripts/unity-preflight.sh` makes a machine ready to build one Unity
project. It:

1. finds the Unity project and reads the exact editor version from its
   `ProjectSettings/ProjectVersion.txt`;
2. installs that editor through a Unity CLI if it is missing;
3. installs the build modules the requested platforms need;
4. verifies the result; and
5. prints where the editor executable is, in a form scripts can consume.

It is written for AI agents and developers working in a worktree of a Unity
project. It has no GitHub Actions dependency and can be reused by CI.
It is idempotent: a second run finds everything in place and installs nothing.

`scripts/unity-preflight.sh` is a thin launcher. It finds Python and runs
`scripts/common/unity_preflight.py`, where all the logic lives.

## Usage

```bash
TOOLKIT=.ci/unity-build-workflows        # wherever the toolkit is checked out

# Editor only
bash "$TOOLKIT/scripts/unity-preflight.sh" --project "$WORKTREE"

# Editor + platform modules (repeat --platform, or comma-separate)
bash "$TOOLKIT/scripts/unity-preflight.sh" --project "$WORKTREE" --platform android
bash "$TOOLKIT/scripts/unity-preflight.sh" --project "$WORKTREE" --platform ios
bash "$TOOLKIT/scripts/unity-preflight.sh" --project "$WORKTREE" --platform webgl,linux64

# Detect only: never installs, exit 4 when something is missing
bash "$TOOLKIT/scripts/unity-preflight.sh" --project "$WORKTREE" --platform android --check
```

| Mode | Detects | Installs | Verifies | Not ready → |
|---|---|---|---|---|
| default | yes | missing editor and modules | yes | installs, or exit 1 if that fails |
| `--check` | yes | never | — | exit 4, plus the exact command a default run would execute |

`--project` may be the Unity project root (the folder with `Assets/` and
`ProjectSettings/`) or a repository root that contains a Unity project up to
two folders down, e.g. a worktree whose project lives in `MyGame/`. If more
than one project is found, preflight exits 2 and lists them; it never guesses.

On Windows, run it from Git Bash, which `git` installs. Calling the Python
script directly also works: `python scripts/common/unity_preflight.py …`.

### Prerequisites

- **Python 3.8+.** The toolkit's other scripts, and the `python3` steps that
  every build lane already runs, need it too. If none is found, the launcher
  exits 3 with install commands. It ignores the Windows Store `python3`
  placeholder, which exists on `PATH` but is not an interpreter. To pin an
  interpreter, set `UNITY_PREFLIGHT_PYTHON`.
- **The Unity CLI or Unity Hub** — see [Which CLI installs Unity](#which-cli-installs-unity).

### Consuming the result

stdout carries only machine output; the human report goes to stderr.

```bash
# bash / zsh
eval "$(bash "$TOOLKIT/scripts/unity-preflight.sh" --project "$WORKTREE" --platform android)"
"$UNITY_EDITOR" -version
```

Every value is single-quoted with Python's `shlex.quote`. `eval` therefore
assigns paths and versions literally. A project path containing `$(…)`,
backticks or `;` is never executed; a test covers this. A line break in a
value is refused for `--format github-actions`, so it cannot forge another
step output.

```powershell
# PowerShell
$unity = python "$TOOLKIT/scripts/common/unity_preflight.py" --project $Worktree --platform android --format json | ConvertFrom-Json
& $unity.UNITY_EDITOR -version
```

## Agent worktree setup

The toolkit has no bootstrap hook of its own, and neither did the consumer
project it was checked against. The entry point is therefore a single command
that any agent, or the person driving it, runs once on entering a Unity
worktree:

```bash
eval "$(bash "$TOOLKIT/scripts/unity-preflight.sh" --project . --platform <Platform>)"
```

After that, `UNITY_EDITOR` is the right editor for this worktree. Worktrees of
the same project share one machine-level installation. Agents starting at the
same time install it once (see [Concurrency](#concurrency)).

To make agents find this command without being told, add it to the consumer
project's agent instructions (`CLAUDE.md`, `AGENTS.md`, …):

```markdown
## Unity environment
Before running Unity, prepare the editor this worktree needs:
    eval "$(bash .ci/unity-build-workflows/scripts/unity-preflight.sh --project . --platform Android)"
It installs the exact version from ProjectSettings/ProjectVersion.txt if missing,
then sets UNITY_EDITOR. Use "$UNITY_EDITOR"; never pick an installed editor by hand.
```

For Claude Code, a `SessionStart` hook in the consumer's
`.claude/settings.json` can report readiness on every session start. It runs
`--check`, so opening a session never triggers a multi-gigabyte download or an
elevation prompt:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup",
        "hooks": [
          {
            "type": "command",
            "command": "bash \"$CLAUDE_PROJECT_DIR/.ci/unity-build-workflows/scripts/unity-preflight.sh\" --project \"$CLAUDE_PROJECT_DIR\" --platform Android --check 2>&1 || true",
            "timeout": 120
          }
        ]
      }
    ]
  }
}
```

The hook prints the readiness report at session start, or the exact command
that would install what is missing. It never fails the session. Installing
remains the explicit command above. Hooks run in a shell, so on Windows they
need Git Bash for `bash`. Check the current Claude Code hooks documentation
for how hook output reaches the session.

| Key | Meaning |
|---|---|
| `UNITY_READY` | `true` when the editor and all required modules are present |
| `UNITY_PROJECT_PATH` | Absolute project path |
| `UNITY_VERSION` | Exact editor version from `ProjectVersion.txt` |
| `UNITY_CHANGESET` | Changeset from `m_EditorVersionWithRevision`, empty if absent |
| `UNITY_EDITOR` | Editor executable. Same name `scripts/ios/run_unity_ios.sh` and the Docker scripts use |
| `UNITY_EDITOR_SOURCE` | `existing`, `installed` (by this run) or `override` |
| `UNITY_VERSION_VERIFIED` | `false` only for an override the CLI does not know |
| `UNITY_PLATFORMS` | Canonical platform names, comma-separated |
| `UNITY_MODULES` | Required module ids (`*` = id prefix, e.g. the versioned OpenJDK) |
| `UNITY_INSTALLED` | What this run installed (`editor`, module ids). Empty on a repeat run |
| `UNITY_CLI_BACKEND` | `unity`, `hub` or `override` |
| `UNITY_XCODE` | iOS only: `present` (macOS with Xcode) or `unsupported-host` (no iOS player build possible here) |

`--format json` prints the same keys as one object plus `warnings`.
`--format github-actions` prints the env lines and also appends lower-case
`key=value` lines to `$GITHUB_OUTPUT`.

Preflight prepares the environment; it is not a reservation. Another process
can still remove an editor afterwards, so the command that runs Unity must
still fail on a missing executable.

### Example report

```text
Project:
  /work/worktree-a

Unity version:
  6000.0.26f1 (changeset ccb7c73d2c02)

Platform:
  Android

Editor:
  MISSING

Android support:
  MISSING (android, android-sdk-ndk-tools, android-open-jdk*)

[unity_preflight] Installing Unity 6000.0.26f1 with modules android, android-sdk-ndk-tools ...
[unity_preflight] Installation complete
Installed:
  editor, android, android-sdk-ndk-tools

Environment:
  READY

Unity executable:
  C:\Program Files\Unity\Hub\Editor\6000.0.26f1\Editor\Unity.exe
```

## How the version is resolved

`ProjectSettings/ProjectVersion.txt` is the only source:

```text
m_EditorVersion: 6000.0.26f1
m_EditorVersionWithRevision: 6000.0.26f1 (ccb7c73d2c02)
```

- `m_EditorVersion` must be a full version (`6000.0.26f1`); anything else fails
  with exit 2.
- The changeset is passed to the CLI as `--changeset`. That pins the exact build
  and lets the CLI install versions that are no longer in its release list.
- Matching is exact. With only `6000.3.9f1` installed, a `6000.0.26f1` project is
  **not ready**, and `6000.0.26f1` is installed alongside. A different version
  is never substituted.
- `config/unity-build-defaults.json` is **not** consulted. It is the fallback
  for Docker image builds, not for a project that has no version file.

## Which CLI installs Unity

`--cli` (or `UNITY_PREFLIGHT_CLI`) picks the tool; the default `auto` takes the
first one found:

| Order | CLI | Located by | Commands used |
|---|---|---|---|
| 1 | Unity CLI `unity` | `--unity-cli` / `UNITY_CLI`, then `PATH`, then `%LOCALAPPDATA%\Unity\bin\unity.exe` | `editors -i`, `install-modules -e V --list`, `install V --changeset C -m … --child-modules --accept-eula --yes`, `install-modules -e V -m …`, `editors verify V` — all with `--json` |
| 2 | Unity Hub headless | `--unity-hub` / `UNITY_HUB`, then `C:\Program Files\Unity Hub\Unity Hub.exe`, `/Applications/Unity Hub.app`, or `unityhub` on `PATH` | `-- --headless editors -i -j`, `install --version V --changeset C -m … --childModules`, `install-modules --version V -m … --childModules` |

The Hub has no module listing and no verify command. Preflight reads the
`modules.json` the Hub keeps in each editor folder (`selected: true` means
installed). It does not trust the Hub's exit code: after every install it
re-detects the editor and modules, and fails if they are not there.

If neither CLI is found, preflight exits 3 and prints how to install one. It
does not install a CLI itself:

```bash
# macOS / Linux
curl -fsSL https://unity.com/install.sh | UNITY_CLI_CHANNEL=beta bash
```

```powershell
# Windows
$env:UNITY_CLI_CHANNEL='beta'; irm https://unity.com/install.ps1 | iex
```

The Unity CLI is still in beta; its command syntax used here was checked
against `1.0.0-beta.11`.

## Where Unity is installed

Editors go wherever the CLI's install path points. Check it with
`unity install-path --get` or `"Unity Hub" -- --headless ip -g`. The default is
the Hub location, e.g. `C:\Program Files\Unity\Hub\Editor\<version>`.
Preflight never changes it.

The Unity CLI and the Hub share one registry. `unity env` reports the Hub's
user-data directory and install path, and editors installed by either tool
appear in both listings. Preflight finds an editor by asking the CLI (the
`location` in `editors -i`), never by guessing a path.

Editors are machine-level. Every worktree of a project, and every project on
the same version, reuses one installation; nothing is installed inside a
worktree. The CLI caches downloads in its own download cache (`unity cache`).

On Windows, installing into `Program Files` raises a UAC prompt. An unattended
agent cannot answer it. Either run the agent elevated, or set
`UNITY_NO_ELEVATE=1` (a Unity CLI variable) together with a user-writable
install path.

## Platform modules

Platform names are the toolkit's (`PLATFORM_MATRIX.md`), case-insensitive;
`linux` and `windows` are accepted for `Linux64` and `Windows64`.

| Platform | Required modules | Notes |
|---|---|---|
| Android | `android`, `android-sdk-ndk-tools`, `android-open-jdk*` | Installed as `android` + child modules. The OpenJDK id is versioned (e.g. `android-open-jdk-17.0.9+9`) |
| iOS | `ios` | Xcode is a host requirement that preflight checks but never installs. On macOS, `xcode-select -p` must succeed, otherwise exit 3. On Windows and Linux the module is installed so the editor can switch to and compile for iOS. The report then says **"iOS player build: NOT POSSIBLE on this host"** and sets `UNITY_XCODE=unsupported-host`. The toolkit builds iOS only on its macOS lane |
| WebGL | `webgl` | |
| Linux64 | `linux-il2cpp` or `linux-mono` | Follows the project's Standalone scripting backend (`scriptingBackend` → `Standalone` in `ProjectSettings.asset`; absent = Mono) |
| LinuxServer | `linux-server` | |
| Windows64 | `windows-il2cpp` or `windows-mono` | Same scripting-backend rule |

Mono support for the host's own platform ships with every editor and is never
requested (Windows64/Mono on Windows, Linux64/Mono on Linux).

When an editor exists but a module is missing, preflight installs only the
missing ids. A module the CLI does not offer for that editor on this host
(e.g. Windows IL2CPP from a Linux host) fails with exit 3.

## Using your own editor: `UNITY_EDITOR`

Resolution order:

1. `--unity-editor` / `UNITY_EDITOR` — an explicit executable path.
2. The project's version, found among the CLI's installed editors.
3. Installation through the CLI.

An explicit editor is never silently replaced:

- If the CLI knows that executable at a **different** version, preflight fails
  (exit 3) and names both versions.
- If the CLI knows it at the right version, the normal flow continues and
  missing modules are installed into it.
- If the CLI does not know it, preflight cannot check its version. It trusts
  it, installs nothing, and reports `UNITY_VERSION_VERIFIED=false` with a
  warning.

Unset `UNITY_EDITOR` in a shell that switched to a project on another version.

## Concurrency

Several agents can run preflight at the same time:

- Checks are read-only and take no lock.
- Anything that installs takes one machine-wide lock first, then **re-checks**.
  A process that waited while another one installed the same editor finds it
  present and installs nothing.
- The lock is an OS file lock (`flock` on macOS/Linux, `msvcrt.locking` on
  Windows) on `unity-install.lock` in:

| Host | Lock directory |
|---|---|
| Windows | `%LOCALAPPDATA%\unity-build-workflows\locks` |
| macOS | `~/Library/Caches/unity-build-workflows/locks` |
| Linux | `${XDG_CACHE_HOME:-~/.cache}/unity-build-workflows/locks` |

Override with `--lock-dir` or `UNITY_PREFLIGHT_LOCK_DIR`.

- The OS releases the lock when its holder exits or is killed. An interrupted
  install never blocks later runs. Re-running preflight retries the install;
  the Unity CLI discards a corrupted partial download and fetches it again.
- Waiters log who holds the lock (pid, host, project, version, start time),
  read from `unity-install.owner.json`. After `--lock-timeout` seconds
  (default 7200) they give up with exit 5.
- Limits:
  - The lock is per OS user unless `UNITY_PREFLIGHT_LOCK_DIR` points at a
    directory shared by all users.
  - It is not reliable on network filesystems.
  - It does not see installs started from the Hub GUI.
  - The Unity CLI's own lock still prevents two installs of the same version
    from corrupting each other.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Ready |
| 1 | Install or verification failed. The CLI's command, exit code, error and output are printed |
| 2 | Usage or project error: missing or malformed `ProjectVersion.txt`, unknown platform |
| 3 | Prerequisite missing: no Unity CLI, Xcode missing, `UNITY_EDITOR` mismatch, module not offered |
| 4 | Not ready, with `--check` |
| 5 | Timed out waiting for another preflight's install |

## Supported hosts

Windows, macOS and Linux need Python 3.8+ and one of the two CLIs. A single
Python script serves all three. The bash launcher only locates Python, and runs
in Git Bash on Windows. There are no per-shell copies of the logic.

## Security notes

- **Output:** values are `shlex`-quoted for `eval`, and line breaks are refused
  for `$GITHUB_OUTPUT` (see [Consuming the result](#consuming-the-result)).
- **Running the CLI:** CLI commands are run as argument lists, never through a
  shell, so project paths are never interpreted as commands.
- **Locating the CLI:** `unity` is found on `PATH` like any tool. On a
  machine whose `PATH` you don't trust, pin it with `--unity-cli` /
  `UNITY_CLI`.
- **Locating the editor:** the editor executable is the location the CLI
  reports for the exact version, or the explicit `UNITY_EDITOR`.
- **Installers:** downloading and checking installers is left to Unity's CLI.
  Preflight downloads nothing itself.
- **Lock files:** they live in the user's cache directory with the user's
  default permissions. The owner file is diagnostic only and is never trusted
  for locking.

Inside the toolkit's Docker images Unity is already installed and
`UNITY_EDITOR=/usr/bin/unity-editor` is set. Preflight treats that as an
override and installs nothing.

## Reuse in CI

Nothing in the pipeline calls preflight yet. The Docker lane bakes the editor
into its image, and the self-hosted lanes still expect a pre-installed editor at
the default Hub path (`SELF_HOSTED_WINDOWS_RUNNER.md`,
`SELF_HOSTED_MACOS_RUNNER.md`). A self-hosted job can adopt it as a step before
the build:

```yaml
- name: Unity preflight
  id: unity
  shell: bash
  run: >
    bash .ci/unity-build-workflows/scripts/unity-preflight.sh
    --project . --platform "${{ matrix.platform }}" --format github-actions
# later steps: ${{ steps.unity.outputs.unity_editor }}
```

Use `--check` there if the runner must never install on its own.

## Testing

`tests/test_unity_preflight.py` runs the real script, as a subprocess, against
stateful fakes of both CLIs (`tests/fixtures/fake_unity_cli.py`,
`tests/fixtures/fake_unity_hub.py`). The suite covers:

- version, changeset and project detection, including a project in a
  subfolder and an ambiguous worktree;
- reuse of an existing editor, and installation of a missing one;
- wrong-version rejection;
- module installation;
- idempotency;
- failure propagation and false-success detection;
- check mode and the three `UNITY_EDITOR` override cases;
- both backends and all three output formats, including an `eval` injection
  attempt;
- two concurrent preflights installing once, for both an editor install and a
  module install;
- the lock timeout;
- the launcher's handling of a missing or placeholder Python.

```bash
cd tests && python -m pytest test_unity_preflight.py -q
```
