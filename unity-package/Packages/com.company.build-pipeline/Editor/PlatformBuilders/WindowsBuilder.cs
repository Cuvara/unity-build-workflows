using System;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace Company.BuildPipeline.Editor
{
    public class WindowsBuilder : IPlatformBuilder
    {
        public BuildTarget Target => BuildTarget.StandaloneWindows64;

        public bool Supports(BuildContext context)
            => context.Configuration.TargetPlatform.Equals("windows", StringComparison.OrdinalIgnoreCase) ||
               context.Configuration.TargetPlatform.Equals("windows64", StringComparison.OrdinalIgnoreCase);

        public void Configure(BuildContext context)
        {
            var cfg = context.Configuration;

            PlayerSettings.productName = cfg.ProductName;
            PlayerSettings.companyName = cfg.CompanyName;
            PlayerSettings.bundleVersion = cfg.BundleVersion;
            PlayerSettings.SetApplicationIdentifier(BuildTargetGroup.Standalone, cfg.AppIdentifier);

            var backend = cfg.ScriptingBackend?.Equals("il2cpp", StringComparison.OrdinalIgnoreCase) == true
                ? ScriptingImplementation.IL2CPP
                : ScriptingImplementation.Mono2x;
            PlayerSettings.SetScriptingBackend(BuildTargetGroup.Standalone, backend);
        }

        public BuildExecutionResult Build(BuildContext context)
        {
            var cfg = context.Configuration;
            var outputDir = cfg.OutputPath;
            Directory.CreateDirectory(outputDir);
            var outputPath = Path.Combine(outputDir, $"{cfg.ProductName}.exe");

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
            long sizeBytes = 0;
            if (success && Directory.Exists(outputDir))
            {
                foreach (var f in Directory.EnumerateFiles(outputDir, "*", SearchOption.AllDirectories))
                    sizeBytes += new FileInfo(f).Length;
            }

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
