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
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.android.billingclient.api.BillingClient
import com.android.billingclient.api.BillingClientStateListener
import com.android.billingclient.api.BillingFlowParams
import com.android.billingclient.api.BillingResult
import com.android.billingclient.api.ConsumeParams
import com.android.billingclient.api.ProductDetails
import com.android.billingclient.api.Purchase
import com.android.billingclient.api.QueryProductDetailsParams
import com.android.billingclient.api.QueryPurchasesParams
import com.android.billingclient.api.AcknowledgePurchaseParams
import java.text.DateFormat
import java.util.Date

private const val PREMIUM_PRODUCT_ID = "familyguard_premium_999_monthly"

class MainActivity : ComponentActivity() {
    private var locationText by mutableStateOf("Location sharing is off. Choose Update location to request access.")
    private var usageText by mutableStateOf("Usage report is available to Premium subscribers after enabling Usage Access.")
    private var premium by mutableStateOf(false)
    private var billingReady by mutableStateOf(false)
    private var billingMessage by mutableStateOf("Connecting to Google Play…")
    private var productDetails: ProductDetails? = null
    private var safetyAlertsEnabled by mutableStateOf(false)
    private var notice by mutableStateOf<String?>(null)

    private val locationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { grants ->
        if (grants[Manifest.permission.ACCESS_FINE_LOCATION] == true ||
            grants[Manifest.permission.ACCESS_COARSE_LOCATION] == true
        ) readCurrentLocation()
        else locationText = "Location permission was not granted. Location remains off."
    }

    private val usageSettingsLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) {
        refreshUsageReport()
    }

    private val billingClient by lazy {
        BillingClient.newBuilder(this)
            .setListener { result, purchases -> handlePurchases(result, purchases.orEmpty()) }
            .enablePendingPurchases()
            .build()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        connectBilling()
        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) {
                FamilyGuardDashboard(
                    premium = premium,
                    billingReady = billingReady,
                    billingMessage = billingMessage,
                    locationText = locationText,
                    usageText = usageText,
                    safetyAlertsEnabled = safetyAlertsEnabled,
                    onLocation = {
                        locationPermissionLauncher.launch(arrayOf(
                            Manifest.permission.ACCESS_FINE_LOCATION,
                            Manifest.permission.ACCESS_COARSE_LOCATION
                        ))
                    },
                    onSafetyToggle = { safetyAlertsEnabled = it },
                    onUpgrade = { launchPremiumPurchase() },
                    onRestore = { restorePurchases() },
                    onUsage = { openUsageAccessSettings() },
                    onSos = { sendSosAlert() },
                    onNotice = { notice = it }
                )
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
        refreshUsageReport()
    }

    override fun onDestroy() {
        if (::billingClient.isInitialized) billingClient.endConnection()
        super.onDestroy()
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
                productDetails = details.firstOrNull()
                billingMessage = if (productDetails == null) {
                    "Subscription product not found. Configure the product in Play Console."
                } else "₹999/month subscription is available through Google Play."
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
        val offerToken = details.subscriptionOfferDetails?.firstOrNull()?.offerToken
        if (offerToken == null) {
            notice = "No eligible monthly subscription offer was returned by Google Play."
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
            billingMessage = "Purchase update: ${result.debugMessage}"
            return
        }
        val matching = purchases.filter { it.products.contains(PREMIUM_PRODUCT_ID) }
        matching.filter { it.purchaseState == Purchase.PurchaseState.PURCHASED && !it.isAcknowledged }
            .forEach { purchase ->
                billingClient.acknowledgePurchase(
                    AcknowledgePurchaseParams.newBuilder()
                        .setPurchaseToken(purchase.purchaseToken)
                        .build()
                ) { ack ->
                    if (ack.responseCode == BillingClient.BillingResponseCode.OK) {
                        premium = true
                        billingMessage = "Premium purchase acknowledged by Google Play."
                    } else {
                        billingMessage = "Purchase acknowledgement pending: ${ack.debugMessage}"
                    }
                }
            }
        premium = matching.any {
            it.purchaseState == Purchase.PurchaseState.PURCHASED && it.isAcknowledged
        } || matching.any { it.purchaseState == Purchase.PurchaseState.PURCHASED && !it.isAcknowledged }
        if (matching.any { it.purchaseState == Purchase.PurchaseState.PENDING }) {
            billingMessage = "Purchase pending. Premium stays unavailable until Google Play completes payment."
        } else if (matching.isEmpty()) {
            premium = false
            billingMessage = "No active Premium purchase found for this Google Play account."
        }
        refreshUsageReport()
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
private fun FamilyGuardDashboard(
    premium: Boolean,
    billingReady: Boolean,
    billingMessage: String,
    locationText: String,
    usageText: String,
    safetyAlertsEnabled: Boolean,
    onLocation: () -> Unit,
    onSafetyToggle: (Boolean) -> Unit,
    onUpgrade: () -> Unit,
    onRestore: () -> Unit,
    onUsage: () -> Unit,
    onSos: () -> Unit,
    onNotice: (String) -> Unit
) {
    Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        Column(
            modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            Text("FamilyGuard", style = MaterialTheme.typography.headlineLarge)
            Text("Parent Dashboard", style = MaterialTheme.typography.titleMedium)
            Card(
                modifier = Modifier.fillMaxWidth(),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer)
            ) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    Text(if (premium) "ACTIVE PLAN: PREMIUM" else "ACTIVE PLAN: FREE", style = MaterialTheme.typography.titleLarge)
                    Text(if (premium) "₹999 / month" else "₹0 • Basic safety features")
                    Text(if (premium) "App usage reports unlocked on this device." else "Upgrade to unlock app usage and screen-time reports.")
                    if (!premium) Button(onClick = onUpgrade, modifier = Modifier.fillMaxWidth()) {
                        Text("Upgrade • ₹999/month")
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
            DashboardCard("SOS Safety Alert", "Send an SOS message by opening the device's SMS app. You review and send it yourself.") {
                Row(horizontalArrangement = Arrangement.SpaceBetween, modifier = Modifier.fillMaxWidth()) {
                    Text("Safety alert reminder")
                    Switch(checked = safetyAlertsEnabled, onCheckedChange = onSafetyToggle)
                }
                Button(onClick = onSos, modifier = Modifier.fillMaxWidth()) { Text("Prepare SOS message") }
            }
            DashboardCard("App Usage & Screen Time", if (premium) usageText else "Premium feature • ₹999/month") {
                if (premium) Button(onClick = onUsage, modifier = Modifier.fillMaxWidth()) {
                    Text("Open Usage Access settings")
                } else OutlinedButton(onClick = onUpgrade, modifier = Modifier.fillMaxWidth()) {
                    Text("Unlock Premium reports")
                }
            }
            DashboardCard("Privacy & consent", "Location is read only after the user grants Android runtime permission. Usage reports require Premium and a separate manual Usage Access grant in Android Settings. Data is displayed on this device; this sample does not silently upload activity or location.") {
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
