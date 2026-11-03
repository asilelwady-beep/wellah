package com.wallah.app;

import org.json.JSONObject;
import org.junit.Test;
import static org.junit.Assert.*;

public class NativeMapRequestTest {
    private JSONObject request() throws Exception {
        return new JSONObject().put("requestId", "map-123-1").put("mapId", "customer-map")
            .put("lat", 29.8513).put("lon", 31.2744).put("zoom", 16);
    }

    @Test public void acceptsEgyptCoordinatesAndSelectionMap() throws Exception {
        NativeMapRequest value = NativeMapRequest.parse(request().toString());
        assertTrue(value.picker);
        assertEquals(29.8513, value.lat, 0.000001);
        assertEquals(31.2744, value.lon, 0.000001);
    }

    @Test public void rejectsNullStringsAndOutOfRangeCoordinates() throws Exception {
        for (Object invalid : new Object[]{JSONObject.NULL, "29.85", 91, -91}) {
            JSONObject value = request().put("lat", invalid);
            assertThrows(IllegalArgumentException.class, () -> NativeMapRequest.parse(value.toString()));
        }
        String outOfBounds = request().put("lon", 181).toString();
        assertThrows(IllegalArgumentException.class, () -> NativeMapRequest.parse(outOfBounds));
        JSONObject value = request();value.remove("lon");
        final String missing = value.toString();
        assertThrows(IllegalArgumentException.class, () -> NativeMapRequest.parse(missing));
    }

    @Test public void rejectsUnknownMapAndCallbackInjection() throws Exception {
        String unknown = request().put("mapId", "unknown").toString();
        assertThrows(IllegalArgumentException.class, () -> NativeMapRequest.parse(unknown));
        String injection = request().put("requestId", "');alert(1)//").toString();
        assertThrows(IllegalArgumentException.class, () -> NativeMapRequest.parse(injection));
    }

    @Test public void trackingCannotBecomePickerFromJson() throws Exception {
        NativeMapRequest value = NativeMapRequest.parse(request().put("mapId", "driver-map").put("picker", true).toString());
        assertFalse(value.picker);
    }

    @Test public void clampsZoomAndBoundsPayloadSize() throws Exception {
        assertEquals(21f, NativeMapRequest.parse(request().put("zoom", 99).toString()).zoom, 0f);
        assertEquals(3f, NativeMapRequest.parse(request().put("zoom", -2).toString()).zoom, 0f);
        assertThrows(IllegalArgumentException.class, () -> NativeMapRequest.parse(" ".repeat(300001)));
    }

    @Test public void rejectedOpenCanReturnToMatchingWebMap() throws Exception {
        JSONObject response = NativeMapRequest.rejectedResult(request().put("lat",91).toString());
        assertNotNull(response);
        assertEquals("map-123-1",response.getString("requestId"));
        assertEquals("unavailable",response.getString("status"));
        assertNull(NativeMapRequest.rejectedResult("not JSON"));
        assertNull(NativeMapRequest.rejectedResult(request().put("requestId","');alert(1)//").toString()));
    }
}
