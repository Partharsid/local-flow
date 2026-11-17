package app.localflow

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.setValue
import app.localflow.ui.Home
import app.localflow.ui.LocalFlowTheme

class MainActivity : ComponentActivity() {
    private var resumes by mutableIntStateOf(0)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent { LocalFlowTheme { Home(resumes) } }
    }

    override fun onResume() {
        super.onResume()
        resumes++                       // makes the setup checklist re-check after Settings screens
    }
}
