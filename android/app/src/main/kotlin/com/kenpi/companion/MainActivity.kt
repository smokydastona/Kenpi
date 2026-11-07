package com.kenpi.companion

import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import com.kenpi.gateway.LiveState
import com.kenpi.gateway.PairingProgress
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

class MainActivity : ComponentActivity() {
    private val model: AppModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { KenpiTheme { KenpiApp(model) } }
    }

    override fun onStart() { super.onStart(); model.foreground(true) }
    override fun onStop() { super.onStop(); model.foreground(false) }
}

@Composable
fun KenpiTheme(content: @Composable () -> Unit) {
    val dark = isSystemInDarkTheme()
    val context = LocalContext.current
    val scheme = when {
        Build.VERSION.SDK_INT >= 31 -> if (dark) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        dark -> darkColorScheme()
        else -> lightColorScheme()
    }
    MaterialTheme(colorScheme = scheme, content = content)
}

private enum class Tab(val label: String) { Dashboard("Dashboard"), Camera("Camera"), Vehicle("Vehicle"), Health("Health"), Assistant("Assistant"), More("More") }

@Composable
fun KenpiApp(model: AppModel) {
    val ui by model.ui.collectAsState()
    when (val screen = ui.screen) {
        Screen.Unpaired -> PairingScreen(ui, model)
        is Screen.Pairing -> WaitingScreen(screen, model)
        Screen.Paired -> PairedScreen(model)
    }
}

@Composable
private fun PairingScreen(ui: Ui, model: AppModel) {
    var manual by rememberSaveable { mutableStateOf("") }
    var name by rememberSaveable { mutableStateOf(Build.MODEL ?: "Android") }
    val scan = rememberLauncherForActivityResult(ScanContract()) { result ->
        result.contents?.let { model.pairFromUri(it, name) }
    }
    Scaffold { pad ->
        Column(Modifier.padding(pad).padding(20.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            Text("Pair with a Kenpi gateway", style = MaterialTheme.typography.headlineSmall)
            Text("On the Raspberry Pi run: kenpi-devices pair  -- then scan the QR code. An administrator must approve this device.")
            ui.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            OutlinedTextField(name, { name = it }, label = { Text("Device name") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
            Button(onClick = {
                scan.launch(ScanOptions().setDesiredBarcodeFormats(ScanOptions.QR_CODE).setPrompt("Scan the Kenpi pairing QR").setBeepEnabled(false))
            }, modifier = Modifier.fillMaxWidth()) { Text("Scan QR code") }
            OutlinedTextField(manual, { manual = it }, label = { Text("or paste kenpi://pair?d=…") }, modifier = Modifier.fillMaxWidth())
            OutlinedButton(onClick = { model.pairFromUri(manual, name) }, enabled = manual.isNotBlank(), modifier = Modifier.fillMaxWidth()) { Text("Pair") }
        }
    }
}

@Composable
private fun WaitingScreen(screen: Screen.Pairing, model: AppModel) {
    Scaffold { pad ->
        Column(Modifier.padding(pad).padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
            CircularProgressIndicator()
            Text(
                when (screen.progress) {
                    PairingProgress.AwaitingApproval -> "Waiting for the administrator to approve this device (kenpi-devices approve)."
                    else -> "Contacting gateway and verifying its certificate…"
                },
                style = MaterialTheme.typography.titleMedium,
            )
            OutlinedButton(onClick = model::cancelPairing) { Text("Cancel") }
        }
    }
}

@Composable
private fun PairedScreen(model: AppModel) {
    var tab by rememberSaveable { mutableStateOf(Tab.Dashboard) }
    val live by model.live.collectAsState()
    val ui by model.ui.collectAsState()
    Scaffold(bottomBar = {
        NavigationBar {
            Tab.values().forEach { t -> NavigationBarItem(selected = tab == t, onClick = { tab = t }, icon = {}, label = { Text(t.label) }) }
        }
    }) { pad ->
        Column(Modifier.padding(pad).padding(16.dp).fillMaxSize().verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            ConnectionBanner(live)
            val state = (live as? LiveState.Live)?.snapshot?.state ?: (live as? LiveState.Disconnected)?.lastKnown?.state
            when (tab) {
                Tab.Dashboard -> {
                    Section("Gateway", listOf("Address" to (ui.gateway ?: "-"), "Device" to (ui.deviceId ?: "-")))
                    (live as? LiveState.Live)?.let { Section("Stream", listOf("Last sequence" to it.lastSequence.toString(), "Resumed" to it.resumed.toString())) }
                }
                Tab.Camera -> Scope("Camera", state?.get("camera"), "Live preview, media browsing and recording control are unavailable: the A810 Lite protocol is not verified.")
                Tab.Vehicle -> Scope("Vehicle", state?.get("vehicle"), null)
                Tab.Health -> Scope("System health", state?.get("status"), null)
                Tab.Assistant -> AssistantTab(model, ui, live)
                Tab.More -> More(model, ui, state?.get("media"))
            }
        }
    }
}

@Composable
private fun ConnectionBanner(live: LiveState) {
    val (text, error) = when (live) {
        LiveState.Idle -> "Not connected" to false
        is LiveState.Connecting -> "Connecting (attempt ${live.attempt})…" to false
        is LiveState.Live -> (if (live.resumed) "Live, resumed" else "Live") to false
        is LiveState.Disconnected -> "Disconnected: ${live.reason}. Showing last known data. Retrying." to true
        is LiveState.RemoteDenied -> "Remote access is disabled for this device." to true
        LiveState.Revoked -> "Device revoked" to true
    }
    Card { Text(text, Modifier.padding(12.dp), color = if (error) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface) }
}

@Composable
private fun Section(title: String, rows: List<Pair<String, String>>) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            if (rows.isEmpty()) Text("No data")
            rows.forEach { (k, v) -> Row(horizontalArrangement = Arrangement.SpaceBetween, modifier = Modifier.fillMaxWidth()) { Text(k); Text(v) } }
        }
    }
}

