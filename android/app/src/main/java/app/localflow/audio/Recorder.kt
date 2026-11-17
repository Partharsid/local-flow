package app.localflow.audio

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import kotlin.concurrent.thread

/**
 * Uses the plain MIC source: on many phones (measured on a Nothing Phone) VOICE_RECOGNITION is 8x quieter
 * because of aggressive noise suppression, which made normal speech look like silence.
 */
class Recorder(private val source: Int = MediaRecorder.AudioSource.MIC, private val onLevel: (Float) -> Unit) {
    @Volatile private var running = false
    private var worker: Thread? = null
    private var rec: AudioRecord? = null
    private val chunks = ArrayList<ShortArray>()

    val isRecording get() = running

    @SuppressLint("MissingPermission")
    fun start(): Boolean {
        if (running) return true
        val min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val r = try {
            AudioRecord(source, RATE, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT, maxOf(min, RATE))      // 1 s buffer: no overruns when the UI hitches
        } catch (e: Exception) { return false }
        if (r.state != AudioRecord.STATE_INITIALIZED) { r.release(); return false }
        rec = r
        chunks.clear()
        running = true
        r.startRecording()
        worker = thread(name = "lf-record", isDaemon = true) {
            val buf = ShortArray(RATE / 25)                              // 40 ms
            while (running) {
                val n = r.read(buf, 0, buf.size)
                if (n <= 0) continue
                synchronized(chunks) { chunks.add(buf.copyOf(n)) }
                var s = 0.0
                for (i in 0 until n) { val v = buf[i] / 32768.0; s += v * v }
                onLevel(Math.sqrt(s / n).toFloat())
            }
        }
        return true
    }

    /**
     * Streaming: when at least [minSec] seconds are buffered and the speaker has just paused for
     * [pauseMs], hand back (and remove) everything so far so it can be transcribed while the person
     * keeps talking. Returns null when it isn't time yet.
     */
    fun takeSegment(minSec: Float, pauseMs: Int): FloatArray? {
        val all = synchronized(chunks) {
            val total = chunks.sumOf { it.size }
            if (total < minSec * RATE) return null
            chunks.toList()
        }
        val tail = pauseMs * RATE / 1000
        var need = tail
        var sTail = 0.0
        var nTail = 0
        for (c in all.asReversed()) {
            for (i in c.indices.reversed()) {
                val v = c[i] / 32768.0; sTail += v * v; nTail++
                if (--need <= 0) break
            }
            if (need <= 0) break
        }
        var sAll = 0.0
        var nAll = 0
        for (c in all) for (v in c) { val f = v / 32768.0; sAll += f * f; nAll++ }
        val rmsTail = Math.sqrt(sTail / maxOf(1, nTail))
        val rmsAll = Math.sqrt(sAll / maxOf(1, nAll))
        if (!(rmsTail < 0.5 * rmsAll && rmsTail < 0.03)) return null           // still talking
        val taken = synchronized(chunks) {
            val n = all.size
            if (chunks.size < n) return null
            val part = chunks.subList(0, n).toList()
            repeat(n) { chunks.removeAt(0) }
            part
        }
        val out = FloatArray(taken.sumOf { it.size })
        var o = 0
        for (c in taken) for (v in c) out[o++] = v / 32768f
        return out
    }

    /** Stops and returns everything recorded as floats in [-1, 1]. */
    fun stop(): FloatArray {
        running = false
        worker?.join(500)
        rec?.let { runCatching { it.stop() }; it.release() }
        rec = null
        val all = synchronized(chunks) { chunks.toList().also { chunks.clear() } }
        val total = all.sumOf { it.size }
        val out = FloatArray(total)
        var o = 0
        for (c in all) for (v in c) out[o++] = v / 32768f
        return out
    }

    fun cancel() { running = false; stop() }

    companion object { const val RATE = 16_000 }
}
