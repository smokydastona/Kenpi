package com.kenpi.companion

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
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.kenpi.companion.network.GatewayCapability
import com.kenpi.companion.ui.GatewayIntent
import com.kenpi.companion.ui.GatewayStore
import com.kenpi.companion.ui.GatewayUiState

class MainActivity : ComponentActivity() {
    private val store = GatewayStore()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background,
                ) {
                    GatewayScreen(store)
                }
            }
        }
    }

    override fun onStop() {
        store.accept(GatewayIntent.StopMonitoring)
        super.onStop()
    }

    override fun onDestroy() {
        store.close()
        super.onDestroy()
    }
}

@Composable
private fun GatewayScreen(store: GatewayStore) {
    val state by store.state.collectAsState()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(
            text = "Kenpi Companion",
            style = MaterialTheme.typography.headlineMedium,
            fontWeight = FontWeight.Bold,
        )
        Text(
            text = "Read-only gateway status. Camera controls and WebSocket updates are not available.",
            style = MaterialTheme.typography.bodyMedium,
        )

        OutlinedTextField(
            value = state.baseUrl,
            onValueChange = { store.accept(GatewayIntent.UpdateBaseUrl(it)) },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("Gateway HTTPS base URL") },
            placeholder = { Text("https://gateway.example/") },
            singleLine = true,
            enabled = !state.isMonitoring,
        )
        Row(
            modifier = Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            if (state.isMonitoring) {
                Button(
                    onClick = { store.accept(GatewayIntent.StopMonitoring) },
                    modifier = Modifier.weight(1f),
                ) {
                    Text("Stop monitoring")
                }
            } else {
                Button(
                    onClick = { store.accept(GatewayIntent.StartMonitoring) },
                    modifier = Modifier.weight(1f),
                ) {
                    Text("Start monitoring")
                }
            }
            TextButton(
                onClick = { store.accept(GatewayIntent.Refresh) },
                enabled = !state.isRefreshing,
            ) {
                Text("Refresh")
            }
        }

        if (state.isRefreshing) {
            CircularProgressIndicator()
        }
        state.error?.let { error ->
            Card(modifier = Modifier.fillMaxWidth()) {
                Text(
                    text = error,
                    color = MaterialTheme.colorScheme.error,
                    modifier = Modifier.padding(16.dp),
                )
            }
        }
        GatewayStatus(state)
    }
}

@Composable
private fun GatewayStatus(state: GatewayUiState) {
    val snapshot = state.snapshot
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text("Gateway", style = MaterialTheme.typography.titleLarge)
            Text(if (snapshot == null) "Status: Not connected" else "Status: ${snapshot.applicationStatus}")
            Text(if (state.isMonitoring) "Monitoring: active (2 s polling)" else "Monitoring: stopped")
            Text("Realtime updates: unavailable (HTTP polling)")
            state.lastUpdated?.let { Text("Last updated: $it") }
            snapshot?.healthDetails?.forEach { (label, value) ->
                Text("$label: $value")
            }
        }
    }

    Spacer(modifier = Modifier.height(2.dp))
    Text("Declared capabilities", style = MaterialTheme.typography.titleLarge)
    if (snapshot == null || snapshot.capabilities.isEmpty()) {
        Text("Capability data will appear after a successful refresh.")
    } else {
        snapshot.capabilities.forEach { capability ->
            CapabilityCard(capability)
        }
    }
}

@Composable
private fun CapabilityCard(capability: GatewayCapability) {
    Card(modifier = Modifier.fillMaxWidth()) {
        Column(
            modifier = Modifier.padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text(capability.name, style = MaterialTheme.typography.titleMedium)
            Text("State: ${capability.state} · Evidence: ${capability.evidence}")
            Text(capability.detail, style = MaterialTheme.typography.bodySmall)
        }
    }
}
