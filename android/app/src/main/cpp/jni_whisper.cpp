// Speech-to-text: whisper.cpp behind a small JNI surface.
#include <jni.h>
#include <sched.h>
#include <unistd.h>
#include <sys/syscall.h>
#include <android/log.h>
#include <algorithm>
#include <string>
#include <vector>
#include "whisper.h"

#define TAG "LocalFlow.whisper"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, TAG, __VA_ARGS__)

extern "C" {

// Pin the calling thread to the given CPU mask (the big cores). Threads that ggml spawns later inherit it,
// so the matrix work never lands on the slow efficiency cores.
JNIEXPORT jboolean JNICALL Java_app_localflow_engine_Native_setAffinity(JNIEnv *, jobject, jint mask) {
    cpu_set_t set;
    CPU_ZERO(&set);
    for (int i = 0; i < 32; i++) if (mask & (1 << i)) CPU_SET(i, &set);
    return sched_setaffinity((pid_t) syscall(SYS_gettid), sizeof(set), &set) == 0;
}

JNIEXPORT jlong JNICALL Java_app_localflow_engine_Native_whisperInit(JNIEnv *env, jobject, jstring path) {
    const char *p = env->GetStringUTFChars(path, nullptr);
    whisper_context_params cp = whisper_context_default_params();
    cp.use_gpu = false;                 // the CPU path is the portable, predictable one
    cp.flash_attn = true;               // less memory traffic on phones
    whisper_context *ctx = whisper_init_from_file_with_params(p, cp);
    env->ReleaseStringUTFChars(path, p);
    LOGI("whisper init: %s | %s", ctx ? "ok" : "FAILED", whisper_print_system_info());
    return reinterpret_cast<jlong>(ctx);
}

JNIEXPORT void JNICALL Java_app_localflow_engine_Native_whisperFree(JNIEnv *, jobject, jlong h) {
    if (h) whisper_free(reinterpret_cast<whisper_context *>(h));
}

// Returns the transcript, or null on failure. `lang` is "auto" or a code like "en"/"hi".
JNIEXPORT jstring JNICALL Java_app_localflow_engine_Native_whisperTranscribe(
        JNIEnv *env, jobject, jlong h, jfloatArray pcm, jstring lang, jstring prompt, jint threads, jboolean shortCtx, jint beam) {
    auto *ctx = reinterpret_cast<whisper_context *>(h);
    if (!ctx) return nullptr;
    jsize n = env->GetArrayLength(pcm);
    std::vector<float> samples(n);
    env->GetFloatArrayRegion(pcm, 0, n, samples.data());
    if (n < 1600) return env->NewStringUTF("");           // under 0.1 s

    const char *l = env->GetStringUTFChars(lang, nullptr);
    const char *pr = prompt ? env->GetStringUTFChars(prompt, nullptr) : nullptr;
    std::string language = l, initial = pr ? pr : "";
    env->ReleaseStringUTFChars(lang, l);
    if (pr) env->ReleaseStringUTFChars(prompt, pr);

    whisper_full_params wp = whisper_full_default_params(beam > 1 ? WHISPER_SAMPLING_BEAM_SEARCH : WHISPER_SAMPLING_GREEDY);
    if (beam > 1) wp.beam_search.beam_size = beam;
    wp.n_threads = threads;
    wp.language = language.c_str();
    wp.detect_language = (language == "auto");
    wp.initial_prompt = initial.empty() ? nullptr : initial.c_str();
    wp.no_timestamps = true;
    wp.single_segment = n < 16000 * 28;
    wp.print_progress = wp.print_realtime = wp.print_timestamps = wp.print_special = false;
    wp.suppress_blank = true;
    wp.temperature = 0.0f;
    wp.temperature_inc = 0.2f;
    // Short clips don't need the full 30 s encoder window: scale it down. This
    // is the single biggest speed-up for dictation (clips are 2-10 s).
    int frames = static_cast<int>(n / 320) + 64;          // 20 ms per encoder frame (+margin)
    wp.audio_ctx = shortCtx ? std::min(1500, std::max(256, (frames + 63) / 64 * 64)) : 0;   // 0 = the model's full 30 s window

    if (whisper_full(ctx, wp, samples.data(), n) != 0) return nullptr;
    std::string out;
    int segs = whisper_full_n_segments(ctx);
    for (int i = 0; i < segs; i++) out += whisper_full_get_segment_text(ctx, i);
    return env->NewStringUTF(out.c_str());
}

}
