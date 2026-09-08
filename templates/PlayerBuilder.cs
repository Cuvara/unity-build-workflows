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
//   Nothing else. Android signing is a POST-BUILD host step
//   (scripts/android/sign_android_build.sh) on the unity-build-android.yml
//   path, so no keystore variables reach the Editor here — do not read them.

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
