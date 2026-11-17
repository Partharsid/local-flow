package app.localflow.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

/** Same warm, light, quiet look as the desktop app. */
object Lf {
    val bg = Color(0xFFFBF8F3)
    val card = Color.White
    val side = Color(0xFFF4EFE7)
    val line = Color(0xFFEBE5DA)
    val text = Color(0xFF1F1B15)
    val muted = Color(0xFF7B7467)
    val faint = Color(0xFFA9A294)
    val accent = Color(0xFF6C4DFF)
    val accentSoft = Color(0xFFEFEAFF)
    val ok = Color(0xFF2F9E6C)
    val warn = Color(0xFFD98A12)
    val bad = Color(0xFFD6455B)
    val serif = FontFamily.Serif
}

private val scheme = lightColorScheme(
    primary = Lf.text, onPrimary = Color.White, background = Lf.bg, onBackground = Lf.text,
    surface = Lf.card, onSurface = Lf.text, secondary = Lf.accent, outline = Lf.line,
)

@Composable
fun LocalFlowTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = scheme,
        typography = Typography(
            headlineLarge = TextStyle(fontFamily = Lf.serif, fontWeight = FontWeight.Medium, fontSize = 32.sp, lineHeight = 38.sp),
            headlineMedium = TextStyle(fontFamily = Lf.serif, fontWeight = FontWeight.Medium, fontSize = 24.sp, lineHeight = 30.sp),
            titleMedium = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 16.sp),
            bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 20.sp),
            bodySmall = TextStyle(fontSize = 12.sp, lineHeight = 16.sp),
            labelLarge = TextStyle(fontWeight = FontWeight.SemiBold, fontSize = 14.sp),
        ),
        content = content,
    )
}
