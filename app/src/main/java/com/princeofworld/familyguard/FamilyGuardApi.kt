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
        val battery = (context.getSystemService(Context.BATTERY_SERVICE) as android.os.BatteryManager)
            .getIntProperty(android.os.BatteryManager.BATTERY_PROPERTY_CAPACITY).takeIf { it in 0..100 }
        post(context, "/devices/register", JSONObject()
            .put("deviceId", deviceId)
            .put("deviceName", if (name.isBlank()) "Android device" else name)
            .put("platform", "android")
            .put("batteryPercent", battery ?: JSONObject.NULL))
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

    private fun rotateRefreshToken(context: Context): String {
        val prefs = context.getSharedPreferences("familyguard_family", Context.MODE_PRIVATE)
        val refresh = prefs.getString("refreshToken", null)
            ?: throw IllegalStateException("Your secure sign-in has expired. Please sign in again.")
        val result = request(BASE_URL + "/auth/refresh", "POST", JSONObject().put("refreshToken", refresh))
        saveSession(context, result)
        return result.getString("idToken")
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
}
