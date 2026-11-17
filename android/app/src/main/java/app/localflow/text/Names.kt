package app.localflow.text

import kotlin.math.max

/**
 * Fixes the spelling of names, brands, apps and places after speech recognition.
 *
 * Speech models spell proper nouns the way they sound ("swiggi", "what's app", "kondapoor"). Here they are
 * snapped to the right spelling from (1) the person's own "Your words" list and (2) a built-in list of
 * names Indian users say all the time. A word is replaced only when it sounds like the name (same consonant
 * skeleton) or is one letter away, and never when it is an ordinary word.
 */
object Names {
    /** Brands, apps, services, cities and areas that come up constantly. Casing here is the casing typed. */
    val BUILT_IN = listOf(
        // apps and services
        "WhatsApp", "Instagram", "Facebook", "YouTube", "Telegram", "Snapchat", "LinkedIn", "Twitter", "Gmail", "Google", "Chrome",
        "Swiggy", "Zomato", "Blinkit", "Zepto", "BigBasket", "Flipkart", "Amazon", "Myntra", "Meesho", "Nykaa", "Ajio", "Paytm",
        "PhonePe", "GPay", "BHIM", "UPI", "Razorpay", "Uber", "Ola", "Rapido", "IRCTC", "RedBus", "MakeMyTrip", "Netflix",
        "Hotstar", "Spotify", "Jio", "Airtel", "Vodafone", "BSNL", "Aadhaar", "DigiLocker", "Zoom", "Slack", "Notion", "ChatGPT",
        "Claude", "Gemini", "Copilot", "GitHub", "VS Code", "Android", "iPhone", "Samsung", "OnePlus", "Xiaomi", "Realme", "Nothing",
        "Coolify", "Cloudflare", "Kubernetes", "Docker", "Supabase", "Firebase", "Postgres", "MongoDB", "LocalFlow",
        // cities and areas
        "Hyderabad", "Secunderabad", "Bengaluru", "Bangalore", "Mumbai", "Delhi", "Chennai", "Kolkata", "Pune", "Ahmedabad", "Jaipur",
        "Lucknow", "Chandigarh", "Vijayawada", "Visakhapatnam", "Warangal", "Kondapur", "Gachibowli", "Madhapur", "Hitech City",
        "Kukatpally", "Banjara Hills", "Jubilee Hills", "Ameerpet", "Secunderabad", "Telangana", "Andhra Pradesh", "Karnataka",
        "Maharashtra", "Gujarat", "Rajasthan", "Kerala", "Tamil Nadu", "India", "Bharat", "Goa", "Noida", "Gurgaon", "Gurugram",
        // common Indian names and honorifics people dictate
        "Rahul", "Priya", "Amit", "Anjali", "Sneha", "Rohit", "Karthik", "Ananya", "Vikram", "Pooja", "Arjun", "Neha", "Suresh",
        "Ramesh", "Lakshmi", "Venkat", "Srinivas", "Sai", "Kiran", "Ravi", "Sunil", "Deepak", "Manish", "Nikhil", "Aditya",
    )

    /** Ordinary words that must never be rewritten into a name. */
    private val ORDINARY = (
        "the and for you are was were with this that have from they will your what when where which there their about would could should " +
        "hai hain tha thi the kya nahi nahin haan aur ya toh bhi hi ho hoga hogi karo kar kiya kiye karna mera meri mujhe tum aap hum woh " +
        "yeh ye kal aaj abhi phir jab tab bas sab kuch koi kaun kab kaha kahan yahan wahan lekin par pe ko ka ki ke se mein ek do teen " +
        "chaar paanch accha achha theek thik bahut thoda zyada kam din raat ghar kaam baat log paisa paise bhai dost samay baad pehle " +
        "liye saath bilkul zaroor shukriya namaste ji sir madam please thanks thank sorry okay ok meeting report office client email phone " +
        "message call time today tomorrow yesterday morning evening night week month year first last next good best new old big small " +
        "free open close start stop send share check review update team work project plan task issue bug fix release build test"
        ).split(" ").toSet()

    private val token = Regex("""[\p{L}][\p{L}\p{N}']*""")

