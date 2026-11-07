package com.kenpi.gateway

import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import java.util.Base64

/** Per-installation P-256 identity. Private material never leaves the implementation. */
interface DeviceKey {
    /** X.509 SubjectPublicKeyInfo DER, standard base64 (what the gateway stores). */
    val publicKeyBase64: String

    /** ECDSA-SHA256, DER signature, standard base64. */
    fun sign(message: ByteArray): String
}

object Proofs {
    private fun join(vararg parts: String) = parts.joinToString("\n").toByteArray(Charsets.UTF_8)

    fun enroll(eid: String, challenge: String, name: String, publicKey: String) =
        join("kenpi-enroll-v1", eid, challenge, name, publicKey)

    fun collect(eid: String) = join("kenpi-collect-v1", eid)

    fun refresh(refreshToken: String) = join("kenpi-refresh-v1", refreshToken)
}

/**
 * In-memory key for JVM tests and for interop checks against the Python gateway. The Android app
 * uses a Keystore-backed implementation instead.
 */
class SoftwareDeviceKey(private val pair: KeyPair = generate()) : DeviceKey {
    override val publicKeyBase64: String = Base64.getEncoder().encodeToString(pair.public.encoded)

    override fun sign(message: ByteArray): String {
        val signature = Signature.getInstance("SHA256withECDSA")
        signature.initSign(pair.private)
        signature.update(message)
        return Base64.getEncoder().encodeToString(signature.sign())
    }

    companion object {
        fun generate(): KeyPair =
            KeyPairGenerator.getInstance("EC").apply { initialize(ECGenParameterSpec("secp256r1")) }.generateKeyPair()
    }
}
