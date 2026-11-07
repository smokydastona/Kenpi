package com.kenpi.companion.ui

import com.kenpi.companion.network.GatewayClient
import com.kenpi.companion.network.GatewaySnapshot
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.time.Instant

data class GatewayUiState(
    val baseUrl: String = "",
    val isMonitoring: Boolean = false,
    val isRefreshing: Boolean = false,
    val snapshot: GatewaySnapshot? = null,
    val error: String? = null,
    val lastUpdated: String? = null,
    val lastUpdatedAtMillis: Long? = null,
)

sealed interface GatewayIntent {
    data class UpdateBaseUrl(val value: String) : GatewayIntent
    data object StartMonitoring : GatewayIntent
    data object StopMonitoring : GatewayIntent
    data object Refresh : GatewayIntent
}

class GatewayStore(
    initialBaseUrl: String = "",
    private val fetchSnapshot: suspend (String) -> GatewaySnapshot = GatewayClient()::fetchSnapshot,
    private val scope: CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate),
    private val persistBaseUrl: (String) -> Unit = {},
    private val nowMillis: () -> Long = System::currentTimeMillis,
) {
    private val refreshMutex = Mutex()
    private val mutableState = MutableStateFlow(GatewayUiState(baseUrl = initialBaseUrl))
    val state = mutableState.asStateFlow()

    private var monitoringJob: Job? = null
    private var urlRevision = 0L

    fun accept(intent: GatewayIntent) {
        when (intent) {
            is GatewayIntent.UpdateBaseUrl -> {
                if (mutableState.value.baseUrl != intent.value) {
                    stopMonitoring()
                    urlRevision++
                    mutableState.value = GatewayUiState(baseUrl = intent.value)
                    persistBaseUrl(intent.value)
                }
            }

            GatewayIntent.StartMonitoring -> startMonitoring()
            GatewayIntent.StopMonitoring -> stopMonitoring()
            GatewayIntent.Refresh -> scope.launch { refreshOnce() }
        }
    }

    fun close() {
        stopMonitoring()
        scope.coroutineContext[Job]?.cancel()
    }

    private fun startMonitoring() {
        if (monitoringJob?.isActive == true) return
        try {
            GatewayClient.normalizeBaseUrl(mutableState.value.baseUrl)
        } catch (error: IllegalArgumentException) {
            mutableState.update { it.copy(snapshot = null, lastUpdated = null, error = error.message) }
            return
        }
        mutableState.update { it.copy(isMonitoring = true, error = null) }
        monitoringJob = scope.launch {
            while (isActive) {
                refreshOnce()
                delay(POLL_INTERVAL_MILLIS)
            }
        }
    }

    private fun stopMonitoring() {
        monitoringJob?.cancel()
        monitoringJob = null
        mutableState.update { it.copy(isMonitoring = false) }
    }

    private suspend fun refreshOnce() {
        refreshMutex.withLock {
            val requestedUrl = mutableState.value.baseUrl
            val requestedRevision = urlRevision
            val normalizedUrl = try {
                GatewayClient.normalizeBaseUrl(requestedUrl)
            } catch (error: IllegalArgumentException) {
                mutableState.update {
                    it.copy(snapshot = null, lastUpdated = null, lastUpdatedAtMillis = null, error = error.message)
                }
                return
            }

            mutableState.update { it.copy(isRefreshing = true, error = null) }
            try {
                val snapshot = fetchSnapshot(normalizedUrl)
                mutableState.update { current ->
                    if (urlRevision == requestedRevision) {
                        current.copy(
                            snapshot = snapshot,
                            error = null,
                            lastUpdated = Instant.now().toString(),
                            lastUpdatedAtMillis = nowMillis(),
                        )
                    } else {
                        current
                    }
                }
            } catch (error: CancellationException) {
                throw error
            } catch (error: Exception) {
                mutableState.update { current ->
                    if (urlRevision == requestedRevision) {
                        current.copy(
                            snapshot = null,
                            lastUpdated = null,
                            lastUpdatedAtMillis = null,
                            error = error.message ?: "Unable to reach the gateway.",
                        )
                    } else {
                        current
                    }
                }
            } finally {
                if (urlRevision == requestedRevision) {
                    mutableState.update { it.copy(isRefreshing = false) }
                }
            }
        }
    }

    private companion object {
        const val POLL_INTERVAL_MILLIS = 2_000L
    }
}

/**
 * Threshold after which the most recent successful refresh is considered stale, even while
 * polling is still active (e.g. the gateway stopped responding but the poll loop keeps retrying).
 * Exposed separately from [GatewayStore] so the UI and tests can reason about staleness without
 * depending on store internals.
 */
const val STALE_DATA_THRESHOLD_MILLIS = 6_000L

/** True when the last successful snapshot is older than [STALE_DATA_THRESHOLD_MILLIS], or absent. */
fun isSnapshotStale(nowMillis: Long, lastUpdatedAtMillis: Long?): Boolean {
    if (lastUpdatedAtMillis == null) return false
    return nowMillis - lastUpdatedAtMillis > STALE_DATA_THRESHOLD_MILLIS
}
