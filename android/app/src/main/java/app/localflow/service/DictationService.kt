package app.localflow.service

import android.Manifest
import android.accessibilityservice.AccessibilityButtonController
import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.PixelFormat
import android.graphics.Rect
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import android.util.Log
import android.view.Gravity
import android.view.MotionEvent
import android.view.ViewConfiguration
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityWindowInfo
import app.localflow.MainActivity
import app.localflow.Settings
import app.localflow.audio.Recorder
import app.localflow.contrib.Contribution
import app.localflow.engine.EngineHost
import kotlin.concurrent.thread
import kotlin.math.abs

/**
 * Shows the dictation bubble over other apps while a text field is focused, records on tap or
 * hold, and types the result into that field. It reads nothing but the focused field.
 *
 * Also answers Android's accessibility button (the small round button at the screen edge): one tap
 * starts dictation, the next stops it, so dictation works even when the bubble is not on screen.
 */
class DictationService : AccessibilityService() {
    private lateinit var wm: WindowManager
    private lateinit var bubble: BubbleView
    private lateinit var lp: WindowManager.LayoutParams
    private lateinit var settings: Settings
    private val main = Handler(Looper.getMainLooper())
    private var recorder: Recorder? = null
    private val parts = ArrayList<FloatArray>()      // all the audio of this recording, kept only if sharing is on
    private val pieces = ArrayList<java.util.concurrent.Future<EngineHost.Piece?>>()   // transcribed while still speaking
    private var attached = false
    private var busy = false
    private var dragged = false
    private var holdMode = false

    override fun onServiceConnected() {
        settings = Settings(this)
        wm = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        bubble = BubbleView(this)
        lp = WindowManager.LayoutParams(
            bubble.maxW, bubble.maxH,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                WindowManager.LayoutParams.FLAG_HARDWARE_ACCELERATED,
            PixelFormat.TRANSLUCENT,
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_ALWAYS
        }
        bubble.setOnTouchListener(::onTouch)
        instance = this
        EngineHost.get(this)                     // detect the device profile early
        if (Build.VERSION.SDK_INT >= 26) {
            accessibilityButtonController.registerAccessibilityButtonCallback(
                object : AccessibilityButtonController.AccessibilityButtonCallback() {
                    override fun onClicked(controller: AccessibilityButtonController) = toggleDictation()
                })
        }
        Log.i(TAG, "connected")
    }

    override fun onUnbind(intent: Intent?): Boolean {
        cancelRecording(); detach(); instance = null
        return super.onUnbind(intent)
    }

    override fun onInterrupt() { cancelRecording() }

