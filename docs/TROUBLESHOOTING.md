# Troubleshooting

Common errors and their fixes for the Docker-mandatory Unity CI/CD platform.

---

## Docker Issues

### Docker daemon unavailable

**Error:** `Cannot connect to the Docker daemon`

**Cause:** Docker Engine is not running on the CI runner or developer machine.

**Fix:**
- CI: Ensure the runner has Docker pre-installed. GitHub-hosted `ubuntu-latest` runners include Docker.
- Local: Start Docker Desktop or the Docker daemon (`sudo systemctl start docker`).
- Self-hosted: Install Docker Engine and ensure the runner user is in the `docker` group.

### Registry authentication failure

**Error:** `denied: permission denied` or `unauthorized: authentication required`

**Cause:** The runner cannot pull the Unity build image from the container registry.

**Fix:**
- Ensure `GITHUB_TOKEN` has `packages: read` permission
- For private registries, add registry credentials to the workflow
- Check that the image reference exists in the registry

### Image not found

**Error:** `manifest unknown` or `not found`

**Cause:** The requested image tag or digest does not exist in the registry.

**Fix:**
- Verify the Unity version and variant are correct
- Check image manifest for available images
- Build the image first via `build-unity-image.yml` workflow

### Image digest mismatch

**Error:** `image digest does not match expected`

**Cause:** The image in the registry has been updated but the manifest still references the old digest.

**Fix:**
- Rebuild and republish the image
- Update the image manifest with the new digest
- In development mode, use tags instead of digests

---

## Unity License Issues

### Unity license activation failed

**Error:** `Unity license activation failed` or `No valid Unity license found`

**Cause:** The `UNITY_LICENSE` secret is missing, invalid, or expired.

**Fix:**
1. Verify `UNITY_LICENSE` repository secret contains the complete `.ulf` file content
2. Check that the license covers the Unity version being used
3. Check `Logs/unity-activate.log` for detailed activation errors
4. For Professional/Plus licenses, ensure `UNITY_EMAIL` and `UNITY_PASSWORD` are also set
5. License files expire — regenerate if older than 2 years

### Machine count exceeded

**Error:** `License activation failed: Machine count exceeded`

**Cause:** Unity's concurrent activation limit reached.

**Fix:** Return licenses from unused machines via Unity Hub (Preferences → Licenses → Return), or upgrade to Unity Build Server for CI.

### License version mismatch

**Error:** `License file is not valid for this version`

**Cause:** The `.ulf` was generated for a different Unity version.

**Fix:** Activate Unity with the exact version used in the workflow and export a new `.ulf`.

---

## Permission Issues

### Permission denied on workspace files

**Error:** `Permission denied` on workspace files or Unity cache

**Cause:** File ownership mismatch between host and container.

**Fix:**
- The `run_unity_container.py` wrapper sets `--user "$(id -u):$(id -g)"` automatically
- Check that the project directory is readable by the current user
- If using a named volume, it may have been created by a different user — delete it: `docker volume rm <volume-name>`

### Root-owned output

**Error:** Build artifacts are owned by root and cannot be cleaned up.

**Cause:** Container ran as root instead of the host user.

**Fix:**
- Always use `scripts/docker/run_unity_container.py` which sets `--user` automatically
- Never run `docker run` manually without `--user`
- Fix existing root-owned files: `sudo chown -R $(id -u):$(id -g) Builds/`

---

## Cache Issues

### Library cache corruption

**Error:** `Failed to import asset` or `Asset database is corrupted`

**Cause:** Library cache is incompatible with the current Unity version or was corrupted by a failed build.

**Fix:**
- Delete the cache volume: `docker volume rm unity-lib-<hash>`
- Use `--clean-build` flag to skip cache restoration
- Set `cache-mode: off` in the workflow

---

## Container Resource Issues

### Container out of memory

**Error:** `Killed` or `OOMKilled` in container status (exit code 137)

**Cause:** Unity build exceeded the container's memory limit.

**Fix:**
- Increase memory limit: `--container-memory 12g`
- IL2CPP Android builds typically need 8+ GB
- WebGL builds may need 8+ GB

### Unity process killed

**Error:** Exit code 137 (SIGKILL)

**Cause:** OOM killer, timeout, or manual cancellation.

