package app.localflow.service

import android.accessibilityservice.AccessibilityService
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Build
import android.os.Bundle
import android.view.accessibility.AccessibilityNodeInfo
import kotlin.math.max
import kotlin.math.min

/** Types text into whichever field has input focus, at the cursor, replacing any selection. */
object TextInserter {
    /** The text the last [insert] actually typed (without the spacing it added). */
    @Volatile var lastInserted: String? = null

    fun focusedField(svc: AccessibilityService): AccessibilityNodeInfo? {
        val root = svc.rootInActiveWindow ?: return null
        val n = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
        if (n != null && n.isEditable) return n
        // Compose and some web views don't answer findFocus: look for a focused, editable node ourselves.
        return findEditable(root, 0)
    }

    private fun findEditable(node: AccessibilityNodeInfo, depth: Int): AccessibilityNodeInfo? {
        if (depth > 40) return null
        if (node.isEditable && (node.isFocused || node.isAccessibilityFocused)) return node
        for (i in 0 until node.childCount) {
            val c = node.getChild(i) ?: continue
            findEditable(c, depth + 1)?.let { return it }
        }
        return null
    }

    fun insert(svc: AccessibilityService, text: String): Boolean {
        val node = focusedField(svc) ?: return false
        val showingHint = Build.VERSION.SDK_INT >= 26 && node.isShowingHintText
        val cur = if (showingHint) "" else node.text?.toString() ?: ""
        var s = if (showingHint) 0 else node.textSelectionStart
        var e = if (showingHint) 0 else node.textSelectionEnd
        if (s < 0 || e < 0) { s = cur.length; e = cur.length }
        val a = min(s, e).coerceIn(0, cur.length)
        val b = max(s, e).coerceIn(0, cur.length)

        val before = cur.substring(0, a)
        val after = cur.substring(b)
        var ins = text.trim()
        if (before.isNotEmpty() && !before.last().isWhitespace()) ins = " $ins"
        if (after.isNotEmpty() && !after.first().isWhitespace() && after.first() !in ".,!?;:") ins += " "
        // Continuing a sentence: no capital letter at the start unless we are after a full stop.
        val end = before.trimEnd().lastOrNull()
        if (before.isNotBlank() && end != '.' && end != '?' && end != '!') {
            ins = Regex("^(\\s*)([A-Z])(?![A-Z])").replace(ins) { m -> m.groupValues[1] + m.groupValues[2].lowercase() }
        }
        lastInserted = ins.trim()
        val newText = before + ins + after
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, newText)
        }
        if (node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) {
            val caret = a + ins.length
            val sel = Bundle().apply {
                putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, caret)
                putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, caret)
            }
            node.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, sel)
            return true
        }
        // Some fields refuse SET_TEXT: paste through the clipboard instead.
        val cm = svc.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
        cm.setPrimaryClip(ClipData.newPlainText("dictation", ins.trim()))
        return node.performAction(AccessibilityNodeInfo.ACTION_PASTE)
    }

    /**
     * Swap the text we typed earlier ([old]) for a better version ([new]), but only if the field still
     * contains it exactly once, so nothing the user typed in the meantime is ever overwritten.
     */
    fun replace(svc: AccessibilityService, old: String, new: String): Boolean {
        val node = focusedField(svc) ?: return false
        val cur = node.text?.toString() ?: return false
        val idx = cur.indexOf(old)
        if (idx < 0 || cur.indexOf(old, idx + 1) >= 0) return false
        var fresh = new.trim()
        // Keep the capitalisation decision insert() made (mid-sentence dictation starts lowercase).
        if (old.firstOrNull()?.isLowerCase() == true) {
            fresh = Regex("^([A-Z])(?![A-Z])").replace(fresh) { m -> m.groupValues[1].lowercase() }
        }
        val newText = cur.substring(0, idx) + fresh + cur.substring(idx + old.length)
        val args = Bundle().apply { putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, newText) }
        if (!node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) return false
        val caret = idx + fresh.length
        val sel = Bundle().apply {
            putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, caret)
            putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, caret)
        }
        node.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, sel)
        lastInserted = fresh
        return true
    }
}
