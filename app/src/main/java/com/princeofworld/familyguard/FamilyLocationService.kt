package com.princeofworld.familyguard

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.BatteryManager
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import androidx.core.app.NotificationCompat
import org.json.JSONObject
import java.util.concurrent.Executors

class FamilyLocationService : Service(), LocationListener {
    private val worker = Executors.newSingleThreadExecutor()
    private val handler = Handler(Looper.getMainLooper())
    private var manager: LocationManager? = null
    private var sessionActive = false

    private val autoStop = Runnable { endSession(autoStopped = true) }

    override fun onCreate() {
        super.onCreate()
        createChannel()
        manager = getSystemService(Context.LOCATION_SERVICE) as LocationManager
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            endSession(autoStopped = false)
            return START_NOT_STICKY
        }

        val fine = checkSelfPermission(android.Manifest.permission.ACCESS_FINE_LOCATION) == android.content.pm.PackageManager.PERMISSION_GRANTED
        val coarse = checkSelfPermission(android.Manifest.permission.ACCESS_COARSE_LOCATION) == android.content.pm.PackageManager.PERMISSION_GRANTED
        if (!fine && !coarse) {
            stopSelf()
            return START_NOT_STICKY
        }
        val prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        if (prefs.getString("familyId", null).isNullOrBlank() || !prefs.getBoolean("locationConsent", false)) {
            stopSelf()
            return START_NOT_STICKY
        }

        try {
            startForeground(NOTIFICATION_ID, sharingNotification())
            sessionActive = true
            handler.removeCallbacks(autoStop)
            handler.postDelayed(autoStop, SESSION_DURATION_MS)
            val provider = if (fine) LocationManager.GPS_PROVIDER else LocationManager.NETWORK_PROVIDER
            manager?.requestLocationUpdates(provider, 30_000L, 0f, this)
            manager?.getLastKnownLocation(provider)?.let { onLocationChanged(it) }
        } catch (_: SecurityException) {
            endSession(autoStopped = false)
            return START_NOT_STICKY
        } catch (_: Exception) {
            endSession(autoStopped = false)
            return START_NOT_STICKY
        }
        return START_NOT_STICKY
    }

    override fun onLocationChanged(location: Location) {
        if (!sessionActive) return
        val prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        val familyId = prefs.getString("familyId", null) ?: return
        if (!prefs.getBoolean("locationConsent", false)) {
            endSession(autoStopped = false)
            return
        }
        worker.execute {
            try {
                val battery = (getSystemService(Context.BATTERY_SERVICE) as BatteryManager)
                    .getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY).takeIf { it in 0..100 }
                FamilyGuardApi.post(this, "/devices/location", JSONObject()
                    .put("latitude", location.latitude).put("longitude", location.longitude)
                    .put("accuracyMeters", location.accuracy.toDouble())
                    .put("batteryPercent", battery ?: JSONObject.NULL)
                    .put("familyId", familyId)
                    .put("deviceId", prefs.getString("deviceId", null))
                    .put("capturedAt", java.time.Instant.ofEpochMilli(location.time).toString()))
            } catch (_: Exception) {
                // Sharing remains visibly active until stopped or the fixed session expires.
            }
        }
    }

    private fun endSession(autoStopped: Boolean) {
        if (!sessionActive) {
            if (autoStopped) stopSelf()
            return
        }
        sessionActive = false
        handler.removeCallbacks(autoStop)
        stopLocationUpdates()
        val prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        val familyId = prefs.getString("familyId", null)
        prefs.edit()
            .putBoolean("locationConsent", false)
            .putString("locationSessionStatus", if (autoStopped) "expired" else "stopped")
            .putLong("locationSessionEndedAt", System.currentTimeMillis())
            .apply()

        if (!familyId.isNullOrBlank()) {
            worker.execute {
                try {
                    FamilyGuardApi.post(this, "/family/consent",
                        JSONObject().put("familyId", familyId).put("shareLocation", false))
                } catch (_: Exception) {
                    // Local consent is revoked immediately; server-side stale points expire from API responses.
                }
            }
        }

        if (autoStopped) {
            getSystemService(NotificationManager::class.java)
                .notify(STATUS_NOTIFICATION_ID, statusNotification("10-minute location session ended. Press Start again to share."))
        }
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
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

    private fun appPendingIntent(): PendingIntent {
        val intent = Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        return PendingIntent.getActivity(this, 0, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
    }

    private fun sharingNotification(): Notification =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setContentTitle("FamilyGuard location sharing is on")
            .setContentText("Sharing with your opted-in family. This session stops automatically after 10 minutes.")
            .setContentIntent(appPendingIntent())
            .setOngoing(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .build()

    private fun statusNotification(message: String): Notification =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setContentTitle("FamilyGuard sharing stopped")
            .setContentText(message)
            .setContentIntent(appPendingIntent())
            .setAutoCancel(true)
            .setOngoing(false)
            .build()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        sessionActive = false
        handler.removeCallbacks(autoStop)
        stopLocationUpdates()
        worker.shutdown()
        super.onDestroy()
    }

    companion object {
        const val ACTION_STOP = "com.princeofworld.familyguard.STOP_LOCATION"
        const val SESSION_DURATION_MS = 10 * 60 * 1000L
        private const val PREFS = "familyguard_family"
        private const val CHANNEL_ID = "family_location_sharing"
        private const val NOTIFICATION_ID = 4102
        private const val STATUS_NOTIFICATION_ID = 4103
    }
}
