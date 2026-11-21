package com.princeofworld.familyguard

import android.content.Context
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

object FamilyGuardApi {
    private const val BASE_URL = "https://princehacker.onrender.com"

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

    fun authenticate(email: String, password: String, create: Boolean): JSONObject {
        require(email.isNotBlank()) { "Enter your email address." }
        require(password.length >= 10) { "Use a password with at least 10 characters." }
        val path = if (create) "/auth/register" else "/auth/login"
        return request(BASE_URL + path, "POST",
            JSONObject().put("email", email).put("password", password))
    }

    fun refreshIdToken(context: Context): String {
        val prefs = context.getSharedPreferences("familyguard_family", Context.MODE_PRIVATE)
        val saved = prefs.getString("idToken", null)
        val expiry = prefs.getLong("tokenExpiry", 0L)
        if (!saved.isNullOrBlank() && System.currentTimeMillis() < expiry - 30_000L) return saved
        throw IllegalStateException("Your secure sign-in has expired. Please sign in again.")
    }

    fun post(context: Context, path: String, body: JSONObject): JSONObject =
        request(BASE_URL + path, "POST", body, refreshIdToken(context))

    fun get(context: Context, path: String): JSONObject =
        request(BASE_URL + path, "GET", null, refreshIdToken(context))
}
