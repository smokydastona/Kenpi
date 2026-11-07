package com.kenpi.companion

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import com.kenpi.gateway.CredentialStore
import com.kenpi.gateway.Credentials

/** Credentials live only in Keystore-encrypted preferences; failure to open them means "not paired". */
class EncryptedCredentialStore(private val prefs: SharedPreferences) : CredentialStore {
    override fun load(): Credentials? {
        val host = prefs.getString("host", null) ?: return null
        return Credentials(
            host,
            prefs.getInt("port", 0),
            prefs.getString("fingerprint", null) ?: return null,
            prefs.getString("device_id", null) ?: return null,
            prefs.getString("access", null) ?: return null,
            prefs.getLong("access_expires", 0),
            prefs.getString("refresh", null) ?: return null,
        )
    }

    override fun save(credentials: Credentials) {
        check(
            prefs.edit()
                .putString("host", credentials.host)
                .putInt("port", credentials.port)
                .putString("fingerprint", credentials.fingerprint)
                .putString("device_id", credentials.deviceId)
                .putString("access", credentials.accessToken)
                .putLong("access_expires", credentials.accessExpiresAtMillis)
                .putString("refresh", credentials.refreshToken)
                .commit(),
        ) { "credential write failed" }
    }

    override fun clear() {
        prefs.edit().clear().commit()
    }

    companion object {
        fun create(context: Context): EncryptedCredentialStore {
            val master = MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build()
            return EncryptedCredentialStore(
                EncryptedSharedPreferences.create(
                    context, "kenpi_credentials", master,
                    EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                    EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
                ),
            )
        }
    }
}
