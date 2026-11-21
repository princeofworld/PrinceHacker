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
import com.android.billingclient.api.BillingClient
import com.android.billingclient.api.BillingClientStateListener
import com.android.billingclient.api.BillingFlowParams
import com.android.billingclient.api.BillingResult
import com.android.billingclient.api.ProductDetails
import com.android.billingclient.api.Purchase
import com.android.billingclient.api.QueryProductDetailsParams
import com.android.billingclient.api.QueryPurchasesParams
import com.android.billingclient.api.AcknowledgePurchaseParams
import com.android.billingclient.api.PendingPurchasesParams
import java.text.DateFormat
import java.util.Date
import java.net.HttpURLConnection
import java.net.URL
import java.io.OutputStreamWriter
import java.util.concurrent.Executors
import org.json.JSONObject

private const val PREMIUM_PRODUCT_ID = "familyguard_premium_999_monthly"
private const val PREMIUM_BASE_PLAN_ID = "monthly-999"
private const val PLAY_PACKAGE_NAME = "com.princeofworld.familyguard"

class MainActivity : ComponentActivity() {
    private var locationText by mutableStateOf("Location sharing is off. Choose Update location to request access.")
    private var consentAccepted by mutableStateOf(false)
    private var usageText by mutableStateOf("Usage report is available to Premium subscribers after enabling Usage Access.")
    private var premium by mutableStateOf(false)
    private var billingReady by mutableStateOf(false)
    private var billingMessage by mutableStateOf("Connecting to Google Play…")
    private var premiumPriceLabel by mutableStateOf("Monthly price shown by Google Play")
    private val verificationExecutor = Executors.newSingleThreadExecutor()
    private var productDetails: ProductDetails? = null
    private var notice by mutableStateOf<String?>(null)
    private var pendingDisclosure by mutableStateOf<String?>(null)
    private var permissionStatusText by mutableStateOf("Checking permissions…")
    private var showDeleteDataDialog by mutableStateOf(false)
    private var familyStatus by mutableStateOf("Sign in with Firebase to create or join a family.")
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

