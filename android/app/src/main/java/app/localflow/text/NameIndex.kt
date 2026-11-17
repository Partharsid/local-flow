package app.localflow.text

import java.io.File
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.channels.FileChannel

/**
 * The big names, brands and places list (about 1.4 million terms), memory-mapped from `names.bin`
 * (built by tools/build_names.py). Nothing is loaded into the heap: lookups binary-search the file.
 *
 *  - [exact]: any term, matched ignoring case, spaces and punctuation (fixes capitalisation / spacing).
 *  - [similar]: only the hand-picked "curated" terms; matched by consonant skeleton (fixes near-miss spellings).
 *  - [isOrdinary]: ordinary English / Hindi words that must never be rewritten.
 */
class NameIndex private constructor(private val b: ByteBuffer) {
    private val n = b.getInt(8)
    private val a = b.getInt(12)
    private val o = b.getInt(16)
    private val pExact = b.getInt(20)
    private val pSkel = b.getInt(24)
    private val pOrdBlob = b.getInt(28)
    private val pOrdIdx = b.getInt(32)
    private val pBlob = 36

    val size get() = n

    private fun nameAt(off: Int): String {
        val p = pBlob + off
        val len = b.get(p).toInt() and 0xff
        val sb = StringBuilder(len)
        for (i in 1..len) sb.append((b.get(p + i).toInt() and 0xff).toChar())
        return sb.toString()
    }

    private fun keyOf(s: String): String {
        val sb = StringBuilder(s.length)
        for (c in s) { val l = c.lowercaseChar(); if (l in 'a'..'z' || l in '0'..'9') sb.append(l) }
        return sb.toString()
    }

    /** Canonical spelling of the term whose key is [key] (lowercase letters and digits), or null. */
    fun exact(key: String): String? {
        var lo = 0; var hi = n - 1
        while (lo <= hi) {
            val mid = (lo + hi) ushr 1
            val s = nameAt(b.getInt(pExact + 4 * mid))
            val c = keyOf(s).compareTo(key)
            if (c == 0) return s
            if (c < 0) lo = mid + 1 else hi = mid - 1
        }
        return null
    }

    /** Curated terms whose consonant skeleton is [sk]. */
    fun similar(sk: String): List<String> {
        var lo = 0; var hi = a - 1; var first = -1
        while (lo <= hi) {
            val mid = (lo + hi) ushr 1
            val c = Names.skeleton(nameAt(b.getInt(pSkel + 4 * mid))).compareTo(sk)
            if (c >= 0) { if (c == 0) first = mid; hi = mid - 1 } else lo = mid + 1
        }
        if (first < 0) return emptyList()
        val out = ArrayList<String>()
        var i = first
        while (i < a && out.size < 64) {
            val s = nameAt(b.getInt(pSkel + 4 * i))
            if (Names.skeleton(s) != sk) break
            out += s; i++
        }
        return out
    }

    /** True when [key] is an ordinary English or Hindi word. */
    fun isOrdinary(key: String): Boolean {
        var lo = 0; var hi = o - 1
        while (lo <= hi) {
            val mid = (lo + hi) ushr 1
            val p = pOrdBlob + b.getInt(pOrdIdx + 4 * mid)
            val len = b.get(p).toInt() and 0xff
            val sb = StringBuilder(len)
            for (i in 1..len) sb.append((b.get(p + i).toInt() and 0xff).toChar())
            val c = sb.toString().compareTo(key)
            if (c == 0) return true
            if (c < 0) lo = mid + 1 else hi = mid - 1
        }
        return false
    }

    companion object {
        /** Opens the file, or returns null when it is missing or not a names index. */
        fun open(f: File): NameIndex? = runCatching {
            if (!f.isFile) return null
            RandomAccessFile(f, "r").use { raf ->
                val m = raf.channel.map(FileChannel.MapMode.READ_ONLY, 0, raf.length()).order(ByteOrder.LITTLE_ENDIAN)
                if (m.limit() < 36 || m.getInt(0) != 0x324e464c) return null        // "LFN2"
                NameIndex(m)
            }
        }.getOrNull()
    }
}
