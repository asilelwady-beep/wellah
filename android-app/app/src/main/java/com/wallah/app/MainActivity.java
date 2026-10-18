package com.wallah.app;

import android.Manifest;
import android.app.Activity;
import android.content.ClipData;
import android.content.ContentValues;
import android.content.Intent;
import android.content.IntentSender;
import android.content.pm.PackageManager;
import android.location.LocationManager;
import android.net.Uri;
import android.os.Bundle;
import android.provider.Settings;
import android.provider.MediaStore;
import android.os.Environment;
import android.webkit.GeolocationPermissions;
import android.webkit.PermissionRequest;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.JavascriptInterface;
import android.widget.Toast;
import android.os.Build;
import android.os.SystemClock;
import com.google.android.gms.common.api.ResolvableApiException;
import com.google.android.gms.location.LocationRequest;
import com.google.android.gms.location.LocationServices;
import com.google.android.gms.location.LocationSettingsRequest;
import com.google.android.gms.location.Priority;
import java.net.URLDecoder;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class MainActivity extends Activity {
    private static final String HOST = "wellah-production.up.railway.app";
    private static boolean isAppHost(String host) {
        return HOST.equals(host) || "walla3ha.com".equals(host);
    }
    private static final int LOCATION_REQUEST = 41;
    private static final int FILE_REQUEST = 42;
    private static final int CAMERA_REQUEST = 43;
    private static final int LOCATION_SETTINGS_REQUEST = 44;
    private static final int NOTIFICATION_REQUEST = 45;
    private static final long BACK_PRESS_WINDOW_MS = 2000;
    private PermissionRequest cameraRequest;
    private WebView web;
    private boolean driverPageLoaded = false;
    private Bundle pendingState;
    private String pendingDestination;
    private boolean appUpdated;
    private boolean gpsPromptShown;
    private boolean notificationPermissionAsked;
    private ValueCallback<Uri[]> fileCallback;
    private Uri captureOutputUri;
    private GeolocationPermissions.Callback locationCallback;
    private String locationOrigin;
    private long lastBackPressAt;
    private Intent pendingDriverServiceIntent;
    private static final Pattern COORDS = Pattern.compile("^\\s*(-?\\d+(?:\\.\\d+)?)\\s*,\\s*(-?\\d+(?:\\.\\d+)?)(?:\\s*\\(([^)]*)\\))?\\s*$");

    private String incomingLocation(Intent intent) {
        if (!"customer".equals(BuildConfig.FLAVOR) || intent == null || !Intent.ACTION_VIEW.equals(intent.getAction())) return null;
        Uri uri = intent.getData();
        if (uri == null || !"geo".equalsIgnoreCase(uri.getScheme())) return null;
        String location = uri.getSchemeSpecificPart();
        if (location == null) return null;
        String[] sections = location.split("\\?", 2);
        String coordinates = sections[0];
        if (sections.length > 1) for (String param : sections[1].split("&")) {
            if (param.startsWith("q=")) {
                try { coordinates = URLDecoder.decode(param.substring(2), "UTF-8"); } catch (Exception ignored) { return null; }
                break;
            }
        }
        Matcher match = COORDS.matcher(coordinates);
        if (!match.matches()) return null;
        try {
            double lat = Double.parseDouble(match.group(1)), lon = Double.parseDouble(match.group(2));
            if (!Double.isFinite(lat) || !Double.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
            Uri.Builder destination = Uri.parse(BuildConfig.HOME_URL).buildUpon()
                .appendQueryParameter("ride_dest_lat", String.valueOf(lat))
                .appendQueryParameter("ride_dest_lon", String.valueOf(lon));
            if (match.group(3) != null && !match.group(3).trim().isEmpty()) destination.appendQueryParameter("ride_dest_name", match.group(3).trim());
            return destination.build().toString();
        } catch (NumberFormatException ignored) { return null; }
    }

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setStatusBarColor(0xffe95716);
        getWindow().setNavigationBarColor(0xffe95716);
        web = new WebView(this);
        web.setFitsSystemWindows(true);
        setContentView(web);
        WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setGeolocationEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setAllowFileAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        android.content.SharedPreferences appPrefs = getSharedPreferences("app_updates", MODE_PRIVATE);
        appUpdated = appPrefs.getInt("version", 0) != BuildConfig.VERSION_CODE;
        if (appUpdated) {
            web.clearCache(true);
            appPrefs.edit().putInt("version", BuildConfig.VERSION_CODE).apply();
        }
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri url = request.getUrl();
                if ("https".equals(url.getScheme()) && isAppHost(url.getHost())) return false;
                if (request.isForMainFrame()) {
                    try { startActivity(new Intent(Intent.ACTION_VIEW, url)); } catch (Exception ignored) { }
                }
                return true;
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public void onGeolocationPermissionsShowPrompt(String origin, GeolocationPermissions.Callback callback) {
                Uri uri = Uri.parse(origin);
                if (!"https".equals(uri.getScheme()) || !isAppHost(uri.getHost())) { callback.invoke(origin, false, false); return; }
                if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED || checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED) {
                    callback.invoke(origin, true, false);
                } else {
                    locationCallback = callback;
                    locationOrigin = origin;
                    requestPermissions(new String[]{Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION}, LOCATION_REQUEST);
                }
            }
            @Override public void onPermissionRequest(PermissionRequest request) {
                runOnUiThread(() -> {
                    Uri origin = request.getOrigin();
                    boolean cameraOnly = request.getResources().length == 1 && PermissionRequest.RESOURCE_VIDEO_CAPTURE.equals(request.getResources()[0]);
                    if (!"https".equals(origin.getScheme()) || !isAppHost(origin.getHost()) || !cameraOnly || !"driver".equals(BuildConfig.FLAVOR)) { request.deny(); return; }
                    if (cameraRequest != null) { request.deny(); return; }
                    if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) request.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
                    else { cameraRequest = request; requestPermissions(new String[]{Manifest.permission.CAMERA}, CAMERA_REQUEST); }
                });
            }
            @Override public void onPermissionRequestCanceled(PermissionRequest request) { if (cameraRequest == request) cameraRequest = null; }
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                try {
                    if ("driver".equals(BuildConfig.FLAVOR) && params.isCaptureEnabled()) {
                        ContentValues values = new ContentValues();
                        values.put(MediaStore.Images.Media.DISPLAY_NAME, "wallah-order-" + System.currentTimeMillis() + ".jpg");
                        values.put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg");
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                            values.put(MediaStore.Images.Media.RELATIVE_PATH, Environment.DIRECTORY_PICTURES + "/Wallah");
                        }
                        captureOutputUri = getContentResolver().insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values);
                        if (captureOutputUri == null) throw new IllegalStateException("تعذر تجهيز صورة الأوردر");
                        Intent camera = new Intent(MediaStore.ACTION_IMAGE_CAPTURE);
                        camera.putExtra(MediaStore.EXTRA_OUTPUT, captureOutputUri);
                        camera.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION);
                        camera.setClipData(ClipData.newUri(getContentResolver(), "wallah-order-photo", captureOutputUri));
                        startActivityForResult(camera, FILE_REQUEST);
                    } else {
                        startActivityForResult(params.createIntent(), FILE_REQUEST);
                    }
                    return true;
                } catch (Exception e) {
                    if (captureOutputUri != null) getContentResolver().delete(captureOutputUri, null, null);
                    captureOutputUri = null;
                    fileCallback = null;
                    callback.onReceiveValue(null);
                    return false;
                }
            }
        });
        web.addJavascriptInterface(new DriverBridge(), "WallahaNative");
        pendingDestination = incomingLocation(getIntent());
        pendingState = state;
        loadInitialPage();
    }

    private void loadInitialPage() {
        if (driverPageLoaded) return;
        driverPageLoaded = true;
        if (pendingDestination != null) web.loadUrl(pendingDestination);
        else if (pendingState != null && !appUpdated) web.restoreState(pendingState);
        else web.loadUrl(Uri.parse(BuildConfig.HOME_URL).buildUpon()
            .appendQueryParameter("app_version", BuildConfig.VERSION_NAME).build().toString());
        pendingState = null;
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        String destination = incomingLocation(intent);
        if (destination != null && web != null) web.loadUrl(destination);
    }

    @Override protected void onResume() {
        super.onResume();
        if (!"customer".equals(BuildConfig.FLAVOR)) return;
        LocationManager locationManager = (LocationManager) getSystemService(LOCATION_SERVICE);
        boolean locationEnabled;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            locationEnabled = locationManager != null && locationManager.isLocationEnabled();
        } else {
            locationEnabled = locationManager != null && (locationManager.isProviderEnabled(LocationManager.GPS_PROVIDER)
                || locationManager.isProviderEnabled(LocationManager.NETWORK_PROVIDER));
        }
        if (locationEnabled) {
            gpsPromptShown = false;
            return;
        }
        if (gpsPromptShown || isFinishing() || isDestroyed()) return;
        gpsPromptShown = true;
        requestGpsEnablement();
    }

    private void requestGpsEnablement() {
        try {
            LocationRequest locationRequest = new LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, 10000L).build();
            LocationSettingsRequest settingsRequest = new LocationSettingsRequest.Builder()
                .addLocationRequest(locationRequest)
                .setAlwaysShow(true)
                .build();
            LocationServices.getSettingsClient(this).checkLocationSettings(settingsRequest)
                .addOnSuccessListener(this, response -> gpsPromptShown = false)
                .addOnFailureListener(this, error -> {
                    if (error instanceof ResolvableApiException) {
                        try {
                            ((ResolvableApiException) error).startResolutionForResult(this, LOCATION_SETTINGS_REQUEST);
                        } catch (IntentSender.SendIntentException ignored) {
                            openLocationSettings();
                        }
                    } else {
                        openLocationSettings();
                    }
                });
        } catch (Exception ignored) {
            openLocationSettings();
        }
    }

    private void openLocationSettings() {
        try { startActivity(new Intent(Settings.ACTION_LOCATION_SOURCE_SETTINGS)); }
        catch (Exception ignored) { startActivity(new Intent(Settings.ACTION_SETTINGS)); }
    }

    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grants) {
        super.onRequestPermissionsResult(requestCode, permissions, grants);
        if (requestCode == NOTIFICATION_REQUEST) {
            notificationPermissionAsked = true;
            startPendingDriverService();
        }
        if (requestCode == CAMERA_REQUEST && cameraRequest != null) {
            PermissionRequest pending = cameraRequest; cameraRequest = null;
            if (grants.length > 0 && grants[0] == PackageManager.PERMISSION_GRANTED) pending.grant(new String[]{PermissionRequest.RESOURCE_VIDEO_CAPTURE});
            else pending.deny();
        }
        if (requestCode == LOCATION_REQUEST && locationCallback != null) {
            boolean allowed = false;
            for (int result : grants) if (result == PackageManager.PERMISSION_GRANTED) allowed = true;
            locationCallback.invoke(locationOrigin, allowed, false);
            locationCallback = null;
            locationOrigin = null;
            if (allowed) startPendingDriverService();
            else {
                pendingDriverServiceIntent = null;
                if (web != null) web.evaluateJavascript("clearBackgroundTrackingToken()", null);
            }
        }
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == LOCATION_SETTINGS_REQUEST && resultCode == RESULT_OK) gpsPromptShown = false;
        if (requestCode == FILE_REQUEST && fileCallback != null) {
            Uri[] results;
            if (captureOutputUri != null) {
                Uri photo = captureOutputUri;
                captureOutputUri = null;
                if (resultCode == RESULT_OK) results = new Uri[]{photo};
                else {
                    getContentResolver().delete(photo, null, null);
                    results = null;
                }
            } else {
                results = WebChromeClient.FileChooserParams.parseResult(resultCode, data);
            }
            fileCallback.onReceiveValue(results);
            fileCallback = null;
        }
    }

    @Override protected void onSaveInstanceState(Bundle out) {
        web.saveState(out);
        super.onSaveInstanceState(out);
    }

    @Override public void onBackPressed() {
        handleBackPressed();
    }

    private void handleBackPressed() {
        if (web.canGoBack()) {
            web.goBack();
            lastBackPressAt = 0;
            return;
        }
        long now = SystemClock.elapsedRealtime();
        if (lastBackPressAt != 0 && now - lastBackPressAt <= BACK_PRESS_WINDOW_MS) {
            finish();
            return;
        }
        lastBackPressAt = now;
        Toast.makeText(this, "اضغط مرة ثانية للخروج", Toast.LENGTH_SHORT).show();
    }

    private final class DriverBridge {
        @JavascriptInterface public void startDriverTracking(String token, String baseUrl) {
            if (!"driver".equals(BuildConfig.FLAVOR) || token == null || token.trim().isEmpty()) return;
            Uri base = Uri.parse(baseUrl == null ? "" : baseUrl);
            if (!"https".equals(base.getScheme()) || !isAppHost(base.getHost())) return;
            pendingDriverServiceIntent = new Intent(MainActivity.this, DriverLocationService.class)
                .putExtra(DriverLocationService.EXTRA_TOKEN, token)
                .putExtra(DriverLocationService.EXTRA_BASE_URL, base.buildUpon().path("").clearQuery().fragment(null).build().toString());
            startPendingDriverService();
        }

        @JavascriptInterface public void stopDriverTracking() {
            if ("driver".equals(BuildConfig.FLAVOR)) {
                pendingDriverServiceIntent = null;
                stopService(new Intent(MainActivity.this, DriverLocationService.class));
            }
        }
    }

    private void startPendingDriverService() {
        if (pendingDriverServiceIntent == null) return;
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED &&
            !notificationPermissionAsked) {
            notificationPermissionAsked = true;
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, NOTIFICATION_REQUEST);
            return;
        }
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED &&
            checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) != PackageManager.PERMISSION_GRANTED) return;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) startForegroundService(pendingDriverServiceIntent);
        else startService(pendingDriverServiceIntent);
    }

    @Override protected void onDestroy() {
        if (fileCallback != null) fileCallback.onReceiveValue(null);
        if (cameraRequest != null) { cameraRequest.deny(); cameraRequest = null; }
        web.destroy();
        super.onDestroy();
    }
}
