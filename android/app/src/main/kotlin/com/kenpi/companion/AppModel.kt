package com.kenpi.companion

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.kenpi.gateway.GatewayApi
import com.kenpi.gateway.GatewayException
import com.kenpi.gateway.LiveChannel
import com.kenpi.gateway.LiveState
import com.kenpi.gateway.PairingException
import com.kenpi.gateway.PairingPayload
import com.kenpi.gateway.PairingProgress
import com.kenpi.gateway.PairingResult
import com.kenpi.gateway.Pairer
import com.kenpi.gateway.PinnedTls
import com.kenpi.gateway.Session
import com.kenpi.gateway.WireGuardParameters
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

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
