# Android Native Maps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open the requested full-screen native dark Google map from existing Walla3ha map controls.

**Architecture:** Add a native MapView overlay and a validated JSON bridge. Retain the web map as a backing view and fallback; confirmed selection uses its existing click handler. Build-time Android keys gate availability.

**Tech Stack:** Android Java, Maps SDK 20.1.0, existing WebView/Leaflet, Node VM tests, JUnit.

**Spec:** docs/superpowers/specs/2026-11-03-android-native-maps-design.md

## Global Constraints

- No Map ID or new billed Google web/search/routing APIs.
- Missing keys retain the web map and never open an empty native map.
- Preserve typed addresses, checkout quotes, existing browser and older APK behavior.
- Do not deploy, merge or release until activation and rendering can be verified.

## Review Focus

- Null/string/out-of-range coordinates must never become an unintended location.
- Cancel/back and results from a previous checkout screen must not alter the active order.
- Android lifecycle changes must dispose/restart MapView and GPS requests safely.
- Native map camera movement must not be reset by live updates.
- Missing key/Play services/map-loading failures must retain a working web fallback.

### Task 1: Web/native bridge

**Files:** maps.js, tests/test_native_maps.cjs

**Interfaces:** openNativeMap(map) -> boolean; closeNativeMap(); window.WallahaMaps.receive(result). Native methods: mapsSdkAvailable(), openMap(json), updateMap(json), closeMap(requestId).

- [ ] Write behavioral tests for selection/cancel, stale/invalid callbacks, live updates, older APKs and Google JavaScript suppression; observe failures.
- [ ] Implement payload snapshots and guarded callbacks; open native screen from expandMap and cancel when clearMaps runs.
- [ ] Run the new tests and existing Node suite.

### Task 2: Native map screen and build configuration

**Files:** android-app/app/build.gradle, AndroidManifest.xml, MainActivity.java, NativeMapRequest.java, NativeMapController.java, NativeMapRequestTest.java, .github/workflows/android-native-maps-check.yml, android-app/NATIVE_MAPS.md

**Interfaces:** NativeMapRequest.parse(raw) validates IDs and numeric coordinates. NativeMapController opens, updates, closes and forwards MapView lifecycle. Confirmation returns {requestId,status,lat,lon,zoom}.

- [ ] Write parser tests for invalid coordinates, unknown IDs and read-only maps.
- [ ] Implement native dark MapView, fixed flag, back, GPS and confirmation controls, key gate and trusted-page bridge.
- [ ] Add independent PR compile/test CI; document Maps-SDK-only key restrictions and activation/release requirements.
- [ ] Run all Node/Python tests and CI compilation if available; record any environmental limits honestly.

### Task 3: Review and delivery

- [ ] Review diff and run a fresh whole-branch code review; fix important findings with regression tests.
- [ ] Commit changes on the feature branch and open a draft PR. Do not merge or claim live map activation.
