# Steam distribution

Steam is a **distribution provider**, not a platform capability. A project can
build and validate Windows and Linux release artifacts with no Steam account at
all; Steam configuration gates *publishing* only (I-015).

That separation is load-bearing, and the pipeline enforces it in both
directions:

- `Build / Release` never mentions Steam. No Steam variable or secret can
  prevent a Windows or Linux build, and a test fails if one appears there.
- The promotion workflows fail **loudly** when Steam configuration is missing.
  A deploy job that skips quietly and reports success is how a release nobody
  shipped gets believed.

## The flow

```
Build / Release
    ↓  release-windows / release-linux  (validated, checksummed, in the Release Set)
23-release-windows.yml  /  24-release-linux.yml
    ↓  Verify identity → Validate artifact
steam-internal      →  Steam branch "internal"
steam-external      →  Steam branch "beta"
steam-production    →  Steam branch "default"  (live)
```

Every phase downloads the artifact again and re-verifies it against the Release
Set. An approval on the internal branch is not evidence about the bytes a later
job fetched.

## Configuration

Public ids are **repository variables**; they are not secrets and appear in any
Steam manifest.

| Variable | Meaning |
|---|---|
| `STEAM_APP_ID` | The Steam app, e.g. `480` |
| `STEAM_DEPOTS` | JSON: `{"Windows64": "480011", "Linux64": "480012"}` |
| `STEAM_DEPOT_WINDOWS64` | Per-platform override, if you prefer separate variables |
| `STEAM_DEPOT_LINUX64` | ditto |
| `STEAM_BRANCH_INTERNAL` | default `internal` |
| `STEAM_BRANCH_EXTERNAL` | default `beta` |
| `STEAM_BRANCH_PRODUCTION` | default `default` — Steam's live branch |

`STEAM_DEPOTS` is the preferred form. A Steam app has one identity with several
depots hanging off it; splitting that into unrelated per-platform variables is
how Windows and Linux end up pointing at different apps.

Credentials are **repository secrets**:

| Secret | Meaning |
|---|---|
| `STEAM_USERNAME` | The publishing account |
| `STEAM_CONFIG_VDF` | base64 of a `config.vdf` whose Steam Guard session is already valid |

### Generating `STEAM_CONFIG_VDF`

Steam Guard cannot be satisfied from CI, so the session is established once,
locally, and the resulting session file is stored as a secret:

```bash
steamcmd +login <account> +quit        # complete the Steam Guard prompt
base64 -w0 ~/.steam/steam/config/config.vdf   # store as STEAM_CONFIG_VDF
```

Treat it as a credential: it is a live session. The deploy script writes it
mode `600` into a directory removed by an exit trap, never echoes it, and never
passes it on a command line where `ps` could read it.

## The immutable boundary and Steam's content directory

SteamCMD wants a content root of its own beside a pair of VDF scripts. That is
the one place a promotion could be tempted into rewriting the bytes it is
supposed to be publishing, so `scripts/steam/deploy_steam.sh`:

1. copies the verified artifact into a staging workspace with `cp -a`, which
   preserves the executable bit a Linux player needs to start;
2. fingerprints both trees — relative path plus content, in a stable order —
   and **refuses to upload** if they differ;
3. removes `artifact-manifest.json` from the staging copy, because it is
   provenance rather than game content and does not belong in a depot;
4. generates the app and depot VDFs from the resolved configuration, with no
   project-specific id anywhere in the toolkit.

Nothing is rebuilt, re-signed, repacked or recompressed.
`check_promotion_never_mutates` fails CI if that ever changes.

## Environments

`steam-internal`, `steam-external` and `steam-production` are GitHub
Environments, so approvals and reviewers live in repository settings rather
than being invented in YAML. They are deliberately separate from the app
stores' `internal-testing` / `external-testing` / `production`: a Steam release
and an App Store release are different approval decisions, and one shared set
of reviewers would conflate them.

Add required reviewers to `steam-production` and a green build can never reach
players on its own.

## Failure modes, and what they mean

| Message | Cause |
|---|---|
| `STEAM_APP_ID is not set` | No Steam configuration. The build succeeded; publishing cannot start. |
| `no Steam depot configured for <platform>` | The app is configured but this platform has no depot. |
| `STEAM_CONFIG_VDF did not decode to anything` | The secret is not base64, or is empty. |
| `The staged content is not identical to the verified artifact` | Staging changed the bytes. Nothing is uploaded; this is a bug in the deploy script, not a configuration problem. |

A dry run (`dry-run: true`) verifies, validates and stages everything without
uploading, and needs no Steam credentials at all — which is what makes the
promotion path testable in a repository that has no Steam account.
