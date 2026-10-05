// PlayerBuilder -- the toolkit's player build entry point for the native and
// Windows-docker lanes:
//
//   Unity -batchmode -buildTarget <T> -executeMethod Company.BuildPipeline.Editor.PlayerBuilder.Build
//
// The pipeline copies this package into the project's Packages/ folder for the
// duration of the build (scripts/common/install_build_package.sh), so a project
// needs no build script of its own. docs/TOOLKIT_BUILD_PACKAGE.md
//
// Unlike BuildCommand it needs no BuildConfig/: it builds the active target
// with the project's own Player Settings, enabled EditorBuildSettings scenes and
// keystore, and changes only what the pipeline decided for this run.
//
// CI contract -- what reusable-build-platform.yml passes:
//   -buildOutput / BUILD_OUTPUT_DIR
//                       output root, default "build", relative to
//                       GITHUB_WORKSPACE. The player lands at
//                       <root>/<BuildTarget>/<productName><ext>.
//   ANDROID_APP_BUNDLE  "1" or "true" for an .aab, otherwise an .apk
//   BUILD_NUMBER        Android bundleVersionCode, iOS CFBundleVersion, macOS
//                       build number
//   APP_VERSION         version name (bundleVersion)
//   ANDROID_KEYSTORE_PASS / ANDROID_KEY_PASS
//                       passwords for the project's custom keystore. Unity
//                       never saves them, so batchmode cannot sign without.
//   -standaloneBuildSubtarget Server
//                       passed by CI for LinuxServer; Unity applies it.
//   BUILD_PROFILE       Unity 6 Build Profile for this platform: a name in
//                       BUILD_PROFILE_DIR (default Assets/Settings/Build Profiles)
//                       or a project-relative .asset path. Empty or
//                       "ProjectSettings" = the project's Player Settings. The
//                       profile must build the job's platform, or the build
//                       fails. It is activated before version, build number and
//                       keystore are applied, so a profile that overrides
//                       Player Settings gets the run's values too. A profile
//                       left active in Library/ is deactivated for a
//                       ProjectSettings build, and whichever profile was
//                       active before is re-activated afterwards.
// Version, build number and keystore changes are made in memory for this build
// and restored afterwards; nothing is written to ProjectSettings. Unity saves
// the active profile during BuildPlayer, so the profile asset is put back byte
// for byte after the build.
// IBuildHook implementations run before and after the build, as with
// BuildCommand.