**Fix:**
- Check container memory limits
- Increase workflow timeout: `timeout-minutes: 90`
- Check for infinite loops in build hooks
- Check `Logs/Editor.log` for last operation before kill

---

## Build Output Issues

### Missing artifact

**Error:** `Expected artifact not found` or build reports success but no output file

**Cause:** Unity exited with code 0 but did not produce the expected build artifact.

**Fix:**
- Check `BuildReports/build-report.json` for build details
- Check `Logs/Editor.log` for Unity-side errors
- Verify `outputDirectory` in BuildConfig matches expected path
- Verify scene list is not empty
- The wrapper treats missing expected artifacts as failure even when Unity exits 0

### Missing Editor.log

**Error:** No log file in the Logs directory after build failure.

**Cause:** Container crashed before Unity wrote any log, or the log mount was misconfigured.

**Fix:**
- Check Docker container exit code for OOM or signal kills
- Verify the Logs directory mount in the docker command
- Use `--dry-run` to inspect the mount configuration

---

## Platform Issues

### Unsupported platform

**Error:** `Target 'iOS' is unsupported by the Docker-only build platform`

**Cause:** Attempting to build an unsupported target (iOS, Windows).

**Fix:**
- Use a dedicated macOS pipeline for iOS builds
- Use a dedicated Windows pipeline for Windows builds
- See [PLATFORM_LIMITATIONS.md](PLATFORM_LIMITATIONS.md)

### Android SDK/NDK mismatch

**Error:** `Android SDK/NDK version mismatch` or Gradle build failures

**Cause:** The project requires a different SDK/NDK version than installed in the image.

**Fix:**
- Check the image manifest for installed SDK/NDK versions
- Update `docker/variants/android.Dockerfile` and rebuild the image
- Do not install SDK components at build time

---

## Schema Validation Errors

### Empty scenes array

**Error:** `scenes: must NOT have fewer than 1 items`

**Fix:** Add at least one scene path to the `scenes` array.

### Invalid bundleVersion

**Error:** `bundleVersion: must match pattern`

**Fix:** Use `MAJOR.MINOR.PATCH` format, e.g. `"1.0.0"` not `"1.0"`.

### Invalid applicationId

**Error:** `android.applicationId: must match pattern`

**Fix:** Use reverse-DNS format: `com.company.game`. Must start with a letter.

### Production dev build

**Error:** `developmentBuild must be false for production`

**Fix:** Set `developmentBuild: false` in `BuildConfig.production.json`.

---

## General Issues

### Slow first build

**Cause:** No Library cache exists. Unity must import all assets.

**Fix:**
- Expected for first build; subsequent builds reuse cached Library volume
- Set `cache-mode: safe` (default) to enable caching

### Build passes locally but fails in CI

**Fix:**
- Use the same image reference locally and in CI
- Use `--dry-run` to compare Docker commands
- Check Unity version matches exactly
- Verify all required secrets are set in CI

---

---

## iOS Issues

### "No signing certificate found"

**Error:** `error: No signing certificate "iPhone Distribution" found`

**Cause:** The distribution certificate is not in the temp keychain.

**Fix:**
1. Verify `IOS_DISTRIBUTION_CERTIFICATE_BASE64` contains a valid P12 (not truncated)
2. Confirm `IOS_DISTRIBUTION_CERTIFICATE_PASSWORD` is correct
3. Check certificate expiry: download and inspect locally:
   ```bash
   echo "$IOS_DISTRIBUTION_CERTIFICATE_BASE64" | base64 --decode > cert.p12
   openssl pkcs12 -in cert.p12 -nokeys -passin pass:YOUR_PASS | openssl x509 -noout -dates
   ```
4. Ensure the certificate is a **Distribution** cert, not a Development cert

### "Provisioning profile doesn't include the application-identifier"

**Error:** `error: Provisioning profile "..." doesn't include the application-identifier`

**Cause:** Bundle ID mismatch between BuildConfig and the provisioning profile.

**Fix:**
- Confirm `ios.bundleIdentifier` in BuildConfig exactly matches the profile's App ID
- Download the profile and inspect: `security cms -D -i profile.mobileprovision | grep -A2 application-identifier`

### "Provisioning profile is expired"

**Error:** `error: Provisioning profile "..." is expired`

