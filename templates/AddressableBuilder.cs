// AddressableBuilder.cs
// Place under Assets/<anywhere>/Editor/ (e.g. Assets/BuildScripts/Editor/).
//
// Requires an Editor assembly definition (.asmdef) in the same or a parent
// Editor/ folder that references "Unity.Addressables.Editor".
// Example asmdef:
//   {
//     "name": "BuildScripts.Editor",
//     "references": ["Unity.Addressables.Editor"],
//     "includePlatforms": ["Editor"],
//     "excludePlatforms": []
//   }
//
// Only needed if you use the build-addressables step in the pipeline
// (build-addressables=true dispatch input or RELEASE_BUILD_ADDRESSABLES
// repository variable). If you do not use Addressables, you can ignore or
// delete this file.
//
// The pipeline calls this via:
//   Unity -batchmode -executeMethod AddressableBuilder.Build
//
// Unity version compatibility: Unity 6 (6000.0.x) with Addressables 2.x+.

using System;
using UnityEditor;
using UnityEditor.AddressableAssets;
using UnityEditor.AddressableAssets.Build;
using UnityEditor.AddressableAssets.Settings;
using UnityEngine;

public static class AddressableBuilder
{
    /// <summary>
    /// Entry point invoked by Unity in batch mode:
    ///   Unity -batchmode -executeMethod AddressableBuilder.Build
    ///
    /// Builds the Addressables content (catalog + asset bundles) using the
    /// active Addressable Asset Settings. Throws on failure so the CI step
    /// exits non-zero and the build job fails cleanly.
    /// </summary>
    public static void Build()
    {
        Debug.Log("[AddressableBuilder] Starting Addressable content build...");

        AddressableAssetSettings settings = AddressableAssetSettingsDefaultObject.Settings;
        if (settings == null)
        {
            throw new InvalidOperationException(
                "[AddressableBuilder] AddressableAssetSettings not found. " +
                "Ensure Addressables is initialised in this project " +
                "(Window → Asset Management → Addressables → Groups).");
        }

        AddressableAssetSettings.BuildPlayerContent(out AddressablesPlayerBuildResult result);

        if (!string.IsNullOrEmpty(result.Error))
        {
            throw new Exception(
                $"[AddressableBuilder] Addressable build failed: {result.Error}");
        }

        Debug.Log(
            $"[AddressableBuilder] Build complete. " +
            $"Duration: {result.Duration:F1}s  " +
            $"Location count: {result.LocationCount}");
    }
}
