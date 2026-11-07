package com.kenpi.companion.ui

import com.kenpi.companion.network.GatewaySnapshot
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class GatewayStoreTest {
    private val snapshot = GatewaySnapshot("ok", listOf("CPU" to "40"), emptyList())

    @Test
    fun successfulRefreshPublishesSnapshotAndTimestamp() = withStore({ snapshot }) { store ->
        store.accept(GatewayIntent.Refresh)

        assertEquals(snapshot, store.state.value.snapshot)
        assertNotNull(store.state.value.lastUpdated)
        assertNull(store.state.value.error)
        assertFalse(store.state.value.isRefreshing)
    }

    @Test
    fun failedRefreshClearsPreviousSnapshotAndTimestamp() {
        var fail = false
        withStore({
            if (fail) error("Gateway returned HTTP 503")
            snapshot
        }) { store ->
            store.accept(GatewayIntent.Refresh)
            fail = true
            store.accept(GatewayIntent.Refresh)

            assertNull(store.state.value.snapshot)
            assertNull(store.state.value.lastUpdated)
            assertEquals("Gateway returned HTTP 503", store.state.value.error)
            assertFalse(store.state.value.isRefreshing)
        }
    }

    @Test
    fun urlChangeResetsSnapshotAndMonitoring() = withStore({ snapshot }) { store ->
        store.accept(GatewayIntent.StartMonitoring)
        assertTrue(store.state.value.isMonitoring)
        assertNotNull(store.state.value.snapshot)

        store.accept(GatewayIntent.UpdateBaseUrl("https://other.example"))

        assertEquals(GatewayUiState(baseUrl = "https://other.example"), store.state.value)
    }

    @Test
    fun unchangedUrlPreservesCurrentSnapshot() = withStore({ snapshot }) { store ->
        store.accept(GatewayIntent.Refresh)
        val previous = store.state.value

        store.accept(GatewayIntent.UpdateBaseUrl(previous.baseUrl))

        assertEquals(previous, store.state.value)
    }

    @Test
    fun inFlightResponseCannotRepopulateStateAfterUrlChangesBack() {
        val response = CompletableDeferred<GatewaySnapshot>()
        withStore({ response.await() }) { store ->
            store.accept(GatewayIntent.Refresh)
            assertTrue(store.state.value.isRefreshing)
            store.accept(GatewayIntent.UpdateBaseUrl("https://other.example"))
            store.accept(GatewayIntent.UpdateBaseUrl("https://gateway.example"))
            response.complete(snapshot)

            assertEquals(GatewayUiState(baseUrl = "https://gateway.example"), store.state.value)
        }
    }

    @Test
    fun oldRequestFailureDoesNotReplaceNewUrlState() {
        val response = CompletableDeferred<GatewaySnapshot>()
        withStore({ response.await() }) { store ->
            store.accept(GatewayIntent.Refresh)
            store.accept(GatewayIntent.UpdateBaseUrl("https://other.example"))
            response.completeExceptionally(IllegalStateException("Old gateway failed"))

            assertEquals(GatewayUiState(baseUrl = "https://other.example"), store.state.value)
        }
    }

    @Test
    fun queuedRefreshUsesNewUrlAndPublishesOnlyItsSnapshot() {
        val oldResponse = CompletableDeferred<GatewaySnapshot>()
        val requestedUrls = mutableListOf<String>()
        val newSnapshot = snapshot.copy(applicationStatus = "degraded")
        withStore({ url ->
            requestedUrls.add(url)
            if (requestedUrls.size == 1) oldResponse.await() else newSnapshot
        }) { store ->
            store.accept(GatewayIntent.Refresh)
            store.accept(GatewayIntent.UpdateBaseUrl("https://other.example/"))
            store.accept(GatewayIntent.Refresh)
            oldResponse.complete(snapshot)

            assertEquals(
                listOf("https://gateway.example", "https://other.example"),
                requestedUrls,
            )
            assertEquals(newSnapshot, store.state.value.snapshot)
            assertNotNull(store.state.value.lastUpdated)
            assertFalse(store.state.value.isRefreshing)
        }
    }

    @Test
    fun successfulRefreshAfterFailureClearsError() {
        var fail = true
        withStore({
            if (fail) error("Gateway unavailable")
            snapshot
        }) { store ->
            store.accept(GatewayIntent.Refresh)
            assertNotNull(store.state.value.error)
            fail = false
            store.accept(GatewayIntent.Refresh)

            assertEquals(snapshot, store.state.value.snapshot)
            assertNull(store.state.value.error)
            assertNotNull(store.state.value.lastUpdated)
        }
    }

    @Test
    fun invalidUrlCannotStartMonitoringOrFetch() = withStore({
        error("Must not fetch an invalid URL")
    }) { store ->
        store.accept(GatewayIntent.UpdateBaseUrl("http://gateway.example"))
        store.accept(GatewayIntent.StartMonitoring)

        assertFalse(store.state.value.isMonitoring)
        assertNull(store.state.value.snapshot)
        assertNull(store.state.value.lastUpdated)
        assertEquals("Use HTTPS; cleartext HTTP is disabled.", store.state.value.error)

        store.accept(GatewayIntent.Refresh)
        assertFalse(store.state.value.isRefreshing)
        assertEquals("Use HTTPS; cleartext HTTP is disabled.", store.state.value.error)
    }

    @Test
    fun stoppingMonitoringCancelsRefreshWithoutConnectionError() {
        val response = CompletableDeferred<GatewaySnapshot>()
        withStore({ response.await() }) { store ->
            store.accept(GatewayIntent.StartMonitoring)
            assertTrue(store.state.value.isRefreshing)

            store.accept(GatewayIntent.StopMonitoring)

            assertFalse(store.state.value.isMonitoring)
            assertFalse(store.state.value.isRefreshing)
            assertNull(store.state.value.error)
            response.complete(snapshot)
            assertNull(store.state.value.snapshot)
        }
    }

    @Test
    fun updatingBaseUrlPersistsTheNewValue() {
        val persisted = mutableListOf<String>()
        val store = GatewayStore(
            fetchSnapshot = { snapshot },
            scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined),
            persistBaseUrl = { persisted.add(it) },
        )
        try {
            store.accept(GatewayIntent.UpdateBaseUrl("https://gateway.example"))
            store.accept(GatewayIntent.UpdateBaseUrl("https://other.example"))

            assertEquals(listOf("https://gateway.example", "https://other.example"), persisted)
        } finally {
            store.close()
        }
    }

    @Test
    fun restartingWithInitialBaseUrlDoesNotPersistOnConstruction() {
        val persisted = mutableListOf<String>()
        val store = GatewayStore(
            initialBaseUrl = "https://restored.example",
            fetchSnapshot = { snapshot },
            scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined),
            persistBaseUrl = { persisted.add(it) },
        )
        try {
            assertEquals("https://restored.example", store.state.value.baseUrl)
            assertTrue(persisted.isEmpty())
        } finally {
            store.close()
        }
    }

    @Test
    fun successfulRefreshRecordsClockTimestampForStalenessChecks() {
        var clock = 1_000L
        val store = GatewayStore(
            fetchSnapshot = { snapshot },
            scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined),
            nowMillis = { clock },
        )
        try {
            store.accept(GatewayIntent.UpdateBaseUrl("https://gateway.example"))
            store.accept(GatewayIntent.Refresh)

            assertEquals(1_000L, store.state.value.lastUpdatedAtMillis)
        } finally {
            store.close()
        }
    }

    @Test
    fun failedRefreshClearsLastUpdatedAtMillis() {
        var fail = false
        val store = GatewayStore(
            fetchSnapshot = {
                if (fail) error("Gateway returned HTTP 503")
                snapshot
            },
            scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined),
            nowMillis = { 1_000L },
        )
        try {
            store.accept(GatewayIntent.UpdateBaseUrl("https://gateway.example"))
            store.accept(GatewayIntent.Refresh)
            assertEquals(1_000L, store.state.value.lastUpdatedAtMillis)

            fail = true
            store.accept(GatewayIntent.Refresh)

            assertNull(store.state.value.lastUpdatedAtMillis)
        } finally {
            store.close()
        }
    }

    @Test
    fun isSnapshotStaleIsFalseWithoutAPriorUpdate() {
        assertFalse(isSnapshotStale(nowMillis = 10_000L, lastUpdatedAtMillis = null))
    }

    @Test
    fun isSnapshotStaleIsFalseJustAfterAnUpdate() {
        assertFalse(isSnapshotStale(nowMillis = 1_500L, lastUpdatedAtMillis = 1_000L))
    }

    @Test
    fun isSnapshotStaleIsTrueAfterTheThresholdElapses() {
        assertTrue(
            isSnapshotStale(
                nowMillis = 1_000L + STALE_DATA_THRESHOLD_MILLIS + 1,
                lastUpdatedAtMillis = 1_000L,
            ),
        )
    }

    private fun withStore(
        fetchSnapshot: suspend (String) -> GatewaySnapshot,
        assertions: (GatewayStore) -> Unit,
    ) {
        val store = GatewayStore(
            fetchSnapshot = fetchSnapshot,
            scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined),
        )
        try {
            store.accept(GatewayIntent.UpdateBaseUrl("https://gateway.example"))
            assertions(store)
        } finally {
            store.close()
        }
    }
}
