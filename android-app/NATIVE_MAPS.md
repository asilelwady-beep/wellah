# Android native Google Maps activation

The expand-map control now opens a native dark Google MapView in Android builds configured with a Maps SDK key. Customer pickup/destination and merchant maps return a fixed-pin selection on **تم**. Back/cancel preserves the order and typed address. Driver/tracking/order/admin maps display existing marker and route snapshots and return without selecting a checkout pin. Search remains in the existing checkout form outside the full-screen native map.

## Pricing boundary

This integration uses the Android **Maps SDK** SKU: no cloud Map ID, Street View, Google Places, Routes, Navigation SDK or JavaScript Google Maps calls are introduced by the native screen. Native-capable APKs also suppress the existing JavaScript Google layer. Existing server-side address search and pricing/routing configuration is unchanged; any separately configured Google web/server APIs still follow their own billing rules. A Google Cloud billing account is required even for the no-cost SDK SKU. Check Google's current pricing before activation:

- https://developers.google.com/maps/billing-and-pricing/pricing
- https://developers.google.com/maps/documentation/android-sdk/configure-map

## Activation

1. In the owner's Google Cloud project, enable **Maps SDK for Android** and link billing. No paid search/navigation API needs enabling for this screen.
2. Create an API key restricted to **Maps SDK for Android** and restrict Android applications by package name **and the signing certificate SHA-1**. Customer: `com.wallah.customer`; driver: `com.wallah.courier`; management: `com.wallah.management`. Debug variants append `.debug` and need their debug certificate only if explicitly tested.
3. Supply `WALLAHA_ANDROID_MAPS_API_KEY` as a protected build environment variable or Gradle property. Do not use the web/server key, paste a key into source, or commit `local.properties`/keystores. The key is inserted into manifest metadata at build time. The value is intentionally not printed by these changes.
4. Build APKs/AABs using the existing release signing identity. Google Play installs require the **Play App Signing** certificate SHA-1; locally installed signed APKs require their signing certificate SHA-1. Existing CI creates a new random signing key for its demo release APKs, so those are unsuitable for updating installed production apps or for a stable restricted Maps key. Preserve the production signing process rather than distributing those demo builds as updates.
5. Test actual map tiles, Arabic labels, pin selection for both checkout points, typed address preservation, quote updates, GPS denied/approximate/disabled, back/cancel, rotation, background/resume, no connectivity, live driver updates and a slow network on a real Android device.

Without a key the native capability is disabled and the existing OpenStreetMap web view remains available. With missing Play services or an unsuccessful map load, the native screen returns to the web map after its loading timeout. No existing installed APK gains the native screen until an updated app is installed. Browser/dashboard maps remain web maps.

## Verification

From the repository root: `node tests/test_native_maps.cjs`.

From `android-app`: `gradle :app:testCustomerDebugUnitTest :app:assembleCustomerDebug :app:assembleDriverDebug :app:assembleAdminDebug`.

The dedicated PR workflow compiles without a key and runs parser tests. This does not verify Google authorization, real tile rendering, production signing or billing activation. Keep the PR a draft until device validation and activation are complete.
