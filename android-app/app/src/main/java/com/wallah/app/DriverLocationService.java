package com.wallah.app;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.IBinder;
import android.util.Log;

import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class DriverLocationService extends Service {
    public static final String EXTRA_TOKEN = "driver_token";
    public static final String EXTRA_BASE_URL = "api_base_url";
    private static final String CHANNEL_ID = "wallaha_driver_location";
    private static final int NOTIFICATION_ID = 7301;
    private static final long UPDATE_INTERVAL_MS = 15000L;
    private static final float UPDATE_DISTANCE_METERS = 0f;

    private LocationManager locationManager;
    private LocationListener locationListener;
    private final ExecutorService network = Executors.newSingleThreadExecutor();
    private volatile String token;
    private volatile String baseUrl;
    private long lastSentAt;

    @Override public void onCreate() {
        super.onCreate();
        locationManager = (LocationManager) getSystemService(LOCATION_SERVICE);
        createNotificationChannel();
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) { stopSelf(); return START_NOT_STICKY; }
        String nextToken = intent.getStringExtra(EXTRA_TOKEN);
        String nextBaseUrl = intent.getStringExtra(EXTRA_BASE_URL);
        if (nextToken != null && !nextToken.trim().isEmpty()) token = nextToken;
        if (isValidBase(nextBaseUrl)) baseUrl = nextBaseUrl;
        if (token == null || !isValidBase(baseUrl)) { stopSelf(); return START_NOT_STICKY; }
        startForeground(NOTIFICATION_ID, createNotification());
        beginLocationUpdates();
        return START_NOT_STICKY;
    }

    private boolean isValidBase(String value) {
        if (value == null) return false;
        Uri uri = Uri.parse(value);
        String host = uri.getHost();
        return "https".equals(uri.getScheme()) && ("walla3ha.com".equals(host) || "wellah-production.up.railway.app".equals(host));
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            NotificationChannel channel = new NotificationChannel(CHANNEL_ID, "موقع الطيار أثناء الشيفت", NotificationManager.IMPORTANCE_LOW);
            channel.setDescription("تحديث موقع الطيار أثناء تشغيل التطبيق في الخلفية");
            NotificationManager manager = getSystemService(NotificationManager.class);
            if (manager != null) manager.createNotificationChannel(channel);
        }
    }

    private Notification createNotification() {
        Intent launch = getPackageManager().getLaunchIntentForPackage(getPackageName());
        PendingIntent pending = launch == null ? null : PendingIntent.getActivity(this, 0, launch,
            PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M ? PendingIntent.FLAG_IMMUTABLE : 0));
        Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
            ? new Notification.Builder(this, CHANNEL_ID) : new Notification.Builder(this);
        builder.setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setContentTitle("ولعه · مشاركة موقع الطيار")
            .setContentText("الموقع يتحدث أثناء الشيفت حتى لو التطبيق في الخلفية")
            .setOngoing(true)
            .setCategory(Notification.CATEGORY_SERVICE);
        if (pending != null) builder.setContentIntent(pending);
        return builder.build();
    }

    private void beginLocationUpdates() {
        if (locationManager == null || locationListener != null) return;
        boolean fine = checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED;
        boolean coarse = checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED;
        if (!fine && !coarse) { stopSelf(); return; }
        locationListener = new LocationListener() {
            @Override public void onLocationChanged(Location location) { sendLocation(location); }
            @Override public void onStatusChanged(String provider, int status, Bundle extras) { }
            @Override public void onProviderDisabled(String provider) { }
            @Override public void onProviderEnabled(String provider) { }
        };
        boolean requested = false;
        try {
            if (locationManager.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
                locationManager.requestLocationUpdates(LocationManager.GPS_PROVIDER, UPDATE_INTERVAL_MS, UPDATE_DISTANCE_METERS, locationListener);
                requested = true;
            }
        } catch (SecurityException ignored) { }
        try {
            if (locationManager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) {
                locationManager.requestLocationUpdates(LocationManager.NETWORK_PROVIDER, UPDATE_INTERVAL_MS, UPDATE_DISTANCE_METERS, locationListener);
                requested = true;
            }
        } catch (SecurityException ignored) { }
        if (!requested) { locationListener = null; stopSelf(); }
    }

    private void sendLocation(Location location) {
        long now = System.currentTimeMillis();
        if (now - lastSentAt < UPDATE_INTERVAL_MS || token == null || !isValidBase(baseUrl)) return;
        lastSentAt = now;
        final String auth = token, endpoint = baseUrl + "/api/location";
        final double lat = location.getLatitude(), lon = location.getLongitude();
        network.execute(() -> {
            HttpURLConnection connection = null;
            try {
                JSONObject body = new JSONObject().put("lat", lat).put("lon", lon);
                connection = (HttpURLConnection) new URL(endpoint).openConnection();
                connection.setRequestMethod("POST");
                connection.setConnectTimeout(12000);
                connection.setReadTimeout(12000);
                connection.setDoOutput(true);
                connection.setRequestProperty("Authorization", "Bearer " + auth);
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                byte[] data = body.toString().getBytes(StandardCharsets.UTF_8);
                try (OutputStream stream = connection.getOutputStream()) { stream.write(data); }
                int status = connection.getResponseCode();
                if (status == HttpURLConnection.HTTP_UNAUTHORIZED) stopSelf();
            } catch (Exception e) {
                Log.w("DriverLocationService", "Location update failed", e);
            } finally {
                if (connection != null) connection.disconnect();
            }
        });
    }

    @Override public void onDestroy() {
        if (locationManager != null && locationListener != null) {
            try { locationManager.removeUpdates(locationListener); } catch (SecurityException ignored) { }
        }
        network.shutdownNow();
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent intent) { return null; }
}
