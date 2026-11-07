package com.kenpi.gateway

import java.io.BufferedReader
import java.io.BufferedWriter
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.delay
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test

/** Drives the real Python gateway (tests/android_interop_server.py). Needs KENPI_INTEROP_PYTHON and KENPI_INTEROP_ROOT. */
class InteropTest {
    private lateinit var proc: Process
    private lateinit var reader: BufferedReader
    private lateinit var writer: BufferedWriter
    private lateinit var info: JsonObject
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    @Before fun start() {
        val python = System.getenv("KENPI_INTEROP_PYTHON")
        val root = System.getenv("KENPI_INTEROP_ROOT")
        assumeTrue("KENPI_INTEROP_PYTHON/ROOT not set; interop skipped", python != null && root != null)
        proc = ProcessBuilder(python, "-m", "tests.android_interop_server")
            .directory(java.io.File(root)).redirectErrorStream(false).start()
        reader = proc.inputStream.bufferedReader()
        writer = proc.outputStream.bufferedWriter()
        info = read()
    }

    @After fun stop() {
        scope.cancel()
        if (::proc.isInitialized) {
            runCatching { cmd("quit") }
            proc.waitFor(5, TimeUnit.SECONDS)
            proc.destroyForcibly()
        }
    }

    private fun read(): JsonObject = Json.parseToJsonElement(reader.readLine()) as JsonObject
    private fun cmd(line: String): JsonObject {
        writer.write(line + "\n"); writer.flush()
        return if (line == "quit") JsonObject(emptyMap()) else read()
    }

    private val port get() = info["port"]!!.jsonPrimitive.content.toInt()
    private val fingerprint get() = info["fingerprint"]!!.jsonPrimitive.content

    private class MemStore : CredentialStore {
        var saved: Credentials? = null
        override fun load() = saved
        override fun save(credentials: Credentials) { saved = credentials }
        override fun clear() { saved = null }
    }

    private data class Paired(val api: GatewayApi, val key: DeviceKey, val store: MemStore, val session: Session, val deviceId: String)

    private fun pair(clock: () -> Long = System::currentTimeMillis): Paired = runBlocking {
        val payload = PairingPayload.parse(cmd("pair")["payload"]!!.jsonPrimitive.content, System.currentTimeMillis() / 1000.0)
        val api = GatewayApi(payload.address, payload.port, PinnedTls.client(payload.fingerprint, 10))
        val key = SoftwareDeviceKey()
        api.enroll(payload, "JVM test", key)
        assertTrue(api.collect(payload.enrollmentId, key) is CollectResult.Waiting)
        cmd("approve ${payload.enrollmentId}")
        val issued = api.collect(payload.enrollmentId, key) as CollectResult.Issued
        val store = MemStore()
        store.save(
            Credentials(payload.address, payload.port, payload.fingerprint, issued.tokens.deviceId, issued.tokens.accessToken,
                clock() + issued.tokens.expiresInSeconds * 1000L, issued.tokens.refreshToken),
        )
        Paired(api, key, store, Session(api, key, store, clock), issued.tokens.deviceId)
    }

    @Test fun pairingSnapshotAndCredentialsCollectedOnce() = runBlocking {
        val p = pair()
        val snap = p.api.snapshot(p.session.accessToken())
        assertTrue(snap.streamId.isNotEmpty())
        assertTrue(snap.state.isNotEmpty())
    }

    @Test fun refreshRotatesAndReplayIsRevocation() = runBlocking {
        var now = System.currentTimeMillis()
        val p = pair { now }
        val first = p.store.saved!!
        now += 400_000 // past the 300 s access TTL
        val token = p.session.accessToken()
        assertNotEquals(first.accessToken, token)
        assertNotEquals(first.refreshToken, p.store.saved!!.refreshToken)
        // Replaying the old refresh token must be refused and burn the device.
        try {
            p.api.refresh(first.refreshToken, p.key)
            fail("replay accepted")
        } catch (e: GatewayException) {
            assertEquals(401, e.status)
        }
        now += 400_000
        try {
            p.session.accessToken()
            fail("family should be revoked")
        } catch (e: RevokedException) {
            assertEquals(null, p.store.saved)
        }
    }

    @Test fun wrongPinIsRejected() = runBlocking {
        val api = GatewayApi("127.0.0.1", port, PinnedTls.client("0".repeat(64), 5))
        try {
            api.snapshot("x")
            fail("connected despite wrong pin")
        } catch (e: java.io.IOException) {
            assertTrue(e !is GatewayException)
        }
    }

    @Test fun badTokenIsUnauthorized() = runBlocking {
        val api = GatewayApi("127.0.0.1", port, PinnedTls.client(fingerprint, 5))
        try {
            api.snapshot("nope")
            fail()
        } catch (e: GatewayException) {
            assertEquals(401, e.status)
        }
    }

    @Test fun liveEventsThenRevocation() = runBlocking {
        val p = pair()
        val live = LiveChannel("127.0.0.1", port, PinnedTls.client(fingerprint, 10), p.session, scope, backoffMillis = { 200 })
        live.start()
        withTimeout(10_000) { while (live.state.value !is LiveState.Live) delay(50) }
        val before = (live.state.value as LiveState.Live).lastSequence
        cmd("event")
        withTimeout(10_000) {
            while (((live.state.value as? LiveState.Live)?.lastSequence ?: -1) <= before) delay(50)
        }
        cmd("revoke ${p.deviceId}")
        withTimeout(15_000) { while (live.state.value !is LiveState.Revoked) delay(50) }
        assertEquals(null, p.store.saved)
        live.stop()
    }

    @Test fun pairerCompletesAfterApproval() = runBlocking {
        val payload = PairingPayload.parse(cmd("pair")["payload"]!!.jsonPrimitive.content, System.currentTimeMillis() / 1000.0)
        val store = MemStore()
        val result = scope.async {
            Pairer(pollMillis = 100).pair(payload, "Pairer", SoftwareDeviceKey(), store)
        }
        delay(500)
        assertTrue(!result.isCompleted)
        cmd("approve ${payload.enrollmentId}")
        val done = withTimeout(10_000) { result.await() }
        assertTrue(done is PairingResult.Paired)
        assertEquals((done as PairingResult.Paired).credentials, store.saved)
    }

    @Test fun pairerReportsDenial() = runBlocking {
        val payload = PairingPayload.parse(cmd("pair")["payload"]!!.jsonPrimitive.content, System.currentTimeMillis() / 1000.0)
        val result = scope.async { Pairer(pollMillis = 100).pair(payload, "Denied", SoftwareDeviceKey(), MemStore()) }
        delay(300)
        cmd("deny ${payload.enrollmentId}")
        assertTrue(withTimeout(10_000) { result.await() } is PairingResult.Rejected)
    }
}