    private val billingClient by lazy {
        BillingClient.newBuilder(this)
            .setListener { result, purchases -> handlePurchases(result, purchases.orEmpty()) }
            .enablePendingPurchases(PendingPurchasesParams.newBuilder().enableOneTimeProducts().build())
            .build()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        consentAccepted = getSharedPreferences("familyguard_privacy", Context.MODE_PRIVATE)
            .getBoolean("parental_disclosure_accepted_v1", false)
        familyId = getSharedPreferences("familyguard_family", Context.MODE_PRIVATE).getString("familyId", "") ?: ""
        familyConsentEnabled = getSharedPreferences("familyguard_family", Context.MODE_PRIVATE).getBoolean("locationConsent", false)
        connectBilling()
        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                if (!consentAccepted) {
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
                    billingReady = billingReady,
                    billingMessage = billingMessage,
                    premiumPriceLabel = premiumPriceLabel,
                    locationText = locationText,
                    usageText = usageText,
                    familyStatus = familyStatus,
                    familyId = familyId,
                    inviteCode = inviteCode,
                    familyLocationsText = familyLocationsText,
                    familyConsentEnabled = familyConsentEnabled,
                    onFamilyAuth = { email, password, create -> authenticateFamily(email, password, create) },
                    onCreateFamily = { name -> createFamily(name) },
                    onJoinFamily = { code -> joinFamily(code) },
                    onFamilyConsent = { enabled -> setFamilyConsent(enabled) },
                    onStartSharing = { startFamilyLocationSharing() },
                    onStopSharing = { stopFamilyLocationSharing() },
                    onFetchFamilyLocations = { fetchFamilyLocations() },
                    onLocation = {
                        locationPermissionLauncher.launch(arrayOf(
                            Manifest.permission.ACCESS_FINE_LOCATION,
                            Manifest.permission.ACCESS_COARSE_LOCATION
                        ))
                    },
                    onUpgrade = { launchPremiumPurchase() },
                    onRestore = { restorePurchases() },
                    onUsage = { openUsageAccessSettings() },
                    onSos = { sendSosAlert() },
                    onNotice = { notice = it },
                    permissionStatusText = permissionStatusText,
                    onSetupPermissions = { beginPermissionSetup() },
                    onPrivacy = { openWebPage("https://raw.githubusercontent.com/princeofworld/PrinceHacker/main/PRIVACY_POLICY.md") },
                    onTerms = { openWebPage("https://raw.githubusercontent.com/princeofworld/PrinceHacker/main/TERMS_OF_SERVICE.md") },
                    onDeleteData = { showDeleteDataDialog = true }
                )
                pendingDisclosure?.let { step ->
                    val disclosure = when (step) {
                        "location" -> "FamilyGuard requests foreground location only. Android will show its permission prompt. Location is not continuously collected or shared in the background."
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
                        text = { Text("This version has no cloud account or server-stored family data. This action clears the local consent preference and resets local app settings. Google Play subscription billing is managed separately in Google Play.") },
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
        if (consentAccepted) refreshUsageReport()
        if (billingReady) restorePurchases()
        updatePermissionStatus()
    }

    override fun onDestroy() {
        billingClient.endConnection()
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

    private fun authenticateFamily(email: String, password: String, create: Boolean) {
        runFamilyTask {
            val result = FamilyGuardApi.authenticate(email.trim(), password, create)
            val expires = result.optString("expiresIn", "3600").toLongOrNull() ?: 3600L
            getSharedPreferences("familyguard_family", MODE_PRIVATE).edit()
                .putString("idToken", result.getString("idToken"))
                .putString("refreshToken", result.getString("refreshToken"))
                .putString("uid", result.getString("localId"))
                .putLong("tokenExpiry", System.currentTimeMillis() + expires * 1000L).apply()
            "Firebase account " + (if (create) "created" else "signed in")
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
        if (BuildConfig.FAMILYGUARD_API_BASE_URL.isBlank() || BuildConfig.FIREBASE_WEB_API_KEY.isBlank()) {
            notice = "Add FAMILYGUARD_API_BASE_URL and FIREBASE_WEB_API_KEY to GitHub Secrets, then rebuild."
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
                "Member " + item.optString("uid").take(8) + ": %.5f, %.5f".format(item.optDouble("latitude"), item.optDouble("longitude"))
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

    private fun connectBilling() {
        billingClient.startConnection(object : BillingClientStateListener {
            override fun onBillingSetupFinished(result: BillingResult) {
                billingReady = result.responseCode == BillingClient.BillingResponseCode.OK
                billingMessage = if (billingReady) "Google Play Billing connected." else
                    "Google Play Billing unavailable: ${result.debugMessage}"
                if (billingReady) {
                    queryPremiumProduct()
                    restorePurchases()
                }
            }
            override fun onBillingServiceDisconnected() {
                billingReady = false
                billingMessage = "Google Play connection interrupted. Try again shortly."
            }
        })
    }

    private fun queryPremiumProduct() {
        val product = QueryProductDetailsParams.Product.newBuilder()
            .setProductId(PREMIUM_PRODUCT_ID)
            .setProductType(BillingClient.ProductType.SUBS)
            .build()
        val params = QueryProductDetailsParams.newBuilder().setProductList(listOf(product)).build()
        billingClient.queryProductDetailsAsync(params) { result, details ->
            if (result.responseCode == BillingClient.BillingResponseCode.OK) {
                productDetails = details.productDetailsList.firstOrNull()
                val monthlyOffer = productDetails?.subscriptionOfferDetails?.firstOrNull { it.basePlanId == PREMIUM_BASE_PLAN_ID }
                val recurringPhase = monthlyOffer?.pricingPhases?.pricingPhaseList?.lastOrNull()
                premiumPriceLabel = if (recurringPhase?.billingPeriod == "P1M") "${recurringPhase.formattedPrice} / month" else "Monthly price shown at Google Play checkout"
                billingMessage = when {
                    productDetails == null -> "Subscription product not found. Configure the product in Play Console."
                    monthlyOffer == null -> "Product found, but base plan $PREMIUM_BASE_PLAN_ID is unavailable in Play Console."
                    recurringPhase?.billingPeriod != "P1M" -> "Base plan $PREMIUM_BASE_PLAN_ID must use a one-month billing period."
                    else -> "Google Play monthly offer available: ${recurringPhase.formattedPrice}."
                }
            } else billingMessage = "Could not load subscription: ${result.debugMessage}"
        }
    }

    private fun launchPremiumPurchase() {
        if (!billingReady) {
            notice = "Google Play Billing is not connected. Install this app from a Play testing track and try again."
            return
        }
        val details = productDetails
        if (details == null) {
            queryPremiumProduct()
            notice = "The subscription is not available yet. Confirm that $PREMIUM_PRODUCT_ID is active in Play Console."
            return
        }
        val offer = details.subscriptionOfferDetails?.firstOrNull { it.basePlanId == PREMIUM_BASE_PLAN_ID }
        val offerToken = offer?.offerToken
        if (offerToken == null || offer?.pricingPhases?.pricingPhaseList?.lastOrNull()?.billingPeriod != "P1M") {
            notice = "The monthly-999 base plan is not available or is not configured for a one-month billing period. Configure product $PREMIUM_PRODUCT_ID with base plan $PREMIUM_BASE_PLAN_ID in Play Console."
            return
        }
        val productParams = BillingFlowParams.ProductDetailsParams.newBuilder()
            .setProductDetails(details)
            .setOfferToken(offerToken)
            .build()
        val params = BillingFlowParams.newBuilder()
            .setProductDetailsParamsList(listOf(productParams))
            .build()
        val result = billingClient.launchBillingFlow(this, params)
        if (result.responseCode != BillingClient.BillingResponseCode.OK) {
            notice = "Google Play could not start checkout: ${result.debugMessage}"
        }
    }

    private fun restorePurchases() {
        if (!billingReady) return
        val params = QueryPurchasesParams.newBuilder()
            .setProductType(BillingClient.ProductType.SUBS)
            .build()
        billingClient.queryPurchasesAsync(params) { result, purchases ->
            if (result.responseCode == BillingClient.BillingResponseCode.OK) {
                handlePurchases(result, purchases)
            } else billingMessage = "Restore failed: ${result.debugMessage}"
        }
    }

    private fun handlePurchases(result: BillingResult, purchases: List<Purchase>) {
        if (result.responseCode != BillingClient.BillingResponseCode.OK) {
            premium = false
            billingMessage = "Purchase update: ${result.debugMessage}"
            return
        }
        val matching = purchases.filter { it.products.contains(PREMIUM_PRODUCT_ID) }
        val purchased = matching.filter { it.purchaseState == Purchase.PurchaseState.PURCHASED }
        val pending = matching.any { it.purchaseState == Purchase.PurchaseState.PENDING }

        if (pending) {
            premium = false
            billingMessage = "Purchase pending. Premium stays unavailable until Google Play completes payment."
            return
        }

        if (purchased.isEmpty()) {
            premium = false
            billingMessage = "No active Premium purchase found for this Google Play account."
            refreshUsageReport()
            return
        }
        val unacknowledged = purchased.filter { !it.isAcknowledged }
        if (unacknowledged.isNotEmpty()) {
            premium = false
            billingMessage = "Purchase received. Acknowledging with Google Play before server verification."
            unacknowledged.forEach { purchase ->
                billingClient.acknowledgePurchase(
                    AcknowledgePurchaseParams.newBuilder().setPurchaseToken(purchase.purchaseToken).build()
                ) { ack ->
                    if (ack.responseCode == BillingClient.BillingResponseCode.OK) {
                        verifyPurchaseWithServer(purchase.purchaseToken)
                    } else {
                        premium = false
                        billingMessage = "Purchase acknowledgement failed: ${ack.debugMessage}"
                    }
                }
            }
        } else {
            verifyPurchaseWithServer(purchased.first().purchaseToken)
        }
    }

    private fun verifyPurchaseWithServer(purchaseToken: String) {
        val endpoint = BuildConfig.FAMILYGUARD_VERIFY_URL.trim()
        if (endpoint.isBlank()) {
            premium = false
            billingMessage = "Server verification is not configured. Premium remains locked until a verification URL is set."
            refreshUsageReport()
            return
        }
        premium = false
        billingMessage = "Verifying subscription securely with Google Play…"
        verificationExecutor.execute {
            var connection: HttpURLConnection? = null
            try {
                connection = (URL(endpoint.trimEnd('/') + "/verify-subscription").openConnection() as HttpURLConnection).apply {
                    requestMethod = "POST"
                    connectTimeout = 10000
                    readTimeout = 12000
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json")
                    setRequestProperty("Authorization", "Bearer " + FamilyGuardApi.refreshIdToken(this@MainActivity))
                }
                val body = JSONObject()
                    .put("packageName", PLAY_PACKAGE_NAME)
                    .put("productId", PREMIUM_PRODUCT_ID)
                    .put("basePlanId", PREMIUM_BASE_PLAN_ID)
                    .put("purchaseToken", purchaseToken)
                OutputStreamWriter(connection.outputStream, Charsets.UTF_8).use { it.write(body.toString()) }
                val code = connection.responseCode
                val stream = if (code in 200..299) connection.inputStream else connection.errorStream
                val responseText = stream?.bufferedReader()?.use { it.readText() }.orEmpty()
                val response = JSONObject(responseText)
                val verifiedActive = code in 200..299 &&
                    response.optBoolean("verified", false) &&
                    response.optBoolean("active", false) &&
                    response.optString("productId") == PREMIUM_PRODUCT_ID &&
                    response.optString("basePlanId") == PREMIUM_BASE_PLAN_ID
                runOnUiThread {
                    premium = verifiedActive
                    billingMessage = if (verifiedActive) {
                        "Premium subscription verified by the FamilyGuard server and Google Play."
                    } else {
                        "Server did not confirm an active subscription. Premium remains locked."
                    }
                    refreshUsageReport()
                }
            } catch (_: Exception) {
                runOnUiThread {
                    premium = false
                    billingMessage = "Server verification unavailable. Premium remains locked; try Restore later."
                    refreshUsageReport()
                }
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
            notice = "App Usage & Screen Time reports are a Premium feature. Subscribe through Google Play first."
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
private fun FamilyGuardDashboard(
    premium: Boolean,
    billingReady: Boolean,
    billingMessage: String,
    premiumPriceLabel: String,
    locationText: String,
    usageText: String,
    familyStatus: String,
    familyId: String,
    inviteCode: String,
    familyLocationsText: String,
    familyConsentEnabled: Boolean,
    onFamilyAuth: (String, String, Boolean) -> Unit,
    onCreateFamily: (String) -> Unit,
    onJoinFamily: (String) -> Unit,
    onFamilyConsent: (Boolean) -> Unit,
    onStartSharing: () -> Unit,
    onStopSharing: () -> Unit,
    onFetchFamilyLocations: () -> Unit,
    onLocation: () -> Unit,
    onUpgrade: () -> Unit,
    onRestore: () -> Unit,
    onUsage: () -> Unit,
    onSos: () -> Unit,
    onNotice: (String) -> Unit,
    permissionStatusText: String,
    onSetupPermissions: () -> Unit,
    onPrivacy: () -> Unit,
    onTerms: () -> Unit,
    onDeleteData: () -> Unit
) {
    Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        Column(
            modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            Text("FamilyGuard: SafeCircle", style = MaterialTheme.typography.headlineLarge)
            Text("Parent Dashboard", style = MaterialTheme.typography.titleMedium)
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer)
            ) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(if (premium) "ACTIVE PLAN: PREMIUM" else "ACTIVE PLAN: FREE", style = MaterialTheme.typography.titleLarge)
                    Text(if (premium) "Premium active • $premiumPriceLabel" else "₹0 • Basic safety features")
                    Text(if (premium) "App usage reports unlocked on this device." else "Upgrade to unlock app usage and screen-time reports.")
                    if (!premium) Button(onClick = onUpgrade, modifier = Modifier.fillMaxWidth()) {
                        Text("Upgrade • $premiumPriceLabel")
                    }
                    OutlinedButton(onClick = onRestore, modifier = Modifier.fillMaxWidth()) {
                        Text("Restore purchases")
                    }
                    Text(billingMessage, style = MaterialTheme.typography.bodySmall)
                    Text(if (billingReady) "Play Billing connected" else "Play Billing not connected", style = MaterialTheme.typography.labelSmall)
                }
            }
            DashboardCard("Live Location", locationText) {
                Button(onClick = onLocation, modifier = Modifier.fillMaxWidth()) { Text("Request / refresh location") }
            }
            FamilySharingCard(familyStatus, familyId, inviteCode, familyLocationsText, familyConsentEnabled, premium,
                onFamilyAuth, onCreateFamily, onJoinFamily, onFamilyConsent,
                onStartSharing, onStopSharing, onFetchFamilyLocations)
            DashboardCard("SOS Safety Alert", "Prepare a message in the device SMS app. Nothing is sent automatically; you review and send it yourself.") {
                Button(onClick = onSos, modifier = Modifier.fillMaxWidth()) { Text("Prepare SOS message") }
            }
            DashboardCard("App Usage & Screen Time", if (premium) usageText else "Premium feature • ₹999/month") {
                if (premium) Button(onClick = onUsage, modifier = Modifier.fillMaxWidth()) {
                    Text("Open Usage Access settings")
                } else OutlinedButton(onClick = onUpgrade, modifier = Modifier.fillMaxWidth()) {
                    Text("Unlock Premium reports")
                }
            }
            DashboardCard("Permission setup", "Each sensitive permission is disclosed before its Android prompt. Background location and battery exemption are not requested because this version has no background tracking service.") {
                Text(permissionStatusText, style = MaterialTheme.typography.bodyMedium)
                Button(onClick = onSetupPermissions, modifier = Modifier.fillMaxWidth()) { Text("One-Click Setup All Permissions") }
                OutlinedButton(onClick = onSetupPermissions, modifier = Modifier.fillMaxWidth()) { Text("Retry Missing Permissions") }
            }
            DashboardCard("About & Settings", "Review the policies or clear local FamilyGuard settings. This version does not create a cloud account.") {
                OutlinedButton(onClick = onPrivacy, modifier = Modifier.fillMaxWidth()) { Text("Privacy Policy") }
                OutlinedButton(onClick = onTerms, modifier = Modifier.fillMaxWidth()) { Text("Terms of Service") }
                OutlinedButton(onClick = onDeleteData, modifier = Modifier.fillMaxWidth()) { Text("Delete Local App Data") }
            }
            DashboardCard("Privacy & consent", "Location is sent to the family server only after explicit opt-in, and only while the visible foreground sharing service is running. Revoking consent stops local sharing and removes the member's last saved location. Usage reports are processed on this device.") {
                Text("Monitoring is visible and user-controlled. The device user can revoke permissions in Android Settings at any time.", style = MaterialTheme.typography.bodySmall)
            }
            Text("FamilyGuard does not record calls, capture screens in the background, read private messages, or hide monitoring.", style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun DashboardCard(title: String, description: String, content: @Composable () -> Unit) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(title, style = MaterialTheme.typography.titleLarge)
            Text(description, style = MaterialTheme.typography.bodyMedium)
            content()
        }
    }
}


@Composable
private fun FamilySharingCard(
    status: String, familyId: String, inviteCode: String, locations: String, consentInitiallyEnabled: Boolean, premium: Boolean,
    onAuth: (String, String, Boolean) -> Unit, onCreate: (String) -> Unit, onJoin: (String) -> Unit,
    onConsent: (Boolean) -> Unit, onStart: () -> Unit, onStop: () -> Unit, onFetch: () -> Unit
) {
    var email by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var familyName by remember { mutableStateOf("My Family") }
    var code by remember { mutableStateOf("") }
    var consent by remember(familyId) { mutableStateOf(consentInitiallyEnabled) }
    DashboardCard("Family Location Sharing", "Sign in, create or join a family, then explicitly opt in. Sharing is visible and revocable.") {
        OutlinedTextField(email, { email = it }, label = { Text("Firebase email") }, modifier = Modifier.fillMaxWidth())
        OutlinedTextField(password, { password = it }, label = { Text("Password") }, visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = { onAuth(email, password, false) }, enabled = email.isNotBlank() && password.length >= 6) { Text("Sign in") }
            OutlinedButton(onClick = { onAuth(email, password, true) }, enabled = email.isNotBlank() && password.length >= 6) { Text("Create account") }
        }
        Text(status, style = MaterialTheme.typography.bodySmall)
        OutlinedTextField(familyName, { familyName = it }, label = { Text("Family name") }, modifier = Modifier.fillMaxWidth())
        Button(onClick = { onCreate(familyName) }, enabled = premium, modifier = Modifier.fillMaxWidth()) { Text("Create family (Premium)") }
        if (inviteCode.isNotBlank()) Text("Invite code: " + inviteCode, style = MaterialTheme.typography.titleLarge)
        OutlinedTextField(code, { code = it.filter(Char::isDigit).take(6) }, label = { Text("6-digit invite code") }, modifier = Modifier.fillMaxWidth())
        OutlinedButton(onClick = { onJoin(code) }, enabled = code.length == 6, modifier = Modifier.fillMaxWidth()) { Text("Join family") }
        if (familyId.isNotBlank()) Text("Family ID: " + familyId, style = MaterialTheme.typography.bodySmall)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Switch(checked = consent, onCheckedChange = { consent = it; onConsent(it) })
            Text("I explicitly consent to share my location with this family.")
        }
        Button(onClick = onStart, enabled = consent && familyId.isNotBlank(), modifier = Modifier.fillMaxWidth()) { Text("Start live sharing") }
        OutlinedButton(onClick = onStop, modifier = Modifier.fillMaxWidth()) { Text("Stop sharing and revoke consent") }
        OutlinedButton(onClick = onFetch, modifier = Modifier.fillMaxWidth()) { Text("Refresh family locations") }
        Text(locations, style = MaterialTheme.typography.bodySmall)
    }
}
