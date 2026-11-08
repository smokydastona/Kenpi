package com.kenpi.gateway

import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.TimeUnit

/**
 * Read-only network survey of a camera on the user's own Wi-Fi: which TCP ports accept a
 * connection and what an HTTP-style request or passive banner returns. It sends no
 * credentials and no commands, refuses hosts outside private/link-local ranges, and
 * records observations only. It is a capture aid, not a camera protocol: nothing here
 * identifies, authenticates or controls an A810 Lite.
 */
object CameraProbe {
    /** Common service ports only; the list is a starting point, not knowledge of the camera. */
    val DEFAULT_PORTS = listOf(21, 22, 23, 80, 443, 554, 7070, 8000, 8080, 8554, 8899)

    data class PortResult(
        val port: Int,
        val open: Boolean,
        val connectMillis: Long?,
        val http: String?,
        val banner: String?,
    )

    data class Report(val host: String, val results: List<PortResult>) {
        fun render(): String = buildString {
            appendLine("Camera network probe (observations only; not a verified protocol)")
            appendLine("host: $host")
            for (r in results) {
                if (!r.open) { appendLine("tcp/${r.port}: closed or filtered"); continue }
                appendLine("tcp/${r.port}: open (${r.connectMillis} ms)")
                r.http?.let { appendLine("  http: ${it.replace("\r\n", " | ")}") }
                r.banner?.let { appendLine("  banner: $it") }
            }
        }
    }

    fun isPrivateHost(address: InetAddress): Boolean =
        address.isSiteLocalAddress || address.isLinkLocalAddress

    /** Mask values that identify the user or network before a report leaves the phone. */
    fun redact(text: String): String = text
        .replace(Regex("(?i)\\b((?:authorization|cookie|set-cookie|x-[\\w-]*token|token|password|ssid)\\s*[:=]\\s*)[^\\r\\n|,;]+"), "$1[redacted]")
        .replace(Regex("\\b[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}\\b"), "[mac]")

    fun probe(
        host: String,
        ports: List<Int> = DEFAULT_PORTS,
        timeoutMillis: Int = 1500,
        connect: (InetSocketAddress, Int) -> Socket = ::tcpConnect,
    ): Report {
        val address = InetAddress.getByName(host)
        require(isPrivateHost(address)) { "refusing to probe a non-local address" }
        require(ports.size <= 64 && ports.all { it in 1..65535 }) { "invalid port list" }
        return Report(host, ports.map { probePort(address, it, timeoutMillis, connect) })
    }

    private fun tcpConnect(target: InetSocketAddress, timeoutMillis: Int): Socket =
        Socket().also { it.connect(target, timeoutMillis) }

    private fun probePort(
        address: InetAddress,
        port: Int,
        timeoutMillis: Int,
        connect: (InetSocketAddress, Int) -> Socket,
    ): PortResult {
        val started = System.nanoTime()
        val socket = try {
            connect(InetSocketAddress(address, port), timeoutMillis)
        } catch (e: java.io.IOException) {
            return PortResult(port, false, null, null, null)
        }
        val elapsed = TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started)
        socket.use {
            it.soTimeout = timeoutMillis
            val banner = readSome(it)
            if (banner != null) return PortResult(port, true, elapsed, null, redact(banner))
            val http = try {
                it.getOutputStream().apply {
                    write("GET / HTTP/1.0\r\nHost: ${address.hostAddress}\r\n\r\n".toByteArray())
                    flush()
                }
                readSome(it)
            } catch (e: java.io.IOException) { null }
            return PortResult(port, true, elapsed, http?.let { h -> redact(headOf(h)) }, null)
        }
    }

    /** Passive read: shorter wait so silent (client-speaks-first) services fall through quickly. */
    private fun readSome(socket: Socket): String? = try {
        val original = socket.soTimeout
        socket.soTimeout = minOf(original, 400)
        val buffer = ByteArray(512)
        val n = socket.getInputStream().read(buffer)
        socket.soTimeout = original
        if (n > 0) String(buffer, 0, n, Charsets.ISO_8859_1).filter { c -> c == '\r' || c == '\n' || c in ' '..'~' } else null
    } catch (e: java.net.SocketTimeoutException) { null } catch (e: java.io.IOException) { null }

    private fun headOf(response: String): String = response.substringBefore("\r\n\r\n").take(400)
}
