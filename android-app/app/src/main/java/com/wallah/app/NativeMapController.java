package com.wallah.app;

import android.Manifest;
import android.app.Activity;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;
import com.google.android.gms.common.ConnectionResult;
import com.google.android.gms.common.GoogleApiAvailability;
import com.google.android.gms.location.LocationServices;
import com.google.android.gms.location.Priority;
import com.google.android.gms.maps.CameraUpdateFactory;
import com.google.android.gms.maps.GoogleMap;
import com.google.android.gms.maps.GoogleMapOptions;
import com.google.android.gms.maps.MapView;
import com.google.android.gms.maps.model.BitmapDescriptor;
import com.google.android.gms.maps.model.BitmapDescriptorFactory;
import com.google.android.gms.maps.model.LatLng;
import com.google.android.gms.maps.model.MapColorScheme;
import com.google.android.gms.maps.model.MarkerOptions;
import com.google.android.gms.maps.model.PolylineOptions;
import com.google.android.gms.tasks.CancellationTokenSource;
import org.json.JSONArray;
import org.json.JSONObject;

/** Owns a single native map overlay. No Map ID, web Maps API, search or routing calls. */
final class NativeMapController {
    static final int LOCATION_PERMISSION_REQUEST = 46;
    interface ResultListener { void onResult(JSONObject value); }
    private final Activity activity;
    private final FrameLayout parent;
    private final ResultListener listener;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private FrameLayout overlay;
    private MapView mapView;
    private GoogleMap map;
    private NativeMapRequest request;
    private Button done;
    private ProgressBar progress;
    private Runnable loadTimeout;
    private CancellationTokenSource locationToken;
    private BitmapDescriptor bikeIcon;
    private boolean started, resumed;
    private String featureSnapshot;

    NativeMapController(Activity activity, FrameLayout parent, ResultListener listener) {
        this.activity = activity;this.parent = parent;this.listener = listener;
    }

    void open(String raw) {
        NativeMapRequest next;
        try { next = NativeMapRequest.parse(raw); }
        catch (IllegalArgumentException error) {
            JSONObject rejected = NativeMapRequest.rejectedResult(raw);
            if (rejected != null) listener.onResult(rejected);
            return;
        }
        if (request != null) close("cancelled");
        request = next;
        if (!BuildConfig.NATIVE_MAPS_CONFIGURED || GoogleApiAvailability.getInstance()
                .isGooglePlayServicesAvailable(activity) != ConnectionResult.SUCCESS) {
            close("unavailable");return;
        }
        try {
            overlay = new FrameLayout(activity);
            overlay.setBackgroundColor(0xff202124);
            overlay.setClickable(true);
            overlay.setFocusable(true);
            overlay.setImportantForAccessibility(View.IMPORTANT_FOR_ACCESSIBILITY_YES);
            // Native normal map: no cloud Map ID that could select a different billing SKU.
            mapView = new MapView(activity, new GoogleMapOptions()
                .mapType(GoogleMap.MAP_TYPE_NORMAL).mapColorScheme(MapColorScheme.DARK)
                .compassEnabled(true).mapToolbarEnabled(false).zoomControlsEnabled(false));
            overlay.addView(mapView, new FrameLayout.LayoutParams(-1,-1));
            parent.addView(overlay, new FrameLayout.LayoutParams(-1,-1));
            mapView.onCreate(null);
            if (started) mapView.onStart();
            if (resumed) mapView.onResume();
            addControls(next);
            final MapView activeView = mapView;
            // A slow or unauthorized Google load returns to the existing web map.
            loadTimeout = () -> { if (request != null && request.requestId.equals(next.requestId) && !done.isEnabled()) close("unavailable"); };
            handler.postDelayed(loadTimeout,25000);
            mapView.getMapAsync(googleMap -> {
                if (mapView != activeView || request == null) return;
                map = googleMap;
                map.setMapColorScheme(MapColorScheme.DARK);
                map.setBuildingsEnabled(true);
                // Leave Google's attribution visible above the confirmation controls.
                map.setPadding(0,dp(76),0,dp(112));
                map.moveCamera(CameraUpdateFactory.newLatLngZoom(new LatLng(next.lat,next.lon),next.zoom));
                drawFeatures(request);
                enableLocationLayer();
                map.setOnMapClickListener(position -> {
                    if (request != null && request.picker) map.animateCamera(CameraUpdateFactory.newLatLng(position));
                });
                map.setOnMapLoadedCallback(() -> {
                    if (mapView != activeView || done == null) return;
                    done.setEnabled(true);progress.setVisibility(View.GONE);
                    if (loadTimeout != null) handler.removeCallbacks(loadTimeout);
                });
            });
        } catch (RuntimeException error) { close("unavailable"); }
    }

