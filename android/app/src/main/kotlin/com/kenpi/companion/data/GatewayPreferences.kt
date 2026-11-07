package com.kenpi.companion.data

import android.content.SharedPreferences

/**
 * Persists the user-entered gateway base URL so it survives process death and app restarts.
 *
 * This class only depends on the [SharedPreferences] interface, not a concrete implementation,
 * so it can be unit tested with an in-memory fake. Production code should supply an
 * [EncryptedSharedPreferences][androidx.security.crypto.EncryptedSharedPreferences] instance via
 * [SecureGatewayPreferencesFactory].
 */
class GatewayPreferences(private val preferences: SharedPreferences) {
    /** Returns the previously saved base URL, or null if none has been saved yet. */
    fun loadBaseUrl(): String? = preferences.getString(KEY_BASE_URL, null)

    /** Saves [value] as the base URL to restore on the next launch. */
    fun saveBaseUrl(value: String) {
        preferences.edit().putString(KEY_BASE_URL, value).apply()
    }

    /** Removes any saved base URL. */
    fun clear() {
        preferences.edit().remove(KEY_BASE_URL).apply()
    }

    private companion object {
        const val KEY_BASE_URL = "gateway_base_url"
    }
}
