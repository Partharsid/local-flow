package app.localflow.contrib

import android.app.job.JobInfo
import android.app.job.JobScheduler
import android.content.ComponentName
import android.content.Context
import android.os.Build
import android.util.Log
import app.localflow.BuildConfig
import app.localflow.Settings
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.UUID

/**
 * "Help improve LocalFlow": opt-in sharing of dictation recordings (and the text that went with them)
 * for training better models. Off unless the person turns it on and accepts [CONSENT_TEXT]. See server/.
 *
 * Samples wait in app-private storage until a Wi-Fi connection is available, are uploaded, and are then
 * deleted from the phone. "Delete my data" removes them from the server too.
 */
object Contribution {
    /** Bump when the text changes: people are asked again. */
    const val CONSENT_VERSION = "1"
    private const val TAG = "LocalFlow.contrib"
    private const val JOB_ID = 7701
    private const val MAX_QUEUE = 300                       // never fill someone's storage
    private const val MAX_SECONDS = 60

    const val CONSENT_TITLE = "Help improve LocalFlow?"
    const val CONSENT_TEXT =
        "If you turn this on, LocalFlow keeps your dictation recordings and uploads them, together with the text it " +
            "heard and the text it typed, to our server (only on Wi-Fi). We use them to train better speech models, " +
            "especially for Hinglish.\n\n" +
            "We do not collect your name, phone number, contacts or anything from other apps, and password fields are " +
            "skipped. A recording contains your voice, which is personal data.\n\n" +
            "Nothing is shared unless you turn this on. You can turn it off at any time, and \"Delete my data\" removes " +
            "everything you have shared from our server straight away."

    /** False in builds that were not given a server address: the option is hidden and nothing can upload. */
    val available: Boolean get() = BuildConfig.UPLOAD_URL.isNotBlank()

    private fun dir(ctx: Context) = File(ctx.filesDir, "contrib").apply { mkdirs() }

    fun enabled(ctx: Context): Boolean {
        val s = Settings(ctx)
        return available && s.contribute && s.consentVersion == CONSENT_VERSION
    }

    fun accept(ctx: Context) {
        Settings(ctx).apply { contribute = true; consentVersion = CONSENT_VERSION }
        kick(ctx)
    }

    fun turnOff(ctx: Context) { Settings(ctx).contribute = false }

    fun pending(ctx: Context): Int = dir(ctx).listFiles { f -> f.name.endsWith(".wav") }?.size ?: 0

    class Meta(
        val mode: String, val model: String, val modelHinglish: String, val tier: String, val sttMs: Long,
        val audioSec: Float, val rawText: String, val finalText: String, val polished: Boolean,
    )

    /** Queue one dictation. Call only when [enabled]; the caller has already excluded password fields. */
    fun save(ctx: Context, pcm: FloatArray, m: Meta) {
        if (!enabled(ctx) || pcm.size < 8000 || pcm.size > MAX_SECONDS * 16000) return
        if (pending(ctx) >= MAX_QUEUE) return
        val id = UUID.randomUUID().toString().replace("-", "")
        val d = dir(ctx)
        runCatching {
            File(d, "$id.wav").writeBytes(wav(pcm))
            File(d, "$id.json").writeText(JSONObject().apply {
                put("mode", m.mode); put("model", m.model); put("model_hinglish", m.modelHinglish); put("tier", m.tier)
                put("app_version", BuildConfig.VERSION_NAME); put("android", Build.VERSION.SDK_INT)
                put("device", Build.MANUFACTURER + " " + Build.MODEL)
                put("stt_ms", m.sttMs); put("audio_sec", m.audioSec.toDouble())
                put("raw_text", m.rawText); put("final_text", m.finalText); put("polished", m.polished)
            }.toString())
        }.onFailure { Log.w(TAG, "could not queue sample", it); return }
        kick(ctx)
    }

