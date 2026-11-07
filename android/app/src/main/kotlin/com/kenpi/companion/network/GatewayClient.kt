package com.kenpi.companion.network

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL

data class GatewayCapability(
    val name: String,
    val state: String,
    val evidence: String,
    val detail: String,
)

data class GatewaySnapshot(
    val applicationStatus: String,
    val healthDetails: List<Pair<String, String>>,
    val capabilities: List<GatewayCapability>,
)

class GatewayClient {
    suspend fun fetchSnapshot(baseUrl: String): GatewaySnapshot =
        withContext(Dispatchers.IO) {
            val normalizedBaseUrl = normalizeBaseUrl(baseUrl)
            val health = getJson("$normalizedBaseUrl/healthz")
            val capabilities = getJson("$normalizedBaseUrl/api/v1/capabilities")

            GatewaySnapshot(
                applicationStatus = health.optString("application_status", "unknown"),
                healthDetails = HEALTH_FIELDS.map { field ->
                    field.label to jsonValue(health, field.key)
                },
                capabilities = parseCapabilities(capabilities),
            )
        }

    private fun getJson(url: String): JSONObject {
        val connection = URL(url).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "GET"
            connection.connectTimeout = REQUEST_TIMEOUT_MILLIS
            connection.readTimeout = REQUEST_TIMEOUT_MILLIS
            connection.instanceFollowRedirects = false
            connection.setRequestProperty("Accept", "application/json")
            connection.setRequestProperty("Cache-Control", "no-cache")

            val responseCode = connection.responseCode
            if (responseCode !in 200..299) {
                throw IllegalStateException("Gateway returned HTTP $responseCode")
            }
            val body = connection.inputStream.use { input -> readBounded(input) }
            return JSONObject(body)
        } finally {
            connection.disconnect()
        }
    }

    private fun parseCapabilities(payload: JSONObject): List<GatewayCapability> {
        val entries = payload.optJSONArray("capabilities")
            ?: throw IllegalStateException("Gateway response has no capabilities list")
        return buildList(entries.length()) {
            for (index in 0 until entries.length()) {
                val item = entries.optJSONObject(index)
                    ?: throw IllegalStateException("Gateway returned an invalid capability")
                add(
                    GatewayCapability(
                        name = item.optString("name", "unknown"),
                        state = item.optString("state", "unknown"),
                        evidence = item.optString("evidence", "unknown"),
                        detail = item.optString("detail", ""),
                    ),
                )
            }
        }
    }

    private fun jsonValue(payload: JSONObject, key: String): String {
        if (!payload.has(key) || payload.isNull(key)) return "Unavailable"
        return payload.opt(key)?.toString() ?: "Unavailable"
    }

    private fun readBounded(input: InputStream): String {
        val output = ByteArrayOutputStream()
        val buffer = ByteArray(8 * 1024)
        var total = 0
        while (true) {
            val count = input.read(buffer)
            if (count < 0) break
            total += count
            if (total > MAX_RESPONSE_BYTES) {
                throw IllegalStateException("Gateway response exceeds the size limit")
            }
            output.write(buffer, 0, count)
        }
        return output.toString(Charsets.UTF_8.name())
    }

    companion object {
        private const val REQUEST_TIMEOUT_MILLIS = 3_000
        private const val MAX_RESPONSE_BYTES = 1_048_576
        private val HEALTH_FIELDS = listOf(
            HealthField("CPU temperature (°C)", "cpu_temperature_c"),
            HealthField("Memory available (bytes)", "memory_available_bytes"),
            HealthField("Memory total (bytes)", "memory_total_bytes"),
            HealthField("Storage free (bytes)", "storage_free_bytes"),
            HealthField("Storage total (bytes)", "storage_total_bytes"),
        )

        fun normalizeBaseUrl(input: String): String {
            val value = input.trim()
            require(value.isNotEmpty()) { "Enter the gateway HTTPS URL." }
            val uri = try {
                URI(value)
            } catch (error: Exception) {
                throw IllegalArgumentException("Enter a valid gateway URL.", error)
            }
            require(uri.scheme?.equals("https", ignoreCase = true) == true) {
                "Use HTTPS; cleartext HTTP is disabled."
            }
            require(!uri.host.isNullOrBlank()) { "The gateway URL must include a host." }
            require(uri.userInfo == null && uri.query == null && uri.fragment == null) {
                "The gateway URL must not include credentials, a query, or a fragment."
            }
            require(uri.port == -1 || uri.port in 1..65535) {
                "The gateway URL has an invalid port."
            }
            return uri.toASCIIString().trimEnd('/')
        }
    }

    private data class HealthField(val label: String, val key: String)
}
