package com.kenpi.companion

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import com.kenpi.gateway.CameraProbe
import com.kenpi.gateway.GatewayApi
import com.kenpi.gateway.GatewayException
import com.kenpi.gateway.LiveChannel
import com.kenpi.gateway.LiveState
import com.kenpi.gateway.LocalHelper
import com.kenpi.gateway.PairingException
import com.kenpi.gateway.PairingPayload
import com.kenpi.gateway.PairingProgress
import com.kenpi.gateway.PairingResult
import com.kenpi.gateway.Pairer
import com.kenpi.gateway.PinnedTls
import com.kenpi.gateway.Session
import com.kenpi.gateway.WireGuardParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

sealed interface Screen {
    data object Unpaired : Screen
    data class Pairing(val progress: PairingProgress?) : Screen
    data object Paired : Screen
}

data class Ui(
    val screen: Screen = Screen.Unpaired,
    val error: String? = null,
    val deviceId: String? = null,
    val gateway: String? = null,
    val wireGuard: WireGuardParameters? = null,
    val wireGuardNote: String? = null,
    val log: List<String> = emptyList(),
    val chat: List<Pair<String, String>> = emptyList(),
    val assistantBusy: Boolean = false,
    val kenpiLogs: List<String> = emptyList(),
    val probeReport: String? = null,
    val probeBusy: Boolean = false,
)

class AppModel(app: Application) : AndroidViewModel(app) {
    private val store = EncryptedCredentialStore.create(app)
    private val _ui = MutableStateFlow(Ui())
    val ui: StateFlow<Ui> = _ui.asStateFlow()
    private val _live = MutableStateFlow<LiveState>(LiveState.Idle)
    val live: StateFlow<LiveState> = _live.asStateFlow()
    private var channel: LiveChannel? = null
    private var session: Session? = null
    private var pairJob: Job? = null
    private var watch: Job? = null
    private var api: GatewayApi? = null
    private var lastReported: String? = null

    init {
        store.load()?.let { connect() }
    }

    private fun note(message: String) {
        _ui.value = _ui.value.copy(log = (_ui.value.log + message).takeLast(100))
    }

    fun pairFromUri(uri: String, deviceName: String) {
        val payload = try {
            PairingPayload.parse(uri.trim(), System.currentTimeMillis() / 1000.0)
        } catch (e: PairingException) {
            _ui.value = _ui.value.copy(error = "Invalid pairing code: ${e.reason.name.lowercase()}")
            return
        }
        pairJob?.cancel()
        _ui.value = _ui.value.copy(screen = Screen.Pairing(null), error = null)
        pairJob = viewModelScope.launch {
            try {
                val result = Pairer().pair(payload, deviceName.ifBlank { "Android" }, KeystoreDeviceKey.loadOrCreate(), store) {
                    _ui.value = _ui.value.copy(screen = Screen.Pairing(it))
                }
                when (result) {
                    is PairingResult.Paired -> connect()
                    is PairingResult.Rejected -> fail("Pairing ${result.state}")
                    PairingResult.TimedOut -> fail("Pairing code expired before approval")
                }
            } catch (e: GatewayException) {
                fail("Gateway refused pairing: ${e.code}")
            } catch (e: java.io.IOException) {
                fail("Cannot reach gateway or its certificate does not match the code: ${e.message}")
            }
        }
    }

    private fun fail(message: String) {
        note(message)
        _ui.value = _ui.value.copy(screen = Screen.Unpaired, error = message)
    }

    fun cancelPairing() {
        pairJob?.cancel()
        _ui.value = _ui.value.copy(screen = Screen.Unpaired)
    }

    private fun connect() {
        val creds = store.load() ?: return
        channel?.stop()
        watch?.cancel()
        val http = PinnedTls.client(creds.fingerprint)
        val api = GatewayApi(creds.host, creds.port, http)
        this.api = api
        val s = Session(api, KeystoreDeviceKey.loadOrCreate(), store)
        session = s
        val live = LiveChannel(creds.host, creds.port, PinnedTls.client(creds.fingerprint, 0), s, viewModelScope)
        channel = live
        _ui.value = _ui.value.copy(screen = Screen.Paired, error = null, deviceId = creds.deviceId, gateway = "${creds.host}:${creds.port}")
        watch = viewModelScope.launch {
            live.state.collect {
                if (it is LiveState.Revoked) {
                    note("Device revoked by gateway; credentials erased")
                    _ui.value = _ui.value.copy(screen = Screen.Unpaired, error = "This device was revoked. Pair again.")
                }
                if (it::class != _live.value::class) note("Connection: ${it::class.simpleName}")
                _live.value = it
                if (it is LiveState.Live || it is LiveState.Disconnected || it is LiveState.RemoteDenied) uploadLogs()
            }
        }
        live.start()
        viewModelScope.launch {
            try {
                val wg = api.wireGuard(s.accessToken())
                _ui.value = _ui.value.copy(wireGuard = wg, wireGuardNote = if (wg == null) "Remote access is not enabled on the gateway" else null)
            } catch (e: Exception) {
                _ui.value = _ui.value.copy(wireGuardNote = "Not available: ${e.message}")
            }
        }
    }

