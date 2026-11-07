package com.kenpi.companion.network

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class GatewayClientTest {
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
