using System.Collections.Generic;

namespace Company.BuildPipeline.Editor
{
    /// <summary>
    /// Static holder that carries iOS BuildConfig values from IOSBuilder.Configure()
    /// to the IOSPostProcessBuild [PostProcessBuild] callback.
    ///
    /// Unity's PostProcessBuild callbacks are static and cannot access BuildContext directly.
    /// This class bridges that gap without using EditorPrefs (which persist across Editor
    /// sessions and could bleed between builds).
    ///
    /// Lifecycle: written once by IOSBuilder.Configure(); read once by
    /// IOSPostProcessBuild.OnPostProcessBuild(). Both execute in the same Editor process
    /// during a single batchmode invocation, so there are no concurrency or stale-value risks.
    /// </summary>
    internal static class IOSBuildParameters
    {
        /// <summary>
        /// Set by IOSBuilder.Configure (the BuildConfig path). False for any other
        /// iOS build in a project that has this package -- PlayerBuilder, a menu
        /// build -- where the post-processor only disables bitcode and leaves the
        /// project's plist and entitlements alone.
        /// </summary>
        public static bool Configured = false;

        /// <summary>Whether this is a development (non-production) build.</summary>
        public static bool IsDevelopmentBuild = false;

        /// <summary>Whether to request dSYM symbol generation from the xcodebuild step.</summary>
        public static bool GenerateSymbols = false;

        /// <summary>Whether bitcode is requested (informational — IOSPostProcessBuild always disables it).</summary>
        public static bool EnableBitcode = false;

        /// <summary>app-store | ad-hoc | enterprise | development</summary>
        public static string ExportMethod = "app-store";

        /// <summary>
        /// Associated domain identifiers to register in the entitlements plist,
        /// e.g. { "applinks:example.com", "webcredentials:example.com" }.
        /// </summary>
        public static string[] AssociatedDomains = new string[0];

        /// <summary>
        /// Apple system framework bundle names to link via PBXProject,
        /// e.g. { "StoreKit.framework" }.
        /// </summary>
        public static string[] AdditionalFrameworks = new string[0];

        /// <summary>
        /// Info.plist NSUsageDescription key overrides.
        /// Key = plist key (e.g. "NSCameraUsageDescription"); Value = user-facing string.
        /// Keys absent from this dictionary receive safe defaults in IOSPostProcessBuild.
        /// </summary>
        public static Dictionary<string, string> UsageDescriptions = new Dictionary<string, string>();
    }
}
