package com.princeofworld.familyguard

import android.Manifest
import android.app.AppOpsManager
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.drawable.Drawable
import android.util.Base64
import java.io.ByteArrayOutputStream
import android.provider.CallLog
import android.provider.ContactsContract
import androidx.work.*
import org.json.JSONArray
import org.json.JSONObject
import java.util.Calendar
import java.util.concurrent.TimeUnit

class DeviceSummarySyncWorker(context: Context, params: WorkerParameters) : Worker(context, params) {
    override fun doWork(): Result {
        val prefs = applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val appsOn = prefs.getBoolean(KEY_APPS, false)
        val callsOn = prefs.getBoolean(KEY_CALLS, false)
        val deviceId = applicationContext.getSharedPreferences("familyguard_family", Context.MODE_PRIVATE)
            .getString("deviceId", null) ?: return Result.retry()
        return try {
            // Fetch authoritative server consent before reading any telemetry.
            val remoteConsent = FamilyGuardApi.getDeviceConsent(applicationContext, deviceId)
            // The server is authoritative. Apply revocation locally before touching usage/call data.
            if (!remoteConsent.optBoolean("summaries", false)) {
                prefs.edit().putBoolean(KEY_APPS, false).putBoolean(KEY_CALLS, false).apply()
                DeviceSummarySyncScheduler.cancel(applicationContext)
                return Result.success()
            }
            val appsOn = appsOn && prefs.getBoolean(KEY_APPS, false)
            val callsOn = callsOn && prefs.getBoolean(KEY_CALLS, false)
            val body = JSONObject()
                .put("location_granted", granted(Manifest.permission.ACCESS_FINE_LOCATION) || granted(Manifest.permission.ACCESS_COARSE_LOCATION))
                .put("usage_granted", hasUsageAccess())
                .put("calls_granted", granted(Manifest.permission.READ_CALL_LOG))
                .put("contacts_granted", granted(Manifest.permission.READ_CONTACTS))
                .put("apps_sync_enabled", appsOn)
                .put("calls_contacts_sync_enabled", callsOn)
                .put("battery_percent", if (remoteConsent.optBoolean("battery", false)) batteryPercent() ?: JSONObject.NULL else JSONObject.NULL)
            if (appsOn && prefs.getBoolean(KEY_APPS, false) && !isStopped && hasUsageAccess()) {
                val appSnapshot = installedApps()
                body.put("installed_apps_count", appSnapshot.first)
                    .put("installed_apps", JSONArray(appSnapshot.second))
                if (isStopped || !prefs.getBoolean(KEY_APPS, false)) return Result.success()
                body.put("screen_time_minutes", screenTimeToday())
            } else body.put("installed_apps_count", 0).put("installed_apps", JSONArray()).put("screen_time_minutes", 0)

            if (isStopped) return Result.success()
            if (callsOn && prefs.getBoolean(KEY_CALLS, false) && !isStopped &&
                granted(Manifest.permission.READ_CALL_LOG) && granted(Manifest.permission.READ_CONTACTS)) {
                val totals = callTotalsToday()
                if (isStopped || !prefs.getBoolean(KEY_CALLS, false)) return Result.success()
                body.put("today_calls_count", totals.first)
                    .put("today_calls_duration_sec", totals.second)
                    .put("contacts_count", contactCount())
            } else body.put("today_calls_count", 0).put("today_calls_duration_sec", 0).put("contacts_count", 0)

            if (isStopped) return Result.success()
            // Re-check both independent consent switches immediately before sending.
            if (!prefs.getBoolean(KEY_APPS, false) || !remoteConsent.optBoolean("summaries", false)) body.put("installed_apps", JSONArray()).put("screen_time_minutes", 0)
            if (!prefs.getBoolean(KEY_CALLS, false) || !remoteConsent.optBoolean("summaries", false)) body.put("today_calls_count", 0).put("today_calls_duration_sec", 0).put("contacts_count", 0)
            FamilyGuardApi.postDeviceSummary(applicationContext, deviceId, body)
            Result.success()
        } catch (_: SecurityException) { Result.success() }
        catch (_: Exception) { Result.retry() }
    }

