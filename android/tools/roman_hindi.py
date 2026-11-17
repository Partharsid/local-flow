"""A small Devanagari -> Roman transliterator (same sounds as app/.../text/Hinglish.kt), used only to turn a Hindi word list
into the Roman spellings that must never be rewritten as names."""
V = {"अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au", "ऑ": "o"}
M = {"ा": "aa", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "े": "e", "ै": "ai", "ो": "o", "ौ": "au", "ॉ": "o", "ृ": "ri"}
C = {"क": "k", "ख": "kh", "ग": "g", "घ": "gh", "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh",
     "ण": "n", "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n", "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m", "य": "y",
     "र": "r", "ल": "l", "व": "v", "श": "sh", "ष": "sh", "स": "s", "ह": "h", "ङ": "n", "ञ": "n"}
H = "्"


def to_roman(w: str) -> str:
    syl = []                                   # [consonant text, vowel or None]
    i = 0
    while i < len(w):
        c = w[i]
        if c in V:
            syl.append(["", V[c]]); i += 1
        elif c in C:
            cons = C[c]; i += 1
            if i < len(w) and w[i] == "़":
                i += 1
            while i + 1 < len(w) and w[i] == H and w[i + 1] in C:
                cons += C[w[i + 1]]; i += 2
            if i < len(w) and w[i] in M:
                syl.append([cons, M[w[i]]]); i += 1
            elif i < len(w) and w[i] == H:
                syl.append([cons, ""]); i += 1
            else:
                syl.append([cons, None])
        elif c in ("ं", "ँ"):
            if syl:
                syl[-1][1] = (syl[-1][1] or "a") + "n"
            i += 1
        else:
            i += 1
    for k in reversed(range(len(syl))):
        if syl[k][1] is None:
            if k == len(syl) - 1:
                syl[k][1] = ""
            elif k > 0 and syl[k + 1][1]:
                syl[k][1] = ""
            else:
                syl[k][1] = "a"
    return "".join(c + (v or "") for c, v in syl)
