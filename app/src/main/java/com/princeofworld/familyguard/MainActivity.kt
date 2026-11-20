package com.princeofworld.familyguard

import android.Manifest
import android.content.Context
import android.content.Intent
import android.location.Location
import android.location.LocationManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.ContactsContract
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
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.preferencesDataStore
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch

private val Context.familyGuardStore by preferencesDataStore(name = "family_guard_local")
private val premiumDemoKey = booleanPreferencesKey("premium_demo_unlocked")

class MainActivity : ComponentActivity() {
    private var locationText by mutableStateOf("Location has not been requested.")
    private var contactText by mutableStateOf("No contact selected.")
    private var notice by mutableStateOf<String?>(null)

    private val contactPicker = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val uri: Uri? = result.data?.data
        if (result.resultCode == RESULT_OK && uri != null) {
            val name = contentResolver.query(
                uri,
                arrayOf(ContactsContract.Contacts.DISPLAY_NAME),
                null,
                null,
                null
            )?.use { cursor ->
                if (cursor.moveToFirst()) cursor.getString(0) else null
            }
            contactText = name?.let { "Selected contact: $it" } ?: "A contact was selected."
        } else {
            contactText = "Contact selection cancelled."
        }
    }

    private val locationPermission = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { grants ->
        val allowed = grants[Manifest.permission.ACCESS_FINE_LOCATION] == true ||
            grants[Manifest.permission.ACCESS_COARSE_LOCATION] == true
        if (allowed) readCurrentLocation() else locationText = "Location permission was not granted."
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                val premium by familyGuardStore.data
                    .map { preferences -> preferences[premiumDemoKey] ?: false }
                    .collectAsState(initial = false)
                FamilyGuardApp(
                    premiumDemo = premium,
                    locationText = locationText,
                    contactText = contactText,
                    onChooseContact = {
                        contactPicker.launch(Intent(Intent.ACTION_PICK, ContactsContract.Contacts.CONTENT_URI))
                    },
                    onRequestLocation = {
                        locationPermission.launch(
                            arrayOf(
                                Manifest.permission.ACCESS_FINE_LOCATION,
                                Manifest.permission.ACCESS_COARSE_LOCATION
                            )
                        )
                    },
                    onComposeSms = {
                        val intent = Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:"))
                            .putExtra("sms_body", "I am sharing this message with your permission.")
                        if (intent.resolveActivity(packageManager) != null) startActivity(intent)
                        else notice = "No SMS app is available on this device."
                    },
                    onCamera = {
                        val intent = Intent("android.media.action.IMAGE_CAPTURE")
                        if (intent.resolveActivity(packageManager) != null) startActivity(intent)
                        else notice = "No camera app is available."
                    },
                    onUnlockDemo = {
                        lifecycleScope.launch {
                            familyGuardStore.edit { it[premiumDemoKey] = true }
                        }
                    },
                    onResetDemo = {
                        lifecycleScope.launch {
                            familyGuardStore.edit { it[premiumDemoKey] = false }
                        }
                    },
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

    @Suppress("MissingPermission")
    private fun readCurrentLocation() {
        val manager = getSystemService(Context.LOCATION_SERVICE) as LocationManager
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                manager.getCurrentLocation(LocationManager.GPS_PROVIDER, null, mainExecutor) { location: Location? ->
                    locationText = location?.let {
                        "Current device location: %.5f, %.5f".format(it.latitude, it.longitude)
                    } ?: "No GPS fix is available. Try again outdoors with Location enabled."
                }
            } else {
                val location = manager.getLastKnownLocation(LocationManager.GPS_PROVIDER)
                    ?: manager.getLastKnownLocation(LocationManager.NETWORK_PROVIDER)
                locationText = location?.let {
                    "Last known device location: %.5f, %.5f".format(it.latitude, it.longitude)
                } ?: "No recent location is available. Try again with Location enabled."
            }
        } catch (_: SecurityException) {
            locationText = "Location permission is required."
        } catch (_: Exception) {
            locationText = "Location is currently unavailable on this device."
        }
    }
}

