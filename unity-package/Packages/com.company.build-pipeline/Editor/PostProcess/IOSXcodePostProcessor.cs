// Compiled only when the active build target is iOS, because UnityEditor.iOS.Xcode
// is available only when iOS Build Support is installed. Linux CI agents that run only
// the Unity build-script step without iOS support are therefore unaffected.
#if UNITY_IOS

using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.Callbacks;
using UnityEditor.iOS.Xcode;
using UnityEngine;

namespace Company.BuildPipeline.Editor
{
    /// <summary>
    /// Post-process hook that modifies the Unity-generated Xcode project via the typed
    /// PBXProject / PlistDocument API. All operations are deterministic, idempotent, and
    /// structurally validated — no regex or text replacement of project.pbxproj.
    ///
    /// Values are driven by the iOS BuildConfig block, passed through the
    /// <see cref="IOSBuildParameters"/> static holder (populated by IOSBuilder.Configure
    /// before BuildPipeline.BuildPlayer is called).
    ///
    /// Responsibilities (in order):
    ///   1. PBXProject: disable bitcode, embed Swift standard libraries, link additional frameworks.
    ///   2. Info.plist: set NSUsageDescription keys (defaults + config overrides).
    ///   3. Entitlements: create/update the entitlements plist with push-notification capability
    ///      (aps-environment) and associated domains.
    /// </summary>
    public static class IOSXcodePostProcessor
    {
        // Priority 100: runs after Unity's own post-process steps (priority 0–99)
        // but before any project-level hooks at higher numbers.
        [PostProcessBuild(100)]
        public static void OnPostProcessBuild(BuildTarget target, string buildPath)
        {
            if (target != BuildTarget.iOS)
                return;

            Debug.Log($"[BuildPipeline:iOS] IOSXcodePostProcessor — buildPath: {buildPath}");

            // Without BuildConfig (PlayerBuilder, a menu build) there is nothing
            // to apply but the bitcode switch every Xcode 14+ build needs. Adding
            // usage strings or a push entitlement the project never asked for
            // would break signing with a profile that lacks Push.
            if (!IOSBuildParameters.Configured)
            {
                DisableBitcode(buildPath);
                Debug.Log("[BuildPipeline:iOS] No BuildConfig: bitcode disabled, plist and entitlements untouched.");
                return;
            }

            ModifyPbxProject(buildPath);
            ModifyInfoPlist(buildPath);
            ModifyEntitlements(buildPath);

            Debug.Log("[BuildPipeline:iOS] IOSXcodePostProcessor complete.");
        }

        // ── PBXProject ────────────────────────────────────────────────────────

        private static void DisableBitcode(string buildPath)
        {
            var pbxPath = PBXProject.GetPBXProjectPath(buildPath);
            var pbx     = new PBXProject();
            pbx.ReadFromFile(pbxPath);
            pbx.SetBuildProperty(pbx.GetUnityMainTargetGuid(),      "ENABLE_BITCODE", "NO");
            pbx.SetBuildProperty(pbx.GetUnityFrameworkTargetGuid(), "ENABLE_BITCODE", "NO");
            pbx.SetBuildProperty(pbx.ProjectGuid(),                 "ENABLE_BITCODE", "NO");
            pbx.WriteToFile(pbxPath);
        }

        private static void ModifyPbxProject(string buildPath)
        {
            var pbxPath = PBXProject.GetPBXProjectPath(buildPath);
            var pbx     = new PBXProject();
            pbx.ReadFromFile(pbxPath);

            // Unity 2019.3+: separate main-app and framework targets.
            var mainTargetGuid      = pbx.GetUnityMainTargetGuid();
            var frameworkTargetGuid = pbx.GetUnityFrameworkTargetGuid();
            var projectGuid         = pbx.ProjectGuid();

            // ── Disable Bitcode ───────────────────────────────────────────────
            // Required for Unity 2022+ and Xcode 14+ (Apple removed bitcode from
            // the App Store). SetBuildProperty overwrites unconditionally → idempotent.
            pbx.SetBuildProperty(mainTargetGuid,      "ENABLE_BITCODE", "NO");
            pbx.SetBuildProperty(frameworkTargetGuid, "ENABLE_BITCODE", "NO");
            pbx.SetBuildProperty(projectGuid,          "ENABLE_BITCODE", "NO");

            // ── Always embed Swift standard libraries ─────────────────────────
            // Required for mixed Obj-C/Swift targets (Unity plugins frequently include Swift).
            pbx.SetBuildProperty(mainTargetGuid, "ALWAYS_EMBED_SWIFT_STANDARD_LIBRARIES", "YES");

            // ── Additional frameworks from config ─────────────────────────────
            // Duplicate framework references are benign for Xcode builds; we do not
            // pre-check for existence to avoid depending on internal PBXProject path
            // formats that differ between Unity versions.
            foreach (var framework in IOSBuildParameters.AdditionalFrameworks)
            {
                if (string.IsNullOrWhiteSpace(framework))
                    continue;
                pbx.AddFrameworkToProject(mainTargetGuid, framework, false /* required */);
                Debug.Log($"[BuildPipeline:iOS] Framework linked: {framework}");
            }

            pbx.WriteToFile(pbxPath);
            Debug.Log("[BuildPipeline:iOS] PBXProject written: ENABLE_BITCODE=NO, Swift embedding=YES.");
        }