private fun flatten(value: kotlinx.serialization.json.JsonElement?, prefix: String = ""): List<Pair<String, String>> = when (value) {
    null -> emptyList()
    is JsonObject -> value.flatMap { (k, v) -> flatten(v, if (prefix.isEmpty()) k else "$prefix.$k") }
    is JsonPrimitive -> listOf(prefix to value.content)
    else -> listOf(prefix to value.toString())
}

@Composable
private fun Scope(title: String, data: kotlinx.serialization.json.JsonElement?, unavailable: String?) {
    if (data == null) Section(title, listOf("State" to "Not provided by the gateway (scope not granted or not connected)"))
    else Section(title, flatten(data))
    unavailable?.let { Card { Text(it, Modifier.padding(12.dp)) } }
}

@Composable
private fun More(model: AppModel, ui: Ui, media: kotlinx.serialization.json.JsonElement?) {
    Scope("Media", media, "Browsing, download and playback are unavailable until the camera protocol is verified.")
    val wg = ui.wireGuard
    Section(
        "Remote connectivity (WireGuard)",
        if (wg == null) listOf("State" to (ui.wireGuardNote ?: "Checking…"))
        else listOf("Tunnel address" to wg.address, "Gateway" to wg.gatewayAddress, "Port" to wg.listenPort.toString()),
    )
    Section("This device", listOf("Device ID" to (ui.deviceId ?: "-"), "Key" to "Android Keystore, P-256, non-exportable"))
    Section("Diagnostics", ui.log.takeLast(15).map { "•" to it })
    OutlinedButton(onClick = model::reconnect, modifier = Modifier.fillMaxWidth()) { Text("Reconnect now") }
    OutlinedButton(onClick = model::unpair, modifier = Modifier.fillMaxWidth()) { Text("Forget this gateway on this device") }
    Text("Forgetting is local only. Ask the administrator to revoke the device on the gateway.", style = MaterialTheme.typography.bodySmall)
}

@Composable
private fun AssistantTab(model: AppModel, ui: Ui, live: LiveState) {
    var text by rememberSaveable { mutableStateOf("") }
    val reachable = com.kenpi.gateway.LocalHelper.assistantReachable(live)
    Card(Modifier.fillMaxWidth()) {
        Text(
            if (reachable) "Connected to Kenpi. Ask for setup help or troubleshooting. Actions are never run from the phone."
            else com.kenpi.gateway.LocalHelper.explain(true, live),
            Modifier.padding(12.dp),
        )
    }
    ui.chat.forEach { (who, message) -> Section(who, listOf("" to message)) }
    OutlinedTextField(value = text, onValueChange = { text = it }, label = { Text("Ask Kenpi") }, modifier = Modifier.fillMaxWidth())
    Button(
        onClick = { model.ask(text); text = "" },
        enabled = reachable && !ui.assistantBusy && text.isNotBlank(),
        modifier = Modifier.fillMaxWidth(),
    ) { Text(if (ui.assistantBusy) "Thinking…" else "Send") }
    OutlinedButton(onClick = model::fetchKenpiLogs, enabled = reachable, modifier = Modifier.fillMaxWidth()) { Text("Fetch Kenpi logs") }
    OutlinedButton(onClick = model::uploadLogs, enabled = reachable, modifier = Modifier.fillMaxWidth()) { Text("Send this phone's logs to Kenpi") }
    if (ui.kenpiLogs.isNotEmpty()) Section("Kenpi logs", ui.kenpiLogs.takeLast(20).map { "•" to it })
}