    private fun granted(p: String) = applicationContext.checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED
    private fun batteryPercent(): Int? {
        val status = applicationContext.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED)) ?: return null
        val level = status.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
        val scale = status.getIntExtra(BatteryManager.EXTRA_SCALE, -1)
        if (level < 0 || scale <= 0) return null
        return (level * 100f / scale).toInt().coerceIn(0, 100)
    }
    private fun hasUsageAccess(): Boolean {
        val ops = applicationContext.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        @Suppress("DEPRECATION")
        return ops.checkOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS, android.os.Process.myUid(),
            applicationContext.packageName) == AppOpsManager.MODE_ALLOWED
    }
    @Suppress("DEPRECATION")
    private fun installedApps(): Pair<Int, List<JSONObject>> {
        val pm = applicationContext.packageManager
        val installed = (if (android.os.Build.VERSION.SDK_INT >= 33) {
            pm.getInstalledApplications(PackageManager.ApplicationInfoFlags.of(0))
        } else {
            pm.getInstalledApplications(0)
        }).filter { it.packageName != applicationContext.packageName }
        val entries = installed.mapNotNull { info ->
            runCatching {
                val label = pm.getApplicationLabel(info).toString().trim().take(100)
                if (label.isBlank()) null else JSONObject()
                    .put("name", label)
                    .put("package", info.packageName.take(180))
                    .put("icon", iconDataUrl(pm.getApplicationIcon(info)))
            }.getOrNull()
        }.distinctBy { it.optString("package") }.sortedBy { it.optString("name").lowercase() }.take(100)
        return installed.size.coerceAtMost(10000) to entries
    }
    private fun iconDataUrl(drawable: Drawable): String {
        val bitmap = Bitmap.createBitmap(48, 48, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        drawable.setBounds(0, 0, canvas.width, canvas.height)
        drawable.draw(canvas)
        val output = ByteArrayOutputStream()
        if (!bitmap.compress(Bitmap.CompressFormat.WEBP, 55, output)) return ""
        val bytes = output.toByteArray()
        if (bytes.size > 6000) return ""
        return "data:image/webp;base64," + Base64.encodeToString(bytes, Base64.NO_WRAP)

    }
    private fun dayStart() = Calendar.getInstance().apply {
        set(Calendar.HOUR_OF_DAY, 0); set(Calendar.MINUTE, 0)
        set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0)
    }.timeInMillis
    private fun screenTimeToday(): Int {
        val stats = (applicationContext.getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager)
            .queryUsageStats(UsageStatsManager.INTERVAL_DAILY, dayStart(), System.currentTimeMillis()).orEmpty()
        return (stats.sumOf { it.totalTimeInForeground.coerceAtLeast(0L) } / 60000L).coerceIn(0L, 1440L).toInt()
    }
    @Suppress("MissingPermission")
    private fun callTotalsToday(): Pair<Int, Int> {
        var count = 0
        var seconds = 0L
        applicationContext.contentResolver.query(CallLog.Calls.CONTENT_URI,
            arrayOf(CallLog.Calls.DATE, CallLog.Calls.DURATION), "${CallLog.Calls.DATE} >= ?",
            arrayOf(dayStart().toString()), null)?.use { cursor ->
            val duration = cursor.getColumnIndexOrThrow(CallLog.Calls.DURATION)
            while (cursor.moveToNext()) { count++; seconds += cursor.getLong(duration).coerceAtLeast(0L) }
        }
        return count.coerceAtMost(10000) to seconds.coerceAtMost(86400L).toInt()
    }
    @Suppress("MissingPermission")
    private fun contactCount(): Int {
        applicationContext.contentResolver.query(ContactsContract.Contacts.CONTENT_URI,
            arrayOf(ContactsContract.Contacts._ID), null, null, null)?.use { return it.count.coerceIn(0, 100000) }
        return 0
    }
    companion object {
        const val PREFS = "familyguard_summary_consent"
        const val KEY_APPS = "installed_apps_sync"
        const val KEY_CALLS = "calls_contacts_sync"
        const val UNIQUE_PERIODIC = "familyguard_summary_periodic"
        const val UNIQUE_NOW = "familyguard_summary_now"
    }
}

object DeviceSummarySyncScheduler {
    private fun constraints() = Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build()
    fun schedule(context: Context) {
        val p = context.getSharedPreferences(DeviceSummarySyncWorker.PREFS, Context.MODE_PRIVATE)
        if (!p.getBoolean(DeviceSummarySyncWorker.KEY_APPS, false) &&
            !p.getBoolean(DeviceSummarySyncWorker.KEY_CALLS, false)) { cancel(context); return }
        val wm = WorkManager.getInstance(context)
        wm.enqueueUniquePeriodicWork(DeviceSummarySyncWorker.UNIQUE_PERIODIC, ExistingPeriodicWorkPolicy.UPDATE,
            PeriodicWorkRequestBuilder<DeviceSummarySyncWorker>(15, TimeUnit.MINUTES).setConstraints(constraints()).build())
        syncNow(context)
    }
    fun syncNow(context: Context) {
        WorkManager.getInstance(context).enqueueUniqueWork(DeviceSummarySyncWorker.UNIQUE_NOW, ExistingWorkPolicy.REPLACE,
            OneTimeWorkRequestBuilder<DeviceSummarySyncWorker>().setConstraints(constraints()).build())
    }
    fun cancel(context: Context) {
        WorkManager.getInstance(context).cancelUniqueWork(DeviceSummarySyncWorker.UNIQUE_PERIODIC)
        WorkManager.getInstance(context).cancelUniqueWork(DeviceSummarySyncWorker.UNIQUE_NOW)
    }
}
