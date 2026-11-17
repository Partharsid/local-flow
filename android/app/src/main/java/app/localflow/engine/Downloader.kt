package app.localflow.engine

import java.io.File
import java.io.RandomAccessFile
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/** Resumable, checksum-verified model download into the app's private storage. */
object Downloader {
    class Failed(msg: String) : Exception(msg)

    fun isInstalled(m: ModelFile, dir: File): Boolean = m.path(dir).let { it.isFile && it.length() == m.size }

    /** Blocking; call from a background thread. [progress] receives bytes done so far. */
    fun fetch(m: ModelFile, dir: File, progress: (Long) -> Unit, cancelled: () -> Boolean = { false }) {
        dir.mkdirs()
        val dest = m.path(dir)
        if (isInstalled(m, dir)) { progress(m.size); return }
        val part = File(dir, m.name + ".part")
        var done = if (part.isFile) part.length() else 0L
        if (done > m.size) { part.delete(); done = 0 }

        var attempt = 0
        while (done < m.size) {
            if (cancelled()) throw Failed("cancelled")
            try {
                val c = (URL(m.url).openConnection() as HttpURLConnection).apply {
                    connectTimeout = 15_000; readTimeout = 30_000; instanceFollowRedirects = true
                    setRequestProperty("User-Agent", "LocalFlow-Android")
                    if (done > 0) setRequestProperty("Range", "bytes=$done-")
                }
                val code = c.responseCode
                if (code != 200 && code != 206) throw Failed("server answered $code")
                if (code == 200 && done > 0) { done = 0; part.delete() }          // server ignored Range
                RandomAccessFile(part, "rw").use { raf ->
                    raf.seek(done)
                    c.inputStream.use { ins ->
                        val buf = ByteArray(256 * 1024)
                        while (true) {
                            if (cancelled()) throw Failed("cancelled")
                            val n = ins.read(buf)
                            if (n < 0) break
                            raf.write(buf, 0, n)
                            done += n
                            progress(done)
                        }
                    }
                }
            } catch (e: Failed) {
                throw e
            } catch (e: Exception) {
                if (++attempt > 8) throw Failed("download failed: ${e.message}")
                Thread.sleep(1500L * attempt)
                done = if (part.isFile) part.length() else 0L
            }
        }
        if (sha256(part) != m.sha256) { part.delete(); throw Failed("${m.name} failed its checksum; try again") }
        if (!part.renameTo(dest)) throw Failed("could not save ${m.name}")
    }

    fun sha256(f: File): String {
        val md = MessageDigest.getInstance("SHA-256")
        f.inputStream().use { ins ->
            val buf = ByteArray(1 shl 20)
            while (true) { val n = ins.read(buf); if (n < 0) break; md.update(buf, 0, n) }
        }
        return md.digest().joinToString("") { "%02x".format(it) }
    }
}
