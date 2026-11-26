package com.princeofworld.familyguard

import android.content.Context
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

object FamilyGuardApi {
    private const val BASE_URL = BuildConfig.FAMILYGUARD_API_BASE_URL

    private fun request(url: String, method: String, body: JSONObject?, token: String? = null): JSONObject {
        val connection = (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 15000
            readTimeout = 15000
            setRequestProperty("Accept", "application/json")
            if (body != null) setRequestProperty("Content-Type", "application/json")
            if (!token.isNullOrBlank()) setRequestProperty("Authorization", "Bearer $token")
            doInput = true
            if (body != null) doOutput = true
        }
        try {
            if (body != null) connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
            val status = connection.responseCode
            val stream = if (status in 200..299) connection.inputStream else connection.errorStream
            val text = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
            val result = if (text.isBlank()) JSONObject() else JSONObject(text)
            if (status !in 200..299) throw IllegalStateException(result.optString("detail", "Request failed ($status)"))
            return result
        } finally { connection.disconnect() }
    }

    fun registerThisDevice(context: Context) {
        val prefs = context.getSharedPreferences("familyguard_family", Context.MODE_PRIVATE)
        var deviceId = prefs.getString("deviceId", null)
        if (deviceId.isNullOrBlank()) {
            deviceId = java.util.UUID.randomUUID().toString()
            prefs.edit().putString("deviceId", deviceId).apply()
        }
        val name = (android.os.Build.MANUFACTURER + " " + android.os.Build.MODEL).trim().take(100)
        // Register identity first; do not read battery until the server confirms opt-in.
        post(context, "/devices/register", JSONObject()
            .put("deviceId", deviceId)
            .put("deviceName", if (name.isBlank()) "Android device" else name)
            .put("platform", "android")
            .put("batteryPercent", JSONObject.NULL))
        val consent = getDeviceConsent(context, deviceId)
        if (consent.optBoolean("battery", false)) {
            val battery = (context.getSystemService(Context.BATTERY_SERVICE) as android.os.BatteryManager)
                .getIntProperty(android.os.BatteryManager.BATTERY_PROPERTY_CAPACITY).takeIf { it in 0..100 }
            post(context, "/devices/register", JSONObject()
                .put("deviceId", deviceId)
                .put("deviceName", if (name.isBlank()) "Android device" else name)
                .put("platform", "android")
                .put("batteryPercent", battery ?: JSONObject.NULL))
        }
    }

    fun postDeviceSummary(context: Context, deviceId: String, summary: JSONObject): JSONObject =
        syncDeviceSummary(context, deviceId, summary)

    fun getDeviceConsent(context: Context, deviceId: String): JSONObject =
        get(context, "/api/privacy/consent?device_id=" + java.net.URLEncoder.encode(deviceId, "UTF-8"))

    fun updateDeviceConsent(context: Context, deviceId: String, location: Boolean? = null,
                             battery: Boolean? = null, summaries: Boolean? = null): JSONObject {
        val body = JSONObject().put("deviceId", deviceId)
        location?.let { body.put("location", it) }
        battery?.let { body.put("battery", it) }
        summaries?.let { body.put("summaries", it) }
        return post(context, "/api/privacy/consent", body)
    }

    fun sendSosAlert(context: Context, deviceId: String, location: android.location.Location): JSONObject {
        require(deviceId.isNotBlank()) { "Device registration is not ready." }
        val body = JSONObject()
            .put("message", "Emergency assistance requested by the device user.")
            .put("latitude", location.latitude)
            .put("longitude", location.longitude)
            .put("accuracyMeters", location.accuracy.toDouble())
            .put("capturedAt", java.time.Instant.ofEpochMilli(location.time).toString())
        return post(context, "/api/devices/" + java.net.URLEncoder.encode(deviceId, "UTF-8") + "/sos", body)
    }

    fun getProfile(context: Context): JSONObject = get(context, "/api/profile")
    fun updateProfile(context: Context, name: String, phone: String): JSONObject =
        requestWithAuth(context, "/api/profile", "PUT", JSONObject().put("name", name).put("phone", phone))

