package com.kenpi.gateway

import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** Everything persisted for one paired gateway; the device private key is held elsewhere. */
data class Credentials(
    val host: String,
    val port: Int,
    val fingerprint: String,
    val deviceId: String,
    val accessToken: String,
    val accessExpiresAtMillis: Long,
    val refreshToken: String,
)

interface CredentialStore {
    fun load(): Credentials?
    fun save(credentials: Credentials)
    fun clear()
}

class RevokedException(message: String) : Exception(message)

/**
 * Hands out a valid access token, refreshing (and rotating the refresh token) when needed.
 * Refreshes are serialised; a rejected refresh means the device was revoked or its refresh token
 * was replayed, so credentials are wiped and every later call fails with [RevokedException].
 */
class Session(
    private val api: GatewayApi,
    private val key: DeviceKey,
    private val store: CredentialStore,
    private val clock: () -> Long = System::currentTimeMillis,
    private val skewMillis: Long = 30_000,
) {
    private val lock = Mutex()
    private var current: Credentials? = store.load()
    @Volatile var revoked = false
        private set

    val credentials: Credentials? get() = current

    suspend fun accessToken(): String = lock.withLock {
        val creds = current ?: throw RevokedException("not paired")
        if (revoked) throw RevokedException("device revoked")
        if (clock() < creds.accessExpiresAtMillis - skewMillis) return creds.accessToken
        refreshLocked(creds)
    }

    /** Force a refresh after the server rejected a token that looked valid locally. */
    suspend fun forceRefresh(rejected: String): String = lock.withLock {
        val creds = current ?: throw RevokedException("not paired")
        if (revoked) throw RevokedException("device revoked")
        if (creds.accessToken != rejected) return creds.accessToken
        refreshLocked(creds)
    }

    private suspend fun refreshLocked(creds: Credentials): String {
        val tokens = try {
            api.refresh(creds.refreshToken, key)
        } catch (e: GatewayException) {
            if (e.isAuthFailure) {
                revoke()
                throw RevokedException("refresh rejected: ${e.code}")
            }
            throw e
        }
        val next = creds.copy(
            deviceId = tokens.deviceId,
            accessToken = tokens.accessToken,
            accessExpiresAtMillis = clock() + tokens.expiresInSeconds * 1000L,
            refreshToken = tokens.refreshToken,
        )
        store.save(next)
        current = next
        return next.accessToken
    }

    suspend fun markRevoked() = lock.withLock { revoke() }

    private fun revoke() {
        revoked = true
        current = null
        store.clear()
    }
}