    private void addControls(NativeMapRequest value) {
        LinearLayout heading = new LinearLayout(activity);
        heading.setGravity(Gravity.CENTER_VERTICAL);
        heading.setPadding(dp(12),dp(8),dp(12),dp(8));
        heading.setBackgroundColor(0xdd202124);
        Button back = button("رجوع",0xff30343b,Color.WHITE);
        back.setContentDescription("الرجوع بدون تغيير المكان");
        back.setOnClickListener(v -> close("cancelled"));
        heading.addView(back,new LinearLayout.LayoutParams(dp(80),dp(48)));
        TextView title = new TextView(activity);title.setText(value.title);title.setTextColor(Color.WHITE);
        title.setTextSize(17);title.setGravity(Gravity.END|Gravity.CENTER_VERTICAL);
        heading.addView(title,new LinearLayout.LayoutParams(0,dp(48),1));
        overlay.addView(heading,new FrameLayout.LayoutParams(-1,dp(64),Gravity.TOP));
        if (value.picker) {
            TextView pin = new TextView(activity);pin.setText("⚑");pin.setTextSize(29);
            pin.setGravity(Gravity.CENTER);pin.setTextColor(0xff353b46);
            pin.setBackground(rounded(Color.WHITE,12));pin.setElevation(dp(5));
            pin.setContentDescription("دبوس المكان المحدد؛ حرّك الخريطة لتغييره");
            FrameLayout.LayoutParams pinLayout = new FrameLayout.LayoutParams(dp(48),dp(48),Gravity.CENTER);
            // Match the map camera's padded center; the bottom of the flag is the target.
            pin.setTranslationY(dp(-42));overlay.addView(pin,pinLayout);
        }
        Button gps = button("⌖",0xff30343b,Color.WHITE);gps.setTextSize(30);
        gps.setContentDescription("تحديد موقعي الحالي");gps.setOnClickListener(v -> locate());
        FrameLayout.LayoutParams gpsLayout = new FrameLayout.LayoutParams(dp(56),dp(56),Gravity.BOTTOM|Gravity.START);
        gpsLayout.setMargins(dp(16),0,dp(16),dp(132));overlay.addView(gps,gpsLayout);
        LinearLayout footer = new LinearLayout(activity);footer.setOrientation(LinearLayout.VERTICAL);
        footer.setPadding(dp(16),dp(6),dp(16),dp(16));footer.setBackgroundColor(0xee202124);
        TextView hint = new TextView(activity);
        hint.setText(value.picker ? "حرّك الخريطة لتحديد المكان ثم اضغط تم" : "يمكنك تحريك الخريطة وتكبيرها");
        hint.setTextColor(Color.WHITE);hint.setTextSize(12);hint.setGravity(Gravity.CENTER);
        footer.addView(hint,new LinearLayout.LayoutParams(-1,dp(26)));
        done = button("تم",0xffe4ed24,0xff202124);done.setEnabled(false);
        done.setContentDescription(value.picker ? "تأكيد المكان والعودة للطلب" : "العودة من الخريطة");
        done.setTextSize(21);done.setOnClickListener(v -> close(request != null && request.picker ? "selected" : "closed"));
        footer.addView(done,new LinearLayout.LayoutParams(-1,dp(56)));
        overlay.addView(footer,new FrameLayout.LayoutParams(-1,dp(104),Gravity.BOTTOM));
        progress = new ProgressBar(activity);progress.setContentDescription("جارٍ تحميل الخريطة");
        overlay.addView(progress,new FrameLayout.LayoutParams(dp(42),dp(42),Gravity.CENTER));
    }

