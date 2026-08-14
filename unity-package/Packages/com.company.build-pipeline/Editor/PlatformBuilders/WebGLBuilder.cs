using System;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace Company.BuildPipeline.Editor
{
    public class WebGLBuilder : IPlatformBuilder
    {
        public BuildTarget Target => BuildTarget.WebGL;

        public bool Supports(BuildContext context)
            => context.Configuration.TargetPlatform.Equals("webgl", StringComparison.OrdinalIgnoreCase);

        public void Configure(BuildContext context)
        {
            var cfg = context.Configuration;

            PlayerSettings.productName = cfg.ProductName;
            PlayerSettings.companyName = cfg.CompanyName;
            PlayerSettings.bundleVersion = cfg.BundleVersion;
            PlayerSettings.SetApplicationIdentifier(BuildTargetGroup.WebGL, cfg.AppIdentifier);

            // WebGL only supports IL2CPP.
            PlayerSettings.SetScriptingBackend(BuildTargetGroup.WebGL, ScriptingImplementation.IL2CPP);

            // Compression — leave at default (Brotli) unless overridden via a hook.
            PlayerSettings.WebGL.compressionFormat = WebGLCompressionFormat.Brotli;

            if (!cfg.IsDevelopmentBuild)
                PlayerSettings.WebGL.exceptionSupport = WebGLExceptionSupport.None;
        }

        public BuildExecutionResult Build(BuildContext context)
        {
            var cfg = context.Configuration;
            var outputPath = cfg.OutputPath;
            Directory.CreateDirectory(outputPath);

            var options = new BuildPlayerOptions
            {
                scenes = cfg.Scenes.ToArray(),
                locationPathName = outputPath,
                target = Target,
                options = cfg.IsDevelopmentBuild
                    ? BuildOptions.Development
                    : BuildOptions.None
            };

            var started = DateTime.UtcNow;
            var report = UnityEditor.BuildPipeline.BuildPlayer(options);
            var duration = DateTime.UtcNow - started;

            bool success = report.summary.result == UnityEditor.Build.Reporting.BuildResult.Succeeded;
            long sizeBytes = 0;
            if (success && Directory.Exists(outputPath))
            {
                foreach (var f in Directory.EnumerateFiles(outputPath, "*", SearchOption.AllDirectories))
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
