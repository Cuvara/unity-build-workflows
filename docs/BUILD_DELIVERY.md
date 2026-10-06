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
| `firebase` | Firebase App Distribution — testers get a direct install link. |

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

## `firebase` — Firebase App Distribution

Testers get a direct install link — no GitHub login, no server to run. Firebase
manages tester access and supports APK, AAB and IPA. AAB files are converted to
APKs server-side for tester devices.

**Setup**

1. Create a Firebase project (or use an existing one).
2. Enable **App Distribution** in the Firebase console.
3. Register your Android and/or iOS app in the Firebase project.
4. Create a **service account** in Google Cloud (IAM → Service Accounts) with the
   `Firebase App Distribution Admin` role.
5. Download the JSON key.
6. The upload runs **fastlane** with the `firebase_app_distribution` plugin from
   the toolkit's `Gemfile`, so it needs Ruby. GitHub-hosted runners get it from
   `ruby/setup-ruby`. On a **self-hosted runner** the build job runs
   `scripts/common/ensure_ruby.sh`, which *runs* each candidate and checks its
   version: `ruby` on PATH, then what `brew --prefix ruby`, `rbenv which ruby`,
   `asdf which ruby` or `where ruby` report. macOS's system Ruby 2.6 is
   rejected (too old; its Bundler needs sudo). With no usable Ruby it installs
   one — `brew install ruby` on macOS; on Windows `winget install
   RubyInstallerTeam.RubyWithDevKit.3.4`, or, where winget is missing (a
   runner service never has it), the RubyInstaller+DevKit download installed
   silently, per user, into `<tool cache>\rb34` and reused by later jobs. It
   puts Ruby on PATH for the job, installs Bundler, and the gems go to the
   runner's tool cache (no sudo). A machine without Homebrew gets the exact
   command to run instead, and the step warns `Firebase delivery skipped`; the
   build stays green.

   fastlane's gems compile C code. The Windows DevKit ships its own GCC. On
   macOS the compiler is Apple's clang, which refuses to run until the Xcode
   licence is accepted; the step then fails with `No working C compiler on
   this Mac`. Fix it once, as an administrator on the runner:
   `sudo xcodebuild -license accept && xcode-select --install`.

   Keep the Windows tool cache path short (`RUNNER_TOOL_CACHE`, e.g.
   `C:\t`): MSYS2 nests headers deep enough to pass the 260-character limit
   under a long root.
7. A failed upload (wrong app ID, service account without the
   `Firebase App Distribution Admin` role, a tester group that does not exist)
   is a `Firebase upload failed` warning on the run — never a failed build, and
   never silent.
8. The Discord build message links the uploaded release twice:

   | Link | Opens | Lasts |
   |---|---|---|
   | `⬇️ testers` | the release's tester page (`appdistribution.firebase.google.com/testerapps/…`); sign in with a Google account in the tester group, then **Download** | does not expire |
   | `⬇️ direct, 1 h` | the APK / IPA file itself, signed by Firebase | about **one hour**; anyone holding it can download, so keep it inside the team |

   Firebase does not offer a longer-lived direct link. For a permanent public
   file URL use `BUILD_DELIVERY=r2` or a self-hosted file server instead.

**Variables** (public ids, not secrets):

| Variable | Example | Required |
|---|---|---|
| `FIREBASE_APP_ID_ANDROID` | `1:123456789:android:abcdef0123` | When building Android |
| `FIREBASE_APP_ID_IOS` | `1:123456789:ios:abcdef0123` | When building iOS |
| `FIREBASE_TESTER_GROUPS` | `internal-testers,qa` | No — omit to skip group assignment |

```bash
gh variable set FIREBASE_APP_ID_ANDROID --body "1:123456789:android:abcdef"
gh variable set FIREBASE_APP_ID_IOS --body "1:123456789:ios:abcdef"
gh variable set FIREBASE_TESTER_GROUPS --body "internal-testers"
```

**Secrets**:

| Secret | |
|---|---|
| `FIREBASE_SERVICE_ACCOUNT_JSON` | The full JSON key file contents (not base64) |

```bash
gh secret set FIREBASE_SERVICE_ACCOUNT_JSON < /path/to/service-account-key.json
```

**How the tester link works.** The Discord notification receives a `testingUri` —
a stable URL on `appdistribution.firebase.google.com`. Testers who have been
invited to the Firebase project (or a tester group) can open it to install the
build. Uninvited people see an access error, which is a feature: builds are not
accidentally public.

**Platforms.** Only Android (APK/AAB) and iOS (IPA) are supported. WebGL, Linux
and Windows builds, Addressables, and unsigned iOS Xcode projects skip Firebase
delivery and retain their GitHub artifacts. Signed iOS builds publish the exported
IPA, not the Xcode archive. AAB uploads require linking the Firebase app to Google Play.

**Storage.** Set `ARTIFACT_STORAGE=firebase` together with `BUILD_DELIVERY=firebase`
to skip GitHub binary uploads for Android and signed iOS builds. Logs, manifests,
and platform results still go to GitHub. Validation and release-manifest jobs
that require downloading the binary are disabled in this mode; use the default
`ARTIFACT_STORAGE=github` for artifact-based promotion workflows.

**AAB support requires linking Google Play.** Firebase App Distribution can
distribute AAB files, but only if your Firebase project is linked to a Google
Play developer account. Without the link, AAB uploads fail with "This project
is not linked to a Google Play account". APK uploads always work without linking.

To link Google Play:

1. Open https://console.firebase.google.com/project/YOUR_PROJECT/settings/integrations
2. Find **Google Play** → click **Link**
3. Select your Google Play developer account
4. Accept the permissions

After linking, Firebase converts AAB to per-device APKs using the same
signing config Play Store uses. Development builds (APK) work without linking.

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
