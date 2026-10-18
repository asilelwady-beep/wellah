package com.wallah.app;

import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import androidx.activity.ComponentActivity;
import androidx.camera.core.CameraSelector;
import androidx.camera.core.ImageCapture;
import androidx.camera.core.ImageCaptureException;
import androidx.camera.core.Preview;
import androidx.camera.lifecycle.ProcessCameraProvider;
import androidx.camera.view.PreviewView;
import androidx.core.content.ContextCompat;
import androidx.core.content.FileProvider;

import com.google.common.util.concurrent.ListenableFuture;

import java.io.File;
import java.util.concurrent.ExecutionException;

/** In-app camera used by the driver order-photo inputs. */
public final class CameraCaptureActivity extends ComponentActivity {
    private static final int CAMERA_PERMISSION_REQUEST = 501;
    private PreviewView previewView;
    private ImageCapture imageCapture;
    private Button captureButton;
    private ProcessCameraProvider cameraProvider;

    @Override protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setStatusBarColor(Color.BLACK);
        getWindow().setNavigationBarColor(Color.BLACK);
        buildCameraScreen();
        if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            startCamera();
        } else {
            requestPermissions(new String[]{Manifest.permission.CAMERA}, CAMERA_PERMISSION_REQUEST);
        }
    }

    private void buildCameraScreen() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(17, 24, 31));

        FrameLayout previewFrame = new FrameLayout(this);
        previewView = new PreviewView(this);
        previewView.setScaleType(PreviewView.ScaleType.FILL_CENTER);
        previewFrame.addView(previewView, new FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));

        TextView hint = new TextView(this);
        hint.setText("صوّر الشحنة كاملة بوضوح");
        hint.setTextColor(Color.WHITE);
        hint.setTextSize(16);
        hint.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        hint.setGravity(Gravity.CENTER);
        hint.setPadding(dp(16), dp(12), dp(16), dp(12));
        hint.setBackground(roundRect(0xaa11181f, dp(14)));
        FrameLayout.LayoutParams hintParams = new FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.WRAP_CONTENT, FrameLayout.LayoutParams.WRAP_CONTENT, Gravity.TOP | Gravity.CENTER_HORIZONTAL);
        hintParams.setMargins(dp(16), dp(20), dp(16), 0);
        previewFrame.addView(hint, hintParams);
        root.addView(previewFrame, new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f));

        LinearLayout controls = new LinearLayout(this);
        controls.setOrientation(LinearLayout.VERTICAL);
        controls.setGravity(Gravity.CENTER);
        controls.setPadding(dp(20), dp(15), dp(20), dp(20));
        controls.setBackgroundColor(Color.rgb(17, 24, 31));

        captureButton = new Button(this);
        captureButton.setText("التقاط صورة الشحنة");
        captureButton.setTextColor(Color.WHITE);
        captureButton.setTextSize(16);
        captureButton.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        captureButton.setAllCaps(false);
        captureButton.setEnabled(false);
        captureButton.setBackground(roundRect(Color.rgb(220, 75, 30), dp(16)));
        captureButton.setOnClickListener(view -> takePhoto());
        controls.addView(captureButton, new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, dp(56)));

        TextView cancel = new TextView(this);
        cancel.setText("إلغاء والعودة للطلب");
        cancel.setTextColor(Color.WHITE);
        cancel.setTextSize(14);
        cancel.setGravity(Gravity.CENTER);
        cancel.setPadding(0, dp(12), 0, dp(4));
        cancel.setOnClickListener(view -> finish());
        controls.addView(cancel, new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, dp(48)));

        root.addView(controls, new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));
        setContentView(root);
    }

    private void startCamera() {
        ListenableFuture<ProcessCameraProvider> future = ProcessCameraProvider.getInstance(this);
        future.addListener(() -> {
            try {
                cameraProvider = future.get();
                Preview preview = new Preview.Builder().build();
                imageCapture = new ImageCapture.Builder()
                    .setCaptureMode(ImageCapture.CAPTURE_MODE_MINIMIZE_LATENCY)
                    .build();
                preview.setSurfaceProvider(previewView.getSurfaceProvider());
                cameraProvider.unbindAll();
                cameraProvider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, preview, imageCapture);
                captureButton.setEnabled(true);
            } catch (ExecutionException | InterruptedException | RuntimeException error) {
                showCameraError();
            }
        }, ContextCompat.getMainExecutor(this));
    }

    private void takePhoto() {
        if (imageCapture == null) {
            showCameraError();
            return;
        }
        captureButton.setEnabled(false);
        File directory = new File(getCacheDir(), "order-captures");
        if ((!directory.exists() && !directory.mkdirs()) || !directory.isDirectory()) {
            captureButton.setEnabled(true);
            Toast.makeText(this, "تعذر تجهيز صورة الشحنة", Toast.LENGTH_SHORT).show();
            return;
        }
        final File photo;
        try {
            photo = File.createTempFile("wallah-order-", ".jpg", directory);
        } catch (Exception error) {
            captureButton.setEnabled(true);
            Toast.makeText(this, "تعذر تجهيز صورة الشحنة", Toast.LENGTH_SHORT).show();
            return;
        }
        ImageCapture.OutputFileOptions output = new ImageCapture.OutputFileOptions.Builder(photo).build();
        imageCapture.takePicture(output, ContextCompat.getMainExecutor(this), new ImageCapture.OnImageSavedCallback() {
            @Override public void onImageSaved(ImageCapture.OutputFileResults result) {
                try {
                    android.net.Uri uri = FileProvider.getUriForFile(
                        CameraCaptureActivity.this, getPackageName() + ".fileprovider", photo);
                    Intent data = new Intent().setData(uri)
                        .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
                    setResult(RESULT_OK, data);
                    finish();
                } catch (Exception error) {
                    photo.delete();
                    captureButton.setEnabled(true);
                    Toast.makeText(CameraCaptureActivity.this, "تعذر حفظ صورة الشحنة", Toast.LENGTH_SHORT).show();
                }
            }

            @Override public void onError(ImageCaptureException error) {
                photo.delete();
                captureButton.setEnabled(true);
                Toast.makeText(CameraCaptureActivity.this, "لم يتم التقاط الصورة، حاول مرة أخرى", Toast.LENGTH_SHORT).show();
            }
        });
    }

    private void showCameraError() {
        Toast.makeText(this, "تعذر تشغيل الكاميرا داخل التطبيق. تحقق من صلاحية الكاميرا.", Toast.LENGTH_LONG).show();
        setResult(RESULT_CANCELED);
        finish();
    }

    @Override public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode == CAMERA_PERMISSION_REQUEST) {
            if (results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) startCamera();
            else showCameraError();
        }
    }

    private GradientDrawable roundRect(int color, int radius) {
        GradientDrawable drawable = new GradientDrawable();
        drawable.setColor(color);
        drawable.setCornerRadius(radius);
        return drawable;
    }

    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
}
