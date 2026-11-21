package com.princeofworld.familyguard

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
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
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

private data class PermissionStatus(
    val label: String,
    val permission: String,
    val granted: Boolean
)

class MainActivity : ComponentActivity() {
    private var locationText by mutableStateOf("Location has not been requested.")
    private var contactText by mutableStateOf("No contact selected.")
    private var photoText by mutableStateOf("No photo selected.")
    private var notice by mutableStateOf<String?>(null)
    private var permissionStatuses by mutableStateOf<List<PermissionStatus>>(emptyList())

    private fun requiredRuntimePermissions(): Array<String> {
        val permissions = mutableListOf(
            Manifest.permission.ACCESS_FINE_LOCATION,
            Manifest.permission.ACCESS_COARSE_LOCATION
        )
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            permissions += Manifest.permission.POST_NOTIFICATIONS
        }
        return permissions.toTypedArray()
    }

    private fun refreshPermissionStatuses() {
        val rows = mutableListOf(
            PermissionStatus(
                "Precise location",
                Manifest.permission.ACCESS_FINE_LOCATION,
                checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
            ),
            PermissionStatus(
                "Approximate location",
                Manifest.permission.ACCESS_COARSE_LOCATION,
                checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED
            )
        )
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            rows += PermissionStatus(
                "Notifications",
                Manifest.permission.POST_NOTIFICATIONS,
                checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
            )
        }
        permissionStatuses = rows
    }

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { grants ->
        refreshPermissionStatuses()
        if (grants[Manifest.permission.ACCESS_FINE_LOCATION] == true ||
            grants[Manifest.permission.ACCESS_COARSE_LOCATION] == true
        ) {
            readCurrentLocation()
        } else {
            locationText = "Location permission is still pending. You can allow it later in Android Settings."
        }
        val denied = grants.filterValues { !it }.keys
        if (denied.isNotEmpty()) {
            notice = "Some permissions were not granted. FamilyGuard will keep those features unavailable; you can change access in Android Settings."
        }
    }

    private val contactPicker = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
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

    private val photoPicker = registerForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        photoText = if (uri != null) {
            "A photo was selected through Android's system picker."
        } else {
            "Photo selection cancelled."
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        refreshPermissionStatuses()
        setContent {
            MaterialTheme {
                FamilyGuardApp(
                    locationText = locationText,
                    contactText = contactText,
                    photoText = photoText,
                    permissionStatuses = permissionStatuses,
                    onAllowAllPermissions = {
                        permissionLauncher.launch(requiredRuntimePermissions())
                    },
                    onChooseContact = {
                        contactPicker.launch(Intent(Intent.ACTION_PICK, ContactsContract.Contacts.CONTENT_URI))
                    },
                    onChoosePhoto = { photoPicker.launch("image/*") },
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
        refreshPermissionStatuses()
    }

    @Suppress("MissingPermission")
    private fun readCurrentLocation() {
        val manager = getSystemService(Context.LOCATION_SERVICE) as LocationManager
        val hasFine = checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
        val provider = if (hasFine) LocationManager.GPS_PROVIDER else LocationManager.NETWORK_PROVIDER
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                manager.getCurrentLocation(provider, null, mainExecutor) { location: Location? ->
                    locationText = location?.let {
                        "Current device location: %.5f, %.5f".format(it.latitude, it.longitude)
                    } ?: "No location fix is available right now. Try again later."
                }
            } else {
                val location = manager.getLastKnownLocation(provider)
                locationText = location?.let {
                    "Last known device location: %.5f, %.5f".format(it.latitude, it.longitude)
                } ?: "No recent location is available. Try again later."
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
    locationText: String,
    contactText: String,
    photoText: String,
    permissionStatuses: List<PermissionStatus>,
    onAllowAllPermissions: () -> Unit,
    onChooseContact: () -> Unit,
    onChoosePhoto: () -> Unit,
    onComposeSms: () -> Unit,
    onCamera: () -> Unit,
    onNotice: (String) -> Unit
) {
    var dialogTitle by androidx.compose.runtime.remember { mutableStateOf<String?>(null) }
    var dialogMessage by androidx.compose.runtime.remember { mutableStateOf("") }
    var showPhotoConsent by androidx.compose.runtime.remember { mutableStateOf(false) }

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
                    Text("Active Plan: Free", style = MaterialTheme.typography.titleMedium)
                    Text("₹0 • No subscription required")
                }
            }

            Text("Step 3 • Permissions & Privacy", style = MaterialTheme.typography.titleLarge)
            Text(
                "Choose Allow All Permissions to review the required Android system prompts. You remain in control and can deny any permission.",
                style = MaterialTheme.typography.bodyMedium
            )
            Button(onClick = onAllowAllPermissions, modifier = Modifier.fillMaxWidth()) {
                Text("Allow All Permissions")
            }
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("Permission status", style = MaterialTheme.typography.titleMedium)
                    permissionStatuses.forEach { status ->
                        Row(modifier = Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text(status.label, modifier = Modifier.weight(1f))
                            Text(if (status.granted) "Granted ✅" else "Pending ⚠️")
                        }
                    }
                    Text(
                        "Photos: Android System Photo Picker is used instead of broad gallery permission.",
                        style = MaterialTheme.typography.bodySmall
                    )
                }
            }

            Text("Step 4 • Family tools", style = MaterialTheme.typography.titleLarge)
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
                buttonText = "Update location",
                onClick = onAllowAllPermissions
            )
            FeatureCard(
                title = "Photos",
                description = "$photoText\nPhotos are chosen only through Android's system photo picker; FamilyGuard does not scan your gallery.",
                buttonText = "Choose a photo",
                onClick = { showPhotoConsent = true }
            )

            Text("Step 5 • Choose your plan", style = MaterialTheme.typography.titleLarge)
            Card(modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("FREE • ₹0", style = MaterialTheme.typography.titleLarge)
                    Text("Available now")
                    Text("• Basic Family Safety Dashboard")
                    Text("• Trusted Contacts selection")
                    Text("• User-initiated SMS composition")
                    Text("• Permission-based location tools")
                    Text("• Individual photo selection")
                    Text("Your current plan. No payment or subscription is required.")
                }
            }
            Card(
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer),
                modifier = Modifier.fillMaxWidth()
            ) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Text("PREMIUM • ₹999 / month", style = MaterialTheme.typography.titleLarge)
                    Text("Planned subscription features")
                    Text("• All Free plan features")
                    Text("• Premium Family Safety Dashboard")
                    Text("• Additional family-management tools as released")
                    Text("• Subscription status and expiry details")
                    Text("• Restore eligible purchases")
                    Text("Status: Not subscribed")
                    Text("Premium access will remain locked until Google Play confirms a real purchase and the purchase is verified.")
                    OutlinedButton(onClick = {
                        showInfo(
                            "Premium subscription coming soon",
                            "The ₹999/month plan is displayed for pricing transparency. Google Play Billing and secure purchase verification are not connected yet, so no purchase can be made and Premium features remain locked."
                        )
                    }, modifier = Modifier.fillMaxWidth()) {
                        Text("Premium subscription coming soon")
                    }
                }
            }

            Text("Step 6 • User-controlled media", style = MaterialTheme.typography.titleLarge)
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

            Text("Step 7 • Subscription security", style = MaterialTheme.typography.titleLarge)
            Text("No local Premium unlock is available. Subscription status will only be granted after a verified Google Play purchase; billing and server-side verification are a later implementation step.")
            Text(
                "Privacy: no SMS inbox permission, no address-book scanning, no hidden location tracking, and no background recording.",
                style = MaterialTheme.typography.bodySmall
            )
        }
    }

    if (showPhotoConsent) {
        AlertDialog(
            onDismissRequest = { showPhotoConsent = false },
            title = { Text("Choose a photo?") },
            text = {
                Text("Android's system photo picker will let you select individual images. FamilyGuard will not request broad gallery access or scan other photos.")
            },
            confirmButton = {
                TextButton(onClick = {
                    showPhotoConsent = false
                    onChoosePhoto()
                }) { Text("Choose photo") }
            },
            dismissButton = {
                TextButton(onClick = { showPhotoConsent = false }) { Text("Cancel") }
            }
        )
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
