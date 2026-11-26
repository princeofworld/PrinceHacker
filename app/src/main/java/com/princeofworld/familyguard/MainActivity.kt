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
import androidx.work.WorkManager
import androidx.core.content.ContextCompat
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.rememberDrawerState
import androidx.compose.material3.DrawerValue
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
import androidx.compose.runtime.rememberCoroutineScope
import kotlinx.coroutines.launch
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.painterResource
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
    private var usageAccessGranted by mutableStateOf(false)
    private var showDeleteDataDialog by mutableStateOf(false)
    private var familyStatus by mutableStateOf("Sign in to FamilyGuard to create or join a family.")
    private var familyId by mutableStateOf("")
    private var inviteCode by mutableStateOf("")
    private var familyLocationsText by mutableStateOf("No family locations loaded.")
    private var familyConsentEnabled by mutableStateOf(false)
    private var locationSharingActive by mutableStateOf(false)
    private var installedAppsSync by mutableStateOf(false)
    private var callsContactsSync by mutableStateOf(false)
    private var profileName by mutableStateOf("")
    private var profilePhone by mutableStateOf("")
    private var profileMessage by mutableStateOf("Loading profile…")
    private var remoteConsentStatus by mutableStateOf("Server consent has not been refreshed yet.")

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
        if (installedAppsSync && hasUsageAccess()) DeviceSummarySyncScheduler.schedule(this)
        else if (installedAppsSync && !hasUsageAccess()) updateInstalledAppsSync(false)
    }

    private val photoPickerLauncher = registerForActivityResult(
        ActivityResultContracts.PickVisualMedia()
    ) { uri ->
        if (uri != null) verificationExecutor.execute {
            try {
                FamilyGuardApi.uploadPhoto(this, uri)
                runOnUiThread { notice = "Selected photo uploaded to your private FamilyGuard gallery." }
            } catch (error: Exception) {
                runOnUiThread { notice = "Photo upload failed: ${error.message}" }
            }
        }
    }

    private val callsSummaryPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { grants ->
        val allowed = grants[Manifest.permission.READ_CALL_LOG] == true &&
            grants[Manifest.permission.READ_CONTACTS] == true
        if (allowed && callsContactsSync) DeviceSummarySyncScheduler.schedule(this)
        else if (callsContactsSync) {
            callsContactsSync = false
            getSharedPreferences(DeviceSummarySyncWorker.PREFS, Context.MODE_PRIVATE).edit()
                .putBoolean(DeviceSummarySyncWorker.KEY_CALLS, false).apply()
            DeviceSummarySyncScheduler.schedule(this)
            notice = "Both Call Log and Contacts permissions are required. Sync remains OFF."
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        consentAccepted = getSharedPreferences("familyguard_privacy", Context.MODE_PRIVATE)
            .getBoolean("parental_disclosure_accepted_v1", false)
        familyId = getSharedPreferences("familyguard_family", Context.MODE_PRIVATE).getString("familyId", "") ?: ""
        familyConsentEnabled = getSharedPreferences("familyguard_family", Context.MODE_PRIVATE).getBoolean("locationConsent", false)
        val summaryPrefs = getSharedPreferences(DeviceSummarySyncWorker.PREFS, Context.MODE_PRIVATE)
        installedAppsSync = summaryPrefs.getBoolean(DeviceSummarySyncWorker.KEY_APPS, false)
        callsContactsSync = summaryPrefs.getBoolean(DeviceSummarySyncWorker.KEY_CALLS, false)
        signedIn = !getSharedPreferences("familyguard_family", Context.MODE_PRIVATE).getString("idToken", null).isNullOrBlank()
        if (signedIn) {
            refreshSubscriptionStatus()
            verificationExecutor.execute {
                try {
                    syncRemoteDeviceConsentNow()
                    refreshProfile()
                } catch (_: Exception) { }
            }
        }
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
                    usageAccessGranted = usageAccessGranted,
                    familyConsentEnabled = familyConsentEnabled,
                    onSetupPermissions = { beginPermissionSetup() },
                    onUsage = { openUsageAccessSettings() },
                    onSos = { sendSosAlert() },
                    onOpenWebsite = { openWebPage("https://princehacker.onrender.com/") },
                    onLogout = { logout() },
                    onPrivacy = { openWebPage("https://raw.githubusercontent.com/princeofworld/PrinceHacker/main/PRIVACY_POLICY.md") },
                    onTerms = { openWebPage("https://raw.githubusercontent.com/princeofworld/PrinceHacker/main/TERMS_OF_SERVICE.md") },
                    onDeleteAccount = { showDeleteDataDialog = true },
                    familyStatus = familyStatus,
                    locationSharingActive = locationSharingActive,
                    onStartSharing = { startFamilyLocationSharing() },
                    onStopSharing = { stopFamilyLocationSharing() },
                    installedAppsSync = installedAppsSync,
                    callsContactsSync = callsContactsSync,
                    onInstalledAppsSyncChanged = { updateInstalledAppsSync(it) },
                    onCallsContactsSyncChanged = { updateCallsContactsSync(it) },
                    profileName = profileName,
                    profilePhone = profilePhone,
                    profileMessage = profileMessage,
                    remoteConsentStatus = remoteConsentStatus,
                    onRefreshConsent = { refreshDeviceConsent(manual = true) },
                    onSaveProfile = { name, phone -> saveProfile(name, phone) },
                    onPickPhoto = { photoPickerLauncher.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) }
                )
                pendingDisclosure?.let { step ->
                    val disclosure = when (step) {
                        "location" -> "LOCATION SHARING DISCLOSURE: If you agree and grant Android foreground location permission, FamilyGuard collects your device's latitude/longitude, accuracy, capture time and optional battery percentage. When you separately opt in and start sharing, these details are sent over HTTPS to members of the family you joined. A persistent notification remains visible. Each session ends automatically after 10 minutes; sharing and consent are then switched off, and you must press Start again for another session. You can stop earlier or revoke permission at any time. FamilyGuard does not request background location permission."
                        "notifications" -> "FamilyGuard requests permission for visible safety and account notices. You can deny this and continue using features that do not require notifications."
                        "usage_restricted" -> "Android blocks Usage Access for some sideloaded apps until the device user allows restricted settings. FamilyGuard cannot switch this on. On the next screen, open the three-dot menu and choose Allow restricted settings. Android may ask you to confirm your device lock. Return to FamilyGuard and tap One-Click Permission Setup again; then enable FamilyGuard in Usage Access. If Android does not offer this option, do not bypass the device's security controls."
                        else -> "This optional Premium report reads app usage on this device. Android will open Usage Access settings so you can explicitly enable or decline it."
                    }
                    AlertDialog(
                        onDismissRequest = { pendingDisclosure = null },
                        title = { Text(if (step == "usage_restricted") "Android restricted settings" else "Before enabling $step") },
                        text = { Text(disclosure) },
                        confirmButton = {
                            TextButton(onClick = {
                                pendingDisclosure = null
                                when (step) {
                                    "location" -> locationPermissionLauncher.launch(arrayOf(Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION))
                                    "notifications" -> if (Build.VERSION.SDK_INT >= 33) notificationsPermissionLauncher.launch(Manifest.permission.POST_NOTIFICATIONS) else if (premium) pendingDisclosure = "usage"
                                    "usage_restricted" -> {
                                        getSharedPreferences("familyguard_permissions", Context.MODE_PRIVATE)
                                            .edit().putBoolean("usage_restricted_help_shown", true).apply()
                                        try {
                                            startActivity(
                                                Intent(
                                                    Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                                                    Uri.parse("package:$packageName")
                                                )
                                            )
                                        } catch (_: Exception) {
                                            notice = "Could not open App Info. Open Android Settings > Apps > FamilyGuard manually, then review the three-dot menu."
                                        }
                                    }
                                    "usage" -> if (premium) usageSettingsLauncher.launch(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
                                    "summary_usage" -> usageSettingsLauncher.launch(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
                                }
                            }) { Text("Agree and continue") }
                        },
                        dismissButton = { TextButton(onClick = { pendingDisclosure = null }) { Text("Not now") } }
                    )
                }
                if (showDeleteDataDialog) {
                    AlertDialog(
                        onDismissRequest = { showDeleteDataDialog = false },
                        title = { Text("Delete FamilyGuard account?") },
                        text = { Text("This permanently deletes your account, device registrations, payment-review records and family membership. Any family you own will be transferred to a remaining member or deleted if empty. This cannot be undone. Direct UPI payments do not create recurring mandates.") },
                        confirmButton = {
                            TextButton(onClick = {
                                showDeleteDataDialog = false
                                notice = "Deleting your account and associated data…"
                                verificationExecutor.execute {
                                    try {
                                        FamilyGuardApi.deleteAccount(this)
                                        stopService(Intent(this, FamilyLocationService::class.java))
                                        getSharedPreferences("familyguard_family", Context.MODE_PRIVATE).edit().clear().apply()
                                        getSharedPreferences("familyguard_privacy", Context.MODE_PRIVATE).edit().clear().apply()
                                        getSharedPreferences("familyguard_permissions", Context.MODE_PRIVATE).edit().clear().apply()
                                        runOnUiThread {
                                            signedIn = false
                                            premium = false
                                            consentAccepted = false
                                            familyConsentEnabled = false
                                            showDeleteDataDialog = false
                                            notice = "Your FamilyGuard account and associated server data were deleted."
                                        }
                                    } catch (e: Exception) {
                                        runOnUiThread { notice = e.message ?: "Account deletion failed. Please try again." }
                                    }
                                }
                            }) { Text("Delete account and data") }
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
        if (signedIn) refreshFamilyContext()
        if (signedIn) refreshProfile()
        locationSharingActive = getSharedPreferences("familyguard_family", Context.MODE_PRIVATE)
            .getString("locationSessionStatus", "stopped") == "active"
        if (signedIn) refreshDeviceConsent()
        updatePermissionStatus()
    }

    override fun onDestroy() {
        verificationExecutor.shutdownNow()
        super.onDestroy()
    }

    private fun requireLoginAfterAuthFailure(error: Exception) {
        if (!FamilyGuardApi.isSessionInvalid(error)) return
        FamilyGuardApi.clearSession(this)
        runOnUiThread {
            signedIn = false
            premium = false
            billingReady = false
            loginMessage = "Your session expired. Sign in again with your email and password."
            profileMessage = "Please sign in again to manage your profile."
            remoteConsentStatus = "Sign in again to verify server consent."
            locationSharingActive = false
        }
        DeviceSummarySyncScheduler.cancel(this)
        stopService(Intent(this, FamilyLocationService::class.java))
    }

    private fun runFamilyTask(action: () -> String) {
        verificationExecutor.execute {
            try {
                val message = action()
                runOnUiThread { familyStatus = message }
            } catch (e: Exception) {
                requireLoginAfterAuthFailure(e)
                runOnUiThread { familyStatus = e.message ?: "Family service request failed." }
            }
        }
    }

    private fun refreshDeviceConsent(manual: Boolean = false) {
        if (!signedIn) return
        verificationExecutor.execute { syncRemoteDeviceConsentNow(manual) }
    }

    // Must run on verificationExecutor: fetch authoritative server flags before any
    // local worker is scheduled. Missing/unreachable consent fails closed for sharing.
    private fun syncRemoteDeviceConsentNow(manual: Boolean = false) {
        try {
            FamilyGuardApi.registerThisDevice(this)
            val familyPrefs = getSharedPreferences("familyguard_family", Context.MODE_PRIVATE)
            val deviceId = familyPrefs.getString("deviceId", null)
                ?: throw IllegalStateException("Device registration is not ready.")
            val remote = FamilyGuardApi.getDeviceConsent(this, deviceId)
            val locationOn = remote.optBoolean("location", false)
            val batteryOn = remote.optBoolean("battery", false)
            val summariesOn = remote.optBoolean("summaries", false)
            val summaryPrefs = getSharedPreferences(DeviceSummarySyncWorker.PREFS, Context.MODE_PRIVATE)

            if (!summariesOn) {
                summaryPrefs.edit()
                    .putBoolean(DeviceSummarySyncWorker.KEY_APPS, false)
                    .putBoolean(DeviceSummarySyncWorker.KEY_CALLS, false)
                    .apply()
                DeviceSummarySyncScheduler.cancel(this)
            }
            if (!locationOn) {
                familyPrefs.edit()
                    .putBoolean("locationConsent", false)
                    .putString("locationSessionStatus", "stopped")
                    .apply()
                // Stop the foreground location service immediately when website consent is revoked.
                stopService(Intent(this, FamilyLocationService::class.java))
            }

            val appsNow = summariesOn && summaryPrefs.getBoolean(DeviceSummarySyncWorker.KEY_APPS, false)
            val callsNow = summariesOn && summaryPrefs.getBoolean(DeviceSummarySyncWorker.KEY_CALLS, false)
            runOnUiThread {
                installedAppsSync = appsNow
                callsContactsSync = callsNow
                locationSharingActive = locationOn &&
                    familyPrefs.getString("locationSessionStatus", "stopped") == "active"
                remoteConsentStatus = "Server consent — Location: " + (if (locationOn) "ON" else "OFF") +
                    " · Battery: " + (if (batteryOn) "ON" else "OFF") +
                    " · Device summaries: " + (if (summariesOn) "ON" else "OFF")
                if (summariesOn && (appsNow || callsNow)) {
                    if (manual) DeviceSummarySyncScheduler.syncNow(this)
                    else DeviceSummarySyncScheduler.schedule(this)
                }
                if (manual) notice = "Latest device consent refreshed from your FamilyGuard account."
            }
        } catch (e: Exception) {
            runOnUiThread {
                installedAppsSync = false
                callsContactsSync = false
                locationSharingActive = false
                remoteConsentStatus = "Could not refresh server consent. Background collection remains blocked until consent is verified."
                if (manual) notice = e.message ?: "Consent refresh failed. No new background sync was started."
            }
            // Fail closed if consent cannot be verified: cancel pending summary work and stop location.
            getSharedPreferences(DeviceSummarySyncWorker.PREFS, Context.MODE_PRIVATE).edit()
                .putBoolean(DeviceSummarySyncWorker.KEY_APPS, false)
                .putBoolean(DeviceSummarySyncWorker.KEY_CALLS, false)
                .apply()
            DeviceSummarySyncScheduler.cancel(this)
            getSharedPreferences("familyguard_family", Context.MODE_PRIVATE).edit()
                .putBoolean("locationConsent", false)
                .putString("locationSessionStatus", "stopped")
                .apply()
            stopService(Intent(this, FamilyLocationService::class.java))
        }
    }

    private fun refreshProfile() {
        verificationExecutor.execute {
            try {
                val profile = FamilyGuardApi.getProfile(this)
                runOnUiThread {
                    profileName = profile.optString("name", "")
                    profilePhone = profile.optString("phone", "")
                    profileMessage = "Profile loaded."
                }
            } catch (e: Exception) {
                requireLoginAfterAuthFailure(e)
                runOnUiThread { profileMessage = e.message ?: "Profile could not be loaded." }
            }
        }
    }

    private fun saveProfile(name: String, phone: String) {
        profileMessage = "Saving profile…"
        verificationExecutor.execute {
            try {
                val profile = FamilyGuardApi.updateProfile(this, name, phone)
                runOnUiThread {
                    profileName = profile.optString("name", "")
                    profilePhone = profile.optString("phone", "")
                    profileMessage = "Profile saved to your FamilyGuard account."
                }
            } catch (e: Exception) {
                requireLoginAfterAuthFailure(e)
                runOnUiThread { profileMessage = e.message ?: "Profile update failed." }
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
        DeviceSummarySyncScheduler.cancel(this)
        stopService(Intent(this, FamilyLocationService::class.java))
        getSharedPreferences("familyguard_family", Context.MODE_PRIVATE).edit()
            .remove("idToken").remove("refreshToken").remove("uid").remove("tokenExpiry").apply()
        signedIn = false
        premium = false
        billingReady = false
        loginMessage = "You have been signed out."
    }

    private fun syncDeviceSummaryNow() {
        DeviceSummarySyncScheduler.syncNow(this)
        notice = "Sync requested. Only enabled categories and currently granted permissions will be uploaded."
    }

    private fun persistSummaryConsent() {
        val id = getSharedPreferences("familyguard_family", Context.MODE_PRIVATE).getString("deviceId", null) ?: return
        val enabled = installedAppsSync || callsContactsSync
        verificationExecutor.execute {
            try { FamilyGuardApi.updateDeviceConsent(this, id, summaries = enabled) }
            catch (e: Exception) { runOnUiThread { notice = e.message ?: "Server consent update failed; uploads remain blocked." } }
        }
    }

    private fun updateInstalledAppsSync(enabled: Boolean) {
        val prefs = getSharedPreferences(DeviceSummarySyncWorker.PREFS, Context.MODE_PRIVATE)
        if (!enabled) {
            installedAppsSync = false
            prefs.edit().putBoolean(DeviceSummarySyncWorker.KEY_APPS, false).apply()
            DeviceSummarySyncScheduler.cancel(this)
            persistSummaryConsent()
            if (callsContactsSync) DeviceSummarySyncScheduler.schedule(this) else clearDeviceSummary()
            notice = "Installed Apps & Screen Time Sync is OFF. Pending work cancelled."
            return
        }
        if (!consentAccepted) { notice = "Accept the privacy disclosure first."; return }
        val id = getSharedPreferences("familyguard_family", Context.MODE_PRIVATE).getString("deviceId", null) ?: return
        verificationExecutor.execute {
            try {
                FamilyGuardApi.updateDeviceConsent(this, id, summaries = true)
                runOnUiThread {
                    installedAppsSync = true
                    prefs.edit().putBoolean(DeviceSummarySyncWorker.KEY_APPS, true).apply()
                    DeviceSummarySyncScheduler.syncNow(this)
                    if (hasUsageAccess()) DeviceSummarySyncScheduler.schedule(this) else pendingDisclosure = "summary_usage"
                }
            } catch (e: Exception) { runOnUiThread { notice = e.message ?: "Consent update failed; sync remains OFF." } }
        }
    }

    private fun updateCallsContactsSync(enabled: Boolean) {
        val prefs = getSharedPreferences(DeviceSummarySyncWorker.PREFS, Context.MODE_PRIVATE)
        if (!enabled) {
            callsContactsSync = false
            prefs.edit().putBoolean(DeviceSummarySyncWorker.KEY_CALLS, false).apply()
            DeviceSummarySyncScheduler.cancel(this)
            persistSummaryConsent()
            if (installedAppsSync) DeviceSummarySyncScheduler.schedule(this) else clearDeviceSummary()
            notice = "Today's Calls & Contacts Summary Sync is OFF. Pending work cancelled."
            return
        }
        if (!consentAccepted) { notice = "Accept the privacy disclosure first."; return }
        val id = getSharedPreferences("familyguard_family", Context.MODE_PRIVATE).getString("deviceId", null) ?: return
        verificationExecutor.execute {
            try {
                FamilyGuardApi.updateDeviceConsent(this, id, summaries = true)
                runOnUiThread {
                    callsContactsSync = true
                    prefs.edit().putBoolean(DeviceSummarySyncWorker.KEY_CALLS, true).apply()
                    DeviceSummarySyncScheduler.syncNow(this)
                    if (hasCallAndContactsPermissions()) DeviceSummarySyncScheduler.schedule(this)
                    else callsSummaryPermissionLauncher.launch(arrayOf(Manifest.permission.READ_CALL_LOG, Manifest.permission.READ_CONTACTS))
                }
            } catch (e: Exception) { runOnUiThread { notice = e.message ?: "Consent update failed; sync remains OFF." } }
        }
    }

    private fun hasCallAndContactsPermissions(): Boolean =
        ContextCompat.checkSelfPermission(this, Manifest.permission.READ_CALL_LOG) == PackageManager.PERMISSION_GRANTED &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.READ_CONTACTS) == PackageManager.PERMISSION_GRANTED

    private fun clearDeviceSummary() {
        val prefs = getSharedPreferences("familyguard_family", Context.MODE_PRIVATE)
        val id = prefs.getString("deviceId", null) ?: return
        verificationExecutor.execute {
            try {
                FamilyGuardApi.postDeviceSummary(this, id, JSONObject()
                    .put("installed_apps", org.json.JSONArray())
                    .put("screen_time_minutes", 0)
                    .put("today_calls_count", 0)
                    .put("today_calls_duration_sec", 0)
                    .put("contacts_count", 0))
            } catch (_: Exception) {
                // Local consent remains OFF even if a network reset must be retried later.
            }
        }
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

    private fun refreshFamilyContext() {
        verificationExecutor.execute {
            try {
                val result = FamilyGuardApi.get(this, "/family/mine")
                val families = result.optJSONArray("families")
                val first = if (families != null && families.length() > 0) families.getJSONObject(0) else null
                val id = first?.optString("familyId").orEmpty()
                val members = first?.optJSONArray("members")
                val uid = getSharedPreferences("familyguard_family", Context.MODE_PRIVATE).getString("uid", null)
                var consent = false
                if (members != null) {
                    for (i in 0 until members.length()) {
                        val member = members.getJSONObject(i)
                        if (member.optString("uid") == uid) consent = member.optBoolean("locationConsent", false)
                    }
                }
                getSharedPreferences("familyguard_family", Context.MODE_PRIVATE).edit()
                    .putString("familyId", id).putBoolean("locationConsent", consent).apply()
                runOnUiThread {
                    familyId = id
                    familyConsentEnabled = consent
                    familyStatus = if (id.isBlank()) "No family joined yet. Join a family on the website first."
                        else "Connected to family. Sharing requires a fresh 10-minute session."
                }
            } catch (e: Exception) {
                runOnUiThread { familyStatus = e.message ?: "Could not load family membership." }
            }
        }
    }

    private fun startFamilyLocationSharing() {
        if (!familyConsentEnabled) {
            notice = "Location sharing is not enabled by your family consent settings. Enable consent on the FamilyGuard website first."
            return
        }
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
            val deviceId = getSharedPreferences("familyguard_family", MODE_PRIVATE).getString("deviceId", null) ?: throw IllegalStateException("Register this device first.")
            FamilyGuardApi.updateDeviceConsent(this, deviceId, location = true)
            FamilyGuardApi.post(this, "/family/consent", JSONObject().put("familyId", familyId).put("shareLocation", true))
            familyConsentEnabled = true
            getSharedPreferences("familyguard_family", MODE_PRIVATE).edit().putBoolean("locationConsent", true).apply()
            val intent = Intent(this, FamilyLocationService::class.java)
            if (Build.VERSION.SDK_INT >= 26) startForegroundService(intent) else startService(intent)
            locationSharingActive = true
            getSharedPreferences("familyguard_family", MODE_PRIVATE).edit().putString("locationSessionStatus", "active").apply()
            "Live sharing started. Android displays a persistent notification; this session automatically ends after 10 minutes."
        }
    }

    private fun stopFamilyLocationSharing() {
        val stopIntent = Intent(this, FamilyLocationService::class.java).setAction(FamilyLocationService.ACTION_STOP)
        try { startService(stopIntent) } catch (_: Exception) { stopService(Intent(this, FamilyLocationService::class.java)) }
        locationSharingActive = false
        getSharedPreferences("familyguard_family", MODE_PRIVATE).edit().putString("locationSessionStatus", "stopped").putBoolean("locationConsent", false).apply()
        runFamilyTask {
            val id = familyId.ifBlank { getSharedPreferences("familyguard_family", MODE_PRIVATE).getString("familyId", "") ?: "" }
            if (id.isNotBlank()) FamilyGuardApi.post(this, "/family/consent", JSONObject().put("familyId", id).put("shareLocation", false))
            val deviceId = getSharedPreferences("familyguard_family", MODE_PRIVATE).getString("deviceId", null)
            if (!deviceId.isNullOrBlank()) FamilyGuardApi.updateDeviceConsent(this, deviceId, location = false)
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
        ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED ||
            ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED

    private fun hasNotificationPermission(): Boolean =
        Build.VERSION.SDK_INT < 33 ||
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    private fun beginPermissionSetup() {
        val restrictedHelpShown = getSharedPreferences("familyguard_permissions", Context.MODE_PRIVATE)
            .getBoolean("usage_restricted_help_shown", false)
        pendingDisclosure = when {
            !hasLocationPermission() -> "location"
            !hasNotificationPermission() -> "notifications"
            premium && !hasUsageAccess() && Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && !restrictedHelpShown -> "usage_restricted"
            premium && !hasUsageAccess() -> "usage"
            else -> null
        }
        if (pendingDisclosure == null) notice = "All permissions currently needed by your enabled features are already granted. Background location is not requested."
    }

    private fun updatePermissionStatus() {
        val location = if (hasLocationPermission()) "Granted" else "Pending"
        val notifications = if (hasNotificationPermission()) "Granted" else "Pending"
        val restrictedHelpShown = getSharedPreferences("familyguard_permissions", Context.MODE_PRIVATE)
            .getBoolean("usage_restricted_help_shown", false)
        val usage = when {
            hasUsageAccess() -> "Granted"
            !premium -> "Premium only"
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && !restrictedHelpShown -> "Action needed: review Android Restricted Settings"
            else -> "Pending: enable FamilyGuard in Usage Access settings"
        }
        usageAccessGranted = hasUsageAccess()
        permissionStatusText = "Location: $location\nNotifications: $notifications\nUsage Access: $usage\nBackground location permission: Not requested; sharing uses a user-started foreground service.\nBattery exemption: Not requested (not needed)"
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
                    billingMessage = if (active) "Premium is active for this account." else
                        "Free plan. UPI payments stay Pending until an administrator verifies the bank credit."
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
        val restrictedHelpShown = getSharedPreferences("familyguard_permissions", Context.MODE_PRIVATE)
            .getBoolean("usage_restricted_help_shown", false)
        if (!hasUsageAccess() && Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && !restrictedHelpShown) {
            pendingDisclosure = "usage_restricted"
        } else {
            usageSettingsLauncher.launch(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS))
        }
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
        val fine = ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
        val coarse = ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED
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
        val fine = ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
        val coarse = ContextCompat.checkSelfPermission(this, Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED
        if (!fine && !coarse) {
            notice = "Grant location permission before sending an SOS location."
            return
        }
        val prefs = getSharedPreferences("familyguard_family", MODE_PRIVATE)
        val deviceId = prefs.getString("deviceId", null)
        if (deviceId.isNullOrBlank()) {
            notice = "Register this device and sign in before sending SOS."
            return
        }
        notice = "Sending SOS alert…"
        verificationExecutor.execute {
            try {
                val consent = FamilyGuardApi.getDeviceConsent(this, deviceId)
                if (!consent.optBoolean("location", false)) {
                    runOnUiThread { notice = "Enable location-sharing consent before sending SOS." }
                    return@execute
                }
                val manager = getSystemService(Context.LOCATION_SERVICE) as LocationManager
                val provider = if (fine) LocationManager.GPS_PROVIDER else LocationManager.NETWORK_PROVIDER
                val location = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                    var result: Location? = null
                    val latch = java.util.concurrent.CountDownLatch(1)
                    manager.getCurrentLocation(provider, null, mainExecutor) { value ->
                        result = value
                        latch.countDown()
                    }
                    if (!latch.await(20, java.util.concurrent.TimeUnit.SECONDS)) null else result
                } else {
                    @Suppress("DEPRECATION")
                    manager.getLastKnownLocation(provider)
                }
                if (location == null) throw IllegalStateException("A fresh GPS location is unavailable. Try again outdoors.")
                FamilyGuardApi.sendSosAlert(this, deviceId, location)
                runOnUiThread { notice = "SOS emergency alert sent to your FamilyGuard account." }
            } catch (e: Exception) {
                runOnUiThread { notice = "SOS was not sent: " + (e.message ?: "network or location error") }
            }
        }
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
            Text("LOCATION SHARING: If you grant foreground location permission and then separately opt in and start a session, FamilyGuard collects latitude/longitude, accuracy, capture time and optional battery percentage and sends them over HTTPS to members of the family you joined. A persistent notification remains visible. Updates may be requested approximately every 60 seconds. Each session automatically stops after 10 minutes, revokes local sharing consent and requires you to press Start again. You can stop earlier or revoke permission at any time. ACCESS_BACKGROUND_LOCATION is not requested.")
            Text("App usage", style = MaterialTheme.typography.titleMedium)
            Text("APP USAGE: The optional report requires Usage Access separately in Android Settings. The separate Installed Apps & Screen Time Sync switch uploads launchable app labels and today's total screen-time minutes only while ON.")
            Text("Calls and contacts summary", style = MaterialTheme.typography.titleMedium)
            Text("CALLS & CONTACTS: A separate switch and Android runtime permission are required. While ON, FamilyGuard reads call dates and durations only to calculate today's total call count and total duration, and reads only the total contacts count. Phone numbers, call details, contact names and contact contents are never uploaded. Turning the switch OFF immediately stops future scheduled sync and clears the stored summary when the network is available.")
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
            Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                        Image(painter = painterResource(R.drawable.princecircle_logo), contentDescription = "PrinceCircle crown logo", modifier = Modifier.size(56.dp), contentScale = ContentScale.Fit)
                        Text("PrinceCircle", style = MaterialTheme.typography.headlineLarge)
                    }
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
    installedAppsSync: Boolean,
    callsContactsSync: Boolean,
    onInstalledAppsSyncChanged: (Boolean) -> Unit,
    onCallsContactsSyncChanged: (Boolean) -> Unit,
    billingMessage: String,
    usageText: String,
    permissionStatusText: String,
    usageAccessGranted: Boolean,
    familyConsentEnabled: Boolean,
    onSetupPermissions: () -> Unit,
    onUsage: () -> Unit,
    onSos: () -> Unit,
    onOpenWebsite: () -> Unit,
    onLogout: () -> Unit,
    onPrivacy: () -> Unit,
    onTerms: () -> Unit,
    onDeleteAccount: () -> Unit,
    familyStatus: String,
    locationSharingActive: Boolean,
    onStartSharing: () -> Unit,
    onStopSharing: () -> Unit,
    profileName: String,
    profilePhone: String,
    profileMessage: String,
    remoteConsentStatus: String,
    onRefreshConsent: () -> Unit,
    onSaveProfile: (String, String) -> Unit,
    onPickPhoto: () -> Unit
) {
    val drawerState = rememberDrawerState(initialValue = DrawerValue.Closed)
    val scope = rememberCoroutineScope()
    ModalNavigationDrawer(
        drawerState = drawerState,
        drawerContent = {
            ModalDrawerSheet {
                Column(
                    modifier = Modifier.fillMaxWidth().padding(20.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    Text("FamilyGuard Menu", style = MaterialTheme.typography.titleLarge)
                    Text("Safety", style = MaterialTheme.typography.titleMedium)
                    Button(
                        onClick = { scope.launch { drawerState.close(); onSos() } },
                        modifier = Modifier.fillMaxWidth()
                    ) { Text("SOS Safety Alert") }
                    Text("Settings", style = MaterialTheme.typography.titleMedium)
                    OutlinedButton(
                        onClick = { scope.launch { drawerState.close(); onPrivacy() } },
                        modifier = Modifier.fillMaxWidth()
                    ) { Text("Privacy Policy") }
                    OutlinedButton(
                        onClick = { scope.launch { drawerState.close(); onTerms() } },
                        modifier = Modifier.fillMaxWidth()
                    ) { Text("Terms of Service") }
                    OutlinedButton(
                        onClick = { scope.launch { drawerState.close(); onDeleteAccount() } },
                        modifier = Modifier.fillMaxWidth()
                    ) { Text("Delete account and data") }
                    OutlinedButton(
                        onClick = { scope.launch { drawerState.close(); onLogout() } },
                        modifier = Modifier.fillMaxWidth()
                    ) { Text("Log out") }
                }
            }
        }
    ) {
        Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            Column(
                modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp)
            ) {
                Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                    Text("FamilyGuard", style = MaterialTheme.typography.headlineLarge)
                    TextButton(onClick = { scope.launch { drawerState.open() } }) { Text("☰ Menu") }
                }
                var editableName by remember(profileName) { mutableStateOf(profileName) }
                var editablePhone by remember(profilePhone) { mutableStateOf(profilePhone) }
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("Profile", style = MaterialTheme.typography.titleLarge)
                        OutlinedTextField(editableName, { editableName = it }, label = { Text("Name") }, modifier = Modifier.fillMaxWidth())
                        OutlinedTextField(editablePhone, { editablePhone = it }, label = { Text("Contact phone") }, modifier = Modifier.fillMaxWidth())
                        Text(profileMessage, style = MaterialTheme.typography.bodySmall)
                        Button(onClick = { onSaveProfile(editableName, editablePhone) }, modifier = Modifier.fillMaxWidth()) { Text("SAVE PROFILE") }
                    }
                }
                Card(modifier = Modifier.fillMaxWidth(), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer)) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(if (premium) "PREMIUM ACTIVE" else "ACCOUNT SIGNED IN", style = MaterialTheme.typography.titleLarge)
                        Text(billingMessage, style = MaterialTheme.typography.bodyMedium)
                        Text(familyStatus, style = MaterialTheme.typography.bodySmall)
                        Button(onClick = onOpenWebsite, modifier = Modifier.fillMaxWidth()) { Text("Open Website Dashboard") }
                    }
                }
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("Private Photo Gallery", style = MaterialTheme.typography.titleLarge)
                        Text("Choose photos yourself using Android Photo Picker. Only selected JPEG, PNG, or WebP photos up to 5 MB are uploaded.", style = MaterialTheme.typography.bodySmall)
                        Button(onClick = onPickPhoto, modifier = Modifier.fillMaxWidth()) { Text("CHOOSE PHOTO") }
                    }
                }
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Text("Location Sharing", style = MaterialTheme.typography.titleLarge)
                        Text(if (locationSharingActive) "LIVE SHARING ON" else "LIVE SHARING OFF", style = MaterialTheme.typography.titleMedium)
                        Text(
                            when {
                                !familyConsentEnabled -> "Family location consent is OFF. Enable it from your FamilyGuard family settings before starting."
                                locationSharingActive -> "Your location is being shared with your family. A persistent Android notification stays visible. Each session automatically ends after 10 minutes."
                                else -> "Family consent is enabled. Turn on the switch to start a visible live-sharing session."
                            },
                            style = MaterialTheme.typography.bodySmall
                        )
                        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text(if (locationSharingActive) "Stop Live Sharing" else "Start Live Sharing")
                            Switch(
                                checked = locationSharingActive,
                                onCheckedChange = { enabled -> if (enabled) onStartSharing() else onStopSharing() },
                                enabled = familyConsentEnabled || locationSharingActive
                            )
                        }
                        Text(familyStatus, style = MaterialTheme.typography.bodySmall)
                    }
                }
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Text("App Usage & Device Summary", style = MaterialTheme.typography.titleLarge)
                        Text("Optional data categories are controlled independently. Only enabled summaries and currently granted permissions are synced.", style = MaterialTheme.typography.bodySmall)
                        Text(remoteConsentStatus, style = MaterialTheme.typography.bodySmall)
                        Button(onClick = onRefreshConsent, modifier = Modifier.fillMaxWidth()) { Text("REFRESH SERVER CONSENT & SYNC") }
                        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Column(Modifier.weight(1f)) {
                                Text("Installed Apps & Screen Time Sync")
                                Text("Uploads launchable app names and today's total usage minutes.", style = MaterialTheme.typography.bodySmall)
                            }
                            Switch(checked = installedAppsSync, onCheckedChange = onInstalledAppsSyncChanged)
                        }
                        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Column(Modifier.weight(1f)) {
                                Text("Today's Calls & Contacts Summary Sync")
                                Text("Uploads today's call count, total duration and contacts count only.", style = MaterialTheme.typography.bodySmall)
                            }
                            Switch(checked = callsContactsSync, onCheckedChange = onCallsContactsSyncChanged)
                        }
                        if (premium) {
                            Text("App Usage & Screen Time", style = MaterialTheme.typography.titleMedium)
                            Text(usageText, style = MaterialTheme.typography.bodyMedium)
                            if (!usageAccessGranted) {
                                OutlinedButton(onClick = onUsage, modifier = Modifier.fillMaxWidth()) { Text("Open Usage Access Settings") }
                            }
                        }
                    }
                }
                Card(modifier = Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                        Text("Permission Setup", style = MaterialTheme.typography.titleLarge)
                        Text("Review each Android permission prompt. Location sharing remains off until family consent is enabled and you explicitly start sharing.")
                        Text(permissionStatusText, style = MaterialTheme.typography.bodyMedium)
                        Button(onClick = onSetupPermissions, modifier = Modifier.fillMaxWidth()) { Text("ONE-CLICK PERMISSION SETUP") }
                    }
                }
                Text("FamilyGuard location sharing is visible, consent-based and revocable. SOS and account settings are available from the menu.", style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}
