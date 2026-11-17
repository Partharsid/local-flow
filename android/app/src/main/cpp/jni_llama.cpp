// Text cleanup: llama.cpp (greedy decoding) behind a small JNI surface.
#include <jni.h>
#include <android/log.h>
#include <chrono>
#include <string>
#include <vector>
#include "llama.h"

#define TAG "LocalFlow.llama"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO, TAG, __VA_ARGS__)

struct LlamaHandle { llama_model *model = nullptr; llama_context *ctx = nullptr; };

static void quiet_log(enum ggml_log_level level, const char *text, void *) {
    if (level < GGML_LOG_LEVEL_ERROR) return;
    for (const char *c = text; *c; c++) {
        if (*c != '.' && *c != '\n' && *c != ' ') { __android_log_print(ANDROID_LOG_ERROR, TAG, "%s", text); return; }
    }
}

extern "C" {

JNIEXPORT jlong JNICALL Java_app_localflow_engine_Native_llamaLoad(
        JNIEnv *env, jobject, jstring path, jint nCtx, jint threads) {
    static bool inited = false;
    if (!inited) { llama_log_set(quiet_log, nullptr); llama_backend_init(); inited = true; }
    const char *p = env->GetStringUTFChars(path, nullptr);
    llama_model_params mp = llama_model_default_params();
    mp.n_gpu_layers = 0;
    mp.load_mode = LLAMA_LOAD_MODE_MMAP; // weights are paged in, not copied: fast start, lower peak RAM
    llama_model *model = llama_model_load_from_file(p, mp);
    env->ReleaseStringUTFChars(path, p);
    if (!model) return 0;
    llama_context_params cp = llama_context_default_params();
    cp.n_ctx = nCtx;
    cp.n_batch = 512;
    cp.n_threads = threads;
    cp.n_threads_batch = threads;
    llama_context *ctx = llama_init_from_model(model, cp);
    if (!ctx) { llama_model_free(model); return 0; }
    auto *h = new LlamaHandle{model, ctx};
    LOGI("llama loaded, ctx=%d threads=%d", nCtx, threads);
    return reinterpret_cast<jlong>(h);
}

JNIEXPORT void JNICALL Java_app_localflow_engine_Native_llamaFree(JNIEnv *, jobject, jlong handle) {
    auto *h = reinterpret_cast<LlamaHandle *>(handle);
    if (!h) return;
    llama_free(h->ctx);
    llama_model_free(h->model);
    delete h;
}

// Draft tokens by prompt lookup: cleanup output mostly copies the input, so find the last few generated
// tokens earlier in the history and propose what followed them. The model verifies a whole draft in one pass.
static std::vector<llama_token> lookup_draft(const std::vector<llama_token> &hist, int ngram, int max_draft) {
    std::vector<llama_token> draft;
    int n = (int) hist.size();
    if (n < ngram + 1) return draft;
    for (int start = n - ngram - 1; start >= 0; start--) {       // most recent earlier match first
        bool same = true;
        for (int k = 0; k < ngram; k++) if (hist[start + k] != hist[n - ngram + k]) { same = false; break; }
        if (!same) continue;
        for (int k = start + ngram; k < n && (int) draft.size() < max_draft; k++) draft.push_back(hist[k]);
        if (!draft.empty()) return draft;
    }
    return draft;
}

static llama_token argmax_logits(const float *logits, int n_vocab) {
    int best = 0;
    float bv = logits[0];
    for (int i = 1; i < n_vocab; i++) if (logits[i] > bv) { bv = logits[i]; best = i; }
    return best;
}

// Greedy completion of an already-formatted prompt. Stops at end-of-generation.
// Same output as plain greedy decoding, several times faster on copy-heavy text.
JNIEXPORT jstring JNICALL Java_app_localflow_engine_Native_llamaComplete(
        JNIEnv *env, jobject, jlong handle, jstring prompt, jint maxTokens) {
    auto *h = reinterpret_cast<LlamaHandle *>(handle);
    if (!h) return nullptr;
    const char *p = env->GetStringUTFChars(prompt, nullptr);
    std::string text = p;
    env->ReleaseStringUTFChars(prompt, p);

    const llama_vocab *vocab = llama_model_get_vocab(h->model);
    const int n_vocab = llama_vocab_n_tokens(vocab);
    std::vector<llama_token> toks(text.size() + 16);
    int n = llama_tokenize(vocab, text.c_str(), (int) text.size(), toks.data(), (int) toks.size(), false, true);
    if (n < 0) return nullptr;
    toks.resize(n);
    if (n + maxTokens + 16 > (int) llama_n_ctx(h->ctx)) return nullptr;

    auto t0 = std::chrono::steady_clock::now();
    llama_memory_t mem = llama_get_memory(h->ctx);
    llama_memory_clear(mem, true);
    llama_batch pb = llama_batch_get_one(toks.data(), n);
    if (llama_decode(h->ctx, pb) != 0) return nullptr;
    auto t1 = std::chrono::steady_clock::now();

    const int MAX_DRAFT = 10, NGRAM = 3;
    llama_batch batch = llama_batch_init(1 + MAX_DRAFT, 0, 1);
    std::vector<llama_token> hist = toks;
    std::string out;
    int n_past = n, generated = 0, steps = 0, accepted_total = 0;
    llama_token next = argmax_logits(llama_get_logits_ith(h->ctx, -1), n_vocab);

    while (generated < maxTokens) {
        if (llama_vocab_is_eog(vocab, next)) break;
        // emit `next`
        char buf[256];
        int len = llama_token_to_piece(vocab, next, buf, sizeof(buf), 0, true);
        if (len > 0) out.append(buf, len);
        hist.push_back(next);
        generated++;
        std::vector<llama_token> draft = lookup_draft(hist, NGRAM, MAX_DRAFT);

        batch.n_tokens = 0;
        auto add = [&](llama_token t, int pos) {
            int i = batch.n_tokens++;
            batch.token[i] = t; batch.pos[i] = pos; batch.n_seq_id[i] = 1; batch.seq_id[i][0] = 0; batch.logits[i] = true;
        };
        add(next, n_past);
        for (size_t k = 0; k < draft.size(); k++) add(draft[k], n_past + 1 + (int) k);
        if (llama_decode(h->ctx, batch) != 0) break;
        steps++;

        // verify the draft against the model's own greedy choices
        int acc = 0;
        llama_token pred = argmax_logits(llama_get_logits_ith(h->ctx, 0), n_vocab);
        bool stop = false;
        while (acc < (int) draft.size() && pred == draft[acc]) {
            if (llama_vocab_is_eog(vocab, pred)) { stop = true; break; }
            len = llama_token_to_piece(vocab, pred, buf, sizeof(buf), 0, true);
            if (len > 0) out.append(buf, len);
            hist.push_back(pred);
            generated++;
            acc++;
            pred = argmax_logits(llama_get_logits_ith(h->ctx, acc), n_vocab);
        }
        accepted_total += acc;
        n_past += 1 + acc;
        if (acc < (int) draft.size()) llama_memory_seq_rm(mem, 0, n_past, -1);   // drop the rejected draft
        if (stop) break;
        next = pred;
    }
    llama_batch_free(batch);
    auto t2 = std::chrono::steady_clock::now();
    LOGI("llama: prompt %d tok in %lld ms, %d tok generated in %lld ms (%d forward passes, %d drafted tokens accepted)", n,
         (long long) std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count(), generated,
         (long long) std::chrono::duration_cast<std::chrono::milliseconds>(t2 - t1).count(), steps, accepted_total);
    return env->NewStringUTF(out.c_str());
}

}
