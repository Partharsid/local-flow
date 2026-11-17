package app.localflow

import app.localflow.text.Hinglish
import app.localflow.text.RuleCleanup
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class HinglishTest {
    @Test fun commonWords() {
        assertEquals("kal meeting hai", Hinglish.toRoman("कल meeting है"))
        assertEquals("mujhe nahi pata", Hinglish.toRoman("मुझे नहीं पता"))
    }

    @Test fun latinTextIsUntouched() {
        val s = "Please send the report by 5 PM, thanks."
        assertEquals(s, Hinglish.toRoman(s))
    }

    @Test fun phoneticFallbackAndSchwa() {
        assertEquals("kamal", Hinglish.toRoman("कमल"))
        assertEquals("samajh", Hinglish.toRoman("समझ"))
        assertEquals("kitaab", Hinglish.toRoman("किताब"))
    }

    @Test fun danda_becomes_full_stop() {
        assertEquals("theek hai.", Hinglish.toRoman("ठीक है।"))
    }

    @Test fun mixedSentenceHasNoDevanagariLeft() {
        val out = Hinglish.toRoman("मैं कल office जाऊंगा और report भेज दूंगा।")
        assertFalse(Hinglish.hasDevanagari(out))
        assertTrue(out.contains("office") && out.contains("report"))
    }

    @Test fun ruleCleanup() {
        assertEquals("Um, hello there. This is a test.", RuleCleanup.clean("um, hello there. this is a test", keepFillers = true))
        assertEquals("Hello there. I think we should go.", RuleCleanup.clean("uh hello there. i think we should go"))
        assertEquals("Send it today.", RuleCleanup.clean("send it it today"))
    }
}
