package app.localflow.engine

import java.io.File

/** One downloadable model file, verified by size and SHA-256. */
data class ModelFile(val name: String, val url: String, val size: Long, val sha256: String, val title: String) {
    fun path(dir: File) = File(dir, name)
}

object ModelCatalog {
    private const val HF = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"

    // Q8_0 builds: ggml repacks them for the phone's int8 matrix instructions, about 2x faster than Q5 files.
    val whisperBase = ModelFile("ggml-base-q8_0.bin", "$HF/ggml-base-q8_0.bin", 81_768_585,
        "c577b9a86e7e048a0b7eada054f4dd79a56bbfa911fbdacf900ac5b567cbb7d9", "Speech (base)")
    val whisperSmall = ModelFile("ggml-small-q8_0.bin", "$HF/ggml-small-q8_0.bin", 264_464_607,
        "49c8fb02b65e6049d5fa6c04f81f53b867b5ec9540406812c643f177317f779f", "Speech (small)")
    val whisperTurbo = ModelFile("ggml-large-v3-turbo-q8_0.bin", "$HF/ggml-large-v3-turbo-q8_0.bin", 874_188_075,
        "317eb69c11673c9de1e1f0d459b253999804ec71ac4c23c17ecf5fbe24e259a1", "Speech (best accuracy)")

    /**
     * Hinglish speech model: Oriserve/Whisper-Hindi2Hinglish-Swift (Apache-2.0, whisper-base fine-tuned to write Hindi
     * in Roman letters), converted to ggml and quantised to Q8_0. Measured on-device: half the word errors of the general
     * model on Hindi/Hinglish clips, and about 10x faster (the general model loops on Hindi).
     */
    val hinglish = ModelFile("localflow-hinglish-q8_0.bin",
        "https://github.com/Partharsid/local-flow/releases/download/models-2/localflow-hinglish-q8_0.bin",
        81_768_585, "8550803dc10fa9f0b3db5cb884f8580c61067b062a67ee169d5789bcd0faec90", "Hinglish speech")

    /** LocalFlow's own fine-tuned dictation cleanup model (Qwen3-1.7B, Q5_K_M), same file as the desktop app. */
    val cleanup = ModelFile("localflow-dictation-1.7b-Q5_K_M.gguf",
        "https://github.com/Partharsid/local-flow/releases/download/models-1/localflow-dictation-1.7b-Q5_K_M.gguf",
        1_257_879_040, "d8936a09b84e264c85e0964dd67160233b2b04fb536f1736ed1e5565f339de0e", "Text cleanup")

    /** What to install for this phone; [accurate] swaps in the big speech model (better Hindi, ~3x slower). */
    fun plan(p: DeviceProfile, accurate: Boolean = false): List<ModelFile> {
        val speech = when {
            p.tier == DeviceProfile.Tier.LITE -> whisperBase
            accurate -> whisperTurbo
            else -> whisperSmall
        }
        val all = mutableListOf(hinglish, speech)
        if (p.tier == DeviceProfile.Tier.FULL) all += cleanup
        return all
    }

    /** The general (English and other languages) speech model for this phone. */
    fun speechFor(p: DeviceProfile, accurate: Boolean = false): ModelFile = when {
        p.tier == DeviceProfile.Tier.LITE -> whisperBase
        accurate -> whisperTurbo
        else -> whisperSmall
    }
}
