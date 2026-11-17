package app.localflow.ui

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings as AndroidSettings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateContentSize
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.localflow.LangMode
import app.localflow.Settings
import app.localflow.engine.EngineHost
import app.localflow.engine.ModelCatalog
import app.localflow.engine.ModelInstaller
import app.localflow.service.DictationService
import kotlinx.coroutines.delay

private fun mic(ctx: Context) = ctx.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

private fun bubbleEnabled(ctx: Context): Boolean {
    val s = AndroidSettings.Secure.getString(ctx.contentResolver, AndroidSettings.Secure.ENABLED_ACCESSIBILITY_SERVICES) ?: ""
    return s.contains("${ctx.packageName}/${DictationService::class.java.name}")
}

@Composable
fun Home(refreshKey: Int) {
    val ctx = LocalContext.current
    val settings = remember { Settings(ctx) }
    val host = remember { EngineHost.get(ctx) }
    var tick by remember { mutableIntStateOf(0) }
    var micOk by remember { mutableStateOf(mic(ctx)) }
    var bubbleOk by remember { mutableStateOf(bubbleEnabled(ctx)) }
    var modelsOk by remember { mutableStateOf(host.modelsInstalled()) }
    val install by ModelInstaller.state.collectAsState()

    // Re-check whenever the user comes back from a system settings screen.
    LaunchedEffect(refreshKey, tick) {
        micOk = mic(ctx); bubbleOk = bubbleEnabled(ctx); modelsOk = host.modelsInstalled()
    }
    LaunchedEffect(install.finished) { if (install.finished) { modelsOk = host.modelsInstalled(); settings.modelsReady = modelsOk } }
    LaunchedEffect(Unit) { while (true) { delay(1500); micOk = mic(ctx); bubbleOk = bubbleEnabled(ctx) } }

    val micLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { micOk = it }
    val ready = micOk && bubbleOk && modelsOk

    Box(Modifier.fillMaxSize().background(Lf.bg)) {
        Column(
            Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().imePadding()
                .verticalScroll(rememberScrollState()).padding(horizontal = 20.dp, vertical = 24.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Mark()
                Text("LocalFlow", fontWeight = FontWeight.SemiBold, fontSize = 18.sp, color = Lf.text)
            }
            Spacer(Modifier.height(10.dp))
            Text(if (ready) "Ready when you are." else "Let's get you set up.", style = androidx.compose.material3.MaterialTheme.typography.headlineLarge, color = Lf.text)
            Text(
                if (ready) "Tap the bubble above your keyboard in any app, speak, and it types for you. Everything runs on this phone."
                else "Three quick steps. Your voice never leaves this phone.",
                color = Lf.muted, fontSize = 15.sp, lineHeight = 21.sp,
            )

            AnimatedVisibility(!ready) {
                Card {
                    Step("1", "Microphone", "So it can hear you.", micOk, "Allow") { micLauncher.launch(Manifest.permission.RECORD_AUDIO) }
                    Divider()
                    Step("2", "Dictation bubble", "Turn on LocalFlow in Accessibility so the bubble can type for you.", bubbleOk, "Open settings") {
                        ctx.startActivity(Intent(AndroidSettings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                    }
                    Divider()
                    Step("3", "Speech models", modelsText(host), modelsOk, if (install.running) "…" else "Download") { ModelInstaller.start(ctx) }
                    if (install.running || install.error.isNotEmpty()) {
                        Column(Modifier.padding(horizontal = 18.dp).padding(bottom = 16.dp)) {
                            LinearProgressIndicator(progress = { install.fraction }, Modifier.fillMaxWidth().height(6.dp).clip(RoundedCornerShape(4.dp)),
                                color = Lf.accent, trackColor = Lf.side)
                            Text(if (install.error.isNotEmpty()) install.error else "${install.current} · ${mb(install.done)} of ${mb(install.total)}",
                                color = if (install.error.isNotEmpty()) Lf.bad else Lf.muted, fontSize = 12.sp, modifier = Modifier.padding(top = 8.dp))
                        }
                    }
                }
            }

            if (ready) {
                SectionLabel("Language")
                var mode by remember { mutableStateOf(settings.mode) }
                Card {
                    LangMode.entries.forEachIndexed { i, m ->
                        if (i > 0) Divider()
                        Row(Modifier.fillMaxWidth().clickable { mode = m; settings.mode = m }.padding(horizontal = 18.dp, vertical = 14.dp),
                            verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(m.label, fontWeight = FontWeight.SemiBold, color = Lf.text)
                                Text(m.hint, color = Lf.muted, fontSize = 12.sp)
                            }
                            Box(Modifier.size(20.dp).clip(CircleShape).background(if (mode == m) Lf.accent else Lf.side), contentAlignment = Alignment.Center) {
                                if (mode == m) Box(Modifier.size(8.dp).clip(CircleShape).background(Color.White))
                            }
                        }
                    }
                }

                SectionLabel("Try it")
                var sample by remember { mutableStateOf("") }
                Card {
                    OutlinedTextField(
                        sample, { sample = it }, Modifier.fillMaxWidth().padding(14.dp),
                        placeholder = { Text("Tap here, then tap the bubble and speak…", color = Lf.faint) },
                        minLines = 3, shape = RoundedCornerShape(12.dp),
                        colors = OutlinedTextFieldDefaults.colors(focusedBorderColor = Lf.accent, unfocusedBorderColor = Lf.line),
                        keyboardOptions = KeyboardOptions.Default,
                    )
                    // Refresh while the screen is open so a dictation shows its numbers right away.
                    var last by remember { mutableStateOf(DictationService.lastHeard) }
                    LaunchedEffect(Unit) { while (true) { delay(700); last = DictationService.lastHeard } }
                    val l = last
                    if (l != null) Text(
                        "Last: %.1fs of speech, heard in %d ms. Mic level: peak %.2f".format(l.audioSec, l.sttMs, l.peak) +
                            if (l.peak < 0.02f) " (very quiet: speak closer to the mic)" else "",
                        color = Lf.muted, fontSize = 12.sp, modifier = Modifier.padding(horizontal = 18.dp).padding(bottom = 14.dp))
                }

                SectionLabel("Preferences")
                var cleanup by remember { mutableStateOf(settings.cleanup) }
                var fieldsOnly by remember { mutableStateOf(settings.bubbleOnlyInTextFields) }
                var haptics by remember { mutableStateOf(settings.haptics) }
                var accurate by remember { mutableStateOf(settings.accurate) }
                Card {
                    Toggle("AI cleanup", "Removes fillers, fixes punctuation (English).", cleanup && host.profile.tier == app.localflow.engine.DeviceProfile.Tier.FULL,
                        host.profile.tier == app.localflow.engine.DeviceProfile.Tier.FULL) { cleanup = it; settings.cleanup = it }
                    Divider()
                    if (host.profile.tier != app.localflow.engine.DeviceProfile.Tier.LITE) {
                        Toggle("Best accuracy", "Bigger speech model (${mb(app.localflow.engine.ModelCatalog.whisperTurbo.size)} download). Better Hindi and accents, about 3x slower.", accurate) {
                            accurate = it; settings.accurate = it; modelsOk = host.modelsInstalled()
                            if (!modelsOk) ModelInstaller.start(ctx)
                        }
                        Divider()
                    }
                    Toggle("Bubble only in text fields", "Hide the bubble unless a keyboard is open.", fieldsOnly) { fieldsOnly = it; settings.bubbleOnlyInTextFields = it }
                    Divider()
                    Toggle("Vibrate on start and stop", null, haptics) { haptics = it; settings.haptics = it }
                }

                SectionLabel("Your words")
                var words by remember { mutableStateOf(settings.dictionary.joinToString(", ")) }
                Card {
                    OutlinedTextField(
                        words, { words = it; settings.dictionary = it.split(",").map(String::trim).filter(String::isNotEmpty) },
                        Modifier.fillMaxWidth().padding(14.dp), placeholder = { Text("Names and terms, separated by commas", color = Lf.faint) },
                        shape = RoundedCornerShape(12.dp),
                        colors = OutlinedTextFieldDefaults.colors(focusedBorderColor = Lf.accent, unfocusedBorderColor = Lf.line),
                    )
                }

                SectionLabel("Models on this phone")
                ModelsCard(host, install) { tick++ }

                SectionLabel("This phone")
                Card {
                    Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text("${host.profile.tier.label} · ${"%.1f".format(host.profile.totalRamMb / 1024f)} GB RAM · ${host.profile.cores} cores", fontWeight = FontWeight.SemiBold, color = Lf.text)
                        Text(host.plan().joinToString(" + ") { it.title }, color = Lf.muted, fontSize = 12.sp)
                    }
                }

                if (app.localflow.contrib.Contribution.available) ContributeCard()
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}

@Composable private fun ModelsCard(host: EngineHost, install: ModelInstaller.State, onChange: () -> Unit) {
    val ctx = LocalContext.current
    var version by remember { mutableIntStateOf(0) }
    Card {
        host.plan().forEachIndexed { i, m ->
            if (i > 0) Divider()
            val installed = remember(version, install.finished, install.running) { app.localflow.engine.Downloader.isInstalled(m, host.modelDir) }
            Row(Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(m.title, fontWeight = FontWeight.SemiBold, color = Lf.text)
                    Text("${mb(m.size)} · " + if (installed) "Installed" else "Not downloaded", color = if (installed) Lf.ok else Lf.muted, fontSize = 12.sp)
                }
                if (installed) {
                    TextButton({ m.path(host.modelDir).delete(); version++; onChange() }) { Text("Remove", color = Lf.muted) }
                } else {
                    Button({ ModelInstaller.start(ctx) }, enabled = !install.running, shape = RoundedCornerShape(11.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = Lf.text),
                        contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 14.dp, vertical = 8.dp)) {
                        Text(if (install.running) "…" else "Download", fontSize = 13.sp)
                    }
                }
            }
        }
        if (install.running || install.error.isNotEmpty()) {
            Column(Modifier.padding(horizontal = 18.dp).padding(bottom = 16.dp)) {
                LinearProgressIndicator(progress = { install.fraction }, Modifier.fillMaxWidth().height(6.dp).clip(RoundedCornerShape(4.dp)),
                    color = Lf.accent, trackColor = Lf.side)
                Text(if (install.error.isNotEmpty()) install.error else "${install.current} · ${mb(install.done)} of ${mb(install.total)}",
                    color = if (install.error.isNotEmpty()) Lf.bad else Lf.muted, fontSize = 12.sp, modifier = Modifier.padding(top = 8.dp))
            }
        }
    }
}

/** "Help improve LocalFlow": off by default; turning it on asks for explicit consent. */
@Composable private fun ContributeCard() {
    val ctx = LocalContext.current
    val c = app.localflow.contrib.Contribution
    var on by remember { mutableStateOf(c.enabled(ctx)) }
    var asking by remember { mutableStateOf(false) }
    var confirmDelete by remember { mutableStateOf(false) }
    var status by remember { mutableStateOf("") }
    var waiting by remember { mutableIntStateOf(c.pending(ctx)) }
    LaunchedEffect(Unit) { while (true) { delay(2000); waiting = c.pending(ctx) } }

    SectionLabel("Help improve LocalFlow")
    Card {
        Toggle("Share my dictations", "Optional. Recordings and typed text, uploaded on Wi-Fi to train better models. Off unless you turn it on.", on) { want ->
            if (want) asking = true else { c.turnOff(ctx); on = false }
        }
        if (on) {
            Divider()
            Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(if (waiting > 0) "$waiting recording${if (waiting == 1) "" else "s"} waiting for Wi-Fi" else "Nothing waiting to upload",
                    color = Lf.muted, fontSize = 12.sp)
            }
        }
        Divider()
        Row(Modifier.fillMaxWidth().clickable { confirmDelete = true }.padding(horizontal = 18.dp, vertical = 14.dp)) {
            Text("Delete my data", color = Lf.bad, fontWeight = FontWeight.SemiBold)
        }
        if (status.isNotEmpty()) Text(status, color = Lf.muted, fontSize = 12.sp, modifier = Modifier.padding(horizontal = 18.dp).padding(bottom = 14.dp))
    }
    if (asking) androidx.compose.material3.AlertDialog(
        onDismissRequest = { asking = false },
        title = { Text(c.CONSENT_TITLE) },
        text = { Text(c.CONSENT_TEXT, fontSize = 13.sp, lineHeight = 19.sp) },
        confirmButton = { TextButton({ c.accept(ctx); on = true; asking = false }) { Text("Turn on") } },
        dismissButton = { TextButton({ asking = false }) { Text("Not now") } },
    )
    if (confirmDelete) androidx.compose.material3.AlertDialog(
        onDismissRequest = { confirmDelete = false },
        title = { Text("Delete my data?") },
        text = { Text("This removes every recording you have shared from our server and clears anything waiting on this phone. It can't be undone.", fontSize = 13.sp) },
        confirmButton = { TextButton({
            confirmDelete = false; status = "Deleting…"
            kotlin.concurrent.thread { val ok = c.deleteAll(ctx); status = if (ok) "Deleted. Nothing of yours is stored on our server." else "Couldn't reach the server. Try again on Wi-Fi."; on = false }
        }) { Text("Delete", color = Lf.bad) } },
        dismissButton = { TextButton({ confirmDelete = false }) { Text("Cancel") } },
    )
}

