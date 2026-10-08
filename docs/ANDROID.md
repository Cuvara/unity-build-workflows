# Android Builds

Android builds run inside Docker containers using the `android` image variant.

---

## Image Variant

```
ghcr.io/<IMAGE_NAMESPACE>/unity-builder:<unity-version>-android-<tooling-version>
```

Example: `ghcr.io/<IMAGE_NAMESPACE>/unity-builder:6000.0.26f1-android-v2.0.0`

Base: `unityci/editor:6000.0.26f1-android-3`

Includes: Unity Editor, Android SDK, Android NDK, JDK, Gradle

> **Image must be published first.** If no image has been built for your Unity version, consumer builds will fail with an image-not-found error. See [IMAGE_LIFECYCLE.md](IMAGE_LIFECYCLE.md#bootstrap) for the bootstrap process.

---

## Workflow Usage

Android builds run in `unity-pipeline.yml`, called by the consumer's numbered entry
workflows (`templates/consumer-10-build-development.yml`, `consumer-11-build-release.yml`).
Put `Android` in the environment's `BUILD_PLATFORMS` repository variable, or pick it in
the dispatch form:

```bash
gh workflow run "Build / Development" --ref develop -f platform=Android
```

The build type decides the artifact: a development build is an APK, a release build
(Build / Release) an App Bundle.

Setup: [CONSUMER_SETUP.md](CONSUMER_SETUP.md). Variables: [REPOSITORY_VARIABLES.md](REPOSITORY_VARIABLES.md).

---

## BuildConfig Fields

See [BUILD_CONFIG.md](BUILD_CONFIG.md#android-object) for full reference.

| Field | Notes |
|---|---|
| `applicationId` | Must match your Play Console application ID exactly |
| `buildAppBundle` | `true` for Play Store, `false` for sideloading/Firebase |
| `architecture` | `ARM64` for modern devices; `All` only for x86 emulators |
| `keystoreMode` | `custom` for production signing, `debug` for dev/test |
| `symbolExport` | Enable `public` or `debugging` for Crashlytics |

---

## Signing

### Debug Signing (development)

When `keystoreMode` is `debug`, Unity uses the Android debug keystore from the SDK inside the container. No secrets required. Not suitable for Play Store.

### Custom Signing (staging/production)

Required secrets:
```
ANDROID_KEYSTORE_BASE64      # base64-encoded .jks or .keystore
ANDROID_KEYSTORE_PASSWORD    # keystore password
ANDROID_KEY_ALIAS            # key alias
ANDROID_KEY_PASSWORD         # key password
```

The signing step runs as a post-container operation. The keystore is decoded to a temporary file, used for signing, and deleted.

### Generating a Keystore

```bash
keytool -genkey -v \
  -keystore my-release-key.keystore \
  -alias my-key-alias \
  -keyalg RSA \
  -keysize 2048 \
  -validity 10000
```

Encode for GitHub secret:
```bash
base64 -w 0 my-release-key.keystore
```

---

## Build Output

| `buildAppBundle` | Output | Use Case |
|---|---|---|
| `true` | `*.aab` | Google Play Store |
| `false` | `*.apk` | Sideloading, Firebase App Distribution |

Output location: `Builds/Android/`

---

## Local Android Build

```bash
python3 scripts/docker/run_unity_container.py \
  --project-path . \
  --unity-version 6000.0.26f1 \
  --target-platform Android \
  --environment development \
  --build-config-path BuildConfig
```

---

## Android SDK/NDK Versions

SDK and NDK versions are pinned in the Docker image. Check the image manifest for installed versions.

If your project requires different versions, update `docker/variants/android.Dockerfile` and rebuild the image. Do not install SDK components at build time.

---

## Troubleshooting

**"Keystore was tampered with, or password was incorrect"**
Verify `ANDROID_KEYSTORE_PASS` matches the keystore. Test locally: `keytool -list -v -keystore decoded.keystore`

**"Gradle build failed: SDK location not found"**
The Android SDK is pre-installed in the Docker image. If using a custom image, verify `ANDROID_SDK_ROOT` is set.

**"IL2CPP build failed: NDK not found"**
The NDK is pre-installed in the android variant image. Verify the image variant is `android`.

**"minSdkVersion X is lower than NDK's minimum"**
Upgrade `minSdkVersion` in BuildConfig to match the NDK minimum (usually 21).
