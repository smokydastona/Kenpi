package com.kenpi.gateway

import java.net.InetAddress
import java.net.ServerSocket
import kotlin.concurrent.thread
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CameraProbeTest {
    @Test fun refusesPublicAddresses() {
        assertFalse(CameraProbe.isPrivateHost(InetAddress.getByName("8.8.8.8")))
        assertTrue(CameraProbe.isPrivateHost(InetAddress.getByName("192.168.0.1")))
        val failure = runCatching { CameraProbe.probe("8.8.8.8", listOf(80)) }.exceptionOrNull()
        assertTrue(failure is IllegalArgumentException)
    }

    @Test fun rejectsBadPortLists() {
        assertTrue(runCatching { CameraProbe.probe("192.168.0.1", listOf(0)) }.isFailure)
        assertTrue(runCatching { CameraProbe.probe("192.168.0.1", List(65) { it + 1 }) }.isFailure)
    }

    @Test fun redactsSecretsAndMacs() {
        val text = CameraProbe.redact("Set-Cookie: sid=abc\r\nX-Auth-Token: zzz\r\nmac 00:11:22:AA:bb:CC ok")
        assertFalse(text.contains("sid=abc"))
        assertFalse(text.contains("zzz"))
        assertFalse(text.contains("00:11:22"))
        assertTrue(text.contains("[mac]"))
    }

    @Test fun reportsOpenHttpAndClosedPortsAgainstLoopbackServer() {
        // Loopback is not "private" for the guard, so inject the connector and address via a LAN-style host.
        val server = ServerSocket(0, 1, InetAddress.getByName("127.0.0.1"))
        val worker = thread {
            server.accept().use { s ->
                s.getInputStream().read(ByteArray(64))
                s.getOutputStream().write("HTTP/1.0 200 OK\r\nServer: test\r\nSet-Cookie: s=1\r\n\r\nbody".toByteArray())
            }
        }
        val closed = ServerSocket(0).use { it.localPort }
        val sockets = mutableListOf<java.net.Socket>()
        val report = CameraProbe.probe(
            "192.168.0.1",
            listOf(server.localPort, closed),
            connect = { target, timeout ->
                val local = java.net.InetSocketAddress("127.0.0.1", target.port)
                java.net.Socket().also { it.connect(local, timeout); sockets += it }
            },
        )
        worker.join(3000)
        server.close()
        val open = report.results[0]
        assertTrue(open.open)
        assertTrue(open.http!!.startsWith("HTTP/1.0 200 OK"))
        assertFalse(open.http!!.contains("s=1"))
        assertNull(open.banner)
        assertFalse(report.results[1].open)
        assertTrue(report.render().contains("not a verified protocol"))
        assertEquals(2, report.results.size)
    }
}