private fun modelsText(host: EngineHost): String =
    "Downloads once (${mb(host.plan().sumOf { it.size })}), then works offline."

private fun mb(b: Long) = if (b >= 1_000_000_000) "%.1f GB".format(b / 1e9) else "${b / 1_000_000} MB"

@Composable private fun Mark() {
    Box(Modifier.size(30.dp).clip(RoundedCornerShape(10.dp)).background(Lf.text), contentAlignment = Alignment.Center) {
        Row(horizontalArrangement = Arrangement.spacedBy(2.5.dp), verticalAlignment = Alignment.CenterVertically) {
            listOf(7, 14, 10, 5).forEach { Box(Modifier.size(2.5.dp, it.dp).clip(RoundedCornerShape(2.dp)).background(Color.White)) }
        }
    }
}

@Composable private fun SectionLabel(t: String) =
    Text(t.uppercase(), color = Lf.faint, fontWeight = FontWeight.SemiBold, fontSize = 11.sp, letterSpacing = 1.sp, modifier = Modifier.padding(start = 4.dp, top = 14.dp))

@Composable private fun Card(content: @Composable () -> Unit) =
    Surface(shape = RoundedCornerShape(16.dp), color = Lf.card, border = BorderStroke(1.dp, Lf.line), shadowElevation = 1.dp,
        modifier = Modifier.fillMaxWidth().animateContentSize()) { Column { content() } }

