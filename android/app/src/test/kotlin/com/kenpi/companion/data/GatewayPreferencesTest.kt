package com.kenpi.companion.data

import android.content.SharedPreferences
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class GatewayPreferencesTest {
    @Test
    fun loadBaseUrlReturnsNullWhenNothingSaved() {
        val preferences = GatewayPreferences(FakeSharedPreferences())

        assertNull(preferences.loadBaseUrl())
    }

    @Test
    fun saveBaseUrlPersistsAndLoadBaseUrlReturnsIt() {
        val preferences = GatewayPreferences(FakeSharedPreferences())

        preferences.saveBaseUrl("https://gateway.example")

        assertEquals("https://gateway.example", preferences.loadBaseUrl())
    }

    @Test
    fun saveBaseUrlOverwritesPreviousValue() {
        val preferences = GatewayPreferences(FakeSharedPreferences())

        preferences.saveBaseUrl("https://old.example")
        preferences.saveBaseUrl("https://new.example")

        assertEquals("https://new.example", preferences.loadBaseUrl())
    }

    @Test
    fun clearRemovesSavedValue() {
        val preferences = GatewayPreferences(FakeSharedPreferences())
        preferences.saveBaseUrl("https://gateway.example")

        preferences.clear()

        assertNull(preferences.loadBaseUrl())
    }

    /**
     * Minimal in-memory test double for [SharedPreferences]/[SharedPreferences.Editor]. This is
     * NOT a real persistence mechanism and must never be used outside of tests: it only exists so
     * [GatewayPreferences] can be unit tested without a real Android Keystore/EncryptedSharedPreferences.
     */
    private class FakeSharedPreferences : SharedPreferences {
        private val values = mutableMapOf<String, Any?>()

        override fun getAll(): MutableMap<String, *> = values
        override fun getString(key: String?, defValue: String?): String? =
            values[key] as? String ?: defValue

        override fun getStringSet(key: String?, defValues: MutableSet<String>?): MutableSet<String>? =
            @Suppress("UNCHECKED_CAST")
            (values[key] as? MutableSet<String>) ?: defValues

        override fun getInt(key: String?, defValue: Int): Int = values[key] as? Int ?: defValue
        override fun getLong(key: String?, defValue: Long): Long = values[key] as? Long ?: defValue
        override fun getFloat(key: String?, defValue: Float): Float = values[key] as? Float ?: defValue
        override fun getBoolean(key: String?, defValue: Boolean): Boolean =
            values[key] as? Boolean ?: defValue

        override fun contains(key: String?): Boolean = values.containsKey(key)

        override fun edit(): SharedPreferences.Editor = FakeEditor()

        override fun registerOnSharedPreferenceChangeListener(
            listener: SharedPreferences.OnSharedPreferenceChangeListener?,
        ) = Unit

        override fun unregisterOnSharedPreferenceChangeListener(
            listener: SharedPreferences.OnSharedPreferenceChangeListener?,
        ) = Unit

        private inner class FakeEditor : SharedPreferences.Editor {
            private val pending = mutableMapOf<String, Any?>()
            private val removals = mutableSetOf<String>()
            private var shouldClear = false

            override fun putString(key: String?, value: String?): SharedPreferences.Editor {
                if (key != null) pending[key] = value
                return this
            }

            override fun putStringSet(
                key: String?,
                values: MutableSet<String>?,
            ): SharedPreferences.Editor {
                if (key != null) pending[key] = values
                return this
            }

            override fun putInt(key: String?, value: Int): SharedPreferences.Editor {
                if (key != null) pending[key] = value
                return this
            }

            override fun putLong(key: String?, value: Long): SharedPreferences.Editor {
                if (key != null) pending[key] = value
                return this
            }

            override fun putFloat(key: String?, value: Float): SharedPreferences.Editor {
                if (key != null) pending[key] = value
                return this
            }

            override fun putBoolean(key: String?, value: Boolean): SharedPreferences.Editor {
                if (key != null) pending[key] = value
                return this
            }

            override fun remove(key: String?): SharedPreferences.Editor {
                if (key != null) removals.add(key)
                return this
            }

            override fun clear(): SharedPreferences.Editor {
                shouldClear = true
                return this
            }

            override fun commit(): Boolean {
                apply()
                return true
            }

            override fun apply() {
                if (shouldClear) values.clear()
                removals.forEach { values.remove(it) }
                values.putAll(pending)
            }
        }
    }
}
