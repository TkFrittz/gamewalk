package com.gamewalk

import android.content.Context

/**
 * Settings the phone owns.
 *
 * The PC owns everything else -- tiers, keybinds, hold timing, detector
 * tuning -- because there has to be exactly one authoritative copy or the two
 * drift apart. What lives here is only what the phone needs *before* it can
 * reach the PC to ask.
 */
class Prefs(context: Context) {
    private val sp = context.getSharedPreferences("gamewalk", Context.MODE_PRIVATE)

    var host: String
        get() = sp.getString("host", "") ?: ""
        set(v) = sp.edit().putString("host", v).apply()

    var port: Int
        get() = sp.getInt("port", 5599)
        set(v) = sp.edit().putInt("port", v).apply()

    var discoveryPort: Int
        get() = sp.getInt("discovery_port", 5598)
        set(v) = sp.edit().putInt("discovery_port", v).apply()

    var token: String
        get() = sp.getString("token", "") ?: ""
        set(v) = sp.edit().putString("token", v).apply()

    /** "A" = hardware step detector, "B" = raw accelerometer, PC detects. */
    var mode: String
        get() = sp.getString("mode", "A") ?: "A"
        set(v) = sp.edit().putString("mode", v).apply()

    /** Mode B sample rate. 50Hz is plenty: a footfall spike is ~100ms wide. */
    var accHz: Int
        get() = sp.getInt("acc_hz", 50)
        set(v) = sp.edit().putInt("acc_hz", v).apply()

    val paired: Boolean get() = host.isNotEmpty() && token.isNotEmpty()

    fun clearPairing() {
        sp.edit().remove("host").remove("token").apply()
    }
}
