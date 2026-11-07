package com.kenpi.companion.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class GatewayClientTest {
    @Test
    fun healthDetailsIncludeReportedHardwareAndCollectionFailures() {
        val details = GatewayClient.formatHealthDetails(
            mapOf(
                "cpu_temperature_c" to 41.5,
                "collection_status" to "partial",
                "collection_errors" to listOf(
                    mapOf("metric" to "storage", "reason" to "permission denied"),
                    mapOf("metric" to "temperature", "reason" to "sensor unavailable"),
                ),
                "hardware" to mapOf(
                    "platform" to mapOf(
                        "state" to "identified",
                        "board" to "reported-board",
                        "model" to "reported-model",
                    ),
                    "uptime_seconds" to 123.25,
                ),
            ),
        ).toMap()

        assertEquals("41.5", details["CPU temperature (°C)"])
        assertEquals("partial", details["Collection status"])
        assertEquals("identified", details["Platform state"])
        assertEquals("reported-board", details["Board"])
        assertEquals("reported-model", details["Model"])
        assertEquals("123.25", details["Uptime (seconds)"])
        assertEquals("storage: permission denied", details["Collection error 1"])
        assertEquals("temperature: sensor unavailable", details["Collection error 2"])
    }

    @Test
    fun healthDetailsDoNotInventMissingHardwareOrCollectionValues() {
        val details = GatewayClient.formatHealthDetails(emptyMap()).toMap()

        assertEquals(11, details.size)
        details.values.forEach { assertEquals("Unavailable", it) }
    }

    @Test
    fun healthDetailsPreserveZeroUptimeAndExplicitEmptyErrors() {
        val details = GatewayClient.formatHealthDetails(
            mapOf(
                "collection_status" to "ok",
                "collection_errors" to emptyList<Any>(),
                "hardware" to mapOf("uptime_seconds" to 0),
            ),
        ).toMap()

        assertEquals("ok", details["Collection status"])
        assertEquals("0", details["Uptime (seconds)"])
        assertEquals("None reported", details["Collection errors"])
        assertEquals("Unavailable", details["Platform state"])
    }

    @Test
    fun healthDetailsHandleNullAndMalformedAdditiveFields() {
        val details = GatewayClient.formatHealthDetails(
            mapOf(
                "collection_status" to null,
                "collection_errors" to listOf(
                    mapOf("metric" to "memory", "reason" to null),
                    "invalid entry",
                ),
                "hardware" to mapOf(
                    "platform" to mapOf("state" to "unavailable", "board" to "", "model" to null),
                    "uptime_seconds" to mapOf("invalid" to 10),
                ),
            ),
        ).toMap()

        assertEquals("Unavailable", details["Collection status"])
        assertEquals("unavailable", details["Platform state"])
        assertEquals("Unavailable", details["Board"])
        assertEquals("Unavailable", details["Model"])
        assertEquals("Unavailable", details["Uptime (seconds)"])
        assertEquals("memory: Unavailable", details["Collection error 1"])
        assertEquals("Unavailable: Unavailable", details["Collection error 2"])
    }

    @Test
    fun normalizeBaseUrlTrimsWhitespaceAndTrailingSlash() {
        assertEquals(
            "https://gateway.example:8443/kenpi",
            GatewayClient.normalizeBaseUrl("  https://gateway.example:8443/kenpi///  "),
        )
    }

    @Test
    fun normalizeBaseUrlAcceptsAValidHttpsAddress() {
        assertEquals(
            "https://192.168.4.2:8443",
            GatewayClient.normalizeBaseUrl("https://192.168.4.2:8443"),
        )
    }

    @Test
    fun normalizeBaseUrlRejectsCleartextHttp() {
        assertThrows(IllegalArgumentException::class.java) {
            GatewayClient.normalizeBaseUrl("http://gateway.example")
        }
    }

    @Test
    fun normalizeBaseUrlRejectsCredentials() {
        assertThrows(IllegalArgumentException::class.java) {
            GatewayClient.normalizeBaseUrl("https://user@gateway.example")
        }
    }

    @Test
    fun normalizeBaseUrlRejectsQueryAndFragment() {
        assertThrows(IllegalArgumentException::class.java) {
            GatewayClient.normalizeBaseUrl("https://gateway.example?token=secret")
        }
        assertThrows(IllegalArgumentException::class.java) {
            GatewayClient.normalizeBaseUrl("https://gateway.example/#fragment")
        }
    }

    @Test
    fun normalizeBaseUrlRejectsInvalidPort() {
        assertThrows(IllegalArgumentException::class.java) {
            GatewayClient.normalizeBaseUrl("https://gateway.example:70000")
        }
    }
}