    /**
     * Sends only the explicitly consented summary categories. This is a second consent
     * check at the network boundary, so a worker that was queued before an OFF toggle
     * cannot upload the disabled category. The payload is rebuilt from an allowlist;
     * call numbers, contact names, IDs, and call-log rows are never forwarded.
     */
    fun syncDeviceSummary(context: Context, deviceId: String, summary: JSONObject): JSONObject {
        require(deviceId.isNotBlank()) { "Device registration is not ready." }
        val consent = context.getSharedPreferences("familyguard_summary_consent", Context.MODE_PRIVATE)
        val appsEnabled = consent.getBoolean("installed_apps_sync", false) && hasUsageAccess(context)
        val callsEnabled = consent.getBoolean("calls_contacts_sync", false) &&
            context.checkSelfPermission(android.Manifest.permission.READ_CALL_LOG) == android.content.pm.PackageManager.PERMISSION_GRANTED &&
            context.checkSelfPermission(android.Manifest.permission.READ_CONTACTS) == android.content.pm.PackageManager.PERMISSION_GRANTED

        val safe = JSONObject()
            .put("location_granted", summary.optBoolean("location_granted", false))
            .put("usage_granted", summary.optBoolean("usage_granted", hasUsageAccess(context)))
            .put("calls_granted", summary.optBoolean("calls_granted",
                context.checkSelfPermission(android.Manifest.permission.READ_CALL_LOG) == android.content.pm.PackageManager.PERMISSION_GRANTED))
            .put("contacts_granted", summary.optBoolean("contacts_granted",
                context.checkSelfPermission(android.Manifest.permission.READ_CONTACTS) == android.content.pm.PackageManager.PERMISSION_GRANTED))
            .put("apps_sync_enabled", consent.getBoolean("installed_apps_sync", false))
            .put("calls_contacts_sync_enabled", consent.getBoolean("calls_contacts_sync", false))
            .put("battery_percent", summary.opt("battery_percent") ?: JSONObject.NULL)
        if (appsEnabled) {
            safe.put("installed_apps_count", summary.optInt("installed_apps_count", 0).coerceIn(0, 10000))
            val source = summary.optJSONArray("installed_apps") ?: org.json.JSONArray()
            val labels = org.json.JSONArray()
            val seen = linkedSetOf<String>()
            for (i in 0 until source.length()) {
                val label = source.optString(i, "").trim().take(100)
                if (label.isNotBlank() && seen.add(label) && labels.length() < 100) labels.put(label)
            }
            safe.put("installed_apps", labels)
            safe.put("screen_time_minutes", summary.optInt("screen_time_minutes", 0).coerceIn(0, 1440))
        } else {
            safe.put("installed_apps_count", 0)
            safe.put("installed_apps", org.json.JSONArray())
            safe.put("screen_time_minutes", 0)
        }

        if (callsEnabled) {
            safe.put("today_calls_count", summary.optInt("today_calls_count", 0).coerceIn(0, 10000))
            safe.put("today_calls_duration_sec", summary.optInt("today_calls_duration_sec", 0).coerceIn(0, 86400))
            safe.put("contacts_count", summary.optInt("contacts_count", 0).coerceIn(0, 100000))
        } else {
            safe.put("today_calls_count", 0)
            safe.put("today_calls_duration_sec", 0)
            safe.put("contacts_count", 0)
        }

        // When both toggles are OFF, allow only an all-zero clearing payload.
        if (!appsEnabled && !callsEnabled) {
            val clearingOnly = safe.getJSONArray("installed_apps").length() == 0 &&
                safe.getInt("screen_time_minutes") == 0 &&
                safe.getInt("today_calls_count") == 0 &&
                safe.getInt("today_calls_duration_sec") == 0 &&
                safe.getInt("contacts_count") == 0
            require(clearingOnly) { "Summary sync is OFF." }
        }
        safe.put("deviceId", deviceId)
        return post(context, "/api/device/apps", safe)
    }

    private fun hasUsageAccess(context: Context): Boolean {
        val appOps = context.getSystemService(Context.APP_OPS_SERVICE) as android.app.AppOpsManager
        @Suppress("DEPRECATION")
        return appOps.checkOpNoThrow(
            android.app.AppOpsManager.OPSTR_GET_USAGE_STATS,
            android.os.Process.myUid(),
            context.packageName
        ) == android.app.AppOpsManager.MODE_ALLOWED
    }

