using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace Company.BuildPipeline.Editor
{
    /// <summary>
    /// Platform builder for iOS. Generates a Unity-linked Xcode project at Builds/iOS/Xcode/
    /// and redirects build reports to BuildReports/iOS/ so downstream CI steps can collect them
    /// independently of the Xcode project artefact.
    ///
    /// Design rules:
    ///   - Certificate material NEVER appears here. Signing configuration only applies
    ///     PlayerSettings.iOS properties (team ID, provisioning profile UUID, signing mode).
    ///   - Xcode project post-processing (entitlements, frameworks, Info.plist, capabilities)
    ///     is performed by IOSPostProcessBuild via Unity's [PostProcessBuild] callback,
    ///     using the typed PBXProject / PlistDocument API exclusively.
    ///   - export-method, bitcode, symbol upload, and TestFlight flags are written into
    ///     build-metadata.json for the xcodebuild / altool shell step to consume; C# does
    ///     not invoke those tools.
    /// </summary>
    public class IOSBuilder : IPlatformBuilder
    {
        // ── Canonical output paths — MUST match CI contract ──────────────────
        private const string XcodeOutputDir  = "Builds/iOS/Xcode";
        private const string ReportOutputDir = "BuildReports/iOS";

        // ─────────────────────────────────────────────────────────────────────

        public BuildTarget Target => BuildTarget.iOS;

        public bool Supports(BuildContext context)
            => context.Configuration.TargetPlatform.Equals("ios", StringComparison.OrdinalIgnoreCase);

        // ── Configure ─────────────────────────────────────────────────────────

        public void Configure(BuildContext context)
        {
            var cfg    = context.Configuration;
            var iosCfg = cfg.iOS; // may be null for legacy configs that rely on signingConfig only

            // ── 1. Switch active build target ─────────────────────────────────
            if (EditorUserBuildSettings.activeBuildTarget != BuildTarget.iOS)
            {
                Debug.Log("[BuildPipeline:iOS] Switching active build target to iOS…");
                bool switched = EditorUserBuildSettings.SwitchActiveBuildTarget(
                    BuildTargetGroup.iOS, BuildTarget.iOS);
                if (!switched)
                    throw new InvalidOperationException(
                        "[BuildPipeline:iOS] EditorUserBuildSettings.SwitchActiveBuildTarget(iOS) returned false. " +
                        "Ensure the iOS Build Support module is installed for this Unity Editor version.");
                Debug.Log("[BuildPipeline:iOS] Active build target switched to iOS.");
            }

            // ── 2. Common product settings ────────────────────────────────────
            PlayerSettings.productName = cfg.ProductName;
            PlayerSettings.companyName = cfg.CompanyName;

            // ── 3. Bundle identifier ──────────────────────────────────────────
            // iOS block takes precedence; fall back to shared appIdentifier.
            var bundleId = (iosCfg != null && !string.IsNullOrWhiteSpace(iosCfg.BundleIdentifier))
                ? iosCfg.BundleIdentifier
                : cfg.AppIdentifier;
            PlayerSettings.SetApplicationIdentifier(BuildTargetGroup.iOS, bundleId);

            // ── 4. Version strings ────────────────────────────────────────────
            // marketingVersion → CFBundleShortVersionString (user-visible "1.2.3").
            var marketingVersion = (iosCfg != null && !string.IsNullOrWhiteSpace(iosCfg.MarketingVersion))
                ? iosCfg.MarketingVersion
                : cfg.BundleVersion;
            PlayerSettings.bundleVersion = marketingVersion;

            // buildNumber → CFBundleVersion (integer build counter, e.g. "42").
            // Priority: iOS.buildNumber > BUILD_NUMBER env > GITHUB_RUN_NUMBER env > "1".
            var buildNumber = (iosCfg != null && !string.IsNullOrWhiteSpace(iosCfg.BuildNumber))
                ? iosCfg.BuildNumber
                : Environment.GetEnvironmentVariable("BUILD_NUMBER")
                  ?? Environment.GetEnvironmentVariable("GITHUB_RUN_NUMBER")
                  ?? "1";
            PlayerSettings.iOS.buildNumber = buildNumber;

            // ── 5. Minimum iOS deployment target ─────────────────────────────
            var targetOs = (iosCfg != null && !string.IsNullOrWhiteSpace(iosCfg.TargetOSVersion))
                ? iosCfg.TargetOSVersion
                : "14.0";
            PlayerSettings.iOS.targetOSVersionString = targetOs;

            // ── 6. SDK version (device vs simulator) ──────────────────────────
            var sdkVersion = iosCfg?.SdkVersion ?? "DeviceSDK";
            PlayerSettings.iOS.sdkVersion =
                sdkVersion.Equals("SimulatorSDK", StringComparison.OrdinalIgnoreCase)
                    ? iOSSdkVersion.SimulatorSDK
                    : iOSSdkVersion.DeviceSDK;

            // ── 7. Architecture ───────────────────────────────────────────────
            // Unity 6 removed both PlayerSettings.iOS.architecture and the iOSArchitecture
            // enum: the iOS player is ARM64-only, so there is no longer a choice to express.
            // ARM64 was already the only App Store-valid option, so no capability is lost - but
            // a config asking for anything else is now unsatisfiable and says so rather than
            // being silently ignored.
            var requestedArchitecture = iosCfg?.Architecture ?? "ARM64";
            if (!requestedArchitecture.Equals("ARM64", StringComparison.OrdinalIgnoreCase))
            {
                Debug.LogWarning(
                    $"[BuildPipeline:iOS] Config requests architecture '{requestedArchitecture}', " +
                    "but Unity 6 builds iOS as ARM64 only. Building ARM64.");
            }

            // ── 8. Scripting backend — IL2CPP is mandatory for iOS ─────────────
            PlayerSettings.SetScriptingBackend(BuildTargetGroup.iOS, ScriptingImplementation.IL2CPP);

            // ── 9. Managed code stripping ──────────────────────────────────────
            PlayerSettings.SetManagedStrippingLevel(
                BuildTargetGroup.iOS,
                cfg.IsDevelopmentBuild
                    ? ManagedStrippingLevel.Minimal   // Unity 2020.1+; preserves more symbols for debugging
                    : ManagedStrippingLevel.High);

            // ── 10. Xcode build configuration ─────────────────────────────────
            // Always use Release for CI distribution builds so that Xcode can strip and optimise.
            // dSYM generation is controlled at the Xcode level via exportOptionsPlist.
            EditorUserBuildSettings.iOSXcodeBuildConfig = XcodeBuildConfig.Release;

            // ── 11. Signing ───────────────────────────────────────────────────
            // Team ID is set for both automatic and manual signing modes.
            // EffectiveTeamId prefers developmentTeamId, falls back to legacy developmentTeam alias.
            var teamId = (iosCfg != null && !string.IsNullOrWhiteSpace(iosCfg.EffectiveTeamId))
                ? iosCfg.EffectiveTeamId
                : cfg.SigningConfig?.TeamId ?? string.Empty;
            if (!string.IsNullOrWhiteSpace(teamId))
                PlayerSettings.iOS.appleDeveloperTeamID = teamId;

            bool useAutoSigning = iosCfg != null &&
                iosCfg.SigningStyle.Equals("automatic", StringComparison.OrdinalIgnoreCase);

            if (useAutoSigning)
            {
                PlayerSettings.iOS.appleEnableAutomaticSigning = true;
            }
            else
            {
                // Manual: provisioning profile UUID / specifier only — NO certificate material.
                PlayerSettings.iOS.appleEnableAutomaticSigning = false;

                var profileId = (iosCfg != null && !string.IsNullOrWhiteSpace(iosCfg.ProvisioningProfileSpecifier))
                    ? iosCfg.ProvisioningProfileSpecifier
                    : cfg.SigningConfig?.ProvisioningProfileId ?? string.Empty;
                if (!string.IsNullOrWhiteSpace(profileId))
                {
                    PlayerSettings.iOS.iOSManualProvisioningProfileID   = profileId;
                    PlayerSettings.iOS.iOSManualProvisioningProfileType = ProvisioningProfileType.Distribution;
                }
            }

            // ── 12. Populate IOSBuildParameters for [PostProcessBuild] hook ───
            // The hook is static and cannot access BuildContext; this static holder bridges them.
            IOSBuildParameters.IsDevelopmentBuild  = cfg.IsDevelopmentBuild;
            IOSBuildParameters.GenerateSymbols      = iosCfg?.GenerateSymbols ?? false;
            IOSBuildParameters.EnableBitcode        = iosCfg?.EnableBitcode ?? false;
            IOSBuildParameters.ExportMethod         = iosCfg?.ExportMethod ?? "app-store";
            IOSBuildParameters.AssociatedDomains    = iosCfg?.AssociatedDomains?.ToArray() ?? new string[0];
            IOSBuildParameters.AdditionalFrameworks = iosCfg?.AdditionalFrameworks?.ToArray() ?? new string[0];
            IOSBuildParameters.UsageDescriptions    = iosCfg?.UsageDescriptions
                ?? new Dictionary<string, string>();

            // ── 13. Propagate export/distribution settings to build metadata ──
            // The shell (xcodebuild / altool) step reads these from build-metadata.json
            // so it can construct the correct exportOptionsPlist without re-parsing the config.
            context.Metadata["ios_exportMethod"]        = iosCfg?.ExportMethod       ?? "app-store";
            context.Metadata["ios_enableBitcode"]       = iosCfg?.EnableBitcode      ?? false;
            context.Metadata["ios_generateSymbols"]     = iosCfg?.GenerateSymbols    ?? false;
            context.Metadata["ios_uploadSymbols"]       = iosCfg?.UploadSymbols      ?? false;
            context.Metadata["ios_uploadToTestFlight"]  = iosCfg?.UploadToTestFlight ?? false;
            context.Metadata["ios_xcodeVersion"]        = iosCfg?.XcodeVersion       ?? string.Empty;
            context.Metadata["ios_codeSignIdentity"]    = iosCfg?.CodeSignIdentity   ?? string.Empty;
            context.Metadata["ios_xcodeOutputPath"]     = XcodeOutputDir;
            context.Metadata["ios_reportOutputPath"]    = ReportOutputDir;
        }

        // ── Build ──────────────────────────────────────────────────────────────

        public BuildExecutionResult Build(BuildContext context)
        {
            var cfg = context.Configuration;

            // Ensure canonical Xcode output directory exists before BuildPlayer runs.
            Directory.CreateDirectory(XcodeOutputDir);

            // Build options.
            var buildOpts = BuildOptions.None;
            if (cfg.IsDevelopmentBuild)
            {
                buildOpts |= BuildOptions.Development;
                if (cfg.IsDebuggingEnabled)
                    buildOpts |= BuildOptions.AllowDebugging;
            }

            var playerOptions = new BuildPlayerOptions
            {
                scenes           = cfg.Scenes.ToArray(),
                locationPathName = XcodeOutputDir,
                target           = Target,
                options          = buildOpts
            };

            // ── Invoke BuildPipeline ──────────────────────────────────────────
            var started = DateTime.UtcNow;
            UnityEditor.Build.Reporting.BuildReport report;

            try
            {
                report = UnityEditor.BuildPipeline.BuildPlayer(playerOptions);
            }
            catch (Exception ex)
            {
                // Redirect reports to iOS report dir before re-throwing so CI can collect them.
                RedirectOutputPath(cfg);
                throw new InvalidOperationException(
                    $"[BuildPipeline:iOS] BuildPipeline.BuildPlayer threw an unexpected exception: {ex.Message}", ex);
            }

            var duration = DateTime.UtcNow - started;
            bool success = report.summary.result == BuildResult.Succeeded;

            // Redirect subsequent BuildReportExporter / BuildMetadataWriter calls to iOS report dir.
            RedirectOutputPath(cfg);

            if (!success)
            {
                return new BuildExecutionResult
                {
                    Success         = false,
                    ErrorMessage    = $"BuildResult={report.summary.result}; errors={report.summary.totalErrors}",
                    WarningCount    = (int)report.summary.totalWarnings,
                    Duration        = duration,
                    OutputPath      = XcodeOutputDir,
                    OutputSizeBytes = 0
                };
            }

            // ── Verify Xcode project was generated ────────────────────────────
            // BuildPipeline.BuildPlayer for iOS creates a directory (not a single file).
            // An empty or absent directory after a successful build indicates a post-build failure.
            if (!Directory.Exists(XcodeOutputDir) ||
                Directory.GetFileSystemEntries(XcodeOutputDir).Length == 0)
            {
                throw new InvalidOperationException(
                    $"[BuildPipeline:iOS] Build reported success but Xcode output directory is missing or empty: " +
                    $"'{XcodeOutputDir}'. Check Editor.log for post-process errors.");
            }

            // Measure Xcode project size for trend tracking.
            // Note: IPA and dSYM sizes are reported post-archive by the shell step.
            long xcodeProjectSize = 0;
            foreach (var f in Directory.EnumerateFiles(XcodeOutputDir, "*", SearchOption.AllDirectories))
                xcodeProjectSize += new FileInfo(f).Length;

            return new BuildExecutionResult
            {
                Success         = true,
                ErrorMessage    = string.Empty,
                WarningCount    = (int)report.summary.totalWarnings,
                Duration        = duration,
                OutputPath      = XcodeOutputDir,
                OutputSizeBytes = xcodeProjectSize
            };
        }

        // ── Helpers ───────────────────────────────────────────────────────────

        /// <summary>
        /// Mutates cfg.OutputPath so that BuildReportExporter and BuildMetadataWriter
        /// write to BuildReports/iOS/ rather than the Xcode project directory.
        /// Called once after BuildPipeline.BuildPlayer returns (success or failure).
        /// </summary>
        private static void RedirectOutputPath(BuildConfiguration cfg)
        {
            cfg.OutputPath = ReportOutputDir;
            Directory.CreateDirectory(ReportOutputDir);
        }
    }
}
