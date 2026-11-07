package com.kenpi.companion.ui

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import com.kenpi.companion.data.GatewayPreferences
import com.kenpi.companion.data.SecureGatewayPreferencesFactory

/**
 * Owns the [GatewayStore] so it (and its in-flight polling state) survives Activity recreation
 * from configuration changes such as screen rotation. [onCleared] only runs when the Activity is
 * actually finishing, not on a configuration change, so monitoring is torn down exactly once.
 */
class GatewayViewModel(preferences: GatewayPreferences?) : ViewModel() {
    val store = GatewayStore(
        initialBaseUrl = preferences?.loadBaseUrl().orEmpty(),
        persistBaseUrl = { value -> preferences?.saveBaseUrl(value) },
    )

    override fun onCleared() {
        store.close()
    }

    companion object {
        fun factory(context: Context): ViewModelProvider.Factory = viewModelFactory {
            initializer {
                // EncryptedSharedPreferences can fail to initialize on a device with a broken
                // Keystore; degrade to in-memory-only state rather than crashing the app.
                val preferences = runCatching {
                    SecureGatewayPreferencesFactory.create(context.applicationContext)
                }.getOrNull()
                GatewayViewModel(preferences)
            }
        }
    }
}
