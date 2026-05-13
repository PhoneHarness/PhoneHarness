package com.phoneharness.vdhelper;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

/**
 * Trigger screenshot via:
 *   adb shell am broadcast -a com.phoneharness.vdhelper.SCREENSHOT
 */
public class ScreenshotReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        Log.i("VDHelper", "Screenshot broadcast received");
        if (VDService.instance != null) {
            VDService.instance.takeScreenshot();
        } else {
            Log.e("VDHelper", "VDService not running");
        }
    }
}
