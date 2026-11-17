package app.localflow.text

/**
 * Hinglish = Hindi written in English letters, English words left as they are
 * ("kal meeting hai, please report bhej dena").
 *
 * Whisper is steered towards this by a Roman-script prompt (see [PROMPT]); when
 * it still answers in Devanagari for some words, [toRoman] converts those
 * words with a phonetic map and everyday Hinglish spellings, and leaves every
 * Latin word untouched.
 */
object Hinglish {
    /** Primes Whisper: Hindi words in Roman letters, English words as English. */
    const val PROMPT = "Kal meeting hai, please report bhej dena. Haan theek hai, main abhi karta hoon. " +
        "Aap ka kaam kaisa chal raha hai? Mujhe lagta hai ki hum next week tak finish kar lenge."

    private val vowels = mapOf('अ' to "a", 'आ' to "aa", 'इ' to "i", 'ई' to "ee", 'उ' to "u", 'ऊ' to "oo", 'ऋ' to "ri",
        'ए' to "e", 'ऐ' to "ai", 'ओ' to "o", 'औ' to "au", 'ऑ' to "o", 'ऍ' to "e")
    private val matras = mapOf('ा' to "aa", 'ि' to "i", 'ी' to "ee", 'ु' to "u", 'ू' to "oo", 'ृ' to "ri",
        'े' to "e", 'ै' to "ai", 'ो' to "o", 'ौ' to "au", 'ॉ' to "o", 'ॅ' to "e")
    private val consonants = mapOf(
        'क' to "k", 'ख' to "kh", 'ग' to "g", 'घ' to "gh", 'ङ' to "n",
        'च' to "ch", 'छ' to "chh", 'ज' to "j", 'झ' to "jh", 'ञ' to "n",
        'ट' to "t", 'ठ' to "th", 'ड' to "d", 'ढ' to "dh", 'ण' to "n",
        'त' to "t", 'थ' to "th", 'द' to "d", 'ध' to "dh", 'न' to "n",
        'प' to "p", 'फ' to "ph", 'ब' to "b", 'भ' to "bh", 'म' to "m",
        'य' to "y", 'र' to "r", 'ल' to "l", 'व' to "v", 'श' to "sh", 'ष' to "sh", 'स' to "s", 'ह' to "h",
        'ळ' to "l")
    /** Consonant + nukta (the dot below): foreign sounds like ज़ (z) and फ़ (f). */
    private val nukta = mapOf('क' to "q", 'ख' to "kh", 'ग' to "g", 'ज' to "z", 'ड' to "r", 'ढ' to "rh", 'फ' to "f")
    private const val HALANT = '्'
    private const val NUKTA = '़'

    /** Words whose everyday Hinglish spelling differs from a strict phonetic reading. */
    private val common = mapOf(
        "है" to "hai", "हैं" to "hain", "हूँ" to "hoon", "हूं" to "hoon", "हो" to "ho", "होगा" to "hoga", "होगी" to "hogi",
        "नहीं" to "nahi", "नही" to "nahi", "मैं" to "main", "में" to "mein", "मेरा" to "mera", "मेरी" to "meri",
        "मुझे" to "mujhe", "तुम" to "tum", "तुम्हें" to "tumhe", "आप" to "aap", "हम" to "hum", "वो" to "woh", "वह" to "woh",
        "यह" to "yeh", "ये" to "ye", "को" to "ko", "का" to "ka", "की" to "ki", "के" to "ke", "से" to "se", "पर" to "par",
        "और" to "aur", "या" to "ya", "तो" to "toh", "भी" to "bhi", "ही" to "hi", "ना" to "na", "न" to "na", "मत" to "mat",
        "क्या" to "kya", "क्यों" to "kyon", "कैसे" to "kaise", "कैसा" to "kaisa", "कौन" to "kaun", "कब" to "kab",
        "कहाँ" to "kahan", "कहां" to "kahan", "यहाँ" to "yahan", "यहां" to "yahan", "वहाँ" to "wahan", "वहां" to "wahan",
        "कल" to "kal", "आज" to "aaj", "अभी" to "abhi", "फिर" to "phir", "जब" to "jab", "तब" to "tab", "अब" to "ab",
        "हाँ" to "haan", "हां" to "haan", "जी" to "ji", "ठीक" to "theek", "अच्छा" to "accha", "अच्छी" to "acchi",
        "बहुत" to "bahut", "थोड़ा" to "thoda", "सब" to "sab", "सब्ज़ी" to "sabzi", "कुछ" to "kuch", "कोई" to "koi",
        "काम" to "kaam", "बात" to "baat", "लोग" to "log", "घर" to "ghar", "पैसे" to "paise", "दिन" to "din", "रात" to "raat",
        "एक" to "ek", "दो" to "do", "तीन" to "teen", "चार" to "chaar", "पाँच" to "paanch", "दस" to "das",
        "था" to "tha", "थी" to "thi", "थे" to "the", "रहा" to "raha", "रही" to "rahi", "रहे" to "rahe",
        "करना" to "karna", "करो" to "karo", "करता" to "karta", "करती" to "karti", "करते" to "karte", "कर" to "kar",
        "चाहिए" to "chahiye", "भाई" to "bhai", "दोस्त" to "dost", "समय" to "samay", "बाद" to "baad", "पहले" to "pehle",
        "लिए" to "liye", "साथ" to "saath", "बस" to "bas", "बिल्कुल" to "bilkul", "ज़रूर" to "zaroor", "जरूर" to "zaroor",
        "शुक्रिया" to "shukriya", "धन्यवाद" to "dhanyavaad", "नमस्ते" to "namaste", "माफ़" to "maaf", "माफ" to "maaf")