**Fix:**
1. Renew the profile at [Apple Developer Portal](https://developer.apple.com/account/resources/profiles/list)
2. Download the new `.mobileprovision`
3. Re-encode: `base64 -i NewProfile.mobileprovision`
4. Update `IOS_PROVISIONING_PROFILE_BASE64` secret

### Xcode Migration Issues

**Symptom:** Builds that worked with Xcode 14 fail with Xcode 15.

**Common causes:**
- Bitcode is removed in Xcode 14+ — set `enableBitcode: false`
- Privacy manifest requirements in Xcode 15 — add `PrivacyInfo.xcprivacy` to your Unity project

**Fix:** Set `xcodeVersion` in BuildConfig to pin the Xcode version:
```json
"ios": {
  "xcodeVersion": "15.2"
}
```

### Archive Fails — "The scheme does not exist"

**Error:** `error: The scheme "MyGame" does not exist`

**Cause:** Unity generated a Xcode project with a different scheme name.

**Fix:** Check what scheme was generated:
```bash
xcodebuild -list -workspace Builds/iOS/Xcode/MyGame.xcworkspace
```
Set the correct scheme via the `SCHEME` environment variable or the iOS build script parameter.

### Missing Unity iOS Build Support

**Error:** `Error building Player: Currently selected build target (iOS) requires...`

**Cause:** The Unity installation on the macOS runner does not have the iOS Build Support module.

**Fix:**
- The `macos-unity-xcode` runner must have Unity installed with iOS Build Support
- Check the runner setup in the workflow: `unity-version` must match the installed version
- For self-hosted runners, reinstall Unity with iOS module: `Unity Hub → Installs → Add Module → iOS Build Support`

### Unity License Failure on macOS

**Error:** `Unity license activation failed`

**Cause:** `UNITY_LICENSE`, `UNITY_EMAIL`, or `UNITY_PASSWORD` secrets are wrong or missing.

**Fix:** Same as Docker lane. See [Unity License Issues](#unity-license-issues) section above.

### TestFlight Upload Rejected

**Error:** `ERROR ITMS-90503: Invalid Bundle` or similar

**Common causes:**
- Build number (CFBundleVersion) already used — increment it
- Marketing version must be `MAJOR.MINOR.PATCH` format
- Missing required capabilities or privacy strings

**Fix:**
- Increment the release tag (new tag → new `github_run_number` → new build number)
- Check App Store Connect for specific rejection details under **Activity → Builds**

### TestFlight Processing Delays

**Symptom:** Build uploaded successfully but not visible to testers.

**Cause:** App Store Connect processing queue. Normal processing takes 5–30 minutes; unusual binary characteristics (new frameworks, large size) can take hours.

**Fix:** Wait. Check App Store Connect → TestFlight → Your App → Builds for status. If stuck in "Processing" for > 2 hours, contact Apple Developer Support.

### Certificate Rotation — Builds Fail After Rotation

**Symptom:** Builds start failing after a certificate was rotated.

**Cause:** Old provisioning profiles are bound to the old certificate. After rotation, all profiles must be regenerated.

**Fix:**
1. Revoke the old certificate in Apple Developer Portal (only after new cert is working)
2. Regenerate all provisioning profiles (they are now signed with the new cert)
3. Update both `IOS_DISTRIBUTION_CERTIFICATE_BASE64` and `IOS_PROVISIONING_PROFILE_BASE64`

### iOS Build Attempted on Linux Runner

**Error:** `Platform 'iOS' requires executor 'macos-unity-xcode'`

**Cause:** `target-platform: iOS` was used in a workflow running on `ubuntu-latest`.

**Fix:** iOS requires a macOS runner with `build-engine: local`. The resolver enforces this — route iOS to a macOS runner (`RUNNER_MACOS_LABEL`, a runner policy, or a runner picked by name in the dispatch form; see [MULTI_RUNNER_SCHEDULING.md](MULTI_RUNNER_SCHEDULING.md)).

---

## Still Stuck?

1. Download the full build log artifact from the failed workflow run
2. Search for `Error` in `Editor.log` (case-sensitive; Unity uses capital-E)
3. For iOS: check `Logs/iOS/xcodebuild.log` for Xcode errors
4. Open an issue with: relevant log lines, BuildConfig (redact secrets), workflow run URL
