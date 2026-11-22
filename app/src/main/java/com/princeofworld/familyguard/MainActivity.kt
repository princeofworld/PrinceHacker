package com.princeofworld.familyguard

import android.Manifest
import android.app.AppOpsManager
import android.app.usage.UsageStatsManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import java.text.DateFormat
import java.util.Date
import java.net.URL
import java.util.concurrent.Executors
import org.json.JSONObject

class MainActivity : ComponentActivity() {
    private var signedIn by mutableStateOf(false)
    private var loginMessage by mutableStateOf("")
    private var locationText by mutableStateOf("Location permission has not been requested.")
    private var consentAccepted by mutableStateOf(false)
    private var usageText by mutableStateOf("Usage report is available to Premium subscribers after enabling Usage Access.")
    private var premium by mutableStateOf(false)
    private var billingReady by mutableStateOf(false)
    private var billingMessage by mutableStateOf("Sign in to check your FamilyGuard plan.")
    private var premiumPriceLabel by mutableStateOf("₹999 / month")
    private val verificationExecutor = Executors.newSingleThreadExecutor()
    private var notice by mutableStateOf<String?>(null)
    private var pendingDisclosure by mutableStateOf<String?>(null)
    private var permissionStatusText by mutableStateOf("Checking permissions…")
    private var showDeleteDataDialog by mutableStateOf(false)
    private var familyStatus by mutableStateOf("Sign in to FamilyGuard to create or join a family.")
    private var familyId by mutableStateOf("")
    private var inviteCode by mutableStateOf("")
    private var familyLocationsText by mutableStateOf("No family locations loaded.")
    private var familyConsentEnabled by mutableStateOf(false)

    private val locationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { grants ->
        if (grants[Manifest.permission.ACCESS_FINE_LOCATION] == true ||
            grants[Manifest.permission.ACCESS_COARSE_LOCATION] == true
        ) readCurrentLocation()
        else locationText = "Location permission was not granted. Location remains off."
        pendingDisclosure = if (Build.VERSION.SDK_INT >= 33) "notifications" else if (premium) "usage" else null
        updatePermissionStatus()
    }

    private val notificationsPermissionLauncher = registerForActivityResult(ActivityResultContracts.RequestPermission()) {
        updatePermissionStatus()
        pendingDisclosure = if (premium) "usage" else null
    }