        // ── Info.plist ────────────────────────────────────────────────────────

        private static void ModifyInfoPlist(string buildPath)
        {
            var plistPath = Path.Combine(buildPath, "Info.plist");
            var plist     = new PlistDocument();
            plist.ReadFromFile(plistPath);
            var root = plist.root;

            // Default usage descriptions applied only when the key is absent (idempotent).
            var defaults = new Dictionary<string, string>
            {
                ["NSCameraUsageDescription"]          = "This app requires camera access.",
                ["NSMicrophoneUsageDescription"]      = "This app requires microphone access.",
                ["NSPhotoLibraryUsageDescription"]    = "This app requires photo library access.",
                ["NSPhotoLibraryAddUsageDescription"] = "This app requires permission to save photos.",
            };

            foreach (var kv in defaults)
                SetStringIfAbsent(root, kv.Key, kv.Value);

            // Config-driven overrides: unconditionally replace the default (or any prior value).
            foreach (var kv in IOSBuildParameters.UsageDescriptions)
            {
                if (!string.IsNullOrWhiteSpace(kv.Key) && kv.Value != null)
                    root.SetString(kv.Key, kv.Value);
            }

            plist.WriteToFile(plistPath);
            Debug.Log("[BuildPipeline:iOS] Info.plist usage descriptions set.");
        }

        // ── Entitlements ──────────────────────────────────────────────────────

        private static void ModifyEntitlements(string buildPath)
        {
            // Unity-iPhone is the canonical main app target name.
            const string TargetName           = "Unity-iPhone";
            const string EntitlementsRelative = TargetName + "/" + TargetName + ".entitlements";
            var           entitlementsAbs     = Path.Combine(buildPath, EntitlementsRelative);

            // Create entitlements file if absent.
            if (!File.Exists(entitlementsAbs))
            {
                Directory.CreateDirectory(Path.GetDirectoryName(entitlementsAbs));
                var blank = new PlistDocument();
                blank.WriteToFile(entitlementsAbs);
                Debug.Log($"[BuildPipeline:iOS] Created entitlements file: {EntitlementsRelative}");
            }

            // Wire CODE_SIGN_ENTITLEMENTS into PBXProject.
            // Re-read PBX because ModifyPbxProject already flushed it above.
            var pbxPath = PBXProject.GetPBXProjectPath(buildPath);
            var pbx     = new PBXProject();
            pbx.ReadFromFile(pbxPath);
            var mainTargetGuid = pbx.GetUnityMainTargetGuid();
            // SetBuildProperty is idempotent (unconditional overwrite).
            pbx.SetBuildProperty(mainTargetGuid, "CODE_SIGN_ENTITLEMENTS", EntitlementsRelative);
            pbx.WriteToFile(pbxPath);

            // Modify the entitlements plist.
            var entitlements = new PlistDocument();
            entitlements.ReadFromFile(entitlementsAbs);
            var root = entitlements.root;

            // Push notification capability (aps-environment).
            // "development" for dev builds; "production" for all distribution builds.
            // SetStringIfAbsent preserves an existing value (idempotent on re-run).
            var apsEnv = IOSBuildParameters.IsDevelopmentBuild ? "development" : "production";
            SetStringIfAbsent(root, "aps-environment", apsEnv);

            // Associated domains — added from config, deduplicated against any existing entries.
            if (IOSBuildParameters.AssociatedDomains.Length > 0)
            {
                const string AssocDomainsKey = "com.apple.developer.associated-domains";

                // Collect existing entries so hand-edited values are preserved.
                var existing = new HashSet<string>(StringComparer.Ordinal);
                if (root.values.TryGetValue(AssocDomainsKey, out var existingToken) &&
                    existingToken is PlistElementArray existingArr)
                {
                    foreach (var el in existingArr.values)
                        if (el is PlistElementString s) existing.Add(s.value);
                }

                existing.UnionWith(IOSBuildParameters.AssociatedDomains);

                // CreateArray replaces the key — write back the full union set.
                var domainArray = root.CreateArray(AssocDomainsKey);
                foreach (var domain in existing)
                    domainArray.AddString(domain);

                Debug.Log($"[BuildPipeline:iOS] Associated domains: {string.Join(", ", existing)}");
            }

            entitlements.WriteToFile(entitlementsAbs);
            Debug.Log($"[BuildPipeline:iOS] Entitlements written (aps-environment={apsEnv}).");
        }

        // ── Helpers ───────────────────────────────────────────────────────────

        /// <summary>
        /// Sets <paramref name="key"/> to <paramref name="value"/> in the plist dict
        /// only when the key is not already present, so re-running the hook does not
        /// overwrite a previously customised value.
        /// </summary>
        private static void SetStringIfAbsent(PlistElementDict dict, string key, string value)
        {
            if (!dict.values.ContainsKey(key))
                dict.SetString(key, value);
        }
    }
}

#endif // UNITY_IOS