    /** Ask Android to upload what is waiting, when an unmetered (Wi-Fi) connection is available. */
    fun kick(ctx: Context) {
        if (!enabled(ctx) || pending(ctx) == 0) return
        val js = ctx.getSystemService(Context.JOB_SCHEDULER_SERVICE) as JobScheduler
        js.schedule(JobInfo.Builder(JOB_ID, ComponentName(ctx, UploadJobService::class.java))
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_UNMETERED)
            .setRequiresBatteryNotLow(true)
            .setBackoffCriteria(60_000, JobInfo.BACKOFF_POLICY_EXPONENTIAL)
            .build())
    }

    // ------------------------------------------------------------------ network
    private fun base() = BuildConfig.UPLOAD_URL.trimEnd('/')

    private fun call(method: String, path: String, token: String? = null, body: ByteArray? = null,
                     headers: Map<String, String> = emptyMap(), contentType: String = "application/json"): Pair<Int, String> {
        val c = (URL(base() + path).openConnection() as HttpURLConnection).apply {
            requestMethod = method; connectTimeout = 15_000; readTimeout = 60_000
            if (token != null) setRequestProperty("Authorization", "Bearer $token")
            for ((k, v) in headers) setRequestProperty(k, v)
            if (body != null) { doOutput = true; setRequestProperty("Content-Type", contentType); setFixedLengthStreamingMode(body.size) }
        }
        try {
            if (body != null) c.outputStream.use { it.write(body) }
            val code = c.responseCode
            val text = (if (code < 400) c.inputStream else c.errorStream)?.bufferedReader()?.readText() ?: ""
            return code to text
        } finally { c.disconnect() }
    }

    private fun ensureRegistered(ctx: Context): String? {
        val s = Settings(ctx)
        if (s.installToken.isNotEmpty()) return s.installToken
        val (code, body) = call("POST", "/v1/register", body = """{"consent_version":"$CONSENT_VERSION"}""".toByteArray())
        if (code != 200) return null
        val j = JSONObject(body)
        s.installId = j.getString("install_id"); s.installToken = j.getString("token")
        return s.installToken
    }

    /** Upload everything queued. True when the queue is empty afterwards (false: try again later). */
    fun uploadAll(ctx: Context): Boolean {
        if (!enabled(ctx)) return true
        var token = ensureRegistered(ctx) ?: return false
        val files = dir(ctx).listFiles { f -> f.name.endsWith(".wav") }?.sortedBy { it.lastModified() } ?: return true
        for (wavFile in files) {
            val metaFile = File(wavFile.path.removeSuffix(".wav") + ".json")
            if (!enabled(ctx)) return true                              // switched off meanwhile: stop at once
            var (code, _) = try {
                call("POST", "/v1/samples", token, wavFile.readBytes(), mapOf("X-Meta" to compact(metaFile)), "audio/wav")
            } catch (e: Exception) { return false }
            if (code == 401) {                                          // the server forgot us: register again once
                Settings(ctx).installToken = ""
                token = ensureRegistered(ctx) ?: return false
                code = try { call("POST", "/v1/samples", token, wavFile.readBytes(), mapOf("X-Meta" to compact(metaFile)), "audio/wav").first } catch (e: Exception) { return false }
            }
            if (code == 200 || code in 400..499 && code != 429) { wavFile.delete(); metaFile.delete() }   // done, or can never succeed
            else return false                                           // 429 / 5xx: later
        }
        return true
    }

    private fun compact(f: File): String = runCatching { JSONObject(f.readText()).toString() }.getOrDefault("{}")

    /** "Delete my data": removes everything from the server, clears the local queue, forgets the install. */
    fun deleteAll(ctx: Context): Boolean {
        val s = Settings(ctx)
        turnOff(ctx)
        dir(ctx).listFiles()?.forEach { it.delete() }
        (ctx.getSystemService(Context.JOB_SCHEDULER_SERVICE) as JobScheduler).cancel(JOB_ID)
        if (s.installToken.isEmpty()) return true                       // nothing was ever uploaded
        return try {
            val (code, _) = call("DELETE", "/v1/me", s.installToken)
            if (code == 200 || code == 401) { s.installId = ""; s.installToken = ""; true } else false
        } catch (e: Exception) { false }
    }

    private fun wav(pcm: FloatArray): ByteArray {
        val n = pcm.size
        val bb = ByteBuffer.allocate(44 + n * 2).order(ByteOrder.LITTLE_ENDIAN)
        bb.put("RIFF".toByteArray()).putInt(36 + n * 2).put("WAVEfmt ".toByteArray()).putInt(16).putShort(1).putShort(1)
            .putInt(16000).putInt(32000).putShort(2).putShort(16).put("data".toByteArray()).putInt(n * 2)
        for (v in pcm) bb.putShort((v * 32767f).toInt().coerceIn(-32768, 32767).toShort())
        return bb.array()
    }
}
