package app.localflow.audio

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.sqrt

/** Energy-based voice trimming: cuts leading and trailing silence so Whisper decodes less audio. */
object Vad {
    private const val FRAME = 320                 // 20 ms at 16 kHz

    fun rms(x: FloatArray, from: Int = 0, to: Int = x.size): Float {
        if (to <= from) return 0f
        var s = 0.0
        for (i in from until to) s += x[i] * x[i]
        return sqrt(s / (to - from)).toFloat()
    }

    /** Loudness stats for diagnostics. */
    data class Stats(val peak: Float, val rms: Float)

    fun stats(x: FloatArray): Stats {
        var p = 0f
        for (v in x) p = max(p, abs(v))
        return Stats(p, rms(x))
    }

    /**
     * Returns the speech part with ~300 ms of padding. Deliberately forgiving: phone microphones
     * can be quiet, so only true silence is dropped; when in doubt the whole clip goes to Whisper,
     * which does its own silence handling.
     */
    fun trim(pcm: FloatArray): FloatArray {
        val frames = pcm.size / FRAME
        if (frames < 5) return FloatArray(0)
        val e = FloatArray(frames) { rms(pcm, it * FRAME, (it + 1) * FRAME) }
        val sorted = e.sortedArray()
        val noise = sorted[(frames * 0.1).toInt()]
        val loud = sorted[(frames * 0.95).toInt().coerceAtMost(frames - 1)]
        if (loud < 0.0015f) return FloatArray(0)                      // truly silent
        val thr = max(noise * 2.0f, loud * 0.06f).coerceAtLeast(0.0012f)
        val first0 = e.indexOfFirst { it > thr }
        val last0 = e.indexOfLast { it > thr }
        if (first0 < 0) return pcm
        val pad = 15
        val first = (first0 - pad).coerceAtLeast(0)
        val last = (last0 + pad).coerceAtMost(frames - 1)
        return pcm.copyOfRange(first * FRAME, (last + 1) * FRAME)
    }

    /**
     * Share of 20 ms frames that carry speech-level energy. Pieces with almost none (a tail of room noise)
     * make small speech models invent words ("Thik hai?"), so the caller skips them.
     */
    fun speechFraction(x: FloatArray): Float {
        val frames = x.size / FRAME
        if (frames == 0) return 0f
        val e = FloatArray(frames) { rms(x, it * FRAME, (it + 1) * FRAME) }
        val loud = e.sortedArray()[(frames * 0.95).toInt().coerceAtMost(frames - 1)]
        val thr = max(loud * 0.25f, 0.004f)
        return e.count { it > thr }.toFloat() / frames
    }

    /** Peak-normalise quiet recordings (phone mics are often low) without amplifying noise much. */
    fun normalise(x: FloatArray): FloatArray {
        var peak = 0f
        for (v in x) peak = max(peak, abs(v))
        if (peak < 0.004f || peak > 0.5f) return x
        val g = (0.6f / peak).coerceAtMost(40f)
        return FloatArray(x.size) { x[it] * g }
    }
}
