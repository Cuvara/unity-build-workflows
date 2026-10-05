// PlayerBuilder.cs — reference implementation for consumers of unity-build-workflows.
//
// Copy to Assets/BuildScripts/Editor/PlayerBuilder.cs in your Unity project and
// adjust to taste. It must stay in an Editor assembly and in the GLOBAL
// namespace, or `-executeMethod PlayerBuilder.Build` cannot resolve the name.
//
// WHO NEEDS THIS
//   Required on the self-hosted lanes (RUNNER_TYPE=self-hosted + BUILD_ENGINE=local):
//   reusable-build-platform.yml substitutes `PlayerBuilder.Build` when the
//   `build-method` input is empty. `-buildTarget` only switches the active
//   target — without an -executeMethod that calls BuildPipeline.BuildPlayer,
//   Unity exits 0 and CI reports success with no player in build/.
//
//   NOT required on the default docker lane: `build-method` defaults to '' and
//   game-ci/unity-builder supplies its own builder.
//
// CI CONTRACT — what the toolkit actually passes (verified against
// reusable-build-platform.yml):
//   BUILD_OUTPUT_DIR    always "build"; the artifact upload takes build/
//   ANDROID_APP_BUNDLE  "true" for an .aab, otherwise an .apk
//   BUILD_NUMBER        the store-facing counter the pipeline decided for this
//                       run: Android bundleVersionCode, iOS CFBundleVersion,
//                       macOS build number. Without it the project's own
//                       value ships, identical on every build, and the second
//                       upload of a version is rejected by the store.
//   APP_VERSION         the version name (bundleVersion), e.g. 1.4.2. Read
//                       from ProjectSettings by the pipeline, so normally the
//                       same; applied when set so a release can pin it.
//   Both are applied in memory for this build and restored afterwards.
//   -buildTarget        already applied by Unity before this method runs
//   ANDROID_KEYSTORE_PASS / ANDROID_KEY_PASS
//                       only on the native lanes, from the optional secrets of
//                       the same names. Used only when the project signs with
//                       its own keystore (Player Settings > Publishing
//                       Settings > Custom Keystore): Unity never saves keystore
//                       passwords in the project, so batchmode cannot sign
//                       without them. Applied in memory for this build; never
//                       written to disk or logged. Without a password a
//                       development APK falls back to Unity's debug key; a
//                       release App Bundle fails.
//   Projects without a custom keystore get Unity's debug signing, as before;
//   release signing on the unity-build-android.yml path is still a post-build
//   host step (scripts/android/sign_android_build.sh).

using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

