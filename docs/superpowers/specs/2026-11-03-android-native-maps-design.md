# Native Google Maps for Walla3ha

The user selected the no-cost Android Maps SDK and supplied a full-screen dark Google map with a fixed selection pin and a large confirmation button. Implement that native screen inside the existing WebView application, retaining checkout fields, order pricing and the existing web maps.

## Approach

Use a native MapView overlay in MainActivity, opened by the existing expand-map button. Customer pickup/destination and merchant screens return a selected coordinate only after confirmation; tracking/driver/order/admin screens show current markers and existing route lines without selecting a checkout location. Existing web search and pricing continue unchanged. Android builds supporting this bridge must never load the billed JavaScript Maps adapter automatically.

Alternative considered: replacing the whole WebView app or rebuilding the Leaflet API over native maps would be much larger and risk checkout regressions. Changing only map colors would not deliver the requested Maps SDK.

## Constraints

- Google Maps SDK only: no Map ID, cloud styling, Street View, Places, Routes or Navigation SDK calls added by this change.
- Supply a Maps SDK for Android restricted key at build time; never commit one. The underlying Google Cloud project still requires billing activation.
- Keep the original web map when the key/device/service is unavailable. Existing apps and browser visits continue to work.
- Confirmed coordinates must enter existing click/selection handlers, preserving typed addresses and current pricing rules. Back/cancel must not change coordinates.
- Close stale overlays on navigation; guard callbacks by request ID and map identity. Restrict native bridge operations to trusted application pages.
- This prepares a new APK/AAB. Activation and release require the owner's Cloud key and the existing app-signing/release process. No production deployment or paid API activation is part of this branch.

## Validation

Exercise bridge selection, cancellation, malformed results, stale pages, old-app fallback and disabled web Google loading with Node tests. Validate native request parsing with JVM tests. Compile Android flavors in CI. Real map rendering, Arabic labels, weak connectivity and signing restrictions need an activated key and Android device.
