package com.princeofworld.familyguard

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Build
import android.os.BatteryManager
import android.os.IBinder
import androidx.core.app.NotificationCompat
import org.json.JSONObject
import java.util.Date
import java.util.concurrent.Executors

class FamilyLocationService : Service(), LocationListener {
    private val worker = Executors.newSingleThreadExecutor()
    private var manager: LocationManager? = null

    override fun onCreate() {
        super.onCreate()
        createChannel()
        startForeground(NOTIFICATION_ID, notification())
        manager = getSystemService(Context.LOCATION_SERVICE) as LocationManager
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            stopLocationUpdates()
            stopForeground(STOP_FOREGROUND_REMOVE)
            stopSelf()
            return START_NOT_STICKY
        }
        val fine = checkSelfPermission(android.Manifest.permission.ACCESS_FINE_LOCATION) == android.content.pm.PackageManager.PERMISSION_GRANTED
        val coarse = checkSelfPermission(android.Manifest.permission.ACCESS_COARSE_LOCATION) == android.content.pm.PackageManager.PERMISSION_GRANTED
        if (!fine && !coarse) {
            stopSelf()
            return START_NOT_STICKY
        }
        try {
            val provider = if (fine) LocationManager.GPS_PROVIDER else LocationManager.NETWORK_PROVIDER
            manager?.requestLocationUpdates(provider, 60_000L, 25f, this)
            manager?.getLastKnownLocation(provider)?.let { onLocationChanged(it) }
        } catch (_: SecurityException) {
            stopSelf()
            return START_NOT_STICKY
        }
        return START_STICKY
    }

    override fun onLocationChanged(location: Location) {
        val prefs = getSharedPreferences("familyguard_family", MODE_PRIVATE)
        val familyId = prefs.getString("familyId", null) ?: return
        if (!prefs.getBoolean("locationConsent", false)) {
            stopLocationUpdates()
            stopSelf()
            return
        }
        worker.execute {
            try {
                val battery = (getSystemService(Context.BATTERY_SERVICE) as BatteryManager)
                    .getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY).takeIf { it in 0..100 }
                FamilyGuardApi.post(this, "/location/update", JSONObject()
                    .put("latitude", location.latitude).put("longitude", location.longitude)
                    .put("accuracyMeters", location.accuracy.toDouble())
                    .put("batteryPercent", battery ?: JSONObject.NULL)
                    .put("familyId", familyId)
                    .put("capturedAt", java.time.Instant.ofEpochMilli(location.time).toString()))
            } catch (_: Exception) {
                // Keep the persistent notification visible; the dashboard can show API errors on refresh.
            }
        }
    }

    private fun stopLocationUpdates() {
        try { manager?.removeUpdates(this) } catch (_: Exception) {}
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT >= 26) {
            val channel = NotificationChannel(CHANNEL_ID, "Live location sharing", NotificationManager.IMPORTANCE_LOW)
            getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
        }
    }

    private fun notification(): Notification =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setContentTitle("FamilyGuard location sharing is on")
            .setContentText("Your location is being shared with your opted-in family. Tap Stop in the app to end sharing.")
            .setOngoing(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .build()

    override fun onBind(intent: Intent?): IBinder? = null
    override fun onDestroy() { stopLocationUpdates(); worker.shutdownNow(); super.onDestroy() }

    companion object {
        const val ACTION_STOP = "com.princeofworld.familyguard.STOP_LOCATION"
        private const val CHANNEL_ID = "family_location_sharing"
        private const val NOTIFICATION_ID = 4102
    }
}