public static class PlayerBuilder
{
    public static void Build()
    {
        try
        {
            BuildTarget target = EditorUserBuildSettings.activeBuildTarget;

            string outputRoot = ReadArg("-buildOutput")
                                ?? Environment.GetEnvironmentVariable("BUILD_OUTPUT_DIR")
                                ?? "build";

            // When the output root is relative ("build"), resolve it against
            // GITHUB_WORKSPACE so the artifact lands where the upload step
            // expects it — <workspace>/build/. Without this, a project-path
            // other than "." causes Unity to write build/ inside the project
            // subdirectory, and the pipeline's upload misses it.
            if (!Path.IsPathRooted(outputRoot))
            {
                string workspace = Environment.GetEnvironmentVariable("GITHUB_WORKSPACE");
                if (!string.IsNullOrEmpty(workspace))
                {
                    outputRoot = Path.Combine(workspace, outputRoot);
                }
            }

            string[] scenes = EditorBuildSettings.scenes
                .Where(s => s.enabled)
                .Select(s => s.path)
                .ToArray();

            if (scenes.Length == 0)
            {
                Fail("No enabled scenes in EditorBuildSettings — nothing to build. "
                     + "Add scenes under File > Build Settings.");
                return;
            }

            bool appBundle = string.Equals(
                Environment.GetEnvironmentVariable("ANDROID_APP_BUNDLE"),
                "true", StringComparison.OrdinalIgnoreCase);

            VersionState version = ApplyVersion(target);
            if (version == null)
            {
                return;
            }

            bool debugSigned = false;
            if (target == BuildTarget.Android)
            {
                EditorUserBuildSettings.buildAppBundle = appBundle;
                if (!ApplyAndroidKeystorePasswords(appBundle, out debugSigned))
                {
                    return;
                }
            }

            string targetDir = Path.Combine(outputRoot, target.ToString());
            Directory.CreateDirectory(targetDir);
            string locationPath = Path.Combine(targetDir, OutputName(target, appBundle));

            Debug.Log($"[PlayerBuilder] target={target} scenes={scenes.Length} out={locationPath}");

            BuildReport report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
            {
                scenes = scenes,
                locationPathName = locationPath,
                target = target,
                options = BuildOptions.None,
            });

            version.Restore();

            if (debugSigned)
            {
                // The switch was for this build only; leave the project as it was.
                PlayerSettings.Android.useCustomKeystore = true;
            }

            BuildSummary summary = report.summary;
            Debug.Log($"[PlayerBuilder] result={summary.result} "
                      + $"size={summary.totalSize} bytes duration={summary.totalTime}");

            if (summary.result != BuildResult.Succeeded)
            {
                // Unity's own exit code does NOT reflect a failed BuildPlayer when
                // the Editor was started with -quit, so fail the job explicitly.
                Fail($"Build did not succeed: {summary.result} "
                     + $"({summary.totalErrors} errors)");
                return;
            }

            EditorApplication.Exit(0);
        }
        catch (Exception e)
        {
            Fail($"Unhandled exception: {e}");
        }
    }

    /// <summary>
    /// A project that signs with its own keystore needs its passwords, which
    /// Unity keeps only in memory. Applies ANDROID_KEYSTORE_PASS /
    /// ANDROID_KEY_PASS when the project has a custom keystore.
    ///
    /// Without a password, a development build (APK) is signed with Unity's
    /// debug key so testers still get an installable build — it cannot update
    /// an install signed with the release key. A release build (App Bundle)
    /// fails with the fix instead of Unity's "Can not sign the application".
    /// Returns false after failing; switchedToDebug tells the caller to restore
    /// the custom keystore setting after the build.
    /// </summary>
    private static bool ApplyAndroidKeystorePasswords(bool appBundle, out bool switchedToDebug)
    {
        switchedToDebug = false;
        if (!PlayerSettings.Android.useCustomKeystore)
        {
            return true;
        }

        string storePass = Environment.GetEnvironmentVariable("ANDROID_KEYSTORE_PASS");
        string keyPass = Environment.GetEnvironmentVariable("ANDROID_KEY_PASS");
        if (string.IsNullOrEmpty(storePass) && !appBundle)
        {
            Debug.LogWarning("[PlayerBuilder] No ANDROID_KEYSTORE_PASS for "
                             + PlayerSettings.Android.keystoreName + ": signing this development "
                             + "APK with Unity's debug key. It installs on its own but cannot update "
                             + "an install signed with the release key. Set ANDROID_KEYSTORE_PASS in "
                             + "the development environment to sign with the project's keystore.");
            PlayerSettings.Android.useCustomKeystore = false;
            switchedToDebug = true;
            return true;
        }
        if (string.IsNullOrEmpty(storePass))
        {
            Fail("This project signs Android builds with its own keystore ("
                 + PlayerSettings.Android.keystoreName + ", alias "
                 + PlayerSettings.Android.keyaliasName + "), but no password reached "
                 + "this release build. Set ANDROID_KEYSTORE_PASS (and "
                 + "ANDROID_KEY_PASS if the alias password differs) in the build's GitHub "
                 + "Environment and list them under secrets: in the caller workflow.");
            return false;
        }

        PlayerSettings.Android.keystorePass = storePass;
        PlayerSettings.Android.keyaliasPass = string.IsNullOrEmpty(keyPass) ? storePass : keyPass;
        Debug.Log("[PlayerBuilder] Signing with the project's keystore "
                  + $"({PlayerSettings.Android.keystoreName}, alias {PlayerSettings.Android.keyaliasName}).");
        return true;
    }

    /// <summary>The project's version settings before this build changed them.</summary>
    private sealed class VersionState
    {
        public string BundleVersion;
        public int AndroidVersionCode;
        public string IosBuildNumber;
        public string MacBuildNumber;

        public void Restore()
        {
            PlayerSettings.bundleVersion = BundleVersion;
            PlayerSettings.Android.bundleVersionCode = AndroidVersionCode;
            PlayerSettings.iOS.buildNumber = IosBuildNumber;
            PlayerSettings.macOS.buildNumber = MacBuildNumber;
        }
    }

    /// <summary>
    /// Applies APP_VERSION and BUILD_NUMBER from the pipeline. The build number
    /// must change on every store upload; the project's own value never does,
    /// so without this every build ships the same versionCode/CFBundleVersion.
    /// A number below the project's current Android versionCode is applied but
    /// warned about: the store has likely seen higher (set BUILD_NUMBER_OFFSET).
    /// Returns null after failing on an invalid BUILD_NUMBER.
    /// </summary>
    private static VersionState ApplyVersion(BuildTarget target)
    {
        var state = new VersionState
        {
            BundleVersion = PlayerSettings.bundleVersion,
            AndroidVersionCode = PlayerSettings.Android.bundleVersionCode,
            IosBuildNumber = PlayerSettings.iOS.buildNumber,
            MacBuildNumber = PlayerSettings.macOS.buildNumber,
        };

        string appVersion = Environment.GetEnvironmentVariable("APP_VERSION");
        if (!string.IsNullOrWhiteSpace(appVersion))
        {
            PlayerSettings.bundleVersion = appVersion.Trim();
        }

        string raw = Environment.GetEnvironmentVariable("BUILD_NUMBER");
        if (string.IsNullOrWhiteSpace(raw))
        {
            Debug.LogWarning("[PlayerBuilder] No BUILD_NUMBER: building with the project's own "
                             + $"build number (Android versionCode {state.AndroidVersionCode}, "
                             + $"iOS {state.IosBuildNumber}).");
            return state;
        }
        if (!int.TryParse(raw.Trim(), out int buildNumber) || buildNumber <= 0)
        {
            Fail($"BUILD_NUMBER must be a positive integer, got '{raw}'.");
            return null;
        }

        switch (target)
        {
            case BuildTarget.Android:
                if (buildNumber < state.AndroidVersionCode)
                {
                    Debug.LogWarning($"[PlayerBuilder] BUILD_NUMBER {buildNumber} is lower than the "
                                     + $"project's versionCode {state.AndroidVersionCode}; Google Play "
                                     + "rejects a versionCode it has already moved past. Set the "
                                     + "repository variable BUILD_NUMBER_OFFSET.");
                }
                PlayerSettings.Android.bundleVersionCode = buildNumber;
                break;
            case BuildTarget.iOS:
                PlayerSettings.iOS.buildNumber = buildNumber.ToString();
                break;
            case BuildTarget.StandaloneOSX:
                PlayerSettings.macOS.buildNumber = buildNumber.ToString();
                break;
        }
        Debug.Log($"[PlayerBuilder] version {PlayerSettings.bundleVersion} build {buildNumber} ({target}).");
        return state;
    }

    /// <summary>Product name for each target, with the extension CI expects.</summary>
    private static string OutputName(BuildTarget target, bool appBundle)
    {
        string product = SanitizeFileName(Application.productName);
        if (string.IsNullOrEmpty(product)) product = "Player";

        switch (target)
        {
            case BuildTarget.Android:
                return product + (appBundle ? ".aab" : ".apk");
            case BuildTarget.StandaloneWindows:
            case BuildTarget.StandaloneWindows64:
                return product + ".exe";
            case BuildTarget.StandaloneLinux64:
                // Dedicated-server builds use the same extensionless name; the
                // subtarget is set by -standaloneBuildSubtarget from CI.
                return product;
            case BuildTarget.StandaloneOSX:
                return product + ".app";
            case BuildTarget.WebGL:
                // WebGL writes a directory, not a file.
                return product;
            case BuildTarget.iOS:
                // Unity emits an Xcode project directory; the macOS lane archives it.
                return product;
            default:
                return product;
        }
    }

    private static string SanitizeFileName(string value)
    {
        if (string.IsNullOrEmpty(value)) return value;
        return string.Concat(value.Split(Path.GetInvalidFileNameChars())).Trim();
    }

    /// <summary>Reads `-name value` from the Editor command line, or null.</summary>
    private static string ReadArg(string name)
    {
        string[] args = Environment.GetCommandLineArgs();
        for (int i = 0; i < args.Length - 1; i++)
        {
            if (string.Equals(args[i], name, StringComparison.Ordinal))
            {
                return args[i + 1];
            }
        }
        return null;
    }

    private static void Fail(string message)
    {
        // "::error::" makes the line show up as an annotation in the Actions UI.
        Debug.LogError($"::error::[PlayerBuilder] {message}");
        EditorApplication.Exit(1);
    }
}
