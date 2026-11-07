package com.kenpi.gateway

import kotlinx.coroutines.delay

sealed interface PairingProgress {
    data object Submitting : PairingProgress
    data object AwaitingApproval : PairingProgress
}

sealed interface PairingResult {
    data class Paired(val credentials: Credentials) : PairingResult
    data class Rejected(val state: String) : PairingResult
    data object TimedOut : PairingResult
}

/** Runs enrol -> wait for administrator approval -> collect credentials -> persist. */
class Pairer(private val clock: () -> Long = System::currentTimeMillis, private val pollMillis: Long = 3_000) {
    suspend fun pair(
        payload: PairingPayload,
        deviceName: String,
        key: DeviceKey,
        store: CredentialStore,
        onProgress: (PairingProgress) -> Unit = {},
    ): PairingResult {
        val api = GatewayApi(payload.address, payload.port, PinnedTls.client(payload.fingerprint))
        onProgress(PairingProgress.Submitting)
        api.enroll(payload, deviceName, key)
        onProgress(PairingProgress.AwaitingApproval)
        while (clock() / 1000.0 < payload.expiresAtEpochSeconds) {
            when (val result = api.collect(payload.enrollmentId, key)) {
                is CollectResult.Issued -> {
                    val t = result.tokens
                    val creds = Credentials(
                        payload.address, payload.port, payload.fingerprint, t.deviceId, t.accessToken,
                        clock() + t.expiresInSeconds * 1000L, t.refreshToken,
                    )
                    store.save(creds)
                    return PairingResult.Paired(creds)
                }
                is CollectResult.Terminal -> return PairingResult.Rejected(result.state)
                is CollectResult.Waiting -> delay(pollMillis)
            }
        }
        return PairingResult.TimedOut
    }
}
