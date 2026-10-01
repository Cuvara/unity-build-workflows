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
//   -buildTarget        already applied by Unity before this method runs
//   ANDROID_KEYSTORE_PASS / ANDROID_KEY_PASS
//                       only on the native lanes, from the optional secrets of
//                       the same names. Used only when the project signs with
//                       its own keystore (Player Settings > Publishing
//                       Settings > Custom Keystore): Unity never saves keystore
//                       passwords in the project, so batchmode cannot sign
//                       without them. Applied in memory for this build; never
//                       written to disk or logged.
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

            if (target == BuildTarget.Android)
            {
                EditorUserBuildSettings.buildAppBundle = appBundle;
                if (!ApplyAndroidKeystorePasswords())
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
    /// ANDROID_KEY_PASS when the project has a custom keystore; fails with the
    /// fix when they are missing, instead of Unity's "Can not sign the
    /// application". Returns false after failing.
    /// </summary>
    private static bool ApplyAndroidKeystorePasswords()
    {
        if (!PlayerSettings.Android.useCustomKeystore)
        {
            return true;
        }

        string storePass = Environment.GetEnvironmentVariable("ANDROID_KEYSTORE_PASS");
        string keyPass = Environment.GetEnvironmentVariable("ANDROID_KEY_PASS");
        if (string.IsNullOrEmpty(storePass))
        {
            Fail("This project signs Android builds with its own keystore ("
                 + PlayerSettings.Android.keystoreName + ", alias "
                 + PlayerSettings.Android.keyaliasName + "), but no password reached "
                 + "the build. Add repository secrets ANDROID_KEYSTORE_PASS (and "
                 + "ANDROID_KEY_PASS if the alias password differs) and pass them "
                 + "to unity-pipeline.yml.");
            return false;
        }

        PlayerSettings.Android.keystorePass = storePass;
        PlayerSettings.Android.keyaliasPass = string.IsNullOrEmpty(keyPass) ? storePass : keyPass;
        Debug.Log("[PlayerBuilder] Signing with the project's keystore "
                  + $"({PlayerSettings.Android.keystoreName}, alias {PlayerSettings.Android.keyaliasName}).");
        return true;
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
