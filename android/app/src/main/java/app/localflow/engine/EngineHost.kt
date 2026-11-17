package app.localflow.engine

import android.content.Context
import android.content.pm.ApplicationInfo
import android.os.Handler
import android.os.Looper
import android.util.Log
import app.localflow.LangMode
import app.localflow.Settings
import app.localflow.audio.Vad
import app.localflow.text.Hinglish
import app.localflow.text.RuleCleanup
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.Executors

/**
 * Owns the on-device models. One instance per process; all inference runs on a single
 * worker thread. Models load when first needed and unload after a few idle minutes
 * so the phone gets its RAM back.
 *
 * Dictation is two steps so text appears fast: [transcribe] (speech to text plus instant rule
 * cleanup, about a second) and, for longer English dictation, [polish] (the AI cleanup model),
 * whose result replaces the first version a few seconds later.
 */
class EngineHost private constructor(private val ctx: Context) {
    /** What was heard. [needsPolish] says the AI cleanup model should still improve [text]. */
    data class Heard(
        val text: String, val sttMs: Long, val audioSec: Float, val peak: Float, val rms: Float,
        val needsPolish: Boolean, val raw: String = "",
    )

    data class Result(val text: String, val sttMs: Long, val cleanMs: Long, val audioSec: Float, val usedLlm: Boolean)

