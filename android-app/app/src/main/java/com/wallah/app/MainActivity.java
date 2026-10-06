package com.wallah.app;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.hardware.biometrics.BiometricPrompt;
import android.os.Build;
import android.os.CancellationSignal;
import android.provider.Settings;
import android.view.View;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Bundle;
import android.webkit.GeolocationPermissions;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import java.net.URLDecoder;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class MainActivity extends Activity {
    private static final String HOST = "wellah-production.up.railway.app";
    private static final int LOCATION_REQUEST = 41;
    private static final int FILE_REQUEST = 42;
    private WebView web;
    private boolean driverUnlocked = false;
    private boolean driverPromptActive = false;
    private boolean driverPageLoaded = false;
    private Bundle pendingState;
    private String pendingDestination;
    private ValueCallback<Uri[]> fileCallback;
    private GeolocationPermissions.Callback locationCallback;
    private String locationOrigin;
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
        getWindow().setStatusBarColor(0xff093d3a);
        getWindow().setNavigationBarColor(0xff093d3a);
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
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri url = request.getUrl();
                if ("https".equals(url.getScheme()) && HOST.equals(url.getHost())) return false;
                if (request.isForMainFrame()) {
                    try { startActivity(new Intent(Intent.ACTION_VIEW, url)); } catch (Exception ignored) { }
                }
                return true;
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public void onGeolocationPermissionsShowPrompt(String origin, GeolocationPermissions.Callback callback) {
                Uri uri = Uri.parse(origin);
                if (!"https".equals(uri.getScheme()) || !HOST.equals(uri.getHost())) { callback.invoke(origin, false, false); return; }
                if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED || checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED) {
                    callback.invoke(origin, true, false);
                } else {
                    locationCallback = callback;
                    locationOrigin = origin;
                    requestPermissions(new String[]{Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION}, LOCATION_REQUEST);
                }
            }
            @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                try { startActivityForResult(params.createIntent(), FILE_REQUEST); return true; }
                catch (Exception e) { fileCallback = null; callback.onReceiveValue(null); return false; }
            }
        });
        pendingDestination = incomingLocation(getIntent());
        pendingState = state;
        if ("driver".equals(BuildConfig.FLAVOR)) web.setVisibility(View.INVISIBLE);
        else loadInitialPage();
    }

    private void loadInitialPage() {
        if (driverPageLoaded) return;
        driverPageLoaded = true;
        if (pendingDestination != null) web.loadUrl(pendingDestination);
        else if (pendingState != null) web.restoreState(pendingState);
        else web.loadUrl(BuildConfig.HOME_URL);
        pendingState = null;
    }

    @Override protected void onResume() {
        super.onResume();
        if ("driver".equals(BuildConfig.FLAVOR) && !driverUnlocked && !driverPromptActive) requireDriverBiometric();
    }

    @Override protected void onStop() {
        if ("driver".equals(BuildConfig.FLAVOR)) {
            driverUnlocked = false;
            web.setVisibility(View.INVISIBLE);
        }
        super.onStop();
    }

    private void requireDriverBiometric() {
        if (Build.VERSION.SDK_INT < 28) {
            biometricUnavailable("التحقق البيومتري يحتاج أندرويد 9 أو أحدث على جهاز الطيار.");
            return;
        }
        driverPromptActive = true;
        CancellationSignal signal = new CancellationSignal();
        new BiometricPrompt.Builder(this)
            .setTitle("تأكيد هوية الطيار")
            .setSubtitle("استخدم بصمة الوجه أو البصمة المسجلة على هاتفك لفتح الطلبات")
            .setNegativeButton("إلغاء", getMainExecutor(), (dialog, which) -> {
                driverPromptActive = false;
                finish();
            })
            .build()
            .authenticate(signal, getMainExecutor(), new BiometricPrompt.AuthenticationCallback() {
                @Override public void onAuthenticationSucceeded(BiometricPrompt.AuthenticationResult result) {
                    driverPromptActive = false;
                    driverUnlocked = true;
                    web.setVisibility(View.VISIBLE);
                    loadInitialPage();
                }
                @Override public void onAuthenticationError(int code, CharSequence message) {
                    driverPromptActive = false;
                    if (!isFinishing()) biometricUnavailable("لم يكتمل التحقق البيومتري. تأكد من تسجيل بصمتك في إعدادات الهاتف.");
                }
            });
    }

    private void biometricUnavailable(String message) {
        if (isFinishing()) return;
        new AlertDialog.Builder(this)
            .setTitle("التحقق مطلوب")
            .setMessage(message)
            .setPositiveButton("إعدادات الهاتف", (dialog, which) -> {
                try { startActivity(new Intent(Settings.ACTION_SECURITY_SETTINGS)); }
                catch (Exception ignored) { finish(); }
            })
            .setNegativeButton("إغلاق", (dialog, which) -> finish())
            .setOnCancelListener(dialog -> finish())
            .show();
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        String destination = incomingLocation(intent);
        if (destination != null && web != null) web.loadUrl(destination);
    }

    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grants) {
        super.onRequestPermissionsResult(requestCode, permissions, grants);
        if (requestCode == LOCATION_REQUEST && locationCallback != null) {
            boolean allowed = false;
            for (int result : grants) if (result == PackageManager.PERMISSION_GRANTED) allowed = true;
            locationCallback.invoke(locationOrigin, allowed, false);
            locationCallback = null;
            locationOrigin = null;
        }
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == FILE_REQUEST && fileCallback != null) {
            fileCallback.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data));
            fileCallback = null;
        }
    }

    @Override protected void onSaveInstanceState(Bundle out) {
        web.saveState(out);
        super.onSaveInstanceState(out);
    }

    @Override public void onBackPressed() {
        if (web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    @Override protected void onDestroy() {
        if (fileCallback != null) fileCallback.onReceiveValue(null);
        web.destroy();
        super.onDestroy();
    }
}
