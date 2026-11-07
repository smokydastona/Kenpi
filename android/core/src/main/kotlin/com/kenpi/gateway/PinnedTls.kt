package com.kenpi.gateway

import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.SSLContext
import javax.net.ssl.X509TrustManager
import okhttp3.OkHttpClient

/**
 * Trust is exactly one certificate: the one whose SHA-256 fingerprint was in the pairing code.
 * No CA, hostname or expiry shortcut is taken; a different certificate is always rejected.
 */
class PinnedTrustManager(fingerprintHex: String) : X509TrustManager {
    private val expected: ByteArray = decode(fingerprintHex)

    override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        val leaf = chain?.firstOrNull() ?: throw CertificateException("no server certificate")
        val actual = MessageDigest.getInstance("SHA-256").digest(leaf.encoded)
        if (!MessageDigest.isEqual(actual, expected)) {
            throw CertificateException("gateway certificate does not match the pinned fingerprint")
        }
    }

    override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        throw CertificateException("client certificates are not accepted")
    }

    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()

    companion object {
        fun decode(hex: String): ByteArray {
            val clean = hex.replace(":", "").lowercase()
            require(clean.length == 64 && clean.all { it in '0'..'9' || it in 'a'..'f' }) { "invalid fingerprint" }
            return ByteArray(32) { clean.substring(it * 2, it * 2 + 2).toInt(16).toByte() }
        }
    }
}

object PinnedTls {
    /** The pin replaces hostname checks because the gateway is addressed by IP literal. */
    fun client(fingerprintHex: String, callTimeoutSeconds: Long = 15): OkHttpClient {
        val trust = PinnedTrustManager(fingerprintHex)
        val context = SSLContext.getInstance("TLS").apply { init(null, arrayOf(trust), SecureRandom()) }
        val verifier = HostnameVerifier { _, _ -> true }
        return OkHttpClient.Builder()
            .sslSocketFactory(context.socketFactory, trust)
            .hostnameVerifier(verifier)
            .connectTimeout(8, TimeUnit.SECONDS)
            .readTimeout(15, TimeUnit.SECONDS)
            .writeTimeout(15, TimeUnit.SECONDS)
            .callTimeout(callTimeoutSeconds, TimeUnit.SECONDS)
            .retryOnConnectionFailure(false)
            .followRedirects(false)
            .followSslRedirects(false)
            .build()
    }
}
