package com.wallah.app;

import android.Manifest;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.media.AudioAttributes;
import android.media.RingtoneManager;
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

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.HashSet;
import java.util.Set;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

public class DriverLocationService extends Service {
    public static final String EXTRA_TOKEN = "driver_token";
    public static final String EXTRA_BASE_URL = "api_base_url";
    private static final String CHANNEL_ID = "wallaha_driver_location";
    private static final String ORDER_CHANNEL_ID = "wallaha_driver_orders_v2";
    private static final int NOTIFICATION_ID = 7301;
    private static final long UPDATE_INTERVAL_MS = 15000L;
    private static final float UPDATE_DISTANCE_METERS = 0f;

    private LocationManager locationManager;
    private LocationListener locationListener;
    private final ScheduledExecutorService network = Executors.newSingleThreadScheduledExecutor();
    private final Set<String> notifiedOffers = new HashSet<>();
    private boolean orderPollingScheduled;
    private volatile String token;
    private volatile String baseUrl;
    private long lastSentAt;

    @Override public void onCreate() {
        super.onCreate();
        locationManager = (LocationManager) getSystemService(LOCATION_SERVICE);
        createNotificationChannel();
        Set<String> saved = getSharedPreferences("driver_order_alerts", MODE_PRIVATE).getStringSet("shown_offers", Collections.emptySet());
        if (saved != null) notifiedOffers.addAll(saved);
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
        if (!orderPollingScheduled) {
            orderPollingScheduled = true;
            network.scheduleWithFixedDelay(this::pollAssignedOrders, 2, 8, TimeUnit.SECONDS);
        }
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
            NotificationManager manager = getSystemService(NotificationManager.class);
            if (manager == null) return;
            NotificationChannel channel = new NotificationChannel(CHANNEL_ID, "موقع الطيار أثناء الشيفت", NotificationManager.IMPORTANCE_LOW);
            channel.setDescription("تحديث موقع الطيار أثناء تشغيل التطبيق في الخلفية");
            manager.createNotificationChannel(channel);

            NotificationChannel orders = new NotificationChannel(ORDER_CHANNEL_ID, "طلبات جديدة للطيار", NotificationManager.IMPORTANCE_HIGH);
            orders.setDescription("تنبيه الطيار عند إسناد طلب جديد أثناء الشيفت");
            orders.enableVibration(true);
            orders.setVibrationPattern(new long[]{0, 180, 120, 180});
            orders.setSound(RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION),
                new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_NOTIFICATION)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build());
            manager.createNotificationChannel(orders);
        }
    }

    private void pollAssignedOrders() {
        String auth = token, base = baseUrl;
        if (auth == null || !isValidBase(base)) return;
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(base + "/api/driver/offers").openConnection();
            connection.setRequestMethod("GET");
            connection.setConnectTimeout(7000);
            connection.setReadTimeout(7000);
            connection.setRequestProperty("Authorization", "Bearer " + auth);
            connection.setRequestProperty("Accept", "application/json");
            int status = connection.getResponseCode();
            if (status == HttpURLConnection.HTTP_UNAUTHORIZED) { stopSelf(); return; }
            if (status != HttpURLConnection.HTTP_OK) return;
            try (InputStream stream = connection.getInputStream(); ByteArrayOutputStream bytes = new ByteArrayOutputStream()) {
                byte[] buffer = new byte[4096];
                int count;
                while ((count = stream.read(buffer)) != -1) bytes.write(buffer, 0, count);
                JSONObject payload = new JSONObject(bytes.toString(StandardCharsets.UTF_8.name()));
                JSONArray offers = payload.optJSONArray("offers");
                if (offers == null) return;
                long now = System.currentTimeMillis() / 1000L;
                for (int i = 0; i < offers.length(); i++) {
                    JSONObject offer = offers.optJSONObject(i);
                    if (offer == null) continue;
                    long id = offer.optLong("id", -1), until = offer.optLong("offer_until", 0);
                    if (id > 0 && until > now) showOrderNotification(id, until);
                }
            }
        } catch (Exception e) {
            Log.w("DriverLocationService", "Order notification check failed", e);
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private void showOrderNotification(long orderId, long offerUntil) {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return;
        String key = orderId + ":" + offerUntil;
        if (!notifiedOffers.add(key)) return;
        if (notifiedOffers.size() > 200) {
            notifiedOffers.clear();
            notifiedOffers.add(key);
        }
        getSharedPreferences("driver_order_alerts", MODE_PRIVATE).edit()
            .putStringSet("shown_offers", new HashSet<>(notifiedOffers)).apply();
        Intent launch = getPackageManager().getLaunchIntentForPackage(getPackageName());
        PendingIntent pending = launch == null ? null : PendingIntent.getActivity(this, (int) (orderId & 0x7fffffff), launch,
            PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M ? PendingIntent.FLAG_IMMUTABLE : 0));
        Notification.Builder builder = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
            ? new Notification.Builder(this, ORDER_CHANNEL_ID) : new Notification.Builder(this);
        builder.setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("ولعه · طلب جديد")
            .setContentText("وصلك طلب توصيل جديد. افتح التطبيق للاطلاع عليه.")
            .setAutoCancel(true)
            .setCategory(Notification.CATEGORY_STATUS)
            .setPriority(Notification.PRIORITY_HIGH);
        if (pending != null) builder.setContentIntent(pending);
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) builder.setDefaults(Notification.DEFAULT_SOUND | Notification.DEFAULT_VIBRATE);
        NotificationManager manager = getSystemService(NotificationManager.class);
        if (manager != null) manager.notify("wallaha-driver-order-" + orderId, 1, builder.build());
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