    private val punct = mapOf('।' to '.', '॥' to '.', '‘' to '\'', '’' to '\'')

    private fun isDevanagari(c: Char) = c in 'ऀ'..'ॿ'

    /** Convert any Devanagari in [text] to Roman; Latin text and punctuation pass through unchanged. */
    fun toRoman(text: String): String {
        if (text.none(::isDevanagari)) return text
        val sb = StringBuilder()
        var i = 0
        while (i < text.length) {
            val c = text[i]
            if (isDevanagari(c) && c != '।' && c != '॥') {
                var j = i
                while (j < text.length && isDevanagari(text[j]) && text[j] != '।' && text[j] != '॥') j++
                sb.append(word(text.substring(i, j)))
                i = j
            } else {
                sb.append(punct[c] ?: c)
                i++
            }
        }
        return sb.toString()
    }

    fun hasDevanagari(text: String) = text.any(::isDevanagari)

    private fun word(w: String): String {
        common[w]?.let { return it }
        // Syllable list: (consonant cluster text, vowel text, has explicit vowel)
        class Syl(var cons: String, var vowel: String?, var explicit: Boolean)
        val syl = ArrayList<Syl>()
        var i = 0
        while (i < w.length) {
            val c = w[i]
            when {
                vowels.containsKey(c) -> { syl.add(Syl("", vowels[c], true)); i++ }
                consonants.containsKey(c) || c == 'ं' || c == 'ँ' || c == 'ः' -> {
                    if (c == 'ं' || c == 'ँ') {
                        // nasal: attaches to the previous syllable as 'n' ('m' before p/b)
                        val next = w.getOrNull(i + 1)
                        val m = next != null && (consonants[next]?.startsWith("p") == true || consonants[next]?.startsWith("b") == true)
                        if (syl.isNotEmpty()) syl.last().let { it.vowel = (it.vowel ?: "a") + (if (m) "m" else "n"); it.explicit = true }
                        i++
                    } else if (c == 'ः') {
                        if (syl.isNotEmpty()) syl.last().let { it.vowel = (it.vowel ?: "a") + "h"; it.explicit = true }
                        i++
                    } else {
                        var base = consonants[c]!!
                        i++
                        if (w.getOrNull(i) == NUKTA) { base = nukta[c] ?: base; i++ }
                        // conjunct: consonant + halant + consonant (+ ...) stays in one syllable
                        val cluster = StringBuilder(base)
                        while (w.getOrNull(i) == HALANT && i + 1 < w.length && consonants.containsKey(w[i + 1])) {
                            cluster.append(consonants[w[i + 1]]!!)
                            i += 2
                            if (w.getOrNull(i) == NUKTA) i++
                        }
                        val tail = w.getOrNull(i)
                        when {
                            tail != null && matras.containsKey(tail) -> { syl.add(Syl(cluster.toString(), matras[tail], true)); i++ }
                            tail == HALANT -> { syl.add(Syl(cluster.toString(), "", true)); i++ }     // final / dead consonant
                            else -> syl.add(Syl(cluster.toString(), null, false))                    // inherent 'a'
                        }
                    }
                }
                else -> i++
            }
        }
        // Schwa deletion, right to left: the inherent 'a' is silent at the end of a word, and
        // inside a word when a syllable before it exists and the next syllable is sounded
        // (kamal, samajh, ladka).
        for (k in syl.indices.reversed()) {
            val s = syl[k]
            if (s.explicit || s.vowel != null) continue
            s.vowel = when {
                k == syl.lastIndex -> ""
                k > 0 && !syl[k + 1].vowel.isNullOrEmpty() -> ""
                else -> "a"
            }
        }
        // Word-final long "aa" is written "a" in everyday Hinglish: pata, mera, accha.
        if (syl.size > 1 && syl.last().vowel == "aa" && syl.last().cons.isNotEmpty()) syl.last().vowel = "a"
        return syl.joinToString("") { (it.cons) + (it.vowel ?: "") }
    }
}
