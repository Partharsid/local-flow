package app.localflow

import app.localflow.text.Names
import org.junit.Assert.assertEquals
import org.junit.Test

class NamesTest {
    @Test fun brandsAreSnappedToTheRightSpelling() {
        assertEquals("order on Swiggy tonight", Names.fix("order on swiggi tonight"))
        assertEquals("send it on WhatsApp now", Names.fix("send it on what s app now"))
        assertEquals("send it on WhatsApp now", Names.fix("send it on whatsapp now"))
        assertEquals("pay with PhonePe", Names.fix("pay with phone pay"))
        assertEquals("open Zomato", Names.fix("open zomatto"))
    }

    @Test fun placesAndNames() {
        assertEquals("main Kondapur mein hoon", Names.fix("main kondapoor mein hoon"))
        assertEquals("Hyderabad se Bengaluru", Names.fix("hyderbad se bangaluru"))
        assertEquals("Rahul ko bolo", Names.fix("rahool ko bolo"))
    }

    @Test fun usersOwnWordsWin() {
        assertEquals("Partharsid ne bheja", Names.fix("parth arsid ne bheja", listOf("Partharsid")))
        assertEquals("call Eggeez today", Names.fix("call eggies today", listOf("Eggeez")))
    }

    @Test fun ordinaryWordsAreLeftAlone() {
        val s = "kal meeting hai, please report bhej dena. Mujhe lagta hai ki sab theek hai"
        assertEquals(s, Names.fix(s))
        val e = "send the report to the client before the meeting"
        assertEquals(e, Names.fix(e, listOf("Swiggy", "Rahul")))
    }

    @Test fun skeletonEquivalences() {
        assertEquals(Names.skeleton("Swiggy"), Names.skeleton("swiggi"))
        assertEquals(Names.skeleton("Kondapur"), Names.skeleton("kondapoor"))
    }
}

class NameIndexTest {
    private val ix = System.getenv("LF_NAMES_BIN")?.let { java.io.File(it) }?.let { app.localflow.text.NameIndex.open(it) }

    @Test fun bigList() {
        org.junit.Assume.assumeTrue(ix != null)
        Names.index = ix
        try {
            assertEquals("I want Kurkure chips", Names.fix("I want kurkure chips"))
            assertEquals("I want Kurkure chips", Names.fix("I want kurkuray chips"))
            assertEquals("buy Too Yumm now", Names.fix("buy too yum now"))
            assertEquals("order on Swiggy", Names.fix("order on swiggi"))
            val s = "kal meeting hai, please report bhej dena. Mujhe lagta hai ki sab theek hai"
            assertEquals(s, Names.fix(s))
            val e = "send the report to the client before the meeting tomorrow morning"
            assertEquals(e, Names.fix(e))
            println("index terms: ${ix!!.size}")
        } finally { Names.index = null }
    }
}
