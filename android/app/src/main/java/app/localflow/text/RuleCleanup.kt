package app.localflow.text

/**
 * Fast, language-agnostic cleanup used for Hinglish and on phones without enough
 * RAM for the cleanup model: fillers, stutters, capitals, spacing, closing full stop.
 */
object RuleCleanup {
    private val fillers = Regex("""(?i)\b(?:u+m+|u+h+|e+r+m*|a+h+|h+m+)\b[,.]?\s*""")
    private val repeated = Regex("""(?i)\b(\w+)(\s+\1\b)+""")
    private val spaceBefore = Regex("""\s+([,.!?;:])""")
    private val manySpaces = Regex("""[ \t]{2,}""")
    private val standaloneI = Regex("""\bi\b""")
    private val sentenceStart = Regex("""(^|[.!?]\s+)([a-z])""")

    fun clean(raw: String, keepFillers: Boolean = false): String {
        var t = raw.trim()
        if (t.isEmpty()) return t
        if (!keepFillers) t = fillers.replace(t, "")
        t = repeated.replace(t) { it.groupValues[1] }
        t = spaceBefore.replace(t, "$1")
        t = manySpaces.replace(t, " ").trim()
        t = standaloneI.replace(t, "I")
        t = sentenceStart.replace(t) { it.groupValues[1] + it.groupValues[2].uppercase() }
        if (t.isNotEmpty() && t.last().isLetterOrDigit() && t.length > 3) t += "."
        return t
    }
}