using System;
using System.IO;
using System.Linq;
using UnityEditor;
#if UNITY_6000_0_OR_NEWER
using UnityEditor.Build.Profile;
#endif
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace Company.BuildPipeline.Editor
{
    public static class PlayerBuilder
    {
        private const string Tag = "[PlayerBuilder]";

        public static void Build()
        {
            try
            {
                Exit(Run());
            }
            catch (Exception e)
            {
                Fail($"Unhandled exception: {e}");
                Exit(1);
            }
        }

        /// <summary>Builds the active target; returns the process exit code.</summary>
        public static int Run()
        {
            BuildTarget target = EditorUserBuildSettings.activeBuildTarget;
            string outputRoot = ResolveOutputRoot();

            // BUILD_PROFILE: the Unity Build Profile for this platform. Empty or
            // "ProjectSettings" builds with the project's own Player Settings,
            // exactly as before profiles were supported.
            string requestedProfile = RequestedProfile(Environment.GetEnvironmentVariable("BUILD_PROFILE"));
#if UNITY_6000_0_OR_NEWER
            BuildProfile profile = null;
            if (requestedProfile != null)
            {
                profile = LoadBuildProfile(requestedProfile, target);
                if (profile == null)
                {
                    return 1;
                }
            }
#else
            if (requestedProfile != null)
            {
                Fail($"BUILD_PROFILE '{requestedProfile}' needs Unity 6 Build Profiles; "
                     + $"this editor is {Application.unityVersion}.");
                return 1;
            }
#endif

            string[] scenes = EditorBuildSettings.scenes
                .Where(s => s.enabled)
                .Select(s => s.path)
                .ToArray();
            // A profile may carry its own scene list, so the shared list only has
            // to be non-empty when no profile is used.
            if (requestedProfile == null && scenes.Length == 0)
            {
                Fail("No enabled scenes in EditorBuildSettings, nothing to build. "
                     + "Add scenes under File > Build Profiles.");
                return 1;
            }

            bool appBundle = IsTrue(Environment.GetEnvironmentVariable("ANDROID_APP_BUNDLE"));

#if UNITY_6000_0_OR_NEWER
            // Activate the profile BEFORE applying version, build number and
            // keystore: a profile with Player Settings overrides owns those
            // values, so they must be set on it, not on the project's settings.
            // Unity saves the active profile to disk during BuildPlayer, run
            // values included; keep its bytes to put back afterwards.
            string profilePath = profile != null ? AssetDatabase.GetAssetPath(profile) : null;
            byte[] profileBytes = profilePath != null ? File.ReadAllBytes(profilePath) : null;
            string previousProfile = ActivateProfile(profile, requestedProfile);
#else
            Debug.Log($"{Tag} build profile: ProjectSettings (the project's Player Settings)");
#endif
            try
            {
                return BuildActiveTarget(target, outputRoot, scenes, appBundle
#if UNITY_6000_0_OR_NEWER
                                         , profile
#endif
                                         );
            }
            finally
            {
#if UNITY_6000_0_OR_NEWER
                RestoreProfile(previousProfile);
                RestoreProfileFile(profilePath, profileBytes);
#endif
            }
        }

#if UNITY_6000_0_OR_NEWER
        private static int BuildActiveTarget(BuildTarget target, string outputRoot, string[] scenes,
                                             bool appBundle, BuildProfile profile)
#else
        private static int BuildActiveTarget(BuildTarget target, string outputRoot, string[] scenes,
                                             bool appBundle)
#endif
        {
            VersionState version = ApplyVersion(target);
            if (version == null)
            {
                return 1;
            }

            bool debugSigned = false;
            bool previousAppBundle = EditorUserBuildSettings.buildAppBundle;
            try
            {
                if (target == BuildTarget.Android)
                {
                    EditorUserBuildSettings.buildAppBundle = appBundle;
                    if (!ApplyAndroidKeystorePasswords(appBundle, out debugSigned))
                    {
                        return 1;
                    }
                }

                string targetDir = Path.Combine(outputRoot, target.ToString());
                Directory.CreateDirectory(targetDir);
                string locationPath = Path.Combine(targetDir, OutputName(target, appBundle));

                // Project IBuildHook implementations see the same context shape
                // as under BuildCommand: the run's facts, no BuildConfig values.
                var context = new BuildContext
                {
                    Configuration = new BuildConfiguration
                    {
                        Environment = NullIfEmpty(Environment.GetEnvironmentVariable("BUILD_ENVIRONMENT"))
                                      ?? "development",
                        TargetPlatform = target.ToString(),
                        OutputPath = locationPath,
                        Workspace = Environment.GetEnvironmentVariable("GITHUB_WORKSPACE") ?? string.Empty,
                        IsDevelopmentBuild = EditorUserBuildSettings.development,
                    },
                    Target = target,
                };
                var hooks = new BuildHookRegistry();
                hooks.RunBeforeValidation(context);
                hooks.RunBeforeBuild(context);

                // The values that actually go into the player, read back after
                // the profile (if any) was activated and the run's values applied.
                Debug.Log($"{Tag} effective version {PlayerSettings.bundleVersion}, "
                          + $"Android versionCode {PlayerSettings.Android.bundleVersionCode}, "
                          + $"iOS build {PlayerSettings.iOS.buildNumber}");

                BuildReport report;
#if UNITY_6000_0_OR_NEWER
                if (profile != null)
                {
                    Debug.Log($"{Tag} target={target} profile={profile.name} out={locationPath}");
                    report = UnityEditor.BuildPipeline.BuildPlayer(new BuildPlayerWithProfileOptions
                    {
                        buildProfile = profile,
                        locationPathName = locationPath,
                        options = BuildOptions.None,
                    });
                }
                else
#endif
                {
                    Debug.Log($"{Tag} target={target} scenes={scenes.Length} out={locationPath}");
                    report = UnityEditor.BuildPipeline.BuildPlayer(new BuildPlayerOptions
                    {
                        scenes = scenes,
                        locationPathName = locationPath,
                        target = target,
                        options = BuildOptions.None,
                    });
                }

                BuildSummary summary = report.summary;
                Debug.Log($"{Tag} result={summary.result} "
                          + $"size={summary.totalSize} bytes duration={summary.totalTime}");

                var result = new BuildExecutionResult
                {
                    Success = summary.result == BuildResult.Succeeded,
                    ErrorMessage = summary.result == BuildResult.Succeeded
                        ? string.Empty
                        : $"{summary.result} ({summary.totalErrors} errors)",
                    WarningCount = summary.totalWarnings,
                    Duration = summary.totalTime,
                    OutputPath = locationPath,
                    OutputSizeBytes = (long)summary.totalSize,
                };
                context.ExecutionResult = result;
                hooks.RunAfterBuild(context, result);

                if (summary.result != BuildResult.Succeeded)
                {
                    // Under -quit Unity's exit code does not reflect a failed
                    // BuildPlayer, so fail the job explicitly.
                    Fail($"Build did not succeed: {summary.result} ({summary.totalErrors} errors)");
                    return 1;
                }
                return 0;
            }
            finally
            {
                version.Restore();
                if (debugSigned)
                {
                    PlayerSettings.Android.useCustomKeystore = true;
                }
                if (target == BuildTarget.Android)
                {
                    EditorUserBuildSettings.buildAppBundle = previousAppBundle;
                }
            }
        }

        internal static string ResolveOutputRoot()
        {
            string outputRoot = ReadArg("-buildOutput")
                                ?? NullIfEmpty(Environment.GetEnvironmentVariable("BUILD_OUTPUT_DIR"))
                                ?? "build";
            // A relative root is the workspace's build/, where the upload step
            // looks -- not build/ inside a project in a subdirectory.
            if (!Path.IsPathRooted(outputRoot))
            {
                string workspace = Environment.GetEnvironmentVariable("GITHUB_WORKSPACE");
                if (!string.IsNullOrEmpty(workspace))
                {
                    outputRoot = Path.Combine(workspace, outputRoot);
                }
            }
            return outputRoot;
        }

        /// <summary>Where profile names are looked up: Assets/Settings/Build Profiles, Unity's default.</summary>
        internal const string DefaultProfileDirectory = "Assets/Settings/Build Profiles";

        /// <summary>Values of BUILD_PROFILE that mean "the project's Player Settings".</summary>
        private static readonly string[] NoProfileValues =
            { "", "projectsettings", "project-settings", "none", "default", "auto" };

        /// <summary>The requested profile, or null for the project's Player Settings.</summary>
        internal static string RequestedProfile(string raw)
        {
            if (raw == null) return null;
            string value = raw.Trim();
            return NoProfileValues.Contains(value.ToLowerInvariant()) ? null : value;
        }

        /// <summary>
        /// A bare name is an asset in BUILD_PROFILE_DIR (default
        /// Assets/Settings/Build Profiles); anything with a slash or an .asset
        /// extension is a project-relative path.
        /// </summary>
        internal static string ProfileAssetPath(string requested, string directory)
        {
            if (requested.EndsWith(".asset", StringComparison.OrdinalIgnoreCase)
                || requested.IndexOf('/') >= 0 || requested.IndexOf('\\') >= 0)
            {
                return requested.Replace('\\', '/');
            }
            string dir = string.IsNullOrWhiteSpace(directory) ? DefaultProfileDirectory : directory.Trim();
            return $"{dir.Replace('\\', '/').TrimEnd('/')}/{requested}.asset";
        }

#if UNITY_6000_0_OR_NEWER
        /// <summary>
        /// Loads BUILD_PROFILE and checks it builds the job's platform. A missing
        /// profile or one for another platform fails the build: building with
        /// the wrong settings, or for the wrong platform, must never pass.
        /// </summary>
        private static BuildProfile LoadBuildProfile(string requested, BuildTarget target)
        {
            string path = ProfileAssetPath(requested,
                                           Environment.GetEnvironmentVariable("BUILD_PROFILE_DIR"));
            var profile = AssetDatabase.LoadAssetAtPath<BuildProfile>(path);
            if (profile == null)
            {
                string available = string.Join(", ", AssetDatabase.FindAssets("t:BuildProfile")
                    .Select(AssetDatabase.GUIDToAssetPath)
                    .OrderBy(p => p, StringComparer.Ordinal));
                Fail($"Build profile '{requested}' not found at {path}. "
                     + $"Profiles in this project: {(available.Length > 0 ? available : "none")}.");
                return null;
            }

            // BuildProfile has no public target accessor; its serialized
            // m_BuildTarget is what the Build Profiles window shows.
            SerializedProperty targetProperty = new SerializedObject(profile).FindProperty("m_BuildTarget");
            if (targetProperty == null)
            {
                Fail($"Cannot read the platform of build profile {path}; refusing to build {target} with it.");
                return null;
            }
            var profileTarget = (BuildTarget)targetProperty.intValue;
            if (profileTarget != target)
            {
                Fail($"Build profile {path} builds {profileTarget}, but this job builds {target}. "
                     + $"Choose a {target} profile, or ProjectSettings.");
                return null;
            }
            return profile;
        }

        /// <summary>
        /// Makes <paramref name="profile"/> the active profile; null selects the
        /// platform's own settings (ProjectSettings). Returns the asset path of
        /// the previously active profile (null = platform settings).
        /// </summary>
        private static string ActivateProfile(BuildProfile profile, string requested)
        {
            // The active profile is remembered in Library/, so one left active by
            // an earlier build or by the Editor would otherwise override a
            // ProjectSettings build with its own version, keystore and defines.
            BuildProfile previous = BuildProfile.GetActiveBuildProfile();
            // Keep the path, not the object: switching profiles reloads assets,
            // which can leave the old reference destroyed.
            string previousPath = previous != null ? AssetDatabase.GetAssetPath(previous) : null;
            if (previous != profile)
            {
                BuildProfile.SetActiveBuildProfile(profile);
            }
            if (profile == null)
            {
                string note = previous != null
                    ? $"; deactivated profile {previousPath} for this build"
                    : string.Empty;
                Debug.Log($"{Tag} build profile: ProjectSettings (the project's Player Settings{note})");
            }
            else
            {
                Debug.Log($"{Tag} build profile: {requested} ({AssetDatabase.GetAssetPath(profile)})");
            }
            return previousPath;
        }

        /// <summary>Re-activates whatever was active before this build.</summary>
        private static void RestoreProfile(string previousPath)
        {
            BuildProfile previous = string.IsNullOrEmpty(previousPath)
                ? null
                : AssetDatabase.LoadAssetAtPath<BuildProfile>(previousPath);
            BuildProfile active = BuildProfile.GetActiveBuildProfile();
            string activePath = active != null ? AssetDatabase.GetAssetPath(active) : null;
            string wantedPath = previous != null ? previousPath : null;
            if (activePath != wantedPath)
            {
                BuildProfile.SetActiveBuildProfile(previous);
            }
        }

        /// <summary>
        /// Puts the profile asset back byte for byte, so the run's version,
        /// build number and keystore never end up in the project's files.
        /// </summary>
        private static void RestoreProfileFile(string path, byte[] original)
        {
            if (path == null || original == null)
            {
                return;
            }
            if (File.Exists(path) && File.ReadAllBytes(path).SequenceEqual(original))
            {
                return;
            }
            File.WriteAllBytes(path, original);
            AssetDatabase.ImportAsset(path, ImportAssetOptions.ForceUpdate);
            Debug.Log($"{Tag} restored {path} (Unity saved this run's values into it during the build)");
        }
#endif

        /// <summary>"1", "true", "yes" (any case) are true. The workflows send "1".</summary>
        internal static bool IsTrue(string value)
        {
            if (string.IsNullOrWhiteSpace(value)) return false;
            string v = value.Trim();
            return v == "1"
                   || string.Equals(v, "true", StringComparison.OrdinalIgnoreCase)
                   || string.Equals(v, "yes", StringComparison.OrdinalIgnoreCase);
        }

        /// <summary>
        /// A project that signs with its own keystore needs its passwords, which
        /// Unity keeps only in memory. Without a password a development APK is
        /// signed with Unity's debug key (installable, but it cannot update an
        /// install signed with the release key); an App Bundle fails with the fix.
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
                Debug.LogWarning($"{Tag} No ANDROID_KEYSTORE_PASS for "
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
                     + "this App Bundle build. Set ANDROID_KEYSTORE_PASS (and "
                     + "ANDROID_KEY_PASS if the alias password differs) in the build's GitHub "
                     + "Environment and list them under secrets: in the caller workflow.");
                return false;
            }

            PlayerSettings.Android.keystorePass = storePass;
            PlayerSettings.Android.keyaliasPass = string.IsNullOrEmpty(keyPass) ? storePass : keyPass;
            Debug.Log($"{Tag} Signing with the project's keystore "
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
        /// Applies APP_VERSION and BUILD_NUMBER. Returns null after failing on an
        /// invalid BUILD_NUMBER.
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
                Debug.LogWarning($"{Tag} No BUILD_NUMBER: building with the project's own "
                                 + $"build number (Android versionCode {state.AndroidVersionCode}, "
                                 + $"iOS {state.IosBuildNumber}).");
                return state;
            }
            if (!int.TryParse(raw.Trim(), out int buildNumber) || buildNumber <= 0)
            {
                state.Restore();
                Fail($"BUILD_NUMBER must be a positive integer, got '{raw}'.");
                return null;
            }

            switch (target)
            {
                case BuildTarget.Android:
                    if (buildNumber < state.AndroidVersionCode)
                    {
                        Debug.LogWarning($"{Tag} BUILD_NUMBER {buildNumber} is lower than the "
                                         + $"project's versionCode {state.AndroidVersionCode}; Google Play "
                                         + "rejects a versionCode it has already moved past. Set "
                                         + "BUILD_NUMBER_OFFSET (docs/VERSIONING.md).");
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
            Debug.Log($"{Tag} version {PlayerSettings.bundleVersion} build {buildNumber} ({target}).");
            return state;
        }

        /// <summary>Product name for each target, with the extension CI expects.</summary>
        internal static string OutputName(BuildTarget target, bool appBundle)
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
                case BuildTarget.StandaloneOSX:
                    return product + ".app";
                default:
                    // Linux (player and server), WebGL and iOS write a directory
                    // or an extensionless binary under this name.
                    return product;
            }
        }

        private static string SanitizeFileName(string value)
        {
            if (string.IsNullOrEmpty(value)) return value;
            return string.Concat(value.Split(Path.GetInvalidFileNameChars())).Trim();
        }

        private static string NullIfEmpty(string value)
        {
            return string.IsNullOrWhiteSpace(value) ? null : value;
        }

        /// <summary>Reads `-name value` from the Editor command line, or null.</summary>
        internal static string ReadArg(string name)
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

        internal static void Fail(string message)
        {
            // "::error::" turns the line into an annotation in the Actions UI.
            Debug.LogError($"::error::{Tag} {message}");
        }

        private static void Exit(int code)
        {
            if (Application.isBatchMode)
            {
                EditorApplication.Exit(code);
            }
        }
    }
}
