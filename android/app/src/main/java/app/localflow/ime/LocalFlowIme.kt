package app.localflow.ime

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.inputmethodservice.InputMethodService
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.text.InputType
import android.view.Gravity
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.inputmethod.EditorInfo
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import app.localflow.LangMode
import app.localflow.MainActivity
import app.localflow.Settings
import app.localflow.audio.Recorder
import app.localflow.contrib.Contribution
import app.localflow.engine.EngineHost
import app.localflow.service.BubbleView
import kotlin.concurrent.thread
import kotlin.math.abs

/**
 * The LocalFlow voice keyboard. Tap (or hold) the capsule, speak, and the text is typed into the
 * app you are using. It needs no accessibility permission and draws nothing over other apps, so
 * banking and UPI apps that refuse to run next to "screen-reading" apps do not object to it.
 * A few keys (space, comma, full stop, backspace, enter, switch keyboard) cover the small edits.
 */
class LocalFlowIme : InputMethodService() {
    private lateinit var settings: Settings
    private lateinit var bubble: BubbleView
    private lateinit var modeChip: TextView
    private lateinit var hint: TextView
    private val main = Handler(Looper.getMainLooper())
    private var recorder: Recorder? = null
    private val parts = ArrayList<FloatArray>()
    private val pieces = ArrayList<java.util.concurrent.Future<EngineHost.Piece?>>()
    private var busy = false
    private var holdMode = false
    private var passwordField = false
    private val d get() = resources.displayMetrics.density

    override fun onCreate() {
        super.onCreate()
        settings = Settings(this)
    }

