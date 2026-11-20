package com.princeofworld.familyguard

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
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
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                FamilyGuardApp()
            }
        }
    }
}

private enum class Screen { Welcome, Dashboard }

@Composable
private fun FamilyGuardApp() {
    var screen by remember { mutableStateOf(Screen.Welcome) }
    var dialogTitle by remember { mutableStateOf<String?>(null) }
    var dialogMessage by remember { mutableStateOf("") }

    fun showInfo(title: String, message: String) {
        dialogTitle = title
        dialogMessage = message
    }

    Surface(
        modifier = Modifier.fillMaxSize(),
        color = MaterialTheme.colorScheme.background
    ) {
        if (screen == Screen.Welcome) {
            Column(
                modifier = Modifier.fillMaxSize().padding(24.dp),
                verticalArrangement = Arrangement.Center
            ) {
                Text("FamilyGuard", style = MaterialTheme.typography.headlineLarge)
                Text(
                    "Family safety starts with trust and consent.",
                    modifier = Modifier.padding(top = 12.dp),
                    style = MaterialTheme.typography.bodyLarge
                )
                Button(
                    onClick = { screen = Screen.Dashboard },
                    modifier = Modifier.padding(top = 24.dp).fillMaxWidth()
                ) {
                    Text("Get started")
                }
            }
        } else {
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
                        Text("Active Plan: Free Plan", style = MaterialTheme.typography.titleMedium)
                        Text("हमेशा मुफ्त", style = MaterialTheme.typography.bodyMedium)
                    }
                }
                Text("Free Plan", style = MaterialTheme.typography.titleLarge)
                FeatureCard(
                    title = "SMS",
                    description = "Consent-based safety setup",
                    buttonText = "Open SMS options",
                    onClick = {
                        showInfo(
                            "SMS",
                            "SMS access is not enabled in this version. FamilyGuard will not read or upload private messages. Any future sharing must be explicitly selected and permission-based."
                        )
                    }
                )
                FeatureCard(
                    title = "Contacts",
                    description = "Choose trusted family contacts",
                    buttonText = "Manage contacts",
                    onClick = {
                        showInfo(
                            "Contacts",
                            "Contact access is not enabled yet. You can plan trusted contacts here; no address book is read or copied by this version."
                        )
                    }
                )
                FeatureCard(
                    title = "Live Location",
                    description = "Share location only with consent",
                    buttonText = "Location sharing",
                    onClick = {
                        showInfo(
                            "Live Location",
                            "Location sharing is not active yet. A future version will request Android location permission and show a clear, ongoing sharing indicator. Sharing can be stopped by the device user."
                        )
                    }
                )
                Spacer(Modifier.height(2.dp))
                Card(
                    colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.secondaryContainer),
                    modifier = Modifier.fillMaxWidth()
                ) {
                    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text("Upgrade to Premium", style = MaterialTheme.typography.titleLarge)
                        Text("₹999 / माह", style = MaterialTheme.typography.headlineSmall)
                        Text("Premium feature preview: consent-based recording only where legally permitted, with a visible privacy indicator and clear start/stop controls.")
                        OutlinedButton(
                            onClick = {
                                showInfo(
                                    "Premium plan",
                                    "Premium checkout is not connected yet. No payment will be taken. Recording is not implemented; any future recording must require clear consent, remain visible, and follow applicable law and platform rules."
                                )
                            },
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Text("View Premium details")
                        }
                    }
                }
                Text(
                    "Privacy first • No hidden monitoring • You stay in control",
                    style = MaterialTheme.typography.bodySmall
                )
            }
        }
    }

    if (dialogTitle != null) {
        AlertDialog(
            onDismissRequest = { dialogTitle = null },
            title = { Text(dialogTitle.orEmpty()) },
            text = { Text(dialogMessage) },
            confirmButton = {
                TextButton(onClick = { dialogTitle = null }) { Text("OK") }
            }
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
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp)
        ) {
            Text(title, style = MaterialTheme.typography.titleLarge)
            Text(description, style = MaterialTheme.typography.bodyMedium)
            Button(onClick = onClick, modifier = Modifier.fillMaxWidth()) {
                Text(buttonText)
            }
        }
    }
}
