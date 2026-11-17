package app.localflow.service

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import android.view.View
import kotlin.math.abs
import kotlin.math.exp
import kotlin.math.max
import kotlin.math.min
import kotlin.math.sin

/**
 * The floating dictation bubble: a tiny translucent glass capsule at rest that grows into a
 * waveform while you speak. Motion is time based, so it stays smooth at 60/90/120 Hz, and
 * nothing is drawn while it rests.
 */
class BubbleView(ctx: Context) : View(ctx) {
    enum class State { IDLE, RECORDING, WORKING, MESSAGE }

    var state = State.IDLE
        private set
    private var message = ""
    private var tone = 0                       // 0 info, 1 ok, 2 warn
    private val d = resources.displayMetrics.density
    private val levels = FloatArray(BARS)
    private var level = 0f
    private var w = 44f * d
    private var h = 14f * d
    private var tw = w
    private var th = h
    private var lastT = System.nanoTime()
    private val t0 = lastT
    private val anim = ValueAnimator.ofFloat(0f, 1f).apply {
        duration = 1000; repeatCount = ValueAnimator.INFINITE
        addUpdateListener { tick() }
    }

    private val bg = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.argb(176, 16, 16, 20) }
    private val border = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE; strokeWidth = d; color = Color.argb(48, 255, 255, 255)
    }
    private val spinner = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE; strokeWidth = 2f * d; color = Color.WHITE; strokeCap = Paint.Cap.ROUND
    }
    private val ink = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.argb(235, 255, 255, 255) }
    private val red = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.rgb(255, 84, 104) }
    private val dot = Paint(Paint.ANTI_ALIAS_FLAG)
    private val text = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE; textSize = 13f * d; typeface = Typeface.create("sans-serif-medium", Typeface.NORMAL)
    }
    private val rect = RectF()

    init { setLayerType(LAYER_TYPE_HARDWARE, null) }

    /** Largest size the bubble ever needs, so the window never has to be resized while animating. */
    val maxW get() = (260f * d).toInt()
    val maxH get() = (46f * d).toInt()

    fun setLevel(v: Float) { level = min(1f, v * 6f) }

    fun show(s: State, msg: String = "", tone: Int = 0) {
        state = s; message = msg; this.tone = tone
        when (s) {
            State.IDLE -> { tw = 44f * d; th = 14f * d }
            State.RECORDING -> { tw = 150f * d; th = 40f * d }
            State.WORKING, State.MESSAGE -> {
                val label = if (s == State.WORKING) "$msg..." else msg
                tw = (text.measureText(label) + 56f * d).coerceAtMost(maxW.toFloat()); th = 36f * d
            }
        }
        if (!anim.isStarted) { lastT = System.nanoTime(); anim.start() }
    }

    private fun tick() {
        val now = System.nanoTime()
        val dt = ((now - lastT) / 1e9f).coerceIn(0.001f, 0.05f)
        lastT = now
        val k = 1f - exp(-20f * dt)
        w += (tw - w) * k
        h += (th - h) * k
        for (i in 0 until BARS - 1) levels[i] = levels[i + 1]
        levels[BARS - 1] += (level - levels[BARS - 1]) * (1f - exp(-30f * dt))
        invalidate()
        if (abs(w - tw) < 0.3f && abs(h - th) < 0.3f && state == State.IDLE) anim.cancel()   // at rest: no frames
    }

    override fun onMeasure(wSpec: Int, hSpec: Int) = setMeasuredDimension(maxW, maxH)

    override fun onDraw(c: Canvas) {
        val cx = width / 2f
        val cy = height / 2f
        rect.set(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        val r = min(h / 2, 20f * d)
        bg.alpha = if (state == State.IDLE) 150 else 176
        c.drawRoundRect(rect, r, r, bg)
        c.drawRoundRect(rect, r, r, border)
        val t = (System.nanoTime() - t0) / 1e9f
        when (state) {
            State.IDLE -> for (i in -2..2) c.drawCircle(cx + i * 5f * d, cy, 1.4f * d, ink)
            State.RECORDING -> {
                val pulse = 0.5f + 0.5f * sin(t * 4f)
                red.alpha = (215 + 40 * pulse).toInt()
                c.drawCircle(rect.left + 15f * d, cy, (3.4f + 0.6f * pulse) * d, red)
                val x0 = rect.left + 30f * d
                val x1 = rect.right - 14f * d
                val step = (x1 - x0) / BARS
                for (i in 0 until BARS) {
                    val bh = max(3f * d, levels[i] * (h - 14f * d))
                    ink.alpha = if (levels[i] > 0.05f) 235 else 90
                    val x = x0 + i * step
                    c.drawRoundRect(x, cy - bh / 2, x + step * 0.5f, cy + bh / 2, 2f * d, 2f * d, ink)
                }
                ink.alpha = 235
            }
            State.WORKING -> {
                val rr = 6f * d
                val l = rect.left + 16f * d - rr
                c.drawArc(l, cy - rr, l + 2 * rr, cy + rr, (t * 420f) % 360f, 100f, false, spinner)
                val dots = ".".repeat(1 + ((t * 2.5f).toInt() % 3))
                c.drawText(message + dots, rect.left + 32f * d, cy + text.textSize / 3, text)
            }
            State.MESSAGE -> {
                dot.color = when (tone) { 1 -> Color.rgb(74, 222, 128); 2 -> Color.rgb(251, 191, 36); else -> Color.rgb(210, 210, 220) }
                c.drawCircle(rect.left + 16f * d, cy, 3.6f * d, dot)
                c.drawText(message, rect.left + 28f * d, cy + text.textSize / 3, text)
            }
        }
    }

    override fun onDetachedFromWindow() { anim.cancel(); super.onDetachedFromWindow() }

    companion object { const val BARS = 18 }
}