    override fun onCreateInputView(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.parseColor("#FBF8F3"))
            setPadding((12 * d).toInt(), (10 * d).toInt(), (12 * d).toInt(), (8 * d).toInt())
        }
        // top row: language mode chip + hint
        val top = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER_VERTICAL }
        modeChip = chip(settings.mode.label) {
            val all = LangMode.entries
            settings.mode = all[(settings.mode.ordinal + 1) % all.size]
            modeChip.text = settings.mode.label
            EngineHost.get(this).warmUp()
        }
        hint = TextView(this).apply { textSize = 12f; setTextColor(Color.parseColor("#7B7467")); gravity = Gravity.END }
        top.addView(modeChip)
        top.addView(hint, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        root.addView(top)

        // the capsule
        bubble = BubbleView(this).apply { idleLabel = "Tap to speak" }
        val capsuleBox = FrameLayout(this).apply { setOnTouchListener(::onCapsuleTouch) }
        capsuleBox.addView(bubble, FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT, bubble.maxH + (28 * d).toInt()))
        root.addView(capsuleBox, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, (84 * d).toInt()))
        bubble.layoutParams = FrameLayout.LayoutParams(bubble.maxW, bubble.maxH, Gravity.CENTER)

        // bottom row of small keys
        val keys = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = Gravity.CENTER }
        keys.addView(key("🌐", 1f) { switchKeyboard() })
        keys.addView(key(",", 1f) { type(",") })
        keys.addView(key("space", 3f) { type(" ") })
        keys.addView(key(".", 1f) { type(".") })
        keys.addView(key("?", 1f) { type("?") })
        val del = key("⌫", 1.3f) { }
        del.setOnTouchListener(::onBackspaceTouch)
        keys.addView(del)
        keys.addView(key("⏎", 1.3f) { enter() })
        root.addView(keys, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, (46 * d).toInt()))
        // Keep the keys clear of the gesture / navigation bar.
        root.setOnApplyWindowInsetsListener { v, ins ->
            val bottom = if (android.os.Build.VERSION.SDK_INT >= 30) ins.getInsets(android.view.WindowInsets.Type.navigationBars()).bottom else ins.systemWindowInsetBottom
            v.setPadding(v.paddingLeft, v.paddingTop, v.paddingRight, bottom + (8 * d).toInt())
            ins
        }
        return root
    }

    private fun chip(text: String, onClick: () -> Unit) = TextView(this).apply {
        this.text = text; textSize = 13f; setTextColor(Color.parseColor("#1F1B15")); typeface = Typeface.DEFAULT_BOLD
        background = GradientDrawable().apply { cornerRadius = 20 * d; setColor(Color.parseColor("#EFEAFF")) }
        setPadding((14 * d).toInt(), (6 * d).toInt(), (14 * d).toInt(), (6 * d).toInt())
        setOnClickListener { onClick() }
    }

    private fun key(label: String, weight: Float, onClick: () -> Unit) = TextView(this).apply {
        text = label; textSize = 16f; gravity = Gravity.CENTER; setTextColor(Color.parseColor("#1F1B15"))
        background = GradientDrawable().apply { cornerRadius = 10 * d; setColor(Color.parseColor("#F1ECE2")) }
        layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.MATCH_PARENT, weight).apply { setMargins((3 * d).toInt(), (3 * d).toInt(), (3 * d).toInt(), (3 * d).toInt()) }
        setOnClickListener { onClick() }
    }

    override fun onStartInputView(info: EditorInfo, restarting: Boolean) {
        super.onStartInputView(info, restarting)
        val variation = info.inputType and InputType.TYPE_MASK_VARIATION
        val cls = info.inputType and InputType.TYPE_MASK_CLASS
        passwordField = (cls == InputType.TYPE_CLASS_TEXT && (variation == InputType.TYPE_TEXT_VARIATION_PASSWORD ||
            variation == InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD || variation == InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD)) ||
            (cls == InputType.TYPE_CLASS_NUMBER && variation == InputType.TYPE_NUMBER_VARIATION_PASSWORD)
        hint.text = if (passwordField) "Voice is off in password fields" else "Tap the capsule and speak"
        modeChip.text = settings.mode.label
        bubble.show(BubbleView.State.IDLE)
        if (!passwordField) EngineHost.get(this).warmUp()
    }

    override fun onFinishInputView(finishingInput: Boolean) {
        super.onFinishInputView(finishingInput)
        cancelRecording()
    }

    override fun onDestroy() { cancelRecording(); super.onDestroy() }

    // ------------------------------------------------------------ the capsule: tap toggles, hold = push to talk
    private val holdRunnable = Runnable { if (!busy && recorder?.isRecording != true) { holdMode = true; startRecording() } }

    private fun onCapsuleTouch(v: View, e: MotionEvent): Boolean {
        when (e.action) {
            MotionEvent.ACTION_DOWN -> { holdMode = false; main.postDelayed(holdRunnable, 380) }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                main.removeCallbacks(holdRunnable)
                if (holdMode) stopAndProcess()
                else if (e.action == MotionEvent.ACTION_UP) { if (recorder?.isRecording == true) stopAndProcess() else if (!busy) startRecording() }
            }
        }
        return true
    }

    // ------------------------------------------------------------ recording
    private fun startRecording() {
        if (passwordField) { flash("Voice is off in password fields", 2); return }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            flash("Allow the microphone in LocalFlow", 2)
            startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            return
        }
        val host = EngineHost.get(this)
        if (!host.modelsInstalled()) {
            flash("Finish setup in LocalFlow", 2)
            startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            return
        }
        val r = Recorder(onLevel = { lv -> bubble.setLevel(lv) })
        if (!r.start()) { flash("Couldn't open the microphone", 2); return }
        recorder = r
        pieces.clear(); parts.clear()
        buzz(15)
        bubble.show(BubbleView.State.RECORDING)
        main.postDelayed(streamTick, 400)
    }

    /** While recording: whenever the speaker pauses after a few seconds, start transcribing that part right away. */
    private val streamTick = object : Runnable {
        override fun run() {
            val r = recorder ?: return
            if (!r.isRecording) return
            r.takeSegment(4f, 600)?.let {
                if (Contribution.enabled(this@LocalFlowIme)) parts.add(it)
                pieces.add(EngineHost.get(this@LocalFlowIme).submitPiece(it))
            }
            main.postDelayed(this, 400)
        }
    }

    private fun cancelRecording() {
        main.removeCallbacks(streamTick)
        recorder?.cancel(); recorder = null; pieces.clear(); parts.clear()
        if (::bubble.isInitialized) bubble.show(BubbleView.State.IDLE)
    }

    private fun stopAndProcess() {
        val r = recorder ?: return
        recorder = null
        main.removeCallbacks(streamTick)
        val pcm = r.stop()
        val shared = if (Contribution.enabled(this)) (parts + listOf(pcm)).let { l ->
            FloatArray(l.sumOf { it.size }).also { out -> var o = 0; for (a in l) { a.copyInto(out, o); o += a.size } } } else null
        parts.clear()
        buzz(10)
        busy = true
        bubble.show(BubbleView.State.WORKING, "Transcribing")
        thread(name = "lf-ime-dictate", isDaemon = true) {
            val host = EngineHost.get(this)
            val all = ArrayList(pieces).also { pieces.clear() }
            val heard = runCatching { all.add(host.submitPiece(pcm)); host.finish(all.map { it.get() }) }.getOrNull()
            main.post {
                busy = false
                when {
                    heard == null -> flash("Couldn't transcribe", 2)
                    heard.text.isEmpty() -> flash(if (heard.peak < 0.01f) "Mic is very quiet" else "No speech heard", if (heard.peak < 0.01f) 2 else 0)
                    else -> deliver(heard, host, shared)
                }
            }
        }
    }

    /** Type the first version at once; for longer English, swap in the AI-polished version when it is ready. */
    private fun deliver(heard: app.localflow.engine.EngineHost.Heard, host: EngineHost, shared: FloatArray?) {
        val ic = currentInputConnection
        if (ic == null) { flash("No text field", 2); return }
        val typed = tidy(heard.text)
        ic.commitText(typed, 1)
        val words = heard.text.split(Regex("\\s+")).size
        fun share(finalText: String, polished: Boolean) {
            if (shared == null || passwordField) return
            val (stt, hinglish, tier) = host.modelNames()
            Contribution.save(this, shared, Contribution.Meta(settings.mode.name, stt, hinglish, tier, heard.sttMs, heard.audioSec,
                heard.raw, finalText, polished))
        }
        if (!heard.needsPolish) { share(heard.text, false); flash("$words words · %.1fs".format(heard.sttMs / 1000f), 1); return }
        busy = true
        bubble.show(BubbleView.State.WORKING, "Polishing")
        thread(name = "lf-ime-polish", isDaemon = true) {
            val polished = runCatching { host.polish(heard.text) }.getOrNull()
            main.post {
                busy = false
                var replaced = false
                val c = currentInputConnection
                if (polished != null && polished != heard.text && c != null) {
                    // only swap if our text is still right before the cursor: never overwrite what the person did meanwhile
                    val before = c.getTextBeforeCursor(typed.length, 0)?.toString()
                    if (before == typed) {
                        val fresh = if (typed.trimStart().firstOrNull()?.isLowerCase() == true)
                            polished.replaceFirstChar { it.lowercase() } else polished
                        c.beginBatchEdit(); c.deleteSurroundingText(typed.length, 0)
                        c.commitText(if (typed.startsWith(" ")) " $fresh" else fresh, 1); c.endBatchEdit()
                        replaced = true
                    }
                }
                share(if (replaced && polished != null) polished else heard.text, replaced)
                flash("$words words" + if (replaced) " · polished" else "", 1)
            }
        }
    }

    /** Spacing and capitalisation next to what is already in the field. */
    private fun tidy(text: String): String {
        val before = currentInputConnection?.getTextBeforeCursor(40, 0)?.toString() ?: ""
        var t = text.trim()
        if (before.isNotEmpty() && !before.last().isWhitespace()) t = " $t"
        val end = before.trimEnd().lastOrNull()
        if (before.isNotBlank() && end != '.' && end != '?' && end != '!') {
            t = Regex("^(\\s*)([A-Z])(?![A-Z])").replace(t) { m -> m.groupValues[1] + m.groupValues[2].lowercase() }
        }
        return t
    }

    private fun flash(msg: String, tone: Int) {
        bubble.show(BubbleView.State.MESSAGE, msg, tone)
        main.postDelayed({ if (!busy && recorder?.isRecording != true) bubble.show(BubbleView.State.IDLE) }, 1700)
    }

    private fun buzz(ms: Long) {
        if (!settings.haptics) return
        (getSystemService(Context.VIBRATOR_SERVICE) as Vibrator).vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE))
    }

    // ------------------------------------------------------------ small keys
    private fun type(s: String) { currentInputConnection?.commitText(s, 1) }

    private fun enter() {
        val ic = currentInputConnection ?: return
        val action = currentInputEditorInfo?.imeOptions?.and(EditorInfo.IME_MASK_ACTION) ?: EditorInfo.IME_ACTION_NONE
        val multiline = (currentInputEditorInfo?.inputType ?: 0) and InputType.TYPE_TEXT_FLAG_MULTI_LINE != 0
        if (!multiline && action != EditorInfo.IME_ACTION_NONE && action != EditorInfo.IME_ACTION_UNSPECIFIED) ic.performEditorAction(action)
        else sendDownUpKeyEvents(KeyEvent.KEYCODE_ENTER)
    }

    private fun switchKeyboard() {
        if (!switchToPreviousInputMethod()) {
            (getSystemService(Context.INPUT_METHOD_SERVICE) as android.view.inputmethod.InputMethodManager).showInputMethodPicker()
        }
    }

    private val repeatDelete = object : Runnable {
        override fun run() { sendDownUpKeyEvents(KeyEvent.KEYCODE_DEL); main.postDelayed(this, 60) }
    }

    private fun onBackspaceTouch(v: View, e: MotionEvent): Boolean {
        when (e.action) {
            MotionEvent.ACTION_DOWN -> { v.isPressed = true; sendDownUpKeyEvents(KeyEvent.KEYCODE_DEL); main.postDelayed(repeatDelete, ViewConfiguration.getLongPressTimeout().toLong()) }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> { v.isPressed = false; main.removeCallbacks(repeatDelete) }
        }
        return true
    }
}
