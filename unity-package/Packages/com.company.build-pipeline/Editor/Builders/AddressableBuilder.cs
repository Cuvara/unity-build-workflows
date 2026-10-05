// AddressableBuilder -- builds Addressables content before the player:
//
//   Unity -batchmode -executeMethod Company.BuildPipeline.Editor.AddressableBuilder.Build
//
// Used by the pipeline's "build-addressables" step. BUILD_PIPELINE_ADDRESSABLES
// is defined by the asmdef's versionDefines when the project has
// com.unity.addressables, so the package still compiles in a project without
// it; calling Build there fails with that explanation.
// docs/TOOLKIT_BUILD_PACKAGE.md

using System;
using UnityEditor;
using UnityEngine;
#if BUILD_PIPELINE_ADDRESSABLES
using UnityEditor.AddressableAssets;
using UnityEditor.AddressableAssets.Build;
using UnityEditor.AddressableAssets.Settings;
#endif

namespace Company.BuildPipeline.Editor
{
    public static class AddressableBuilder
    {
        private const string Tag = "[AddressableBuilder]";

        public static void Build()
        {
            int code;
            try
            {
                code = Run();
            }
            catch (Exception e)
            {
                Debug.LogError($"::error::{Tag} Unhandled exception: {e}");
                code = 1;
            }
            if (Application.isBatchMode)
            {
                EditorApplication.Exit(code);
            }
        }

        /// <summary>Builds Addressables content; returns the process exit code.</summary>
        public static int Run()
        {
#if BUILD_PIPELINE_ADDRESSABLES
            Debug.Log($"{Tag} Building Addressables content...");
            AddressableAssetSettings settings = AddressableAssetSettingsDefaultObject.Settings;
            if (settings == null)
            {
                Debug.LogError($"::error::{Tag} AddressableAssetSettings not found. Initialise "
                               + "Addressables in the project (Window > Asset Management > "
                               + "Addressables > Groups) or turn off build-addressables.");
                return 1;
            }

            AddressableAssetSettings.BuildPlayerContent(out AddressablesPlayerBuildResult result);
            if (!string.IsNullOrEmpty(result.Error))
            {
                Debug.LogError($"::error::{Tag} Addressables build failed: {result.Error}");
                return 1;
            }

            Debug.Log($"{Tag} Done in {result.Duration:F1}s, {result.LocationCount} locations.");
            return 0;
#else
            Debug.LogError($"::error::{Tag} This project does not use Addressables "
                           + "(com.unity.addressables is not installed). Turn off build-addressables "
                           + "(ADDRESSABLES_ENABLED / the dispatch checkbox) or add the package.");
            return 1;
#endif
        }
    }
}
