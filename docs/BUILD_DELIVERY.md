# Build delivery

Getting a finished build to a person who can install it.

## The problem this solves

A GitHub Actions artifact link does not work for anyone who is not signed in
with access to the repository. Not "works but asks for a login" — it 404s.
Measured, unauthenticated, on a **public** repository:

```
GET /Cuvara/NDCUnityTemplate/actions/runs/34748998646/artifacts/10315556340
  → 307  /suites/94114249500/artifacts/10315556340
  → 404  (9 bytes)
```

GitHub hides the artifact's existence rather than prompting for a login, and
being a public repo makes no difference: Actions artifacts are never public.
So the link in a build notification was unusable by testers, artists, producers
— everyone a build notification is actually for.

Delivery copies the build somewhere that serves it, and puts that URL in the
Discord message instead.

## Choosing a provider

One repository variable, `BUILD_DELIVERY`:

| Value | Where the build goes |
|---|---|
| `none` | **Default.** Nowhere. The Discord link stays the GitHub artifact URL. |
| `r2` | Cloudflare R2, over the S3 API. |
| `local` | A directory on your self-hosted runner, served by your own web server. |

```bash
gh variable set BUILD_DELIVERY --body "r2"
```

Nothing changes until you set it. A project that has not chosen a host still
builds, and delivery never fails a build — a green build that could not be
copied somewhere is still a green build. **Misconfiguration is different**:
asking for `r2` without credentials fails the step, because the alternative is
a pipeline that looks healthy and quietly delivers nothing.

## `r2` — Cloudflare R2

The recommended option, for one reason above the others: **egress is free**. A
33 MB build fetched by a team several times a day is real bandwidth, and R2
does not bill for it. Storage is free to 10 GB.

**Setup**

1. Create an R2 bucket.
2. Create an R2 API token — this gives an access key id and a secret.
3. Make the bucket public, or attach a custom domain, and note the base URL.

**Variables** (public ids, not secrets):

| Variable | Example |
|---|---|
| `R2_BUCKET` | `game-builds` |
| `R2_ACCOUNT_ID` | your Cloudflare account id — falls back to `CLOUDFLARE_ACCOUNT_ID` if you already set that for Pages |
| `R2_PUBLIC_BASE_URL` | `https://builds.yourstudio.com` |

**Secrets**:

| Secret | |
|---|---|
| `R2_ACCESS_KEY_ID` | |
| `R2_SECRET_ACCESS_KEY` | |

Without `R2_PUBLIC_BASE_URL` the upload still happens, but the URL handed to
Discord is the private S3 endpoint, which does not open in a browser. The step
warns rather than pretending that link is useful.

Objects are written with `Content-Disposition: attachment`, so the browser
downloads the file instead of trying to display it.

### Two things to set up on the bucket

**A lifecycle rule.** Builds accumulate; 10 GB goes quickly. Expire objects on
a schedule — matching the artifact retention tiers is a sensible default:
development after 7 days, release after 90.

**Think about whether public is what you want.** A public bucket means anyone
with the URL has the build. Paths include the branch, run number and commit
(`develop/42/abc1234/game.apk`), so they are not guessable, but they are not
secret either — a link pasted anywhere is a link that works. For an unreleased
game, prefer a private bucket with signed URLs; the pipeline change is small
and this document should be updated when it happens.

## `local` — your own runner

If you already run a self-hosted runner and a web server, the build is on that
machine the moment it finishes. Delivery copies it into a directory the server
serves and composes the URL.

**Variables**:

| Variable | Example |
|---|---|
| `BUILD_PUBLISH_DIR` | `/var/www/builds` |
| `BUILD_PUBLISH_BASE_URL` | `https://builds.yourstudio.com` |

**What the toolkit does not do**: install a web server, configure TLS, open a
port, or check that the URL resolves. It copies the file and reports the link
you told it to report. If the path is wrong or the server is down, the link
will be wrong and nothing here can tell.

**This only works on the self-hosted lane.** The file has to be on the machine
that serves it, and a GitHub-hosted runner is a fresh VM that disappears. Set
`RUNNER_TYPE=self-hosted` if you use `local`.

## What the layout looks like

```
<base>/<branch>/<run-number>/<short-sha>/<file>

https://builds.yourstudio.com/develop/42/abc1234/game.apk
https://builds.yourstudio.com/release-1.4/7/9f2b1c0/game.aab
```

The commit is in the path on purpose: two builds of the same branch and run
number cannot collide, and the URL says which commit it came from without
opening anything.

## Other options, and why they are not here

**Discord attachment.** Already supported for builds under the server's upload
limit — 8 MB on a non-boosted server, 50 MB at Boost Level 2. It is the nicest
result when it fits, because the file is in the message. It is not a link,
though: Discord's CDN URLs now expire, so one copied out of the channel stops
working within about a day.

**GitHub Release assets.** Genuinely public and need no new credentials, but
they turn a versioning mechanism into a file host: one release per build,
release list full of noise, and on a public repo every build permanently
world-downloadable.

**Google Drive.** A service account has no Drive storage quota of its own, so
uploads into an ordinary user's Drive fail with `storageQuotaExceeded`. It
needs Google Workspace and a Shared Drive. Files over 100 MB also get an
interstitial virus-scan page, which is exactly the click-through the whole
exercise was meant to remove.
