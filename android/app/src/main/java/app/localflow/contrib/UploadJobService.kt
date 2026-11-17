package app.localflow.contrib

import android.app.job.JobParameters
import android.app.job.JobService
import kotlin.concurrent.thread

/** Runs when Wi-Fi is available and uploads the queued, opted-in samples. */
class UploadJobService : JobService() {
    @Volatile private var worker: Thread? = null

    override fun onStartJob(params: JobParameters): Boolean {
        worker = thread(name = "lf-upload", isDaemon = true) {
            val done = runCatching { Contribution.uploadAll(applicationContext) }.getOrDefault(false)
            jobFinished(params, !done)                       // not done: Android retries with backoff
        }
        return true
    }

    override fun onStopJob(params: JobParameters): Boolean = true     // reschedule if interrupted
}
