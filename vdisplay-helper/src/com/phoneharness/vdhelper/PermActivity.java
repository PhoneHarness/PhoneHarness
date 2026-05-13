package com.phoneharness.vdhelper;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.util.Log;

/**
 * Simple launcher activity — just starts VDService and finishes.
 * No MediaProjection permission needed anymore.
 */
public class PermActivity extends Activity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Log.i("VDHelper", "Starting VDService");
        startForegroundService(new Intent(this, VDService.class));
        finish();
    }
}
