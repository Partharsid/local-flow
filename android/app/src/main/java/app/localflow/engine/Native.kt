package app.localflow.engine

/** The JNI surface of libflow.so (whisper.cpp + llama.cpp). Only the engine classes call this. */
internal object Native {
    init { System.loadLibrary("lflow") }

    external fun setAffinity(mask: Int): Boolean

    external fun whisperInit(path: String): Long
    external fun whisperFree(handle: Long)
    external fun whisperTranscribe(handle: Long, pcm: FloatArray, lang: String, prompt: String?, threads: Int, shortCtx: Boolean, beam: Int): String?

    external fun llamaLoad(path: String, nCtx: Int, threads: Int): Long
    external fun llamaFree(handle: Long)
    external fun llamaComplete(handle: Long, prompt: String, maxTokens: Int): String?
}
