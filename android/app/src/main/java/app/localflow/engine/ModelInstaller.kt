package app.localflow.engine

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlin.concurrent.thread

/** Downloads whatever this phone needs, once, and reports progress to the UI. */
object ModelInstaller {
    data class State(
        val running: Boolean = false,
        val done: Long = 0,
        val total: Long = 0,
        val current: String = "",
        val error: String = "",
        val finished: Boolean = false,
    ) {
        val fraction get() = if (total > 0) (done.toFloat() / total).coerceIn(0f, 1f) else 0f
    }

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state
    @Volatile private var cancel = false

    fun start(ctx: Context) {
        if (_state.value.running) return
        val host = EngineHost.get(ctx)
        val todo = host.plan().filterNot { Downloader.isInstalled(it, host.modelDir) }
        if (todo.isEmpty()) { _state.value = State(finished = true); return }
        cancel = false
        val total = todo.sumOf { it.size }
        _state.value = State(running = true, total = total, current = todo.first().title)
        thread(name = "lf-download", isDaemon = true) {
            var base = 0L
            try {
                for (m in todo) {
                    _state.value = _state.value.copy(current = m.title)
                    Downloader.fetch(m, host.modelDir, { got -> _state.value = _state.value.copy(done = base + got) }) { cancel }
                    base += m.size
                }
                _state.value = State(finished = true, done = total, total = total)
            } catch (e: Exception) {
                _state.value = State(error = e.message ?: "download failed", total = total, done = base)
            }
        }
    }

    fun cancel() { cancel = true }
}
