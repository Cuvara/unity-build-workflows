# iOS Signing Setup

This document covers certificate and provisioning profile setup for iOS CI signing.

---

## Overview

iOS CI signing requires:

1. **Distribution certificate** — P12 file (`.p12`) issued by Apple, base64-encoded into a GitHub Secret
2. **Provisioning profile** — `.mobileprovision` file linked to your certificate, base64-encoded
3. **App Store Connect API key** — for TestFlight uploads (`.p8` file + key ID + issuer ID)

All credentials are GitHub Secrets. **No secrets live in BuildConfig JSON or workflow YAML files.**

---

## Required GitHub Secrets

Navigate to **Settings → Secrets and variables → Actions** in your game repository and add:

| Secret Name | Description |
|---|---|
| `IOS_DISTRIBUTION_CERTIFICATE_BASE64` | Base64-encoded P12 distribution certificate |
| `IOS_DISTRIBUTION_CERTIFICATE_PASSWORD` | P12 export password |
| `IOS_PROVISIONING_PROFILE_BASE64` | Base64-encoded `.mobileprovision` file |
| `APP_STORE_CONNECT_KEY_ID` | App Store Connect API key ID (e.g. `AB1CD2EF3G`) |
| `APP_STORE_CONNECT_ISSUER_ID` | App Store Connect issuer ID (UUID) |
| `APP_STORE_CONNECT_PRIVATE_KEY` | Contents of the `.p8` ASC API key file |
| `UNITY_LICENSE` | Complete Unity `.ulf` license file content |
| `UNITY_EMAIL` | Unity account email (for license activation) |
| `UNITY_PASSWORD` | Unity account password (for license activation) |

Secrets for TestFlight (`APP_STORE_CONNECT_*`) are only required when `uploadToTestFlight: true`.
Scope these to a `production` GitHub Environment for branch protection. See [IOS_RELEASE.md](IOS_RELEASE.md).

**Pipeline (`unity-pipeline.yml`) builds:** set the three signing secrets
(`IOS_DISTRIBUTION_CERTIFICATE_BASE64`, `IOS_DISTRIBUTION_CERTIFICATE_PASSWORD`,
`IOS_PROVISIONING_PROFILE_BASE64`) as **environment secrets** in each of
`development`, `staging` and `production`, under exactly these names. The
build job declares its environment and reads them from there; the caller
still lists the three names under `secrets:` (see Step 4 of the guide). See
[MIGRATING_TO_ENVIRONMENT_SECRETS.md](MIGRATING_TO_ENVIRONMENT_SECRETS.md).

---

## Step 1: Export Your Distribution Certificate

### From Keychain Access (macOS)