    void update(String raw) {
        if (request == null) return;
        try {
            NativeMapRequest next = NativeMapRequest.parse(raw);
            if (!next.requestId.equals(request.requestId) || !next.mapId.equals(request.mapId)) return;
            request = next;
            if (map != null) drawFeatures(next);
        } catch (IllegalArgumentException ignored) { }
    }

    private void drawFeatures(NativeMapRequest value) {
        String features = value.markers.toString()+value.lines.toString();
        if (features.equals(featureSnapshot)) return;
        featureSnapshot = features;
        map.clear();
        for (int i=0;i<value.markers.length();i++) {
            JSONObject marker = value.markers.optJSONObject(i);if (marker == null) continue;
            try {
                LatLng position = new LatLng(NativeMapRequest.coordinate(marker,"lat",90),NativeMapRequest.coordinate(marker,"lon",180));
                MarkerOptions options = new MarkerOptions().position(position);
                if (marker.optBoolean("driver")) options.icon(bikeIcon()).title("موقع الطيار").anchor(.5f,.5f);
                else options.icon(BitmapDescriptorFactory.defaultMarker(BitmapDescriptorFactory.HUE_ORANGE));
                map.addMarker(options);
            } catch (IllegalArgumentException ignored) { }
        }
        for (int i=0;i<value.lines.length();i++) {
            JSONObject line = value.lines.optJSONObject(i);if (line == null) continue;
            JSONArray points = line.optJSONArray("points");if (points == null || points.length()>1001) continue;
            double width = line.optDouble("width",5);
            if (!Double.isFinite(width)) width=5;
            PolylineOptions options = new PolylineOptions().color(lineColor(line.optString("color")))
                .width((float)Math.max(1,Math.min(12,width)));
            boolean valid = true;
            for (int j=0;j<points.length();j++) {
                JSONObject point = points.optJSONObject(j);
                try {
                    if (point == null) throw new IllegalArgumentException();
                    options.add(new LatLng(NativeMapRequest.coordinate(point,"lat",90),NativeMapRequest.coordinate(point,"lon",180)));
                } catch (IllegalArgumentException ignored) { valid=false;break; }
            }
            if (valid && points.length()>1) map.addPolyline(options);
        }
    }

    private int lineColor(String value) {
        if (value.matches("#[0-9a-fA-F]{3}")) value="#"+value.charAt(1)+value.charAt(1)+value.charAt(2)+value.charAt(2)+value.charAt(3)+value.charAt(3);
        try { return Color.parseColor(value); } catch (IllegalArgumentException ignored) { return 0xffe84b36; }
    }

