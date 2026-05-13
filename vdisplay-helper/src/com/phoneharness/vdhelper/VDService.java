package com.phoneharness.vdhelper;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.PixelFormat;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.os.IBinder;
import android.util.Log;

import java.io.File;
import java.io.FileOutputStream;
import java.nio.ByteBuffer;

/**
 * Foreground service that creates a VirtualDisplay via DisplayManager (NOT MediaProjection).
 * This creates an independent display for apps — not a screen mirror.
 *
 * - Apps launched with `am start --display <id>` render here.
 * - ImageReader surface captures the frames.
 * - Screenshot triggered by broadcast: com.phoneharness.vdhelper.SCREENSHOT
 */
public class VDService extends Service {
    private static final String TAG = "VDHelper";
    private static final String CHANNEL_ID = "vdhelper_channel";
    private static final int WIDTH = 1080;
    private static final int HEIGHT = 1920;
    private static final int DPI = 320;

    private VirtualDisplay virtualDisplay;
    private ImageReader imageReader;

    static VDService instance;
    static int displayId = -1;

    @Override
    public void onCreate() {
        super.onCreate();
        instance = this;
        createNotificationChannel();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        // Start as foreground service
        Notification notification = new Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("VDHelper")
            .setContentText("Background display active")
            .setSmallIcon(android.R.drawable.ic_menu_camera)
            .build();
        startForeground(1, notification);

        if (virtualDisplay != null) {
            Log.i(TAG, "VirtualDisplay already exists: displayId=" + displayId);
            return START_NOT_STICKY;
        }

        // Create ImageReader as the rendering surface
        imageReader = ImageReader.newInstance(WIDTH, HEIGHT, PixelFormat.RGBA_8888, 2);
        imageReader.setOnImageAvailableListener(reader -> {
            // Just let images accumulate; we grab them on screenshot request
        }, null);

        // Create VirtualDisplay via DisplayManager — this is an independent app display
        DisplayManager dm = (DisplayManager) getSystemService(DISPLAY_SERVICE);
        virtualDisplay = dm.createVirtualDisplay(
            "agent-background",
            WIDTH, HEIGHT, DPI,
            imageReader.getSurface(),
            DisplayManager.VIRTUAL_DISPLAY_FLAG_OWN_CONTENT_ONLY
                | DisplayManager.VIRTUAL_DISPLAY_FLAG_PUBLIC
        );

        if (virtualDisplay == null) {
            Log.e(TAG, "Failed to create VirtualDisplay");
            stopSelf();
            return START_NOT_STICKY;
        }

        displayId = virtualDisplay.getDisplay().getDisplayId();
        Log.i(TAG, "VirtualDisplay created: displayId=" + displayId);

        // Write displayId to file
        try {
            File f = new File("/sdcard/vdhelper_display_id.txt");
            FileOutputStream fos = new FileOutputStream(f);
            fos.write(String.valueOf(displayId).getBytes());
            fos.close();
            Log.i(TAG, "Display ID written to /sdcard/vdhelper_display_id.txt");
        } catch (Exception e) {
            Log.e(TAG, "Failed to write display ID", e);
        }

        return START_NOT_STICKY;
    }

    /** Capture current frame and save to /sdcard/vdhelper_screenshot.png */
    void takeScreenshot() {
        if (imageReader == null) {
            Log.e(TAG, "ImageReader not initialized");
            return;
        }
        Image image = imageReader.acquireLatestImage();
        if (image == null) {
            Log.w(TAG, "No image available");
            return;
        }
        try {
            Image.Plane[] planes = image.getPlanes();
            ByteBuffer buffer = planes[0].getBuffer();
            int pixelStride = planes[0].getPixelStride();
            int rowStride = planes[0].getRowStride();
            int rowPadding = rowStride - pixelStride * WIDTH;

            Bitmap bitmap = Bitmap.createBitmap(
                WIDTH + rowPadding / pixelStride, HEIGHT,
                Bitmap.Config.ARGB_8888);
            bitmap.copyPixelsFromBuffer(buffer);

            // Crop padding
            if (bitmap.getWidth() != WIDTH) {
                Bitmap cropped = Bitmap.createBitmap(bitmap, 0, 0, WIDTH, HEIGHT);
                bitmap.recycle();
                bitmap = cropped;
            }

            File out = new File("/sdcard/vdhelper_screenshot.png");
            FileOutputStream fos = new FileOutputStream(out);
            bitmap.compress(Bitmap.CompressFormat.PNG, 90, fos);
            fos.close();
            bitmap.recycle();
            Log.i(TAG, "Screenshot saved: " + out.getAbsolutePath());
        } catch (Exception e) {
            Log.e(TAG, "Screenshot failed", e);
        } finally {
            image.close();
        }
    }

    @Override
    public void onDestroy() {
        if (virtualDisplay != null) virtualDisplay.release();
        if (imageReader != null) imageReader.close();
        instance = null;
        displayId = -1;
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) { return null; }

    private void createNotificationChannel() {
        NotificationChannel ch = new NotificationChannel(
            CHANNEL_ID, "VDHelper", NotificationManager.IMPORTANCE_LOW);
        getSystemService(NotificationManager.class).createNotificationChannel(ch);
    }
}
