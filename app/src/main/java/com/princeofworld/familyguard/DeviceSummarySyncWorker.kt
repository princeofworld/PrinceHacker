package com.princeofworld.familyguard

import android.Manifest
import android.app.AppOpsManager
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.content.pm.PackageManager
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
            val body = JSONObject()
                .put("location_granted", granted(Manifest.permission.ACCESS_FINE_LOCATION) || granted(Manifest.permission.ACCESS_COARSE_LOCATION))
                .put("usage_granted", hasUsageAccess())
                .put("calls_granted", granted(Manifest.permission.READ_CALL_LOG))
                .put("contacts_granted", granted(Manifest.permission.READ_CONTACTS))
                .put("apps_sync_enabled", appsOn)
                .put("calls_contacts_sync_enabled", callsOn)
                .put("battery_percent", batteryPercent() ?: JSONObject.NULL)
            if (appsOn && hasUsageAccess()) {
                body.put("installed_apps", JSONArray(launchableApps()))
                body.put("screen_time_minutes", screenTimeToday())
            } else body.put("installed_apps", JSONArray()).put("screen_time_minutes", 0)

            if (callsOn && granted(Manifest.permission.READ_CALL_LOG) && granted(Manifest.permission.READ_CONTACTS)) {
                val totals = callTotalsToday()
                body.put("today_calls_count", totals.first)
                    .put("today_calls_duration_sec", totals.second)
                    .put("contacts_count", contactCount())
            } else body.put("today_calls_count", 0).put("today_calls_duration_sec", 0).put("contacts_count", 0)

            // Re-check both independent consent switches immediately before sending.
            if (!prefs.getBoolean(KEY_APPS, false)) body.put("installed_apps", JSONArray()).put("screen_time_minutes", 0)
            if (!prefs.getBoolean(KEY_CALLS, false)) body.put("today_calls_count", 0).put("today_calls_duration_sec", 0).put("contacts_count", 0)
            if (!prefs.getBoolean(KEY_APPS, false) && !prefs.getBoolean(KEY_CALLS, false)) return Result.success()
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
    private fun launchableApps(): List<String> {
        val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        return applicationContext.packageManager.queryIntentActivities(intent, 0).mapNotNull {
            runCatching { applicationContext.packageManager.getApplicationLabel(it.activityInfo.applicationInfo)
                .toString().trim().take(100).takeIf { label -> label.isNotBlank() } }.getOrNull()
        }.distinct().sorted().take(100)
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
            PeriodicWorkRequestBuilder<DeviceSummarySyncWorker>(6, TimeUnit.HOURS).setConstraints(constraints()).build())
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
