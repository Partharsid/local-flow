"""Generate the synthetic dictation dataset.

    python training/gen_data.py --n 14000 --seed 1 --out training/data/synth_train.jsonl

Each line: {"mode", "app", "vocab", "input", "output"}. `output` is built
first from clean sentences; `input` is the same content spoken: fillers,
repeats, self-corrections ("5, no wait, 6"), "scratch that", spelled-out
numbers and emails, lost punctuation or Whisper-style punctuation. So the
target never contains anything the speaker didn't say.
"""
from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

import banks as B

R = random.Random(1)

ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen " \
       "seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def words(n: int) -> str:
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("" if n % 10 == 0 else " " + ONES[n % 10])
    if n < 1000:
        return ONES[n // 100] + " hundred" + ("" if n % 100 == 0 else " " + words(n % 100))
    if n < 100000:
        return words(n // 1000) + " thousand" + ("" if n % 1000 == 0 else " " + words(n % 1000))
    return str(n)


# -- slot values: (written, spoken) -------------------------------------------
def v_time():
    h = R.randint(1, 12)
    m = R.choice([0, 0, 0, 0, 0, 30, 30, 30, 15, 45])
    suf = R.choice(["AM", "PM", "PM", "PM", ""])
    clean = f"{h}:{m:02d}" if m else f"{h}"
    if suf:
        clean += " " + suf
    if R.random() < 0.5:
        sp = f"{h}:{m:02d}" if m else f"{h}"
    else:
        sp = words(h) + ("" if not m else " " + words(m))
    if suf:
        sp += " " + R.choice([suf.lower(), suf[0].lower() + "." + suf[1].lower() + ".", suf])
    return clean, sp


def v_num():
    n = R.choice([10, 12, 15, 20, 25, 30, 40, 45, 50, 60, 80, 100, 120, 150, 200, 250, 300, 500, 800, 1000,
                  1200, 1500, 2000, 5000])
    clean = f"{n:,}" if n >= 10000 else str(n)
    return clean, (words(n) if R.random() < 0.35 else str(n))


def v_pct():
    n = R.choice([5, 10, 12, 15, 20, 25, 30, 40, 50])
    return f"{n}%", R.choice([f"{n} percent", f"{words(n)} percent", f"{n}%", f"{n} per cent"])


def spoken_address(s: str) -> str:
    return s.replace("@", " at ").replace(".", " dot ").replace("/", " slash ")


def v_email(name=None):
    name = (name or R.choice(B.NAMES)).lower()
    e = R.choice([name, name + str(R.randint(1, 99)), name + "." + R.choice(["k", "r", "work", "s"])]) + "@" + R.choice(B.DOMAINS)
    return e, (e if R.random() < 0.3 else spoken_address(e))


def v_site():
    s = R.choice(B.SITES)
    return s, (s if R.random() < 0.3 else spoken_address(s))


class Slots:
    """Fills {slots} consistently in one sample (name2 differs from name...)."""

    def __init__(self):
        self.vals: dict[str, tuple[str, str]] = {}

    def get(self, key: str) -> tuple[str, str]:
        if key in self.vals:
            return self.vals[key]
        base = key.rstrip("0123456789")
        used = {v[0] for k, v in self.vals.items() if k.rstrip("0123456789") == base}
        for _ in range(20):
            if base == "name":
                v = R.choice(B.NAMES); v = (v, v)
            elif base == "day":
                v = R.choice(B.WEEKDAYS); v = (v, v)
            elif base == "place":
                v = R.choice(B.PLACES); v = (v, v)
            elif base == "time":
                v = v_time()
            elif base == "num":
                v = v_num()
            elif base == "pct":
                v = v_pct()
            elif base == "email":
                v = v_email()
            elif base == "site":
                v = v_site()
            elif base == "me":
                v = R.choice(B.ME); v = (v, v)
            else:
                raise KeyError(key)
            if v[0] not in used:
                break
        self.vals[key] = v
        return v

    def other(self, key: str) -> str:
        """A different, wrong value of the same kind (spoken), for self-corrections."""
        base = key.rstrip("0123456789")
        cur = self.get(key)[0]
        for _ in range(20):
            v = Slots().get(base)
            if v[0] != cur:
                return v[1]
        return v[1]


SLOT = re.compile(r"\{(\w+)\}")
CORRECTABLE = ("name", "day", "time", "num", "place", "pct")


def fill(template: str, slots: Slots, correct: bool = False) -> tuple[str, str, bool]:
    """-> (written, spoken, whether a self-correction was spoken)."""
    keys = SLOT.findall(template)
    fix = None
    if correct:
        cands = [k for k in keys if k.rstrip("0123456789") in CORRECTABLE]
        if cands:
            fix = R.choice(cands)
    clean = SLOT.sub(lambda m: slots.get(m.group(1))[0], template)
    done = False

    def sp(m):
        nonlocal done
        k = m.group(1)
        val = slots.get(k)[1]
        if k == fix and not done:
            done = True
            return slots.other(k) + R.choice(B.CORRECTIONS) + val
        return val
    spoken = SLOT.sub(sp, template)
    return clean, spoken, done


# -- turning clean text into speech ------------------------------------------
COMMON = set("""the a an we it this that can could should would will is are was were do does did let's let my our
you your they there here so and but then also maybe if when what how why where who which please just
""".split())
TECH_LOWER = {t.lower(): t for t in B.TECH}


def lower_first(s: str) -> str:
    w = s.split(" ", 1)[0]
    if w.lower().strip(",.?!") in COMMON and not w.startswith("I'") and w != "I":
        return s[0].lower() + s[1:]
    return s


def upper_first(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def strip_end(s: str) -> str:
    return s.rstrip(" .")


_PUNCT_END = re.compile(r"[.,?!;:]+$")


def realize(sents: list[str], style: str | None = None, messy: float = 1.0) -> str:
    """Spoken sentences (written punctuation) -> what speech recognition gives."""
    style = style or ("raw" if R.random() < 0.3 else "whisper")
    out = []
    for i, s in enumerate(sents):
        toks = s.split()
        if not toks:
            continue
        # Repeats and false starts: "we could we could meet"
        if R.random() < 0.16 * messy and len(toks) > 3:
            j = R.randint(0, len(toks) - 2)
            k = R.randint(1, min(3, len(toks) - j))
            chunk = [_PUNCT_END.sub("", t) for t in toks[j:j + k]]
            mid = R.choice(["", "", "uh", "um"]) if style == "raw" else R.choice(["", "", "uh,", "um,"])
            toks = toks[:j] + chunk + ([mid] if mid else []) + toks[j:]
            toks = [t for t in toks if t]
        # Fillers inside
        if R.random() < 0.25 * messy and len(toks) > 4:
            j = R.randint(1, len(toks) - 1)
            f = R.choice(["um", "uh", "like", "you know", "I mean", "uh", "um", "kind of like"])
            if style == "whisper":
                toks[j - 1] = _PUNCT_END.sub("", toks[j - 1]) + ","
                f += ","
            toks.insert(j, f)
        # Filler at the start
        if R.random() < (0.4 if i == 0 else 0.18) * messy:
            f = R.choice(B.START_FILLERS)
            if style == "whisper":
                toks[0] = lower_first(toks[0])
                f = upper_first(f) + ","
            toks.insert(0, f)
        s = " ".join(toks)
        # Tech words lose their casing sometimes ("github", "Javascript")
        if R.random() < 0.3:
            for low, good in TECH_LOWER.items():
                if good in s and R.random() < 0.5:
                    s = s.replace(good, R.choice([low, good.capitalize()]))
        out.append(s)
    if style == "raw":
        text = " ".join(out)
        toks = [_PUNCT_END.sub("", t) for t in text.split()]
        text = " ".join(t for t in toks if t)
        text = text.replace(",", "")
        if R.random() < 0.7:
            text = text.lower()
        return text
    # Whisper style: mostly punctuated, with typical mistakes.
    res = ""
    for i, s in enumerate(out):
        if R.random() < 0.45:
            s = s.replace("?", ".")
        if R.random() < 0.4:
            s = s.replace(",", "")
        if i and R.random() < 0.25 and res.endswith("."):
            res = res[:-1] + R.choice([",", ""]) + " " + lower_first(s)
        else:
            res += (" " if res else "") + s
    if R.random() < 0.25:
        res = res.rstrip(".")
    return res


PROPER = set(B.NAMES) | set(B.TECH) | {t for t, _ in B.VOCAB} | set(B.WEEKDAYS) | {"I", "I'm", "I've", "I'll", "I'd"}


def spoken_of(clean: str) -> str:
    """Spoken version of a clean bullet or step (no end punctuation, lead lowercase)."""
    s = strip_end(clean)
    w = s.split(" ", 1)[0].strip(",")
    return s if w in PROPER or w.isupper() else s[:1].lower() + s[1:]


# -- sample makers -------------------------------------------------------------
def pick_topic(mode: str) -> str:
    if mode == "casual":
        return R.choice(["plans", "personal", "personal", "shopping", "home", "college", "health"])
    if mode == "formal":
        return R.choice(["work", "work", "business", "business", "college"])
    if mode == "technical":
        return "tech"
    return R.choice(list(B.TOPICS))


def sentences(topic: str, n: int, slots: Slots, questions: bool = True) -> list[tuple[str, str]]:
    pool = [s for s in B.TOPICS[topic] if questions or not s.endswith("?")]
    picked = R.sample(pool, min(n, len(pool)))
    res = []
    corrected = False
    for t in picked:
        c, s, done = fill(t, slots, correct=(not corrected and R.random() < 0.28))
        corrected = corrected or done
        res.append((c, s))
    return res


def maybe_scratch(pairs: list[tuple[str, str]], topic: str, slots: Slots) -> list[str]:
    """Spoken list; sometimes a sentence is said then cancelled with 'scratch that'."""
    spoken = [s for _, s in pairs]
    if R.random() < 0.07 and pairs:
        extra = fill(R.choice([s for s in B.TOPICS[topic] if not s.endswith("?")]), slots)[1]
        at = R.randint(0, len(spoken))
        cancel = R.choice(["scratch that", "delete that", "never mind that", "forget that last part"])
        spoken.insert(at, strip_end(extra) + ", " + cancel + ".")
    return spoken


def casual_end(text: str) -> str:
    """Chat style: one short statement has no full stop."""
    if text.count(". ") == 0 and text.endswith(".") and "\n" not in text and len(text.split()) <= 14:
        return text[:-1]
    return text


def gonna(spoken: str) -> str:
    def rep(m):
        nxt = m.group(2)
        if nxt.lower() in {"the", "a", "an", "my", "his", "her", "your", "our", "their", "this", "that"}:
            return m.group(0)
        return m.group(1) + "gonna " + nxt
    s = re.sub(r"\b(I'm |we're |We're |you're |I am )going to (\w+)", rep, spoken)
    return re.sub(r"\bwant to\b", "wanna", s) if R.random() < 0.5 else s


def make_message(mode: str) -> dict:
    slots = Slots()
    topic = pick_topic(mode)
    n = R.choices([1, 2, 3, 4], [4, 4, 3, 1])[0]
    pairs = sentences(topic, n, slots)
    clean = " ".join(c for c, _ in pairs)
    spoken_sents = maybe_scratch(pairs, topic, slots)
    if mode == "formal":
        body = clean
        spoken = spoken_sents
        if R.random() < 0.5:
            g = R.choice(B.GREETINGS)
            gs, gc = g[0].format(name=slots.get("name9")[0]), g[1].format(name=slots.get("name9")[0])
            body = gc + "\n\n" + body
            spoken = [gs] + spoken
        if R.random() < 0.5:
            so = R.choice(B.SIGNOFFS)
            me = slots.get("me")[0]
            body = body + "\n\n" + so[1].format(me=me)
            spoken = spoken + [so[0].format(me=me)]
        spoken = [gonna(s) if R.random() < 0.5 else s for s in spoken]
        return dict(mode=mode, input=realize(spoken), output=body)
    if mode == "casual":
        clean = casual_end(clean)
    if mode == "notes":
        clean = "\n".join("- " + strip_end(c) for c, _ in pairs)
    return dict(mode=mode, input=realize(spoken_sents), output=clean)


QUESTIONS = [
    "What's the capital of France?", "How do I make a good cup of coffee?", "What's the difference between RAM and storage?",
    "Can you explain what a closure is in JavaScript, with a simple example?", "Why is the sky blue?",
    "How many days are left until {day}?", "What's a good name for a bakery?", "Is it going to rain tomorrow?",
    "What should I gift my mom for her birthday?", "How do I center a div in CSS?", "Who won the match yesterday?",
    "Can you check if the train to {place} is on time?", "What does this error mean?", "How long does it take to learn Python?",
    "Should I take the job offer or wait for the other one?", "Do you know any good restaurants in {place}?",
    "What time is it in London right now?", "How do I convert a PDF to Word?", "Can you recommend a good book on habits?",
    "What's the best way to learn system design?", "Is it safe to eat raw eggs?", "Why does my phone get hot when charging?",
    "How do I reset my router?", "What are the side effects of this medicine?", "Can you summarize this for me?",
    "Which is better for a beginner, React or Vue?", "How much does a trip to {place} cost?",
]


def make_question(mode: str) -> dict:
    slots = Slots()
    if R.random() < 0.5:
        t = R.choice(QUESTIONS)
    else:
        t = R.choice([s for s in B.TOPICS[pick_topic(mode)] if s.endswith("?")])
    c, s, _ = fill(t, slots, correct=R.random() < 0.15)
    return dict(mode=mode, input=realize([s]), output=c)


def items_text(items, numbered: bool, period: bool) -> str:
    out = []
    for i, it in enumerate(items, 1):
        t = upper_first(it)
        if period and not t.endswith((".", "?", "!")):
            t += "."
        out.append(f"{i}. {t}" if numbered else f"- {t}")
    return "\n".join(out)


COUNT_SPOKEN = [["first", "second", "third", "fourth", "fifth"], ["one", "two", "three", "four", "five"],
                ["number one", "number two", "number three", "number four", "number five"],
                ["firstly", "secondly", "thirdly", "fourthly", "fifthly"]]


def make_list(mode: str) -> dict:
    slots = Slots()
    lead_t, pool = R.choice(B.LISTS)
    k = 3 if "three" in lead_t else R.randint(3, min(5, len(pool)))
    idx = sorted(R.sample(range(len(pool)), k))
    items = [fill(pool[i], slots)[0] for i in idx]
    lead = fill(lead_t, slots)[0]
    if R.random() < 0.1 and "three" not in lead_t:      # two things stay in one sentence
        items = items[:2]
        out = f"{lead} {items[0]} and {items[1]}."
        return dict(mode=mode, input=realize([f"{lead} {items[0]} and {items[1]}"]), output=out)
    counted = R.random() < 0.35
    if counted:
        cw = R.choice(COUNT_SPOKEN)
        sp = lead + " " + " ".join(f"{cw[i]} {it}" for i, it in enumerate(items))
    else:
        joins = [R.choice([", ", " ", ", "]) for _ in items[:-1]]
        last = R.choice([" and ", " and also ", ", and ", " and then "])
        sp = lead + " " + "".join(it + j for it, j in zip(items[:-2], joins)) + items[-2] + last + items[-1]
    out = lead + ":\n" + items_text(items, counted, period=False)
    return dict(mode=mode, input=realize([sp + "."]), output=out)


POINT_JOIN = [" also ", " and also ", " another thing is ", " plus ", " and the next thing is ", " and ", " oh and "]


def make_points(mode: str) -> dict:
    sp_lead, lead, pool = R.choice(B.POINTS)
    k = R.randint(3, len(pool))
    pts = pool[:k] if R.random() < 0.5 else R.sample(pool, k)
    counted = R.random() < 0.3
    sents = []
    for i, p in enumerate(pts):
        s = strip_end(p)
        if counted:
            s = R.choice(COUNT_SPOKEN[:3])[i] + " " + lower_first(s)
        elif i:
            j = R.choice(POINT_JOIN)
            if i == 2 and R.random() < 0.4:
                j = " and the third thing is "
            s = j.strip() + " " + lower_first(s)
        sents.append(s + ".")
    sents[0] = sp_lead + " " + lower_first(sents[0])
    notes = mode == "notes"
    out = lead + ":\n" + items_text([strip_end(p) for p in pts] if notes else pts, counted, period=not notes)
    return dict(mode=mode, input=realize(sents), output=out)


STEP_JOIN = ["then", "and then", "and after that", "after that", "once that's done", "next", "then"]


def make_steps(mode: str) -> dict:
    slots = Slots()
    bg, steps = R.choice(B.STEPS)
    k = R.randint(3, len(steps))
    steps = [fill(s, slots)[0] for s in steps[:k]]
    bgc = [fill(b, slots, correct=R.random() < 0.2) for b in bg]
    sents = [sp for _, sp, _ in bgc]
    parts = []
    for i, st in enumerate(steps):
        s = spoken_of(st)
        if i == 0:
            s = R.choice(["", "first ", "can you ", "so first ", "just ", "please "]) + s
        else:
            s = R.choice(STEP_JOIN) + " " + s
            if i == len(steps) - 1 and R.random() < 0.4:
                s = R.choice(["and finally ", "and at the end ", "and lastly "]) + spoken_of(st)
        parts.append(s)
    if R.random() < 0.5:
        sents.append(" ".join(parts) + ".")
    else:
        sents += [upper_first(p) + "." for p in parts]
    bg_clean = " ".join(c for c, _, _ in bgc)
    out = (bg_clean + "\n\n" if bg_clean else "") + items_text(steps, True, period=True)
    return dict(mode=mode, input=realize(sents), output=out)


TRANSITIONS = ["anyway", "okay and", "oh and", "and one more thing", "another thing", "so yeah and", "and um"]


def make_paragraphs(mode: str) -> dict:
    slots = Slots()
    topics = R.sample(list(B.TOPICS), R.randint(2, 3))
    groups, spoken = [], []
    for t in topics:
        pairs = sentences(t, R.randint(2, 3), slots, questions=R.random() < 0.5)
        groups.append(" ".join(c for c, _ in pairs))
        ss = [s for _, s in pairs]
        if spoken and R.random() < 0.6:
            ss[0] = R.choice(TRANSITIONS) + " " + lower_first(ss[0])
        spoken += ss
    return dict(mode=mode, input=realize(spoken), output="\n\n".join(groups))


def make_prompt(mode: str = "prompt") -> dict:
    slots = Slots()
    sc = R.choice(B.PROMPTS)
    goal = fill(sc["goal"], slots)[0]
    ctx = [fill(c, slots, correct=R.random() < 0.15) for c in sc["context"] if R.random() < 0.85]
    steps = [fill(s, slots)[0] for s in sc["steps"]] if R.random() < 0.85 else []
    req = [fill(s, slots)[0] for s in sc["req"] if R.random() < 0.75]
    con = [fill(s, slots)[0] for s in sc["con"] if R.random() < 0.75]
    if len(steps) < 2:
        steps = []
    is_q = goal.endswith("?")
    if not is_q:
        pre_s, pre_c = R.choice(B.GOAL_PREFIX)
        g = strip_end(goal)
        if pre_c in ("Can you", "Could you"):
            goal = f"{pre_c} {lower_first(g[0].lower() + g[1:])}?"
        elif pre_c:
            goal = f"{pre_c} {g[0].lower() + g[1:]}."
        spoken_goal = (pre_s + " " if pre_s else "") + spoken_of(sc_goal_fix(g, pre_c))
    else:
        spoken_goal = strip_end(goal).replace("?", "")
    goal = goal.replace("?.", "?")
    # spoken
    hedge = lambda s: s + (" " + R.choice(B.PROMPT_HEDGES) if R.random() < 0.25 else "")
    ctx_s = []
    for i, (c, s, _) in enumerate(ctx):
        lead = "" if i == 0 else R.choice(["and ", "also ", "basically ", "", "and like "])
        ctx_s.append(hedge(lead + lower_first(strip_end(s))) + ".")
    step_s = []
    for i, st in enumerate(steps):
        lead = R.choice(["", "first ", "so first "]) if i == 0 else R.choice(STEP_JOIN) + " "
        step_s.append(lead + spoken_of(st))
    req_s = [R.choice(["and make sure ", "and ", "also ", ""]) + lower_first(strip_end(r)) + "." for r in req]
    con_s = [R.choice(["but ", "and ", "", "oh and "]) + lower_first(strip_end(c)) + "." for c in con]
    sents = []
    if ctx_s and R.random() < 0.55:
        sents += ctx_s + [hedge(spoken_goal) + "."]
    else:
        sents += [hedge(spoken_goal) + "."] + ctx_s
    if step_s:
        sents += [" ".join(step_s) + "."] if R.random() < 0.6 else [upper_first(s) + "." for s in step_s]
    extra = req_s + con_s
    R.shuffle(extra)
    sents += extra
    # written
    pieces = len(ctx) + len(req) + len(con)
    if not steps and pieces <= 1:
        tail = [c for c, _, _ in ctx] + req + con
        out = " ".join([goal] + tail)
    else:
        blocks = [goal]
        if ctx:
            blocks.append("Context:\n" + items_text([c for c, _, _ in ctx], False, True))
        if steps:
            blocks.append("Steps:\n" + items_text(steps, True, True))
        if req:
            blocks.append("Requirements:\n" + items_text(req, False, True))
        if con:
            blocks.append("Constraints:\n" + items_text(con, False, True))
        out = "\n\n".join(blocks)
    return dict(mode=mode, input=realize(sents), output=out)


def sc_goal_fix(g: str, pre: str) -> str:
    return g[0].lower() + g[1:] if pre else g


CODE_T = ["Open {c} and change the port to {num}.", "Run {c} before you push.", "The error is coming from {c}.",
          "Can you add a new script to {c}?", "I think {c} is missing a dependency.", "Delete {c} and run {c2} again.",
          "Check {c}, the API key is in there.", "After that, run {c2}.", "Move the database settings into {c}.",
          "Why does {c2} fail on my machine?", "Add a comment at the top of {c}.", "Don't commit {c}."]


def make_code(mode: str) -> dict:
    t = R.choice(CODE_T)
    a, b = R.sample(B.CODE, 2)
    slots = Slots()
    t = fill(t.replace("{c}", "\0").replace("{c2}", "\1"), slots)
    tick = mode in ("technical", "prompt")
    w = lambda x: f"`{x}`" if tick else x
    clean = t[0].replace("\0", w(a[1])).replace("\1", w(b[1]))
    sp = t[1].replace("\0", a[0] if R.random() < 0.7 else a[1]).replace("\1", b[0] if R.random() < 0.7 else b[1])
    return dict(mode=mode, input=realize([sp]), output=clean)


VOCAB_T = ["Can you send the {v} report to {name}?", "I pushed the {v} changes this morning.", "The {v} meeting is on {day}.",
           "I'm working on {v} today.", "Did you check {v}?", "{v} is down again.", "Add {v} to the list.",
           "I spoke to the {v} team yesterday.", "The {v} demo went well.", "Let's set up {v} on the new server.",
           "I'll call {v} after lunch.", "We need to update {v} before {day}."]


def make_vocab(mode: str) -> dict:
    term, wrong = R.choice(B.VOCAB)
    slots = Slots()
    c, s, _ = fill(R.choice(VOCAB_T).replace("{v}", "\0"), slots)
    use_vocab = R.random() < 0.8
    said = R.choice(wrong) if use_vocab else term
    clean = c.replace("\0", term)
    spoken = s.replace("\0", said)
    others = [t for t, _ in R.sample(B.VOCAB, R.randint(2, 9)) if t != term]
    vocab = others + [term] if use_vocab else others[:R.randint(0, 4)]
    R.shuffle(vocab)
    if mode == "casual":
        clean = casual_end(clean)
    return dict(mode=mode, input=realize([spoken]), output=clean, vocab=vocab)


MAKERS = [  # (maker, weight, modes)
    (make_message, 30, ["neutral", "neutral", "casual", "casual", "formal", "technical", "notes"]),
    (make_question, 9, ["neutral", "casual", "prompt", "technical", "formal"]),
    (make_list, 11, ["neutral", "casual", "notes", "formal", "neutral"]),
    (make_points, 7, ["neutral", "formal", "casual", "notes"]),
    (make_steps, 8, ["neutral", "technical", "casual", "formal"]),
    (make_paragraphs, 7, ["neutral", "formal", "neutral"]),
    (make_prompt, 16, ["prompt"]),
    (make_code, 5, ["technical", "technical", "prompt", "neutral"]),
    (make_vocab, 7, ["neutral", "casual", "technical", "formal", "prompt"]),
]


def one() -> dict:
    maker, _, modes = R.choices(MAKERS, [m[1] for m in MAKERS])[0]
    mode = R.choice(modes)
    d = maker(mode)
    d["output"] = d["output"].replace("  ", " ").strip()
    d["input"] = re.sub(r"\s+", " ", d["input"]).strip()
    d.setdefault("vocab", [])
    if not d["vocab"] and R.random() < 0.2:          # unrelated dictionary words must not leak in
        d["vocab"] = [t for t, _ in R.sample(B.VOCAB, R.randint(1, 8))]
    d["app"] = R.choice(B.APPS[d["mode"]])
    return d


def identity(d: dict) -> dict | None:
    """Already-clean speech stays as it is."""
    if "\n" in d["output"] or d["mode"] in ("notes", "prompt"):
        return None
    return dict(d, input=d["output"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=14000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", default="training/data/synth_train.jsonl")
    ap.add_argument("--exclude", default="", help="jsonl whose inputs must not repeat")
    a = ap.parse_args()
    R.seed(a.seed)
    seen = set()
    if a.exclude and Path(a.exclude).exists():
        seen = {json.loads(l)["input"] for l in open(a.exclude, encoding="utf-8")}
    rows = []
    while len(rows) < a.n:
        d = one()
        if R.random() < 0.05:
            d = identity(d) or d
        if d["input"] in seen or len(d["input"]) < 3:
            continue
        seen.add(d["input"])
        rows.append(d)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        for d in rows:
            f.write(json.dumps({k: d[k] for k in ("mode", "app", "vocab", "input", "output")}, ensure_ascii=False) + "\n")
    print("wrote", len(rows), "to", a.out)


if __name__ == "__main__":
    main()
