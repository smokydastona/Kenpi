package com.kenpi.companion.data

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Builds a [GatewayPreferences] backed by Android Keystore-encrypted storage, so the saved
 * gateway URL (and any future endpoint configuration) is not stored in plaintext on disk.
 *
 * Construction can fail on devices with a broken or unavailable Keystore; callers must treat
 * that as "no persisted preferences available" and fall back to in-memory-only state rather than
 * crashing the app over a non-essential convenience feature.
 */
object SecureGatewayPreferencesFactory {
    private const val PREFS_FILE_NAME = "kenpi_companion_secure_prefs"

    fun create(context: Context): GatewayPreferences {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        val preferences = EncryptedSharedPreferences.create(
            context,
            PREFS_FILE_NAME,
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
        return GatewayPreferences(preferences)
    }
}