    private fun appVersion(): String = try {
        val app = getApplication<Application>()
        app.packageManager.getPackageInfo(app.packageName, 0).versionName.orEmpty()
    } catch (e: Exception) {
        "unknown"
    }

    private fun connectionLabel(): String = _live.value.let { it::class.simpleName.orEmpty() + (if (it is LiveState.Disconnected) ": ${it.reason}" else "") }

    /** Shares this phone's recent connection log with Kenpi to help diagnose problems. Best effort. */
    /** Survey the camera on the phone's Wi-Fi (read-only); the report is for capturing evidence. */
    fun probeCamera() {
        if (_ui.value.probeBusy) return
        _ui.value = _ui.value.copy(probeBusy = true, probeReport = null)
        viewModelScope.launch {
            val report = withContext(Dispatchers.IO) {
                try {
                    val cm = getApplication<Application>().getSystemService(ConnectivityManager::class.java)
                    val wifi = cm.allNetworks.firstOrNull {
                        cm.getNetworkCapabilities(it)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true
                    } ?: return@withContext "Not connected to Wi-Fi. Join the camera's Wi-Fi first."
                    val gateway = cm.getLinkProperties(wifi)?.routes
                        ?.firstOrNull { it.isDefaultRoute && it.gateway != null }?.gateway?.hostAddress
                        ?: return@withContext "No gateway address on this Wi-Fi network."
                    CameraProbe.probe(gateway, connect = { target, timeout ->
                        wifi.socketFactory.createSocket().also { it.connect(target, timeout) }
                    }).render()
                } catch (e: Exception) {
                    "Probe failed: ${e.javaClass.simpleName}"
                }
            }
            _ui.value = _ui.value.copy(probeBusy = false, probeReport = report)
        }
    }

    fun uploadLogs() {
        val api = api ?: return
        val s = session ?: return
        val label = connectionLabel()
        if (label == lastReported) return
        viewModelScope.launch {
            try {
                api.uploadLogs(s.accessToken(), appVersion(), label, _ui.value.log.takeLast(50))
                lastReported = label
            } catch (e: Exception) {
                // Expected while disconnected; the next state change retries.
            }
        }
    }

    fun ask(text: String) {
        if (text.isBlank() || _ui.value.assistantBusy) return
        val chat = _ui.value.chat + ("You" to text)
        _ui.value = _ui.value.copy(chat = chat, assistantBusy = true)
        viewModelScope.launch {
            val reply = try {
                val api = api ?: throw GatewayException(0, "not_connected")
                val body = api.assistantAsk(session!!.accessToken(), text)
                (body["message"] as? kotlinx.serialization.json.JsonPrimitive)?.content ?: body.toString()
            } catch (e: GatewayException) {
                when (e.code) {
                    "scope_required" -> "This phone was not granted the assistant.use scope. Re-pair with --scopes status.read,vehicle.read,assistant.use."
                    "feature_disabled" -> "The assistant is switched off on Kenpi. Turn it on with kenpi-features enable assistant."
                    else -> LocalHelper.explain(true, _live.value) + " (${e.code})"
                }
            } catch (e: java.io.IOException) {
                LocalHelper.explain(true, _live.value)
            }
            _ui.value = _ui.value.copy(chat = _ui.value.chat + ("Kenpi" to reply), assistantBusy = false)
        }
    }

    fun fetchKenpiLogs() {
        val api = api ?: return
        viewModelScope.launch {
            try {
                val lines = api.kenpiLogs(session!!.accessToken())["lines"] as? kotlinx.serialization.json.JsonArray
                _ui.value = _ui.value.copy(kenpiLogs = lines?.map { (it as? kotlinx.serialization.json.JsonPrimitive)?.content.orEmpty() } ?: emptyList())
            } catch (e: Exception) {
                note("Could not fetch Kenpi logs: ${e.message}")
            }
        }
    }

    /** Local-only: forgets credentials and the identity key. The administrator should also revoke the device. */
    fun unpair() {
        channel?.stop()
        watch?.cancel()
        store.clear()
        KeystoreDeviceKey.delete()
        _live.value = LiveState.Idle
        _ui.value = Ui(log = _ui.value.log)
    }

    fun reconnect() {
        channel?.stop()
        channel?.start()
    }

    /** Hold the socket only while the app is visible. */
    fun foreground(visible: Boolean) {
        if (_ui.value.screen != Screen.Paired) return
        if (visible) channel?.start() else channel?.stop()
    }

    override fun onCleared() {
        channel?.stop()
    }
}
