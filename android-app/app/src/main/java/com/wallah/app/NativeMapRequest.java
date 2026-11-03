package com.wallah.app;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

/** Validated, bounded input from the trusted Walla3ha web page. */
final class NativeMapRequest {
    private static final Set<String> MAP_IDS = new HashSet<>(Arrays.asList(
        "customer-map", "pickup-map", "merchant-map", "driver-map", "tracking-map", "trip-map", "admin-map"));
    final String requestId, mapId, title;
    final boolean picker;
    final double lat, lon;
    final float zoom;
    final JSONArray markers, lines;

    static NativeMapRequest parse(String raw) {
        if (raw == null || raw.length() > 300000) throw new IllegalArgumentException("Invalid map payload");
        try { return new NativeMapRequest(new JSONObject(raw)); }
        catch (JSONException error) { throw new IllegalArgumentException("Invalid map JSON", error); }
    }

    static JSONObject rejectedResult(String raw) {
        if (raw == null || raw.length() > 300000) return null;
        try {
            String id = new JSONObject(raw).optString("requestId", "");
            if (!id.matches("[A-Za-z0-9_-]{1,80}")) return null;
            return new JSONObject().put("requestId",id).put("status","unavailable");
        } catch (JSONException ignored) { return null; }
    }

    private NativeMapRequest(JSONObject value) throws JSONException {
        requestId = value.optString("requestId", "");
        mapId = value.optString("mapId", "");
        if (!requestId.matches("[A-Za-z0-9_-]{1,80}") || !MAP_IDS.contains(mapId))
            throw new IllegalArgumentException("Invalid map identity");
        lat = coordinate(value, "lat", 90);
        lon = coordinate(value, "lon", 180);
        double requestedZoom = value.optDouble("zoom", 16);
        zoom = (float) (Double.isFinite(requestedZoom) ? Math.max(3, Math.min(21, requestedZoom)) : 16);
        picker = "customer-map".equals(mapId) || "pickup-map".equals(mapId) || "merchant-map".equals(mapId);
        String label = value.optString("title", picker ? "حدد المكان" : "الخريطة");
        title = label.substring(0, Math.min(120, label.length()));
        JSONArray inputMarkers = value.optJSONArray("markers"), inputLines = value.optJSONArray("lines");
        if ((inputMarkers != null && inputMarkers.length() > 100) || (inputLines != null && inputLines.length() > 4))
            throw new IllegalArgumentException("Too many map features");
        markers = inputMarkers == null ? new JSONArray() : inputMarkers;
        lines = inputLines == null ? new JSONArray() : inputLines;
    }

    static double coordinate(JSONObject value, String key, int limit) {
        Object raw = value.opt(key);
        if (!(raw instanceof Number)) throw new IllegalArgumentException("Missing numeric coordinate");
        double result = ((Number) raw).doubleValue();
        if (!Double.isFinite(result) || Math.abs(result) > limit) throw new IllegalArgumentException("Coordinate outside bounds");
        return result;
    }
}