    private val usageSettingsLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) {
        refreshUsageReport()
        updatePermissionStatus()
        pendingDisclosure = null
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        consentAccepted = getSharedPreferences("familyguard_privacy", Context.MODE_PRIVATE)
            .getBoolean("parental_disclosure_accepted_v1", false)
        familyId = getSharedPreferences("familyguard_family", Context.MODE_PRIVATE).getString("familyId", "") ?: ""
        familyConsentEnabled = getSharedPreferences("familyguard_family", Context.MODE_PRIVATE).getBoolean("locationConsent", false)
        signedIn = !getSharedPreferences("familyguard_family", Context.MODE_PRIVATE).getString("idToken", null).isNullOrBlank()
        if (signedIn) refreshSubscriptionStatus()
        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                if (!signedIn) {
                    LoginScreen(
                        message = loginMessage,
                        onLogin = { email, password -> authenticateFamily(email, password) },
                        onOpenWebsite = { openWebPage("https://princehacker.onrender.com/") }
                    )
                } else if (!consentAccepted) {
                    ParentalConsentDisclosureScreen(
                        onAccept = {
                            getSharedPreferences("familyguard_privacy", Context.MODE_PRIVATE)
                                .edit().putBoolean("parental_disclosure_accepted_v1", true).apply()
                            consentAccepted = true
                            refreshUsageReport()
                        },
                        onDecline = { finish() }
                    )
                } else FamilyGuardDashboard(
                    premium = premium,
                    billingMessage = billingMessage,
                    usageText = usageText,
                    permissionStatusText = permissionStatusText,
                    onSetupPermissions = { beginPermissionSetup() },
                    onUsage = { openUsageAccessSettings() },
                    onSos = { sendSosAlert() },
                    onOpenWebsite = { openWebPage("https://princehacker.onrender.com/") },
                    onLogout = { logout() },
                    onPrivacy = { openWebPage("https://raw.githubusercontent.com/princeofworld/PrinceHacker/main/PRIVACY_POLICY.md") },
                    onTerms = { openWebPage("https://raw.githubusercontent.com/princeofworld/PrinceHacker/main/TERMS_OF_SERVICE.md") }
                )
                pendingDisclosure?.let { step ->
                    val disclosure = when (step) {
                        "location" -> "FamilyGuard requests foreground location. Location is sent to your opted-in family only after you separately enable consent and press Start live sharing. A persistent Android notification stays visible while sharing is active; Stop sharing revokes consent."
                        "notifications" -> "FamilyGuard requests permission for visible safety and account notices. You can deny this and continue using features that do not require notifications."
                        else -> "This optional Premium report reads app usage on this device. Android will open Usage Access settings so you can explicitly enable or decline it."
                    }
                    AlertDialog(
                        onDismissRequest = { pendingDisclosure = null },
                        title = { Text("Before enabling $step") },
                        text = { Text(disclosure) },
                        confirmButton = {
                            TextButton(onClick = {
                                pendingDisclosure = null
                                when (step) {
                                    "location" -> locationPermissionLauncher.launch(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION))
                                    "notifications" -> if (Build.VERSION.SDK_INT >= 33) notificationsPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS) else if (premium) pendingDisclosure = "usage"
                                    "usage" -> if (premium) usageSettingsLauncher.launch(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
                                }
                            }) { Text("Agree and continue") }
                        },
                        dismissButton = { TextButton(onClick = { pendingDisclosure = null }) { Text("Not now") } }
                    )
                }
                if (showDeleteDataDialog) {
                    AlertDialog(
                        onDismissRequest = { showDeleteDataDialog = false },
                        title = { Text("Delete FamilyGuard data?") },
                        text = { Text("FamilyGuard account and family data are stored on the service. This local action clears app settings; use the account-data endpoint to request server data deletion. Recurring payment cancellation is managed through the payment provider.") },
                        confirmButton = {
                            TextButton(onClick = {
                                getSharedPreferences("familyguard_privacy", Context.MODE_PRIVATE).edit().clear().apply()
                                consentAccepted = false
                                premium = false
                                showDeleteDataDialog = false
                                recreate()
                            }) { Text("Delete local data") }
                        },
                        dismissButton = { TextButton(onClick = { showDeleteDataDialog = false }) { Text("Cancel") } }
                    )
                }
                notice?.let { message ->
                    AlertDialog(
                        onDismissRequest = { notice = null },
                        title = { Text("FamilyGuard") },
                        text = { Text(message) },
                        confirmButton = { TextButton(onClick = { notice = null }) { Text("OK") } }
                    )
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        if (signedIn && consentAccepted) refreshUsageReport()
        if (signedIn) refreshSubscriptionStatus()
        updatePermissionStatus()
    }

    override fun onDestroy() {
        verificationExecutor.shutdownNow()
        super.onDestroy()
    }

    private fun runFamilyTask(action: () -> String) {
        verificationExecutor.execute {
            try {
                val message = action()
                runOnUiThread { familyStatus = message }
            } catch (e: Exception) {
                runOnUiThread { familyStatus = e.message ?: "Family service request failed." }
            }
        }
    }

    private fun authenticateFamily(email: String, password: String) {
        loginMessage = "Signing in…"
        verificationExecutor.execute {
            try {
                val result = FamilyGuardApi.authenticate(email.trim(), password)
                FamilyGuardApi.saveSession(this, result)
                runOnUiThread {
                    signedIn = true
                    loginMessage = "Signed in successfully."
                    refreshSubscriptionStatus()
                }
            } catch (e: Exception) {
                runOnUiThread { loginMessage = e.message ?: "Sign-in failed. Check your details and try again." }
            }
        }
    }

    private fun logout() {
        stopService(Intent(this, FamilyLocationService::class.java))
        getSharedPreferences("familyguard_family", Context.MODE_PRIVATE).edit()
            .remove("idToken").remove("refreshToken").remove("uid").remove("tokenExpiry").apply()
        signedIn = false
        premium = false
        billingReady = false
        loginMessage = "You have been signed out."
    }

    private fun createFamily(name: String) {
        runFamilyTask {
            val result = FamilyGuardApi.post(this, "/family/create", JSONObject().put("displayName", name))
            familyId = result.getString("familyId")
            inviteCode = result.getString("inviteCode")
            familyConsentEnabled = false
            getSharedPreferences("familyguard_family", MODE_PRIVATE).edit().putString("familyId", familyId).putBoolean("locationConsent", false).apply()
            "Family created. Invite code " + inviteCode + " (valid for 15 minutes)."
        }
    }

    private fun joinFamily(code: String) {
        runFamilyTask {
            val result = FamilyGuardApi.post(this, "/family/join", JSONObject().put("inviteCode", code))
            familyId = result.getString("familyId")
            familyConsentEnabled = false
            getSharedPreferences("familyguard_family", MODE_PRIVATE).edit().putString("familyId", familyId).putBoolean("locationConsent", false).apply()
            "Joined family " + result.optString("familyName", "")
        }
    }

    private fun setFamilyConsent(enabled: Boolean) {
        if (!enabled) stopService(Intent(this, FamilyLocationService::class.java))
        runFamilyTask {
            val id = familyId.ifBlank { getSharedPreferences("familyguard_family", MODE_PRIVATE).getString("familyId", "") ?: "" }
            require(id.isNotBlank()) { "Create or join a family first." }
            FamilyGuardApi.post(this, "/family/consent", JSONObject().put("familyId", id).put("shareLocation", enabled))
            familyConsentEnabled = enabled
            getSharedPreferences("familyguard_family", MODE_PRIVATE).edit().putBoolean("locationConsent", enabled).apply()
            if (!enabled) stopService(Intent(this, FamilyLocationService::class.java))
            if (enabled) "Location consent enabled. Press Start live sharing to begin." else "Location consent revoked."
        }
    }

    private fun startFamilyLocationSharing() {
        if (!hasLocationPermission()) {
            notice = "Grant foreground location permission first, then try again."
            return
        }
        if (!hasNotificationPermission()) {
            notice = "Allow notifications so Android can show the persistent sharing notification."
            if (Build.VERSION.SDK_INT >= 33) notificationsPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS)
            return
        }
        if (familyId.isBlank()) { notice = "Sign in and create or join a family first."; return }
        if (BuildConfig.FAMILYGUARD_API_BASE_URL.isBlank()) {
            notice = "FamilyGuard service URL is not configured. Please try again after the service is available."
            return
        }
        runFamilyTask {
            FamilyGuardApi.post(this, "/family/consent", JSONObject().put("familyId", familyId).put("shareLocation", true))
            familyConsentEnabled = true
            getSharedPreferences("familyguard_family", MODE_PRIVATE).edit().putBoolean("locationConsent", true).apply()
            val intent = Intent(this, FamilyLocationService::class.java)
            if (Build.VERSION.SDK_INT >= 26) startForegroundService(intent) else startService(intent)
            "Live sharing is on. Android displays a persistent notification."
        }
    }

    private fun stopFamilyLocationSharing() {
        stopService(Intent(this, FamilyLocationService::class.java))
        runFamilyTask {
            val id = familyId.ifBlank { getSharedPreferences("familyguard_family", MODE_PRIVATE).getString("familyId", "") ?: "" }
            if (id.isNotBlank()) FamilyGuardApi.post(this, "/family/consent", JSONObject().put("familyId", id).put("shareLocation", false))
            familyConsentEnabled = false
            getSharedPreferences("familyguard_family", MODE_PRIVATE).edit().putBoolean("locationConsent", false).apply()
            "Sharing stopped and consent revoked."
        }
    }

    private fun fetchFamilyLocations() {
        runFamilyTask {
            val id = familyId.ifBlank { getSharedPreferences("familyguard_family", MODE_PRIVATE).getString("familyId", "") ?: "" }
            require(id.isNotBlank()) { "Create or join a family first." }
            val result = FamilyGuardApi.get(this, "/location/family?familyId=" + java.net.URLEncoder.encode(id, "UTF-8"))
            val array = result.optJSONArray("locations")
            val rows = (0 until (array?.length() ?: 0)).map { i ->
                val item = array!!.getJSONObject(i)
                "Member " + item.optString("uid").take(8) + ": %.5f, %.5f".format(item.optDouble("latitude"), item.optDouble("longitude")) +
                    " • Battery " + (if (item.isNull("batteryPercent")) "N/A" else item.optInt("batteryPercent").toString() + "%") +
                    " • " + if (item.optBoolean("isOnline")) "Online" else "Last check-in stale"
            }
            familyLocationsText = if (rows.isEmpty()) "No opted-in family locations available." else rows.joinToString("\n")
            "Family locations refreshed."
        }
    }

    private fun openWebPage(url: String) {
        try { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }
        catch (_: Exception) { notice = "The page could not be opened." }
    }

    private fun hasLocationPermission(): Boolean =
        checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED ||
            checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED

    private fun hasNotificationPermission(): Boolean =
        Build.VERSION.SDK_INT < 33 ||
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    private fun beginPermissionSetup() {
        pendingDisclosure = when {
            !hasLocationPermission() -> "location"
            !hasNotificationPermission() -> "notifications"
            premium && !hasUsageAccess() -> "usage"
            else -> null
        }
        if (pendingDisclosure == null) notice = "All permissions currently needed by your enabled features are already granted. Background location is not requested."
    }

    private fun updatePermissionStatus() {
        val location = if (hasLocationPermission()) "Granted" else "Pending"
        val notifications = if (hasNotificationPermission()) "Granted" else "Pending"
        val usage = if (hasUsageAccess()) "Granted" else if (premium) "Pending" else "Premium only"
        permissionStatusText = "Location: $location\nNotifications: $notifications\nUsage Access: $usage\nBackground location: Not requested (no background tracking)\nBattery exemption: Not requested (not needed)"
    }

    private fun openPremiumCheckout() {
        billingMessage = "Open the official website, sign in with this same account and complete the ₹999/month checkout."
        openWebPage("https://princehacker.onrender.com/#pricing")
    }

    private fun refreshSubscriptionStatus() {
        verificationExecutor.execute {
            try {
                val status = FamilyGuardApi.get(this, "/billing/status")
                val active = status.optBoolean("premium", false)
                runOnUiThread {
                    premium = active
                    billingReady = true
                    billingMessage = if (active) "Razorpay subscription is active." else
                        "Free plan. Premium activates only after server-verified payment."
                    refreshUsageReport()
                    updatePermissionStatus()
                }
            } catch (e: Exception) {
                runOnUiThread {
                    premium = false
                    billingReady = false
                    billingMessage = e.message ?: "Sign in to refresh plan status."
                    refreshUsageReport()
                    updatePermissionStatus()
                }
            }
        }
    }

    private fun checkForAppUpdate() {
        verificationExecutor.execute {
            var connection: java.net.HttpURLConnection? = null
            try {
                connection = (java.net.URL("https://princehacker.onrender.com/app/latest-version").openConnection() as java.net.HttpURLConnection).apply {
                    requestMethod = "GET"
                    connectTimeout = 10000
                    readTimeout = 12000
                    setRequestProperty("Accept", "application/json")
                }
                val code = connection.responseCode
                require(code in 200..299) { "Update service is temporarily unavailable." }
                val versionData = JSONObject(connection.inputStream.bufferedReader().use { it.readText() })
                val latestCode = versionData.optLong("versionCode", 0L)
                val currentInfo = packageManager.getPackageInfo(packageName, 0)
                val currentCode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) currentInfo.longVersionCode else currentInfo.versionCode.toLong()
                val apkUrl = versionData.optString("apkUrl", "")
                runOnUiThread {
                    if (latestCode > currentCode && apkUrl.startsWith("https://")) {
                        notice = "FamilyGuard update " + versionData.optString("versionName") + " is available. The official APK will open in your browser. Review Android's installer prompt before updating."
                        openWebPage(apkUrl)
                    } else {
                        notice = "FamilyGuard is up to date. Current version: " + packageManager.getPackageInfo(packageName, 0).versionName + "."
                    }
                }
            } catch (e: Exception) {
                runOnUiThread { notice = e.message ?: "Could not check for updates. Try again later." }
            } finally {
                connection?.disconnect()
            }
        }
    }

    private fun hasUsageAccess(): Boolean {
        val appOps = getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        @Suppress("DEPRECATION")
        val mode = appOps.checkOpNoThrow(
            AppOpsManager.OPSTR_GET_USAGE_STATS,
            android.os.Process.myUid(),
            packageName
        )
        return mode == AppOpsManager.MODE_ALLOWED
    }

    private fun openUsageAccessSettings() {
        if (!premium) {
            notice = "App Usage & Screen Time reports are a Premium feature. Subscribe through the official FamilyGuard website first."
            return
        }
        usageSettingsLauncher.launch(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
    }

    private fun refreshUsageReport() {
        if (!consentAccepted) {
            usageText = "Usage Access remains off until the parent/guardian disclosure is accepted."
            return
        }
        if (!premium) {
            usageText = "Premium required. Your usage data is not read or collected on the Free plan."
            return
        }
        if (!hasUsageAccess()) {
            usageText = "Usage Access is not enabled. Open Usage Access settings and explicitly allow FamilyGuard."
            return
        }
        try {
            val manager = getSystemService(Context.USAGE_STATS_SERVICE) as UsageStatsManager
            val end = System.currentTimeMillis()
            val stats = manager.queryUsageStats(UsageStatsManager.INTERVAL_DAILY, end - 24 * 60 * 60 * 1000L, end)
                .orEmpty()
                .filter { it.totalTimeInForeground > 0 }
                .sortedByDescending { it.totalTimeInForeground }
                .take(8)
            usageText = if (stats.isEmpty()) "No app usage data is available for the last 24 hours." else {
                stats.joinToString("\n") { item ->
                    val label = try {
                        packageManager.getApplicationLabel(packageManager.getApplicationInfo(item.packageName, 0)).toString()
                    } catch (_: Exception) { item.packageName }
                    val minutes = item.totalTimeInForeground / 60_000L
                    "$label — $minutes min"
                }
            }
        } catch (_: Exception) {
            usageText = "Usage report could not be loaded. Check Usage Access permission."
        }
    }

    @Suppress("MissingPermission")
    private fun readCurrentLocation() {
        val fine = checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
        val coarse = checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED
        if (!fine && !coarse) {
            locationText = "Location permission is required."
            return
        }
        try {
            val manager = getSystemService(Context.LOCATION_SERVICE) as LocationManager
            val provider = if (fine) LocationManager.GPS_PROVIDER else LocationManager.NETWORK_PROVIDER
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                manager.getCurrentLocation(provider, null, mainExecutor) { location: Location? ->
                    locationText = location?.let {
                        "This device: %.5f, %.5f\nUpdated: %s".format(
                            it.latitude, it.longitude, DateFormat.getTimeInstance().format(Date(it.time))
                        )
                    } ?: "No location fix available. Try again outdoors."
                }
            } else {
                val location = manager.getLastKnownLocation(provider)
                locationText = location?.let {
                    "This device: %.5f, %.5f".format(it.latitude, it.longitude)
                } ?: "No recent location is available."
            }
        } catch (_: Exception) {
            locationText = "Location is currently unavailable."
        }
    }

    private fun sendSosAlert() {
        val text = "SOS: I need help. This alert was prepared by the FamilyGuard user."
        val intent = Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:"))
            .putExtra("sms_body", text)
        if (intent.resolveActivity(packageManager) != null) startActivity(intent)
        else notice = "No SMS app is available. SOS was not sent."
    }
}