1. Open **Keychain Access** → **My Certificates**
2. Find **iPhone Distribution: My Studio (TEAMID)**
3. Right-click → **Export** → choose `.p12` format
4. Set a strong export password (you'll need it as `IOS_DISTRIBUTION_CERTIFICATE_PASSWORD`)
5. Base64-encode the `.p12`:

```bash
base64 -i MyDistribution.p12 | pbcopy
# Pastes the base64 string to clipboard
```

6. Add the clipboard contents as secret `IOS_DISTRIBUTION_CERTIFICATE_BASE64`

### Verify Certificate Validity

```bash
# Check expiry
openssl pkcs12 -in MyDistribution.p12 -nokeys -passin pass:YOUR_PASSWORD \
  | openssl x509 -noout -dates

# Expected output:
# notBefore=Jan  1 00:00:00 2024 GMT
# notAfter=Jan  1 00:00:00 2025 GMT
```

Distribution certificates are valid for **1 year**. Rotate them before expiry.

---

## Step 2: Download Your Provisioning Profile

1. Go to [Apple Developer Portal](https://developer.apple.com/account/resources/profiles/list)
2. Select the distribution profile matching your app (App Store, Ad Hoc, or Enterprise)
3. Download the `.mobileprovision` file
4. Base64-encode it:

```bash
base64 -i MyProfile.mobileprovision | pbcopy
```

5. Add as secret `IOS_PROVISIONING_PROFILE_BASE64`

### Profile Contents Check

```bash
# Inspect profile expiry and entitlements
security cms -D -i MyProfile.mobileprovision | \
  plutil -p - | grep -E "ExpirationDate|Name|TeamIdentifier"
```

Provisioning profiles expire. Renew them **before** the `ExpirationDate`.

---

## Step 3: Create an App Store Connect API Key

TestFlight uploads and symbol uploads use the App Store Connect API (not Apple ID + password).

1. Go to [App Store Connect → Users and Access → Keys](https://appstoreconnect.apple.com/access/api)
2. Click **+** to generate a new key
3. Name: `iOS CI Deploy` — Role: **App Manager** (or Developer for test builds only)
4. Download the `.p8` file — **it can only be downloaded once**
5. Note the **Key ID** (shown in the key list) and **Issuer ID** (shown at the top of the Keys page)
6. Add secrets:
   - `APP_STORE_CONNECT_KEY_ID` = the 10-char key ID
   - `APP_STORE_CONNECT_ISSUER_ID` = the UUID issuer ID
   - `APP_STORE_CONNECT_PRIVATE_KEY` = the full `.p8` file contents (include `-----BEGIN PRIVATE KEY-----` header)

---

## How Signing Works in CI

The `setup_signing.sh` script:

1. Creates a **temporary keychain** (`ios-build-temp.keychain-db`) with a random password
2. Imports the distribution certificate from `IOS_DISTRIBUTION_CERTIFICATE_BASE64`
3. Sets the keychain as the default for the build session
4. Decodes and installs the provisioning profile to `~/Library/MobileDevice/Provisioning Profiles/`
5. Writes the ASC private key to a temp `.p8` file

The `cleanup_ios.sh` script (always runs via `trap`):

1. Deletes the temp keychain: `security delete-keychain ios-build-temp.keychain-db`
2. Removes the installed provisioning profile
3. Deletes the temp `.p8` file

```bash
# setup_signing.sh — simplified excerpt
KEYCHAIN_PATH="${RUNNER_TEMP}/ios-build-temp.keychain-db"
KEYCHAIN_PASSWORD="$(openssl rand -hex 24)"

security create-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH"
security set-keychain-settings -lut 21600 "$KEYCHAIN_PATH"
security unlock-keychain -p "$KEYCHAIN_PASSWORD" "$KEYCHAIN_PATH"

echo "$IOS_DISTRIBUTION_CERTIFICATE_BASE64" | base64 --decode -o /tmp/cert.p12
security import /tmp/cert.p12 -P "$IOS_DISTRIBUTION_CERTIFICATE_PASSWORD" \
  -A -t cert -f pkcs12 -k "$KEYCHAIN_PATH"
rm -f /tmp/cert.p12

security list-keychain -d user -s "$KEYCHAIN_PATH"
```

---

### Pipeline lane (`unity-pipeline.yml`): signing comes from the profile

On the pipeline's iOS release build the player is built with the project's own
PlayerSettings, which may carry no Apple team and automatic signing. Before the
archive, `scripts/ios/configure_xcode_signing.py` reads everything signing
needs from the provisioning profile and writes manual signing into the
generated Xcode project:

| Value | Source |
|---|---|
| Team ID | the profile's `TeamIdentifier` |
| Provisioning profile | the profile's `Name` (set as `PROVISIONING_PROFILE_SPECIFIER` on the `Unity-iPhone` target only; framework targets get the team, no profile) |
| Bundle ID | the `Unity-iPhone` target's `PRODUCT_BUNDLE_IDENTIFIER`, checked against the profile's `application-identifier` (wildcards accepted) |
| Export method | must match the profile type: an **App Store** profile for the default `ios-export-method: app-store` |

So the project needs no `appleDeveloperTeamID` or manual-signing setup in
PlayerSettings: the three signing secrets are enough. A bundle ID or profile
type mismatch fails before `xcodebuild` runs, with the reason in the log.

### Signed development builds

By default only release builds are signed: a development iOS build is an Xcode
project, which needs a Mac and Xcode to install. Set
`BUILD_IOS_SIGN_DEVELOPMENT=true` to sign development builds too:

- The build also exports an IPA (`…_development_ios_ipa`), signed with the
  `IOS_*` secrets of the **`development`** environment.
- The export method is whatever that environment's profile allows — use an
  **Ad Hoc** profile (or Development) whose devices include the testers'
  devices. Release builds keep `ios-export-method` (default `app-store`).
- With `BUILD_DELIVERY=firebase` the IPA goes to Firebase App Distribution, so
  testers get the same install link as for Android. Only devices registered in
  the profile can install it.

## Manual Signing vs. Automatic Signing

### Manual Signing (Recommended for CI)

```json
"ios": {
  "signingStyle": "manual",
  "developmentTeamId": "YOURTEAMID1",
  "codeSignIdentity": "iPhone Distribution",
  "provisioningProfileSpecifier": "My Game App Store"
}
```

xcodebuild flags set by the archive script:
```bash
CODE_SIGN_STYLE=Manual
CODE_SIGN_IDENTITY="iPhone Distribution"
PROVISIONING_PROFILE_SPECIFIER="My Game App Store"
DEVELOPMENT_TEAM="YOURTEAMID1"
```

### Automatic Signing (Not Recommended for CI)

Automatic signing requires Xcode to connect to Apple Developer Portal, which is unreliable in CI without interactive authentication. Use manual signing for reproducible CI builds.

If you must use automatic signing:
```json
"ios": {
  "signingStyle": "automatic",
  "developmentTeamId": "YOURTEAMID1"
}
```

Requires an Apple ID authenticated via `fastlane spaceauth` or similar.

---

## Certificate Rotation

Certificates expire after 1 year. When rotating:

1. Export the new `.p12` from Keychain Access
2. Update `IOS_DISTRIBUTION_CERTIFICATE_BASE64` and `IOS_DISTRIBUTION_CERTIFICATE_PASSWORD` secrets
3. Generate a new provisioning profile signed with the new certificate
4. Update `IOS_PROVISIONING_PROFILE_BASE64`
5. Trigger a build to verify signing works before the old certificate expires

The old certificate can be left in secrets until it expires — CI will pick up the new one. Do NOT delete secrets while a build is in progress.

---

## Troubleshooting Signing Issues

### "No signing certificate found"

- Verify `IOS_DISTRIBUTION_CERTIFICATE_BASE64` is not truncated (base64 encoding sometimes wraps lines)
- Check `IOS_DISTRIBUTION_CERTIFICATE_PASSWORD` is correct
- Ensure the certificate is a **Distribution** certificate (not Development)
- Check certificate expiry: `openssl x509 -noout -dates -in cert.pem`

### "Provisioning profile doesn't match"

- Confirm the bundle ID in BuildConfig matches the profile's App ID
- Check the profile type (App Store vs. Ad Hoc vs. Development)
- Verify `developmentTeamId` matches the profile's team

### "No account for team" with automatic signing

Switch to manual signing. Automatic signing requires interactive Apple ID authentication which is unavailable in CI.

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md#ios-issues) for more.