    val profile = DeviceProfile.detect(ctx)
    /** Names of the models used for a dictation (recorded with opted-in samples so they can be grouped later). */
    fun modelNames(): Triple<String, String, String> = Triple(
        ModelCatalog.speechFor(profile, settings.accurate).name, ModelCatalog.hinglish.name, profile.tier.label)
    val modelDir = File(ctx.filesDir, "models")
    private val settings = Settings(ctx)
    private val debuggable = ctx.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0
    private val worker = Executors.newSingleThreadExecutor { r ->
        Thread({
            // Speed: audio priority, and (when the OS lets us) the big cores only; ggml's helper threads inherit both.
            android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_URGENT_AUDIO)
            if (profile.bigCoreMask != 0) Native.setAffinity(profile.bigCoreMask)
            r.run()
        }, "lf-engine")
    }
    private val main = Handler(Looper.getMainLooper())

    private val whispers = HashMap<String, Long>()          // model file name -> native handle
    private var llama = 0L
    private var llamaWarm = false
    @Volatile private var threadsOverride = 0
    private val threads get() = if (threadsOverride > 0) threadsOverride else profile.threads
    private val unload = Runnable { worker.execute { release() } }

    fun plan() = ModelCatalog.plan(profile, settings.accurate)
    fun modelsInstalled(): Boolean = plan().all { Downloader.isInstalled(it, modelDir) }
    private fun cleanupInstalled() = profile.tier == DeviceProfile.Tier.FULL &&
        (if (llmOverride != null) llmPath().isFile else Downloader.isInstalled(ModelCatalog.cleanup, modelDir))

    /** Warm the models in the background (called when the bubble appears), so the first dictation is not slow. */
    fun warmUp() = worker.execute {
        runCatching {
            touch()
            ensureWhisper(speechFile(settings.mode))
            // Run one tiny request so the cleanup model's weights are paged in before the first real
            // dictation (otherwise its first use costs 10+ seconds).
            if (settings.cleanup && settings.mode == LangMode.ENGLISH && ensureLlama() && !llamaWarm) {
                Native.llamaComplete(llama, cleanupPrompt("hello there", emptyList()), 4)
                llamaWarm = true
            }
        }
    }

    /** Debug/benchmark hook: use other model files / thread counts. */
    @Volatile private var whisperOverride: String? = null
    /** Benchmark only: force the decode language ('' = use the mode's own) and drop the prompt. */
    @Volatile var debugLang: String = ""
    @Volatile var debugNoPrompt = false
    @Volatile var debugFullCtx = false
    @Volatile var beam = 1
    /** Fine-tuned Hinglish models are sensitive to the shortened encoder window; the generic models are not. */
    @Volatile var shortCtx = true
    @Volatile private var llmOverride: String? = null
    fun useFiles(whisperFile: String?, llmFile: String?, threadCount: Int = 0) {
        whisperOverride = whisperFile; llmOverride = llmFile; threadsOverride = threadCount
        worker.submit { release() }.get()
    }

    private fun llmPath() = llmOverride?.let { File(modelDir, it) } ?: ModelCatalog.cleanup.path(modelDir)

    /** Which speech model hears this mode: the Hinglish-tuned one when it is installed, else the general one. */
    private fun speechFile(mode: LangMode): File {
        whisperOverride?.let { return File(modelDir, it) }
        if ((mode == LangMode.HINGLISH || mode == LangMode.HINDI) && ModelCatalog.hinglish.path(modelDir).isFile)
            return ModelCatalog.hinglish.path(modelDir)
        return ModelCatalog.speechFor(profile, settings.accurate).path(modelDir)
    }

    private fun ensureWhisper(f: File): Long {
        whispers[f.name]?.let { return it }
        if (!f.isFile) return 0
        val h = Native.whisperInit(f.absolutePath)
        if (h != 0L) whispers[f.name] = h
        return h
    }

    private fun ensureLlama(): Boolean {
        if (llama != 0L) return true
        if (!cleanupInstalled()) return false
        llama = Native.llamaLoad(llmPath().absolutePath, 1024, threads)
        return llama != 0L
    }

    private fun release() {
        for (h in whispers.values) Native.whisperFree(h)
        whispers.clear()
        if (llama != 0L) { Native.llamaFree(llama); llama = 0; llamaWarm = false }
        Log.i(TAG, "models unloaded")
    }

    private fun touch() { main.removeCallbacks(unload); main.postDelayed(unload, IDLE_MS) }

    // ------------------------------------------------------------------ step 1: hear
    /** One piece of speech, decoded to raw text (no cleanup yet). */
    data class Piece(val text: String, val sttMs: Long, val audioSec: Float, val peak: Float, val rms: Float)

    /** Queue a recorded piece for transcription now (used while the user is still speaking). */
    fun submitPiece(pcm: FloatArray): java.util.concurrent.Future<Piece?> = worker.submit<Piece?> { hearPiece(pcm) }

    /** Blocking. Joins the pieces in order and cleans the whole text once. Null if the speech model failed. */
    fun finish(pieces: List<Piece?>): Heard? {
        if (pieces.any { it == null }) return null
        val ps = pieces.filterNotNull()
        val mode = settings.mode
        val raw = ps.map { it.text }.filter { it.isNotEmpty() }.joinToString(" ")
        val sttMs = ps.sumOf { it.sttMs }
        val sec = ps.sumOf { it.audioSec.toDouble() }.toFloat()
        val peak = ps.maxOfOrNull { it.peak } ?: 0f
        val rms = ps.maxOfOrNull { it.rms } ?: 0f
        if (raw.isEmpty()) return Heard("", sttMs, sec, peak, rms, false)
        val text = when (mode) {
            LangMode.HINGLISH -> RuleCleanup.clean(Hinglish.toRoman(raw))
            else -> RuleCleanup.clean(raw)
        }
        return Heard(text, sttMs, sec, peak, rms, wantsPolish(text, mode), raw)
    }

    /** Whole recording at once (benchmark, or when nothing was streamed). */
    fun transcribe(raw: FloatArray): Heard? = finish(listOf(submitPiece(raw).get()))

    private fun hearPiece(raw: FloatArray): Piece? {
        touch()
        val st = Vad.stats(raw)
        val audio = Vad.normalise(Vad.trim(raw))
        Log.i(TAG, "audio %.1fs peak=%.4f rms=%.4f -> %.1fs after trim".format(raw.size / 16000f, st.peak, st.rms, audio.size / 16000f))
        if (debuggable) saveWav(raw)
        if (audio.isEmpty() || audio.size < 6400 || Vad.speechFraction(audio) < 0.12f) return Piece("", 0, 0f, st.peak, st.rms)
        val mode = settings.mode
        val file = speechFile(mode)
        val handle = ensureWhisper(file)
        if (handle == 0L) return null
        val tuned = file.name == ModelCatalog.hinglish.name           // fine-tuned to write Hinglish: no prompt needed
        val (lang, prompt) = when {
            tuned -> "hi" to null
            mode == LangMode.ENGLISH -> "en" to dictionaryPrompt()
            mode == LangMode.HINGLISH -> "hi" to (Hinglish.PROMPT + " " + dictionaryPrompt())
            mode == LangMode.HINDI -> "hi" to null
            else -> "auto" to dictionaryPrompt()
        }
        val t0 = System.nanoTime()
        val heard = Native.whisperTranscribe(handle, audio, debugLang.ifEmpty { lang }, if (debugNoPrompt) null else prompt,
            threads, shortCtx && !debugFullCtx, beam) ?: return null
        val sttMs = (System.nanoTime() - t0) / 1_000_000
        val cleaned = junk(heard.trim())
        Log.i(TAG, "heard in ${sttMs}ms (${file.name}): ${cleaned.take(80)}")
        return Piece(cleaned, sttMs, audio.size / 16000f, st.peak, st.rms)
    }

    /** The AI model earns its time on longer English dictation; short phrases are fine with the instant rules. */
    private fun wantsPolish(text: String, mode: LangMode): Boolean =
        settings.cleanup && mode == LangMode.ENGLISH && cleanupInstalled() &&
            text.split(Regex("\\s+")).size >= LLM_MIN_WORDS

    // ------------------------------------------------------------------ step 2: polish
    /** Blocking. The cleaned text, or null if the model is unavailable or drifted from what was said. */
    fun polish(text: String): String? = worker.submit<String?> {
        touch()
        if (!ensureLlama()) null else llmClean(text)
    }.get()

    /** Both steps, for the benchmark. */
    fun process(pcm: FloatArray): Result? {
        val h = transcribe(pcm) ?: return null
        if (h.text.isEmpty() || !h.needsPolish) return Result(h.text, h.sttMs, 0, h.audioSec, false)
        val t = System.nanoTime()
        val p = polish(h.text)
        return Result(p ?: h.text, h.sttMs, (System.nanoTime() - t) / 1_000_000, h.audioSec, p != null)
    }

    private fun dictionaryPrompt(): String = settings.dictionary.take(30).joinToString(", ").let {
        if (it.isEmpty()) "" else "Names: $it."
    }

    /** Whisper invents "[BLANK_AUDIO]", "(music)" and similar on near-silence. */
    private fun junk(s: String): String {
        val t = s.replace(Regex("""\[[^\]]*]|\([^)]*\)|♪+"""), "").trim()
        return if (t.length < 2) "" else t
    }

    /** The exact three-line format the cleanup model was trained on (see localflow/tuned.py on the desktop). */
    private fun cleanupPrompt(text: String, vocab: List<String>): String {
        val header = buildString {
            append("mode: neutral\napp: unknown")
            if (vocab.isNotEmpty()) append("\nvocab: ").append(vocab.joinToString(", "))
        }
        return "<|im_start|>system\n$header<|im_end|>\n<|im_start|>user\n${text.trim()}<|im_end|>\n<|im_start|>assistant\n"
    }

    private fun llmClean(text: String): String? {
        val prompt = cleanupPrompt(text, settings.dictionary.take(20))
        val maxTokens = (text.length / 2.5).toInt() + 64
        val out = Native.llamaComplete(llama, prompt, maxTokens)?.trim() ?: return null
        if (out.isEmpty()) return null
        val ratio = out.length.toFloat() / text.length
        if (ratio > 2.2f || ratio < 0.35f) return null
        return out
    }

    /** Debug builds keep the last recording so a "voice not detected" report can be replayed (adb run-as). */
    private fun saveWav(pcm: FloatArray) = runCatching {
        val n = pcm.size
        val bb = ByteBuffer.allocate(44 + n * 2).order(ByteOrder.LITTLE_ENDIAN)
        bb.put("RIFF".toByteArray()).putInt(36 + n * 2).put("WAVEfmt ".toByteArray()).putInt(16).putShort(1).putShort(1)
            .putInt(16000).putInt(32000).putShort(2).putShort(16).put("data".toByteArray()).putInt(n * 2)
        for (v in pcm) bb.putShort((v * 32767f).toInt().coerceIn(-32768, 32767).toShort())
        File(ctx.filesDir, "last.wav").writeBytes(bb.array())
    }

    companion object {
        private const val TAG = "LocalFlow.engine"
        private const val IDLE_MS = 5 * 60_000L
        private const val LLM_MIN_WORDS = 10
        @Volatile private var inst: EngineHost? = null
        fun get(ctx: Context): EngineHost = inst ?: synchronized(this) {
            inst ?: EngineHost(ctx.applicationContext).also { inst = it }
        }
    }
}
