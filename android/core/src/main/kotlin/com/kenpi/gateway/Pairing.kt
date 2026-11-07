package com.kenpi.gateway

import java.net.InetAddress
import java.util.Base64
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.doubleOrNull

class PairingException(val reason: Reason, message: String) : Exception(message) {
    enum class Reason { MALFORMED, UNSUPPORTED_VERSION, EXPIRED, BAD_ADDRESS }
}

/** Contents of the `kenpi://pair?d=...` QR code, validated against what the gateway emits. */
data class PairingPayload(
    val address: String,
    val port: Int,
    val fingerprint: String,
    val enrollmentId: String,
    val challenge: String,
    val expiresAtEpochSeconds: Double,
) {
    companion object {
        private val HEX64 = Regex("^[0-9a-f]{64}$")
        private val EID = Regex("^[0-9a-f]{32}$")
        private val CHALLENGE = Regex("^[A-Za-z0-9_-]{16,128}$")
        const val MAX_URI_LENGTH = 2048

        fun parse(uri: String, nowEpochSeconds: Double): PairingPayload {
            if (uri.length > MAX_URI_LENGTH || !uri.startsWith("kenpi://pair?d=")) {
                throw PairingException(PairingException.Reason.MALFORMED, "not a Kenpi pairing code")
            }
            val encoded = uri.removePrefix("kenpi://pair?d=")
            val root = try {
                Json.parseToJsonElement(String(Base64.getUrlDecoder().decode(encoded), Charsets.UTF_8)) as? JsonObject
            } catch (e: Exception) {
                null
            } ?: throw PairingException(PairingException.Reason.MALFORMED, "pairing data is unreadable")

            fun text(key: String) = (root[key] as? JsonPrimitive)?.takeIf { it.isString }?.contentOrNull
                ?: throw PairingException(PairingException.Reason.MALFORMED, "missing $key")

            if ((root["v"] as? JsonPrimitive)?.intOrNull != 1) {
                throw PairingException(PairingException.Reason.UNSUPPORTED_VERSION, "unsupported pairing version")
            }
            val address = text("address")
            requireLiteralAddress(address)
            val port = (root["port"] as? JsonPrimitive)?.intOrNull
            if (port == null || port !in 1..65535) {
                throw PairingException(PairingException.Reason.MALFORMED, "invalid port")
            }
            val fingerprint = text("fingerprint").lowercase()
            val eid = text("enrollment_id")
            val challenge = text("challenge")
            if (!HEX64.matches(fingerprint) || !EID.matches(eid) || !CHALLENGE.matches(challenge)) {
                throw PairingException(PairingException.Reason.MALFORMED, "invalid pairing fields")
            }
            val expires = (root["expires"] as? JsonPrimitive)?.doubleOrNull
                ?: throw PairingException(PairingException.Reason.MALFORMED, "missing expiry")
            if (expires <= nowEpochSeconds) {
                throw PairingException(PairingException.Reason.EXPIRED, "pairing code expired")
            }
            return PairingPayload(address, port, fingerprint, eid, challenge, expires)
        }

        /** The gateway only ever advertises IP literals, so names (and DNS rebinding) are refused. */
        private fun requireLiteralAddress(address: String) {
            val v4 = Regex("^(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})\\.(\\d{1,3})$").matchEntire(address)
            val ok = if (v4 != null) {
                v4.groupValues.drop(1).all { it.toInt() in 0..255 }
            } else {
                address.contains(':') && Regex("^[0-9a-fA-F:.]+$").matches(address) &&
                    runCatching { InetAddress.getByName(address) }.isSuccess
            }
            if (!ok) throw PairingException(PairingException.Reason.BAD_ADDRESS, "address must be an IP literal")
        }
    }
}
