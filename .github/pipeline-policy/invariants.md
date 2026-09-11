# Pipeline invariants

Read this before changing anything under `.github/workflows/`.

These are the properties the pipeline is *built around*. Breaking one does not
usually break a test or a build — it produces a pipeline that looks fine and
ships the wrong bytes. Several of them were broken at some point and were only
caught by running a real release.

`scripts/common/validate_pipeline_invariants.py` checks the ones that can be
checked statically. It runs in CI. If it fails, the fix is the pipeline, not
the checker — unless the invariant itself is wrong, in which case change it
here first and say why.

| ID | Invariant | Checked |
|---|---|---|
| I-001 | CI must not build player artifacts. | ✅ |
| I-002 | Development artifacts are disposable. | docs |
| I-003 | Build / Release creates the final production artifacts. | ✅ |
| I-004 | Production signing happens before the immutable artifact upload. | ✅ |
| I-005 | Promotion never rebuilds, re-exports or re-signs. | ✅ |
| I-006 | Every Release Set has a manifest. | ✅ |
| I-007 | Every production artifact has an identity and a checksum. | ✅ |
| I-008 | Release builds use an immutable Unity image reference. | ⚠️ partial |
| I-009 | Every platform in a Release Set shares one commit, Unity version and image digest. | ✅ |
| I-010 | Promotion consumes an exact `source-run-id`. | ✅ |
| I-011 | Production deployment is protected by a GitHub Environment. | ✅ |
| I-012 | Build numbers increase monotonically. | ✅ |
| I-013 | Android, iOS, WebGL, Windows and Linux are first-class capabilities. | ✅ |
| I-014 | Enabled platforms come from project configuration. | ✅ |
| I-015 | Distribution providers are separate from platform capabilities. | ✅ |
| I-016 | Disabled platforms do not execute build or release jobs. | ✅ |
| I-017 | Promotion consumes the exact artifact of its Release Set. | ✅ |

## Why each exists

**I-001 — CI must not build players.**
A merge check that builds every platform costs more than it proves. CI answers
"is this safe to merge?"; `Build / Development` answers "give me something to
test". The CI entry point passes `platform: None`.

**I-003, I-004, I-005 — the immutable artifact boundary.**
The one that is easiest to break by accident and worst to get wrong. If the
artifact is produced, modified or re-signed *after* QA has approved it, then
the binary that shipped is not the binary anyone tested.

This was broken once: iOS signing ran during promotion, so QA validated an
Xcode project while an IPA built from it afterwards is what would have
shipped. Signing now happens in `Build / Release` stage 03b. A promotion may
only download, verify, test, approve and publish.

**I-008 — immutable build image.** *Partially enforced.* The release lane
currently uses `game-ci/unity-builder`, which selects its own image; there is
no digest to pin. The checker enforces that nothing *claims* release-mode
digest pinning while using a mutable tag. Closing this properly means deciding
whether `Build / Release` moves to the digest-pinned custom image lane.

**I-012 — monotonic build numbers.** Both stores reject a build number they
have seen. Android used to derive `bundleVersionCode` from the *major* version,
so every `1.x.y` release uploaded `1` and the second was refused.

**I-013 to I-016 — platform capabilities.** The toolkit supports five
platforms; a project enables a subset via the `PLATFORMS` variable. Capability
("can this project build iOS?") is not the same question as branch selection
("does `develop` build iOS?"). Capability wins. A project that cannot build a
platform must never see a job for it.

**I-015 — distribution is separate.** Windows and Linux produce valid
immutable release artifacts with no distribution provider configured. Steam is
a delivery choice, not a build prerequisite. Requiring one to produce an
artifact would make desktop a second-class platform.

## Adding a platform

`docs/PIPELINE_ARCHITECTURE.md` §9. In short: it is a row in the matrix
resolver plus, if it has one, a validator — not a new job.
