package com.kenpi.gateway

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener

data class GatewayEvent(
    val eventId: String,
    val sequence: Long,
    val timestamp: String,
    val type: String,
    val source: String,
    val payload: JsonObject,
)

sealed interface LiveState {
    data object Idle : LiveState
    data class Connecting(val attempt: Int) : LiveState

    /** [snapshot] is the last authoritative state; [lastSequence] is the last event applied to it. */
    data class Live(val snapshot: Snapshot, val lastSequence: Long, val resumed: Boolean, val lastFrameMillis: Long) : LiveState
    data class Disconnected(val attempt: Int, val reason: String, val lastKnown: Snapshot?) : LiveState
    data class RemoteDenied(val lastKnown: Snapshot?) : LiveState
    data object Revoked : LiveState
}

/**
 * Authenticated gateway event stream. The token travels in the first frame, never in the URL.
 * After a drop it reconnects with the last seen sequence; whenever the gateway cannot resume
 * (a gap or a different stream) it sends a fresh snapshot and this class replaces its state
 * instead of assuming it saw everything.
 */
class LiveChannel(
    private val host: String,
    private val port: Int,
    private val http: OkHttpClient,
    private val session: Session,
    private val scope: CoroutineScope,
    private val idleTimeoutMillis: Long = 45_000,
    private val backoffMillis: (attempt: Int) -> Long = { minOf(30_000L, 1_000L shl minOf(it - 1, 5)) },
    private val clock: () -> Long = System::currentTimeMillis,
    private val reauthCheckMillis: Long = 5_000,
) {
    private val _state = MutableStateFlow<LiveState>(LiveState.Idle)
    val state: StateFlow<LiveState> = _state.asStateFlow()
    private val _events = MutableSharedFlow<GatewayEvent>(extraBufferCapacity = 256)
    val events: SharedFlow<GatewayEvent> = _events.asSharedFlow()
    private var job: Job? = null

    private var snapshot: Snapshot? = null
    private var lastSequence: Long = -1

    fun start() {
        if (job?.isActive == true) return
        job = scope.launch { run() }
    }

    fun stop() {
        job?.cancel()
        job = null
        if (_state.value !is LiveState.Revoked) _state.value = LiveState.Idle
    }

    private sealed interface Frame {
        data object Open : Frame
        data class Text(val text: String) : Frame
        data class Closed(val code: Int, val reason: String) : Frame
        data class Failed(val reason: String) : Frame
    }

    private suspend fun run() {
        var attempt = 0
        while (true) {
            attempt++
            _state.value = LiveState.Connecting(attempt)
            val outcome = try {
                connectOnce { attempt = 0 }
            } catch (e: CancellationException) {
                throw e
            } catch (e: RevokedException) {
                _state.value = LiveState.Revoked
                return
            } catch (e: Exception) {
                Outcome.Retry(e.message ?: e.javaClass.simpleName)
            }
            when (outcome) {
                Outcome.Revoked -> {
                    session.markRevoked()
                    _state.value = LiveState.Revoked
                    return
                }
                Outcome.RemoteDenied -> _state.value = LiveState.RemoteDenied(snapshot)
                is Outcome.Retry -> _state.value = LiveState.Disconnected(maxOf(attempt, 1), outcome.reason, snapshot)
            }
            delay(backoffMillis(maxOf(attempt, 1)))
        }
    }

    private sealed interface Outcome {
        data object Revoked : Outcome
        data object RemoteDenied : Outcome
        data class Retry(val reason: String) : Outcome
    }

    private suspend fun connectOnce(onSynchronised: () -> Unit): Outcome {
        var token = session.accessToken()
        val frames = Channel<Frame>(Channel.UNLIMITED)
        val request = Request.Builder().url("https://${GatewayApi.bracket(host)}:$port/ws").build()
        val socket = http.newWebSocket(
            request,
            object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) { frames.trySend(Frame.Open) }
                override fun onMessage(webSocket: WebSocket, text: String) { frames.trySend(Frame.Text(text)) }
                override fun onClosing(webSocket: WebSocket, code: Int, reason: String) { frames.trySend(Frame.Closed(code, reason)) }
                override fun onClosed(webSocket: WebSocket, code: Int, reason: String) { frames.trySend(Frame.Closed(code, reason)) }
                override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                    frames.trySend(Frame.Failed(response?.let { "HTTP ${it.code}" } ?: (t.message ?: t.javaClass.simpleName)))
                }
            },
        )
        try {
            var lastRx = clock()
            var lastReauthCheck = clock()
            var synchronised = false
            while (true) {
                val frame = withTimeoutOrNull(reauthCheckMillis) { frames.receive() }
                val now = clock()
                if (frame == null) {
                    if (now - lastRx > idleTimeoutMillis) return Outcome.Retry("no data from gateway")
                    if (synchronised && now - lastReauthCheck >= reauthCheckMillis) {
                        lastReauthCheck = now
                        val fresh = session.accessToken()
                        if (fresh != token) {
                            token = fresh
                            socket.send(buildJsonObject { put("type", "reauth"); put("token", fresh) }.toString())
                        }
                    }
                    continue
                }
                lastRx = now
                when (frame) {
                    Frame.Open -> {
                        val hello = buildJsonObject {
                            put("type", "auth")
                            put("token", token)
                            val seq = lastSequence
                            val stream = snapshot?.streamId
                            if (seq >= 0 && stream != null) {
                                put("last_sequence", seq)
                                put("stream_id", stream)
                            }
                        }
                        socket.send(hello.toString())
                    }
                    is Frame.Failed -> return Outcome.Retry(frame.reason)
                    is Frame.Closed -> return when (frame.code) {
                        4401 -> {
                            // Expired or revoked: a refresh decides which; refusal there means revoked.
                            try {
                                session.forceRefresh(token)
                                Outcome.Retry("token rejected, refreshed")
                            } catch (e: RevokedException) {
                                Outcome.Revoked
                            }
                        }
                        4403 -> Outcome.RemoteDenied
                        else -> Outcome.Retry("closed ${frame.code} ${frame.reason}")
                    }
                    is Frame.Text -> {
                        if (frame.text.length > MAX_MESSAGE) {
                            socket.close(1009, "message too large")
                            return Outcome.Retry("oversized message")
                        }
                        val result = apply(frame.text)
                        if (result == null) {
                            socket.close(1007, "bad message")
                            return Outcome.Retry("malformed message from gateway")
                        }
                        if (result) {
                            synchronised = true
                            onSynchronised()
                        }
                    }
                }
            }
        } finally {
            socket.cancel()
            frames.close()
        }
    }

    /** Returns true when this message synchronised state, false otherwise, null if invalid. */
    private suspend fun apply(text: String): Boolean? {
        val message = try {
            Json.parseToJsonElement(text) as? JsonObject
        } catch (e: Exception) {
            null
        } ?: return null
        val type = (message["type"] as? JsonPrimitive)?.contentOrNull ?: return null
        return try {
            when (type) {
                "snapshot", "resync" -> {
                    val snap = GatewayApi.parseSnapshot(message)
                    snapshot = snap
                    lastSequence = snap.sequence
                    publish(resumed = false)
                    true
                }
                "resumed" -> {
                    val stream = GatewayApi.str(message, "stream_id")
                    val current = snapshot ?: return null
                    if (stream != current.streamId) return null
                    publish(resumed = true)
                    true
                }
                "event" -> {
                    val event = parseEvent(message["event"] as? JsonObject ?: return null)
                    if (event.sequence > lastSequence) {
                        lastSequence = event.sequence
                        _events.emit(event)
                        publish(resumed = (_state.value as? LiveState.Live)?.resumed ?: false)
                    }
                    false
                }
                "heartbeat" -> {
                    (_state.value as? LiveState.Live)?.let { _state.value = it.copy(lastFrameMillis = clock()) }
                    false
                }
                "pong" -> false
                else -> false
            }
        } catch (e: GatewayException) {
            null
        }
    }

    private fun publish(resumed: Boolean) {
        val snap = snapshot ?: return
        _state.value = LiveState.Live(snap, lastSequence, resumed, clock())
    }

    private fun parseEvent(obj: JsonObject): GatewayEvent = GatewayEvent(
        GatewayApi.str(obj, "event_id"),
        (obj["sequence"] as? JsonPrimitive)?.longOrNull?.takeIf { it >= 0 } ?: throw GatewayException(200, "malformed_response"),
        GatewayApi.str(obj, "timestamp"),
        GatewayApi.str(obj, "type"),
        GatewayApi.str(obj, "source"),
        obj["payload"] as? JsonObject ?: JsonObject(emptyMap()),
    )

    companion object {
        const val MAX_MESSAGE = 1_048_576
    }
}
