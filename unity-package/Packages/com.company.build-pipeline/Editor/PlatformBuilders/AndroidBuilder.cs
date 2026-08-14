using System;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace Company.BuildPipeline.Editor
{
    public class AndroidBuilder : IPlatformBuilder
    {
        public BuildTarget Target => BuildTarget.Android;

        public bool Supports(BuildContext context)
            => context.Configuration.TargetPlatform.Equals("android", StringComparison.OrdinalIgnoreCase);

        public void Configure(BuildContext context)
        {
            var cfg     = context.Configuration;
            var android = cfg.Android; // may be null for legacy configs

            PlayerSettings.productName = cfg.ProductName;
            PlayerSettings.companyName = cfg.CompanyName;
            PlayerSettings.bundleVersion = cfg.BundleVersion;

            // Application ID: android block takes precedence over legacy flat appIdentifier.
            var appId = (android != null && !string.IsNullOrWhiteSpace(android.ApplicationId))
                ? android.ApplicationId
                : cfg.AppIdentifier;
            PlayerSettings.SetApplicationIdentifier(BuildTargetGroup.Android, appId);

            var backend = cfg.ScriptingBackend?.Equals("il2cpp", StringComparison.OrdinalIgnoreCase) == true
                ? ScriptingImplementation.IL2CPP
                : ScriptingImplementation.Mono2x;
            PlayerSettings.SetScriptingBackend(BuildTargetGroup.Android, backend);

            if (backend == ScriptingImplementation.IL2CPP)
            {
                // architecture from android block (default ARM64).
                var arch = android?.Architecture ?? "ARM64";
                PlayerSettings.Android.targetArchitectures =
                    arch.Equals("All",   StringComparison.OrdinalIgnoreCase) ? AndroidArchitecture.All :
                    arch.Equals("ARMv7", StringComparison.OrdinalIgnoreCase) ? AndroidArchitecture.ARMv7 :
                    arch.Equals("x86_64",StringComparison.OrdinalIgnoreCase) ? AndroidArchitecture.X86_64 :
                    /* default ARM64 */ AndroidArchitecture.ARM64;
            }

            // AAB mode: android block takes precedence.
            EditorUserBuildSettings.buildAppBundle = android?.BuildAppBundle ?? false;

            // Signing: use debug keystore when keystoreMode=debug; otherwise apply custom keystore.
            bool useDebugKeystore = android?.KeystoreMode?.Equals("debug", StringComparison.OrdinalIgnoreCase) == true;
            var signing = cfg.SigningConfig;
            if (!useDebugKeystore && signing != null && !string.IsNullOrWhiteSpace(signing.KeystorePath))
            {
                PlayerSettings.Android.useCustomKeystore = true;
                PlayerSettings.Android.keystoreName = signing.KeystorePath;

                if (!string.IsNullOrWhiteSpace(signing.KeystorePasswordEnvVar))
                    PlayerSettings.Android.keystorePass = Environment.GetEnvironmentVariable(signing.KeystorePasswordEnvVar) ?? string.Empty;

                PlayerSettings.Android.keyaliasName = signing.KeyAlias;

                if (!string.IsNullOrWhiteSpace(signing.KeyAliasPasswordEnvVar))
                    PlayerSettings.Android.keyaliasPass = Environment.GetEnvironmentVariable(signing.KeyAliasPasswordEnvVar) ?? string.Empty;
            }

            // Build number from bundle version (strip non-numeric segments).
            if (int.TryParse(cfg.BundleVersion.Split('.')[0], out int major))
                PlayerSettings.Android.bundleVersionCode = major;
        }

        public BuildExecutionResult Build(BuildContext context)
        {
            var cfg = context.Configuration;
            var outputDir = cfg.OutputPath;
            Directory.CreateDirectory(outputDir);
            // Output extension: .aab for App Bundle, .apk otherwise.
            var ext = (cfg.Android?.BuildAppBundle ?? false) ? ".aab" : ".apk";
            var outputPath = Path.Combine(outputDir, $"{cfg.ProductName}{ext}");

            var options = new BuildPlayerOptions
            {
                scenes = cfg.Scenes.ToArray(),
                locationPathName = outputPath,
                target = Target,
                options = cfg.IsDevelopmentBuild
                    ? BuildOptions.Development | (cfg.IsDebuggingEnabled ? BuildOptions.AllowDebugging : BuildOptions.None)
                    : BuildOptions.None
            };

            var started = DateTime.UtcNow;
            var report = UnityEditor.BuildPipeline.BuildPlayer(options);
            var duration = DateTime.UtcNow - started;

            bool success = report.summary.result == UnityEditor.Build.Reporting.BuildResult.Succeeded;
            long sizeBytes = success && File.Exists(outputPath) ? new FileInfo(outputPath).Length : 0;

            return new BuildExecutionResult
            {
                Success = success,
                ErrorMessage = success ? string.Empty : report.summary.result.ToString(),
                WarningCount = (int)report.summary.totalWarnings,
                Duration = duration,
                OutputPath = outputPath,
                OutputSizeBytes = sizeBytes
            };
        }
    }
}
