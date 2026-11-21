package com.princeofworld.familyguard

import android.content.Context
import android.util.Base64
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

object FamilyGuardApi {
    private fun request(url: String, method: String, body: JSONObject?, token: String? = null): JSONObject {
        val connection = (URL(url).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 12000
            readTimeout = 12000
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

    fun authenticate(email: String, password: String, create: Boolean): JSONObject {
        val key = BuildConfig.FIREBASE_WEB_API_KEY
        require(key.isNotBlank()) { "Firebase is not configured. Add FIREBASE_WEB_API_KEY to GitHub Secrets and rebuild." }
        val action = if (create) "signUp" else "signInWithPassword"
        return request("https://identitytoolkit.googleapis.com/v1/accounts:$action?key=$key", "POST",
            JSONObject().put("email", email).put("password", password).put("returnSecureToken", true))
    }

    fun refreshIdToken(context: Context): String {
        val prefs = context.getSharedPreferences("familyguard_family", Context.MODE_PRIVATE)
        val saved = prefs.getString("idToken", null)
        val expiry = prefs.getLong("tokenExpiry", 0L)
        if (!saved.isNullOrBlank() && System.currentTimeMillis() < expiry - 60_000L) return saved
        val refresh = prefs.getString("refreshToken", null) ?: error("Sign in to FamilyGuard first")
        val key = BuildConfig.FIREBASE_WEB_API_KEY
        require(key.isNotBlank()) { "Firebase API key is not configured" }
        val form = "grant_type=refresh_token&refresh_token=" + java.net.URLEncoder.encode(refresh, "UTF-8")
        val c = (URL("https://securetoken.googleapis.com/v1/token?key=$key").openConnection() as HttpURLConnection).apply {
            requestMethod = "POST"; doOutput = true; connectTimeout = 12000; readTimeout = 12000
            setRequestProperty("Content-Type", "application/x-www-form-urlencoded")
        }
        val result = try {
            c.outputStream.use { it.write(form.toByteArray()) }
            val status = c.responseCode
            val stream = if (status in 200..299) c.inputStream else c.errorStream
            JSONObject(stream?.bufferedReader()?.use { it.readText() }.orEmpty())
        } finally { c.disconnect() }
        val token = result.getString("id_token")
        val newRefresh = result.optString("refresh_token", refresh)
        val expires = result.optString("expires_in", "3600").toLongOrNull() ?: 3600L
        prefs.edit().putString("idToken", token).putString("refreshToken", newRefresh)
            .putLong("tokenExpiry", System.currentTimeMillis() + expires * 1000L).apply()
        return token
    }

    fun post(context: Context, path: String, body: JSONObject): JSONObject {
        val base = BuildConfig.FAMILYGUARD_API_BASE_URL.trimEnd('/')
        require(base.startsWith("https://")) { "Set FAMILYGUARD_API_BASE_URL to your HTTPS Render service URL and rebuild." }
        return request("$base$path", "POST", body, refreshIdToken(context))
    }

    fun get(context: Context, path: String): JSONObject {
        val base = BuildConfig.FAMILYGUARD_API_BASE_URL.trimEnd('/')
        require(base.startsWith("https://")) { "Set FAMILYGUARD_API_BASE_URL to your HTTPS Render service URL and rebuild." }
        return request("$base$path", "GET", null, refreshIdToken(context))
    }
}
