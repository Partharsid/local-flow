package app.localflow.engine

import android.app.ActivityManager
import android.content.Context
import java.io.File

/** What this phone can run: picks the speech model and whether the cleanup model fits. */
data class DeviceProfile(
    val totalRamMb: Int,
    val cores: Int,
    val supportsDotprod: Boolean,
    val tier: Tier,
    val bigCoreMask: Int = 0,
) {
    enum class Tier(val label: String) {
        LITE("Lite"), BALANCED("Balanced"), FULL("Full");
    }

    /** Threads for matrix work: the big cores only. Phones have 2+3+3 or 4+4 layouts; extra little-core threads slow it down. */
    val threads: Int get() = when { cores >= 8 -> 4; cores >= 6 -> 4; else -> 2 }

    companion object {
        fun detect(ctx: Context): DeviceProfile {
            val am = ctx.getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
            val mi = ActivityManager.MemoryInfo().also { am.getMemoryInfo(it) }
            val ramMb = (mi.totalMem / (1024 * 1024)).toInt()
            val cores = Runtime.getRuntime().availableProcessors()
            val dot = cpuFeatures().let { "asimddp" in it || android.os.Build.SUPPORTED_ABIS.firstOrNull() == "x86_64" }
            // Marketing "8 GB" phones report ~7.4 GB here, "6 GB" ~5.5 GB, "4 GB" ~3.6 GB.
            val tier = when {
                !dot || ramMb < 3200 -> Tier.LITE
                ramMb >= 6800 -> Tier.FULL
                ramMb >= 4800 -> Tier.BALANCED
                else -> Tier.LITE
            }
            return DeviceProfile(ramMb, cores, dot, tier, bigCores(cores))
        }

        /** Bit mask of the fastest cores. Reads each core's max frequency; falls back to the top half of the cores. */
        private fun bigCores(cores: Int): Int {
            val freqs = IntArray(cores) { i ->
                runCatching { File("/sys/devices/system/cpu/cpu$i/cpufreq/cpuinfo_max_freq").readText().trim().toInt() }.getOrDefault(0)
            }
            val top = freqs.max()
            val want = if (cores >= 8) 4 else maxOf(2, cores / 2)
            var mask = 0
            if (top > 0) {
                // the `want` fastest cores
                freqs.withIndex().sortedByDescending { it.value }.take(want).forEach { mask = mask or (1 shl it.index) }
            } else {
                for (i in cores - want until cores) mask = mask or (1 shl i)
            }
            return mask
        }

        private fun cpuFeatures(): String = try {
            File("/proc/cpuinfo").readLines().firstOrNull { it.startsWith("Features") } ?: ""
        } catch (e: Exception) { "" }
    }
}