@Composable private fun Divider() = Box(Modifier.fillMaxWidth().height(1.dp).background(Lf.line))

@Composable private fun Step(n: String, title: String, desc: String, done: Boolean, action: String, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().padding(18.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
        Box(Modifier.size(28.dp).clip(CircleShape).background(if (done) Lf.ok else Lf.side), contentAlignment = Alignment.Center) {
            Text(if (done) "✓" else n, color = if (done) Color.White else Lf.muted, fontWeight = FontWeight.Bold, fontSize = 13.sp)
        }
        Column(Modifier.weight(1f)) {
            Text(title, fontWeight = FontWeight.SemiBold, color = Lf.text)
            Text(desc, color = Lf.muted, fontSize = 12.sp, lineHeight = 16.sp)
        }
        if (!done) Button(onClick, shape = RoundedCornerShape(11.dp), colors = ButtonDefaults.buttonColors(containerColor = Lf.text), contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 14.dp, vertical = 8.dp)) {
            Text(action, fontSize = 13.sp)
        }
    }
}

@Composable private fun Toggle(title: String, desc: String?, checked: Boolean, enabled: Boolean = true, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 12.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, fontWeight = FontWeight.SemiBold, color = if (enabled) Lf.text else Lf.faint)
            if (desc != null) Text(if (enabled) desc else "$desc Needs a phone with 8 GB RAM.", color = Lf.muted, fontSize = 12.sp)
        }
        Switch(checked, onChange, enabled = enabled, colors = SwitchDefaults.colors(checkedTrackColor = Lf.accent, checkedThumbColor = Color.White,
            uncheckedTrackColor = Lf.line, uncheckedThumbColor = Color.White, uncheckedBorderColor = Lf.line))
    }
}