@Composable
private fun ParentalConsentDisclosureScreen(
    onAccept: () -> Unit,
    onDecline: () -> Unit
) {
    var parentConfirmed by remember { mutableStateOf(false) }
    Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        Column(
            modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(24.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            Text("FamilyGuard — Important privacy notice", style = MaterialTheme.typography.headlineMedium)
            Text(
                "A parent or legal guardian must review and accept this notice before FamilyGuard can request location permission or open Usage Access settings.",
                style = MaterialTheme.typography.bodyLarge
            )
            Text("Location", style = MaterialTheme.typography.titleMedium)
            Text("If you choose the location feature, Android will ask for foreground location permission (precise or approximate). Location is used only when you actively request an update and is displayed on this device. This app does not request background location and does not continuously track location.")
            Text("App usage", style = MaterialTheme.typography.titleMedium)
            Text("If you choose the Premium usage report, Android Settings will ask you to grant Usage Access (PACKAGE_USAGE_STATS). FamilyGuard reads app usage on this device to show a recent usage summary. You can revoke this access in Android Settings.")
            Text("Data, sharing and safety", style = MaterialTheme.typography.titleMedium)
            Text("Location sharing is optional. When the device user explicitly enables it, the app sends location to the FamilyGuard server over HTTPS and shows an ongoing Android notification. The user can stop sharing and revoke consent at any time. Never use this app for covert monitoring.")
            Text("Parental consent", style = MaterialTheme.typography.titleMedium)
            Text("A parent or legal guardian should accept only after explaining the features to the device user and obtaining any consent required by local law. This on-device confirmation is not identity or age verification.")
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Switch(checked = parentConfirmed, onCheckedChange = { parentConfirmed = it })
                Text("I am the parent/legal guardian (or authorized adult) and have read this notice and consent to enabling the selected features.")
            }
            Button(
                onClick = onAccept,
                enabled = parentConfirmed,
                modifier = Modifier.fillMaxWidth()
            ) { Text("Accept and continue") }
            OutlinedButton(onClick = onDecline, modifier = Modifier.fillMaxWidth()) {
                Text("Decline and exit")
            }
            Text("You can change Android permissions at any time in Settings. Read PRIVACY_POLICY.md in the project for more information.", style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun LoginScreen(
    message: String,
    onLogin: (String, String) -> Unit,
    onOpenWebsite: () -> Unit
) {
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        Column(modifier = Modifier.fillMaxSize().padding(24.dp), verticalArrangement = Arrangement.Center) {
            Text("FamilyGuard", style = MaterialTheme.typography.headlineLarge)
            Spacer(Modifier.height(8.dp))
            Text("Sign in with the account created on the official website.")
            Spacer(Modifier.height(24.dp))
            OutlinedTextField(email, { email = it }, label = { Text("Login ID / Email") }, modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(12.dp))
            OutlinedTextField(password, { password = it }, label = { Text("Password") },
                visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
            Spacer(Modifier.height(16.dp))
            Button(onClick = { onLogin(email, password) }, enabled = email.isNotBlank() && password.isNotBlank(),
                modifier = Modifier.fillMaxWidth()) { Text("LOGIN") }
            OutlinedButton(onClick = onOpenWebsite, modifier = Modifier.fillMaxWidth()) {
                Text("Open FamilyGuard Website")
            }
            if (message.isNotBlank()) Text(message, style = MaterialTheme.typography.bodySmall)
            Spacer(Modifier.height(12.dp))
            Text("New accounts and Premium subscriptions are managed only on the website.",
                style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun FamilyGuardDashboard(
    premium: Boolean,
    billingMessage: String,
    usageText: String,
    permissionStatusText: String,
    onSetupPermissions: () -> Unit,
    onUsage: () -> Unit,
    onSos: () -> Unit,
    onOpenWebsite: () -> Unit,
    onLogout: () -> Unit,
    onPrivacy: () -> Unit,
    onTerms: () -> Unit
) {
    Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        Column(modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp)) {
            Text("FamilyGuard", style = MaterialTheme.typography.headlineLarge)
            Card(modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer)) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text(if (premium) "PREMIUM ACTIVE" else "ACCOUNT SIGNED IN", style = MaterialTheme.typography.titleLarge)
                    Text(billingMessage, style = MaterialTheme.typography.bodyMedium)
                    Text("Manage Premium, family members and settings on the official website.",
                        style = MaterialTheme.typography.bodySmall)
                    Button(onClick = onOpenWebsite, modifier = Modifier.fillMaxWidth()) {
                        Text("Open Website Dashboard")
                    }
                }
            }
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text("Permission Setup", style = MaterialTheme.typography.titleLarge)
                    Text("Review each Android permission prompt. Location sharing remains off until you explicitly enable it from the website and consent on this device.")
                    Text(permissionStatusText, style = MaterialTheme.typography.bodyMedium)
                    Button(onClick = onSetupPermissions, modifier = Modifier.fillMaxWidth()) {
                        Text("ONE-CLICK PERMISSION SETUP")
                    }
                }
            }
            if (premium) Card(modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("App Usage & Screen Time", style = MaterialTheme.typography.titleLarge)
                    Text(usageText, style = MaterialTheme.typography.bodyMedium)
                    OutlinedButton(onClick = onUsage, modifier = Modifier.fillMaxWidth()) {
                        Text("Open Usage Access Settings")
                    }
                }
            }
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("SOS Safety Alert", style = MaterialTheme.typography.titleLarge)
                    Text("Prepare a message in your SMS app. Review and send it yourself.")
                    Button(onClick = onSos, modifier = Modifier.fillMaxWidth()) { Text("PREPARE SOS MESSAGE") }
                }
            }
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Settings", style = MaterialTheme.typography.titleLarge)
                    OutlinedButton(onClick = onPrivacy, modifier = Modifier.fillMaxWidth()) { Text("Privacy Policy") }
                    OutlinedButton(onClick = onTerms, modifier = Modifier.fillMaxWidth()) { Text("Terms of Service") }
                    OutlinedButton(onClick = onLogout, modifier = Modifier.fillMaxWidth()) { Text("Log out") }
                }
            }
            Text("FamilyGuard does not hide monitoring. Location sharing is optional, visible and revocable.",
                style = MaterialTheme.typography.bodySmall)
        }
    }
}