    private BitmapDescriptor bikeIcon() {
        if (bikeIcon != null) return bikeIcon;
        int size=dp(44);Bitmap bitmap=Bitmap.createBitmap(size,size,Bitmap.Config.ARGB_8888);
        Canvas canvas=new Canvas(bitmap);Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);
        paint.setColor(Color.WHITE);canvas.drawCircle(size/2f,size/2f,size/2f,paint);
        paint.setColor(0xffe84b36);canvas.drawCircle(size/2f,size/2f,size/2f-dp(3),paint);
        paint.setTextAlign(Paint.Align.CENTER);paint.setTextSize(dp(25));
        canvas.drawText("🏍",size/2f,size/2f-(paint.ascent()+paint.descent())/2,paint);
        bikeIcon=BitmapDescriptorFactory.fromBitmap(bitmap);return bikeIcon;
    }

    private boolean hasLocationPermission() {
        return activity.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION)==PackageManager.PERMISSION_GRANTED
            || activity.checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION)==PackageManager.PERMISSION_GRANTED;
    }

    private void enableLocationLayer() {
        if (map == null || !hasLocationPermission()) return;
        try { map.setMyLocationEnabled(true);map.getUiSettings().setMyLocationButtonEnabled(false); }
        catch (SecurityException ignored) { }
    }

    void onLocationPermissionResult() {
        if (request == null) return;
        if (hasLocationPermission()) { enableLocationLayer();locate(); }
        else Toast.makeText(activity,"اسمح بإذن الموقع أو حدد المكان يدويًا",Toast.LENGTH_LONG).show();
    }

    private void locate() {
        if (map == null || request == null) return;
        if (!hasLocationPermission()) {
            activity.requestPermissions(new String[]{Manifest.permission.ACCESS_FINE_LOCATION,Manifest.permission.ACCESS_COARSE_LOCATION},LOCATION_PERMISSION_REQUEST);return;
        }
        enableLocationLayer();
        if (locationToken != null) locationToken.cancel();
        locationToken = new CancellationTokenSource();
        final String activeId=request.requestId;
        try {
            LocationServices.getFusedLocationProviderClient(activity)
                .getCurrentLocation(Priority.PRIORITY_HIGH_ACCURACY,locationToken.getToken())
                .addOnSuccessListener(activity,location -> {
                    if (request == null || !request.requestId.equals(activeId) || map == null) return;
                    if (location == null) { locationUnavailable();return; }
                    map.animateCamera(CameraUpdateFactory.newLatLngZoom(new LatLng(location.getLatitude(),location.getLongitude()),17));
                }).addOnFailureListener(activity,error -> {
                    if (request != null && request.requestId.equals(activeId)) locationUnavailable();
                });
        } catch (SecurityException error) { locationUnavailable(); }
    }

    private void locationUnavailable() {
        Toast.makeText(activity,"تعذر تحديد موقعك؛ شغّل GPS أو حدد المكان يدويًا",Toast.LENGTH_LONG).show();
    }

    void close(String status) {
        if (request == null) return;
        NativeMapRequest finished=request;request=null;
        JSONObject result=new JSONObject();
        try {
            result.put("requestId",finished.requestId).put("status",status);
            if ("selected".equals(status) && finished.picker && map != null) {
                result.put("lat",map.getCameraPosition().target.latitude).put("lon",map.getCameraPosition().target.longitude)
                    .put("zoom",map.getCameraPosition().zoom);
            }
        } catch (org.json.JSONException ignored) { }
        if (loadTimeout != null) { handler.removeCallbacks(loadTimeout);loadTimeout=null; }
        if (locationToken != null) { locationToken.cancel();locationToken=null; }
        if (mapView != null) {
            if (resumed) mapView.onPause();
            if (started) mapView.onStop();
            mapView.onDestroy();mapView=null;
        }
        if (overlay != null) { parent.removeView(overlay);overlay=null; }
        map=null;done=null;progress=null;featureSnapshot=null;bikeIcon=null;
        listener.onResult(result);
    }

    void closeRequest(String requestId) { if (request != null && request.requestId.equals(requestId)) close("cancelled"); }
    boolean onBackPressed() { if (request == null) return false;close("cancelled");return true; }
    void onStart() { started=true;if (mapView != null) mapView.onStart(); }
    void onResume() { resumed=true;if (mapView != null) mapView.onResume(); }
    void onPause() { if (mapView != null) mapView.onPause();resumed=false; }
    void onStop() { if (mapView != null) mapView.onStop();started=false; }
    void onLowMemory() { if (mapView != null) mapView.onLowMemory(); }
    void destroy() { close("cancelled");handler.removeCallbacksAndMessages(null); }
    private int dp(int value) { return Math.round(value*activity.getResources().getDisplayMetrics().density); }
    private GradientDrawable rounded(int color,int radius) { GradientDrawable background=new GradientDrawable();background.setColor(color);background.setCornerRadius(dp(radius));return background; }
    private Button button(String text,int color,int textColor) {
        Button button=new Button(activity);button.setText(text);button.setAllCaps(false);button.setTextColor(textColor);
        button.setTypeface(Typeface.DEFAULT,Typeface.BOLD);button.setBackground(rounded(color,16));return button;
    }
}
