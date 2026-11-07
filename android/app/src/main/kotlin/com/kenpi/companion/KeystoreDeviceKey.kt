package com.kenpi.companion

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import com.kenpi.gateway.DeviceKey
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import java.util.Base64

/** P-256 identity generated inside Android Keystore; the private key is non-exportable. */
class KeystoreDeviceKey private constructor(
    private val privateKey: PrivateKey,
    override val publicKeyBase64: String,
) : DeviceKey {
    override fun sign(message: ByteArray): String {
        val signature = Signature.getInstance("SHA256withECDSA")
        signature.initSign(privateKey)
        signature.update(message)
        return Base64.getEncoder().encodeToString(signature.sign())
    }

    companion object {
        private const val ALIAS = "kenpi-device-identity"

        fun loadOrCreate(): KeystoreDeviceKey {
            val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
            if (!ks.containsAlias(ALIAS)) generate()
            return open(ks)
        }

        /** Discards the identity; the gateway must enrol the replacement as a new device. */
        fun replace(): KeystoreDeviceKey {
            val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
            if (ks.containsAlias(ALIAS)) ks.deleteEntry(ALIAS)
            generate()
            return open(ks)
        }

        fun delete() {
            val ks = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
            if (ks.containsAlias(ALIAS)) ks.deleteEntry(ALIAS)
        }

        private fun generate() {
            val generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore")
            generator.initialize(
                KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
                    .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                    .setDigests(KeyProperties.DIGEST_SHA256)
                    .build(),
            )
            generator.generateKeyPair()
        }

        private fun open(ks: KeyStore): KeystoreDeviceKey {
            val key = ks.getKey(ALIAS, null) as PrivateKey
            val publicEncoded = ks.getCertificate(ALIAS).publicKey.encoded
            return KeystoreDeviceKey(key, Base64.getEncoder().encodeToString(publicEncoded))
        }
    }
}