    fun authenticate(email: String, password: String): JSONObject {
        require(email.isNotBlank()) { "Enter your email address." }
        require(password.isNotBlank()) { "Enter your password." }
        return request(BASE_URL + "/auth/login", "POST",
            JSONObject().put("email", email).put("password", password))
    }

    fun saveSession(context: Context, result: JSONObject) {
        val expires = result.optString("expiresIn", "3600").toLongOrNull() ?: 3600L
        context.getSharedPreferences("familyguard_family", Context.MODE_PRIVATE).edit()
            .putString("idToken", result.getString("idToken"))
            .putString("refreshToken", result.getString("refreshToken"))
            .putString("uid", result.getString("localId"))
            .putLong("tokenExpiry", System.currentTimeMillis() + expires * 1000L)
            .apply()
    }

    private val refreshTokenLock = Any()

    private fun rotateRefreshToken(context: Context): String {
        val prefs = context.getSharedPreferences("familyguard_family", Context.MODE_PRIVATE)
        // Snapshot before locking so a waiter can detect that another caller
        // has already rotated this exact session while it was waiting.
        val observedIdToken = prefs.getString("idToken", null)
        val observedRefreshToken = prefs.getString("refreshToken", null)

        return synchronized(refreshTokenLock) {
            val currentIdToken = prefs.getString("idToken", null)
            val currentRefreshToken = prefs.getString("refreshToken", null)
            val currentExpiry = prefs.getLong("tokenExpiry", 0L)
            val now = System.currentTimeMillis()

            // Another request may have completed rotation while this request
            // waited for the lock. Reuse its fresh access token, never rotate
            // the same one-time refresh token twice.
            val sessionChanged = currentIdToken != observedIdToken ||
                currentRefreshToken != observedRefreshToken
            if (sessionChanged && !currentIdToken.isNullOrBlank() &&
                now < currentExpiry - 30_000L) {
                return@synchronized currentIdToken
            }

            val refresh = currentRefreshToken
                ?: throw IllegalStateException("Your secure sign-in has expired. Please sign in again.")
            val result = request(BASE_URL + "/auth/refresh", "POST",
                JSONObject().put("refreshToken", refresh))
            saveSession(context, result)
            result.getString("idToken")
        }
    }

    fun refreshIdToken(context: Context): String {
        val prefs = context.getSharedPreferences("familyguard_family", Context.MODE_PRIVATE)
        val saved = prefs.getString("idToken", null)
        val expiry = prefs.getLong("tokenExpiry", 0L)
        if (!saved.isNullOrBlank() && System.currentTimeMillis() < expiry - 30_000L) return saved
        return rotateRefreshToken(context)
    }

    fun post(context: Context, path: String, body: JSONObject): JSONObject {
        var access = refreshIdToken(context)
        return try { request(BASE_URL + path, "POST", body, access) }
        catch (e: IllegalStateException) {
            if (!e.message.orEmpty().contains("expired", true) && !e.message.orEmpty().contains("invalid", true)) throw e
            access = rotateRefreshToken(context)
            request(BASE_URL + path, "POST", body, access)
        }
    }

    fun get(context: Context, path: String): JSONObject {
        var access = refreshIdToken(context)
        return try { request(BASE_URL + path, "GET", null, access) }
        catch (e: IllegalStateException) {
            if (!e.message.orEmpty().contains("expired", true) && !e.message.orEmpty().contains("invalid", true)) throw e
            access = rotateRefreshToken(context)
            request(BASE_URL + path, "GET", null, access)
        }
    }

    private fun requestWithAuth(context: Context, path: String, method: String, body: JSONObject): JSONObject {
        var access = refreshIdToken(context)
        return try { request(BASE_URL + path, method, body, access) }
        catch (e: IllegalStateException) {
            if (!e.message.orEmpty().contains("expired", true) && !e.message.orEmpty().contains("invalid", true)) throw e
            access = rotateRefreshToken(context)
            request(BASE_URL + path, method, body, access)
        }
    }

    fun deleteAccount(context: Context): JSONObject {
        var access = refreshIdToken(context)
        return try { request(BASE_URL + "/account-data", "DELETE", null, access) }
        catch (e: IllegalStateException) {
            if (!e.message.orEmpty().contains("expired", true) && !e.message.orEmpty().contains("invalid", true)) throw e
            access = rotateRefreshToken(context)
            request(BASE_URL + "/account-data", "DELETE", null, access)
        }
    }
}