@Composable
private fun FamilyGuardApp(
    premiumDemo: Boolean,
    locationText: String,
    contactText: String,
    onChooseContact: () -> Unit,
    onRequestLocation: () -> Unit,
    onComposeSms: () -> Unit,
    onCamera: () -> Unit,
    onUnlockDemo: () -> Unit,
    onResetDemo: () -> Unit,
    onNotice: (String) -> Unit
) {
    var dialogTitle by androidx.compose.runtime.remember { mutableStateOf<String?>(null) }
    var dialogMessage by androidx.compose.runtime.remember { mutableStateOf("") }

    fun showInfo(title: String, message: String) {
        dialogTitle = title
        dialogMessage = message
    }

    Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        Column(
            modifier = Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp)
        ) {
            Text("FamilyGuard", style = MaterialTheme.typography.headlineLarge)
            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.primaryContainer),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(Modifier.padding(16.dp)) {
                    Text(if (premiumDemo) "Active Plan: Premium (Demo)" else "Active Plan: Free Plan", style = MaterialTheme.typography.titleMedium)
                    Text(if (premiumDemo) "Local sandbox unlock • no payment taken" else "Free plan • local-only mode")
                }
            }

            Text("Step 3 • Family tools", style = MaterialTheme.typography.titleLarge)
            FeatureCard(
                title = "SMS",
                description = "Open your SMS app and compose a message yourself. FamilyGuard does not read your inbox.",
                buttonText = "Compose SMS",
                onClick = onComposeSms
            )
            FeatureCard(
                title = "Trusted Contacts",
                description = contactText,
                buttonText = "Choose a contact",
                onClick = onChooseContact
            )
            FeatureCard(
                title = "Current Location",
                description = locationText,
                buttonText = "Request location permission",
                onClick = onRequestLocation
            )

            Text("Step 4 • Premium", style = MaterialTheme.typography.titleLarge)
            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Premium • ₹999 / month", style = MaterialTheme.typography.titleLarge)
                    Text("Sandbox only: unlock is stored on this device. This is not a real payment, subscription, or receipt.")
                    if (!premiumDemo) {
                        Button(onClick = {
                            showInfo(
                                "Sandbox demo unlock",
                                "This will enable the local Premium demo state without charging money. It is not a real ₹999 payment."
                            )
                        }, modifier = Modifier.fillMaxWidth()) { Text("Unlock Premium Demo") }
                        if (dialogTitle == "Sandbox demo unlock") {
                            TextButton(onClick = {
                                onUnlockDemo()
                                dialogTitle = null
                            }) { Text("Confirm demo unlock") }
                        }
                    } else {
                        Text("Premium demo is unlocked on this device.")
                        OutlinedButton(onClick = onResetDemo, modifier = Modifier.fillMaxWidth()) {
                            Text("Reset demo status")
                        }
                    }
                }
            }

            Text("Step 5 • User-controlled media", style = MaterialTheme.typography.titleLarge)
            FeatureCard(
                title = "Camera self-capture",
                description = "Opens the Android camera app. You control capture and can cancel at any time.",
                buttonText = "Open camera",
                onClick = onCamera
            )
            FeatureCard(
                title = "Screen recording",
                description = "Use Android's built-in Screen record control in Quick Settings. The operating system displays its own recording indicator and stop control. FamilyGuard does not capture the screen in the background.",
                buttonText = "How to start safely",
                onClick = {
                    showInfo(
                        "Android screen recording",
                        "Open Quick Settings, tap Screen record, review the Android consent prompt, and start only when ready. The system shows a recording indicator and lets you stop. FamilyGuard does not start or hide a recording session."
                    )
                }
            )
            FeatureCard(
                title = "Call recording",
                description = "Not provided. FamilyGuard does not intercept, record, or monitor calls.",
                buttonText = "Privacy information",
                onClick = {
                    showInfo(
                        "Call privacy",
                        "Call recording is not included. Android and local laws impose restrictions, and FamilyGuard will not secretly record calls."
                    )
                }
            )

            Text("Step 6 • Local data & backend-ready design", style = MaterialTheme.typography.titleLarge)
            Text("Premium demo status is saved locally with Android DataStore. No Firebase project, API key, or google-services.json is required. Cloud sync and real payment verification are not connected.")
            Text("Privacy: no SMS inbox permission, no address-book scanning, no hidden location tracking, and no background recording.", style = MaterialTheme.typography.bodySmall)
        }
    }

    if (dialogTitle != null && dialogTitle != "Sandbox demo unlock") {
        AlertDialog(
            onDismissRequest = { dialogTitle = null },
            title = { Text(dialogTitle.orEmpty()) },
            text = { Text(dialogMessage) },
            confirmButton = { TextButton(onClick = { dialogTitle = null }) { Text("OK") } }
        )
    }
}

@Composable
private fun FeatureCard(
    title: String,
    description: String,
    buttonText: String,
    onClick: () -> Unit
) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(title, style = MaterialTheme.typography.titleLarge)
            Text(description, style = MaterialTheme.typography.bodyMedium)
            Button(onClick = onClick, modifier = Modifier.fillMaxWidth()) { Text(buttonText) }
        }
    }
}