    // ------------------------------------------------------------ when to show the bubble
    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null || !::settings.isInitialized) return
        when (event.eventType) {
            AccessibilityEvent.TYPE_VIEW_FOCUSED, AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED,
            AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED, AccessibilityEvent.TYPE_VIEW_TEXT_CHANGED -> refreshSoon()
        }
    }

    private val refresh = Runnable { refreshBubble() }

    /** The accessibility tree of a new window is often not ready for ~100 ms; look again a few times. */
    private fun refreshSoon() {
        main.removeCallbacks(refresh)
        for (ms in longArrayOf(120, 400, 900)) main.postDelayed(refresh, ms)
    }

    private fun refreshBubble() {
        if (busy || recorder?.isRecording == true) return
        val field = TextInserter.focusedField(this)
        val keyboard = keyboardBounds()
        val show = if (settings.bubbleOnlyInTextFields) field != null && keyboard != null else true
        if (!show) { detach(); return }
        attach()
        place(keyboard)
        if (bubble.state != BubbleView.State.IDLE && bubble.state != BubbleView.State.MESSAGE) bubble.show(BubbleView.State.IDLE)
        EngineHost.get(this).warmUp()
    }

    private fun keyboardBounds(): Rect? {
        for (w in windows) if (w.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD) {
            val r = Rect(); w.getBoundsInScreen(r); if (r.height() > 0) return r
        }
        return null
    }

    private fun place(keyboard: Rect?) {
        val dm = resources.displayMetrics
        val bottom = keyboard?.top ?: (dm.heightPixels - (96 * dm.density).toInt())
        val x = if (settings.bubbleX >= 0) settings.bubbleX else (dm.widthPixels - lp.width) / 2
        lp.x = x.coerceIn(0, dm.widthPixels - lp.width)
        lp.y = bottom - lp.height - (6 * dm.density).toInt()
        if (attached) runCatching { wm.updateViewLayout(bubble, lp) }
    }

    private fun attach() {
        if (attached) return
        runCatching { wm.addView(bubble, lp); attached = true }.onFailure { Log.e(TAG, "addView failed", it) }
    }

    private fun detach() {
        if (!attached) return
        runCatching { wm.removeView(bubble) }
        attached = false
    }

    // ------------------------------------------------------------ touch: tap toggles, hold = push to talk, drag moves
    private var downRawX = 0f
    private var startLpX = 0
    private val holdRunnable = Runnable {
        if (!dragged && !busy && recorder?.isRecording != true) { holdMode = true; startRecording() }
    }

    private fun onTouch(v: android.view.View, e: MotionEvent): Boolean {
        val slop = ViewConfiguration.get(this).scaledTouchSlop
        when (e.action) {
            MotionEvent.ACTION_DOWN -> {
                dragged = false; holdMode = false
                downRawX = e.rawX; startLpX = lp.x
                main.postDelayed(holdRunnable, 380)
            }
            MotionEvent.ACTION_MOVE -> if (abs(e.rawX - downRawX) > slop * 2 && !holdMode) {
                dragged = true; main.removeCallbacks(holdRunnable)
                lp.x = (startLpX + (e.rawX - downRawX).toInt()).coerceIn(0, resources.displayMetrics.widthPixels - lp.width)
                runCatching { wm.updateViewLayout(bubble, lp) }
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                main.removeCallbacks(holdRunnable)
                when {
                    dragged -> settings.bubbleX = lp.x
                    holdMode -> stopAndProcess()
                    e.action == MotionEvent.ACTION_UP -> toggleDictation()
                }
            }
        }
        return true
    }

    /** Bubble tap or the system accessibility button: start, or stop and type. */
    private fun toggleDictation() {
        if (recorder?.isRecording == true) { stopAndProcess(); return }
        if (busy) return
        if (!attached) { attach(); place(keyboardBounds()) }
        startRecording()
    }

    // ------------------------------------------------------------ recording and typing
    private fun startRecording() {
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            flash("Allow the microphone in LocalFlow", 2)
            openApp()
            return
        }
        val host = EngineHost.get(this)
        if (!host.modelsInstalled()) {
            flash("Finish setup in LocalFlow", 2)
            openApp()
            return
        }
        RecordingService.start(this)
        val r = Recorder(onLevel = { lv -> bubble.setLevel(lv) })
        if (!r.start()) { RecordingService.stop(this); flash("Couldn't open the microphone", 2); return }
        recorder = r
        pieces.clear(); parts.clear()
        buzz(15)
        bubble.show(BubbleView.State.RECORDING)
        host.warmUp()
        main.postDelayed(streamTick, 400)
    }

    /** While recording: whenever the speaker pauses after a few seconds, start transcribing that part right away. */
    private val streamTick = object : Runnable {
        override fun run() {
            val r = recorder ?: return
            if (!r.isRecording) return
            r.takeSegment(4f, 600)?.let {
                if (Contribution.enabled(this@DictationService)) parts.add(it)
                pieces.add(EngineHost.get(this@DictationService).submitPiece(it))
            }
            main.postDelayed(this, 400)
        }
    }

    private fun openApp() = startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))

    private fun cancelRecording() {
        main.removeCallbacks(streamTick)
        recorder?.cancel(); recorder = null; pieces.clear(); parts.clear()
        RecordingService.stop(this)
        main.post { if (::bubble.isInitialized) bubble.show(BubbleView.State.IDLE) }
    }

    private fun stopAndProcess() {
        val r = recorder ?: return
        recorder = null
        main.removeCallbacks(streamTick)
        val pcm = r.stop()
        val sharedAudio = if (Contribution.enabled(this)) (parts + listOf(pcm)).let { l -> FloatArray(l.sumOf { it.size }).also { out -> var o = 0; for (a in l) { a.copyInto(out, o); o += a.size } } } else null
        parts.clear()
        RecordingService.stop(this)
        buzz(10)
        busy = true
        bubble.show(BubbleView.State.WORKING, "Transcribing")
        thread(name = "lf-dictate", isDaemon = true) {
            val host = EngineHost.get(this)
            // Parts already transcribed while speaking, plus the last bit: only that tail is left to wait for.
            val all = ArrayList(pieces).also { pieces.clear() }
            val heard = runCatching {
                all.add(host.submitPiece(pcm))
                host.finish(all.map { it.get() })
            }.getOrNull()
            lastHeard = heard
            main.post {
                busy = false
                when {
                    heard == null -> flash("Couldn't transcribe", 2)
                    heard.text.isEmpty() ->
                        flash(if (heard.peak < 0.01f) "Mic is very quiet" else "No speech heard", if (heard.peak < 0.01f) 2 else 0)
                    else -> deliver(heard, host, sharedAudio)
                }
            }
        }
    }

    /** Type the first version at once; for longer English, swap in the AI-polished version when it is ready. */
    private fun deliver(heard: EngineHost.Heard, host: EngineHost, sharedAudio: FloatArray?) {
        // Password fields are never shared, whatever the setting says.
        val isPassword = TextInserter.focusedField(this)?.isPassword == true
        val ok = TextInserter.insert(this, heard.text)
        fun share(finalText: String, polished: Boolean) {
            if (sharedAudio == null || isPassword || !ok) return
            val (stt, hinglish, tier) = host.modelNames()
            Contribution.save(this, sharedAudio, Contribution.Meta(settings.mode.name, stt, hinglish, tier, heard.sttMs,
                heard.audioSec, heard.raw, finalText, polished))
        }
        val words = heard.text.split(Regex("\\s+")).size
        if (!ok) { flash("Copied: tap the field and paste", 2); return }
        if (!heard.needsPolish) { share(heard.text, false); flash("$words words · %.1fs".format(heard.sttMs / 1000f), 1); return }
        busy = true
        bubble.show(BubbleView.State.WORKING, "Polishing")
        val typed = TextInserter.lastInserted
        thread(name = "lf-polish", isDaemon = true) {
            val polished = runCatching { host.polish(heard.text) }.getOrNull()
            main.post {
                busy = false
                var replaced = false
                if (polished != null && polished != heard.text && typed != null) {
                    replaced = TextInserter.replace(this, typed, polished)
                }
                share(if (replaced && polished != null) polished else heard.text, replaced)
                flash("$words words" + if (replaced) " · polished" else "", 1)
            }
        }
    }

    private fun flash(msg: String, tone: Int) {
        bubble.show(BubbleView.State.MESSAGE, msg, tone)
        main.postDelayed({
            if (!busy && recorder?.isRecording != true) {
                bubble.show(BubbleView.State.IDLE)
                refreshBubble()
            }
        }, 1700)
    }

    private fun buzz(ms: Long) {
        if (!settings.haptics) return
        val v = getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
        v.vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE))
    }

    companion object {
        private const val TAG = "LocalFlow.service"
        @Volatile var instance: DictationService? = null
        @Volatile var lastHeard: EngineHost.Heard? = null
        fun isRunning() = instance != null
    }
}