    /** Replace near-miss spellings of [user] words and the built-in names. Plain text in, plain text out. */
    fun fix(text: String, user: List<String> = emptyList()): String {
        if (text.isBlank()) return text
        val terms = (user.map { it.trim() }.filter { it.length >= 2 } + BUILT_IN).distinctBy { it.lowercase() }
        if (terms.isEmpty()) return text
        val byKey = HashMap<String, MutableList<String>>()
        for (t in terms) byKey.getOrPut(skeleton(t)) { mutableListOf() }.add(t)

        // Work on word tokens; allow joining two or three neighbours ("what s app" -> WhatsApp).
        val matches = token.findAll(text).toList()
        val sb = StringBuilder()
        var pos = 0
        var i = 0
        while (i < matches.size) {
            var done = false
            for (span in 3 downTo 1) {
                if (i + span > matches.size) continue
                // only join tokens separated by a single space or apostrophe-like gap
                val first = matches[i]
                val last = matches[i + span - 1]
                val between = text.substring(first.range.first, last.range.last + 1)
                if (span > 1 && between.count { it.isWhitespace() } != span - 1) continue
                if (span > 1 && between.any { !it.isLetterOrDigit() && !it.isWhitespace() && it != '\'' }) continue
                val words = (i until i + span).map { matches[it].value }
                val joined = words.joinToString("")
                val best = match(joined, words, terms, byKey) ?: continue
                sb.append(text, pos, first.range.first).append(best)
                pos = last.range.last + 1
                i += span
                done = true
                break
            }
            if (!done) i++
        }
        sb.append(text, pos, text.length)
        return sb.toString()
    }

    private fun match(joined: String, words: List<String>, terms: List<String>, byKey: Map<String, List<String>>): String? {
        val low = joined.lowercase().replace("'", "")
        if (low.length < 4) return null
        if (words.size == 1 && words[0].lowercase() in ORDINARY) return null
        if (words.size > 1 && words.all { it.lowercase() in ORDINARY }) return null
        // exact (ignoring case) names get their proper casing
        terms.firstOrNull { it.lowercase().replace(" ", "") == low }?.let { return it }
        val sk = skeleton(joined)
        val candidates = byKey[sk].orEmpty()
        for (t in candidates) {
            val tl = t.lowercase().replace(" ", "")
            if (similarity(low, tl) >= 0.6) return t
        }
        // one letter off, for names long enough that this is unlikely to be a coincidence
        for (t in terms) {
            val tl = t.lowercase().replace(" ", "")
            if (tl.length >= 6 && low.length >= 6 && kotlin.math.abs(tl.length - low.length) <= 1 && levenshtein(low, tl) <= 1) return t
        }
        return null
    }

    /** Consonant skeleton with Roman-Hindi sound equivalences: 'swiggi', 'swigi', 'Swiggy' all become 'svg'. */
    fun skeleton(s: String): String {
        var x = s.lowercase().replace(Regex("[^a-z]"), "")
        x = x.replace("ph", "f").replace("ck", "k").replace("kh", "k").replace("gh", "g").replace("sh", "s").replace("ch", "c")
            .replace("th", "t").replace("dh", "d").replace("bh", "b").replace("x", "ks").replace("q", "k")
        val out = StringBuilder()
        for ((n, c) in x.withIndex()) {
            val m = when (c) { 'w' -> 'v'; 'z' -> 's'; 'c' -> 'k'; 'j' -> 'j'; 'y' -> if (n == 0) 'y' else '\u0000'; else -> c }
            if (m == '\u0000') continue
            if (n > 0 && c in "aeiouh") continue                        // vowels and 'h' vanish unless the word starts with one
            if (out.isNotEmpty() && out.last() == m) continue            // double letters
            out.append(m)
        }
        return out.toString()
    }

    private fun similarity(a: String, b: String): Double = 1.0 - levenshtein(a, b).toDouble() / max(a.length, b.length)

    private fun levenshtein(a: String, b: String): Int {
        var prev = IntArray(b.length + 1) { it }
        for (i in 1..a.length) {
            val cur = IntArray(b.length + 1); cur[0] = i
            for (j in 1..b.length) cur[j] = minOf(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + if (a[i - 1] == b[j - 1]) 0 else 1)
            prev = cur
        }
        return prev[b.length]
    }
}
