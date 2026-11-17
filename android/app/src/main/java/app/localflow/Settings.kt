package app.localflow

import android.content.Context

enum class LangMode(val label: String, val hint: String) {
    HINGLISH("Hinglish", "Hindi + English mixed, written in English letters"),
    ENGLISH("English", "English only"),
    HINDI("Hindi", "Hindi in Devanagari"),
    AUTO("Auto-detect", "Any language Whisper knows"),
}

/** Tiny SharedPreferences wrapper; everything the user can change lives here. */
class Settings(ctx: Context) {
    private val p = ctx.getSharedPreferences("localflow", Context.MODE_PRIVATE)

    var mode: LangMode
        get() = runCatching { LangMode.valueOf(p.getString("mode", null) ?: "") }.getOrDefault(LangMode.HINGLISH)
        set(v) = p.edit().putString("mode", v.name).apply()
    var cleanup: Boolean
        get() = p.getBoolean("cleanup", true)
        set(v) = p.edit().putBoolean("cleanup", v).apply()
    var bubbleOnlyInTextFields: Boolean
        get() = p.getBoolean("bubble_fields_only", true)
        set(v) = p.edit().putBoolean("bubble_fields_only", v).apply()
    /** Use the big speech model (better Hindi and accents, about 3x slower). */
    var accurate: Boolean
        get() = p.getBoolean("accurate", false)
        set(v) = p.edit().putBoolean("accurate", v).apply()
    var haptics: Boolean
        get() = p.getBoolean("haptics", true)
        set(v) = p.edit().putBoolean("haptics", v).apply()
    var bubbleX: Int
        get() = p.getInt("bubble_x", -1)
        set(v) = p.edit().putInt("bubble_x", v).apply()
    var bubbleY: Int
        get() = p.getInt("bubble_y", -1)
        set(v) = p.edit().putInt("bubble_y", v).apply()
    var modelsReady: Boolean
        get() = p.getBoolean("models_ready", false)
        set(v) = p.edit().putBoolean("models_ready", v).apply()
    var dictionary: List<String>
        get() = (p.getString("dictionary", "") ?: "").split("\n").filter { it.isNotBlank() }
        set(v) = p.edit().putString("dictionary", v.joinToString("\n")).apply()

    // ---- "Help improve LocalFlow" (off unless the person turns it on and accepts the consent text)
    var contribute: Boolean
        get() = p.getBoolean("contribute", false)
        set(v) = p.edit().putBoolean("contribute", v).apply()
    var consentVersion: String
        get() = p.getString("consent_version", "") ?: ""
        set(v) = p.edit().putString("consent_version", v).apply()
    var installId: String
        get() = p.getString("install_id", "") ?: ""
        set(v) = p.edit().putString("install_id", v).apply()
    var installToken: String
        get() = p.getString("install_token", "") ?: ""
        set(v) = p.edit().putString("install_token", v).apply()
}
