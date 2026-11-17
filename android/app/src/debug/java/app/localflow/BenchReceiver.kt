package app.localflow

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import app.localflow.engine.EngineHost
import app.localflow.service.DictationService
import app.localflow.service.TextInserter
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.concurrent.thread

/** Debug only: runs the engine on a 16 kHz mono WAV and logs text + timings under tag LF-BENCH. */
class BenchReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, i: Intent) {
        when (i.action) {
            "app.localflow.INSERT" -> {       // type text into the focused field, like a finished dictation
                val ok = DictationService.instance?.let { TextInserter.insert(it, i.getStringExtra("text") ?: "") }
                Log.i("LF-BENCH", "insert ok=$ok service=${DictationService.instance != null}")
                return
            }
            "app.localflow.EVAL" -> {         // transcribe every WAV in a folder; log "file | text"
                val dir = File(i.getStringExtra("dir") ?: return)
                                thread {
                    try {
                        val host = EngineHost.get(ctx)
                        Settings(ctx).mode = LangMode.valueOf(i.getStringExtra("mode") ?: "HINGLISH")
                        host.useFiles(i.getStringExtra("whisper"), null, i.getIntExtra("threads", 0))
                        host.debugLang = i.getStringExtra("lang") ?: ""
                        host.debugNoPrompt = i.getBooleanExtra("noprompt", false)
                        host.debugFullCtx = i.getBooleanExtra("fullctx", false)
                        host.beam = i.getIntExtra("beam", 1)
                        Log.i("LF-BENCH", "EVAL whisper=${i.getStringExtra("whisper")} lang=${host.debugLang} noprompt=${host.debugNoPrompt}")
                        var totalMs = 0L; var totalSec = 0f
                        for (f in dir.listFiles { x -> x.name.endsWith(".wav") }!!.sortedBy { it.name }) {
                            val bytes = f.readBytes()
                            val bb = ByteBuffer.wrap(bytes, 44, bytes.size - 44).order(ByteOrder.LITTLE_ENDIAN)
                            val pcm = FloatArray((bytes.size - 44) / 2) { bb.short / 32768f }
                            val h = host.transcribe(pcm)
                            totalMs += h?.sttMs ?: 0; totalSec += pcm.size / 16000f
                            Log.i("LF-EVAL", "${f.name.take(40)} | ${h?.sttMs}ms | ${h?.text}")
                        }
                        Log.i("LF-EVAL", "TOTAL audio=%.1fs stt=%dms rtf=%.2f".format(totalSec, totalMs, totalMs / 1000f / totalSec))
                    } catch (e: Throwable) { Log.e("LF-EVAL", "failed", e) } finally { }
                }
                return
            }
            "app.localflow.UPLOAD" -> {       // upload the queue now (normally it waits for Wi-Fi)
                thread {
                    val ok = runCatching { app.localflow.contrib.Contribution.uploadAll(ctx) }
                    Log.i("LF-BENCH", "upload result=$ok pending=${app.localflow.contrib.Contribution.pending(ctx)}")
                }
                return
            }
            "app.localflow.REC" -> {          // record N seconds from a given audio source and log the loudness
                val src = i.getIntExtra("source", 6)
                val secs = i.getIntExtra("secs", 4)
                thread {
                    val r = app.localflow.audio.Recorder(src) {}
                    if (!r.start()) { Log.e("LF-BENCH", "rec: could not start source $src"); return@thread }
                    Thread.sleep(secs * 1000L)
                    val pcm = r.stop()
                    val st = app.localflow.audio.Vad.stats(pcm)
                    Log.i("LF-BENCH", "rec source=$src secs=$secs samples=${pcm.size} peak=${"%.4f".format(st.peak)} rms=${"%.5f".format(st.rms)}")
                }
                return
            }
            "app.localflow.PLAY" -> {         // play a 16 kHz mono WAV through the speaker so the mic can hear it
                val path = i.getStringExtra("path") ?: return
                thread {
                    val b = File(path).readBytes()
                    val at = AudioTrack.Builder()
                        .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_MEDIA).build())
                        .setAudioFormat(AudioFormat.Builder().setSampleRate(16000).setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
                        .setBufferSizeInBytes(b.size - 44).setTransferMode(AudioTrack.MODE_STATIC).build()
                    at.write(b, 44, b.size - 44); at.setVolume(1f); at.play()
                    Thread.sleep((b.size - 44) / 32L + 500); at.release()
                }
                return
            }
        }
        val path = i.getStringExtra("path") ?: return
        val modeName = i.getStringExtra("mode") ?: "ENGLISH"
        val runs = i.getIntExtra("runs", 2)
                thread {
            try {
                val bytes = File(path).readBytes()
                val bb = ByteBuffer.wrap(bytes, 44, bytes.size - 44).order(ByteOrder.LITTLE_ENDIAN)
                val pcm = FloatArray((bytes.size - 44) / 2) { bb.short / 32768f }
                val settings = Settings(ctx)
                settings.mode = LangMode.valueOf(modeName)
                settings.cleanup = i.getBooleanExtra("cleanup", true)
                val host = EngineHost.get(ctx)
                host.useFiles(i.getStringExtra("whisper"), i.getStringExtra("llm"), i.getIntExtra("threads", 0))
                Log.i("LF-BENCH", "whisper=${i.getStringExtra("whisper")} llm=${i.getStringExtra("llm")}")
                Log.i("LF-BENCH", "device=${host.profile} audio=${pcm.size / 16000f}s mode=$modeName")
                repeat(runs) { n ->
                    val t0 = System.nanoTime()
                    val r = host.process(pcm)
                    val total = (System.nanoTime() - t0) / 1_000_000
                    Log.i("LF-BENCH", "run ${n + 1}: stt=${r?.sttMs}ms clean=${r?.cleanMs}ms total=${total}ms llm=${r?.usedLlm} text=${r?.text}")
                }
            } catch (e: Throwable) {
                Log.e("LF-BENCH", "failed", e)
            } finally { }
        }
    }
}
