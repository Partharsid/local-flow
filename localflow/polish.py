"""Deterministic text shaping: works with or without the LLM.

pre_clean()  -> fillers, voice commands, replacements (before the LLM)
fit_to_context() -> spacing / casing / punctuation that matches the text
                    already around the cursor (after the LLM)
apply_snippets() -> whole-utterance and inline snippet expansion
"""
from __future__ import annotations

import re

from .context import FocusContext

FILLERS = re.compile(r"(?i)(?:^|(?<=[\s,.;!?]))(?:um+|uh+|uhm+|erm+|hmm+|mm+|ah+)(?=[\s,.;!?]|$)[,.]?\s*")

NO_SPACE_AFTER = set("([{\n\t/@#$‘“-_") | {""}
SENTENCE_END = set(".!?\n:")   # after these the next word is capitalised
KEEP_CAPS = {"I", "I'm", "I'll", "I've", "I'd", "OK", "TV", "AI", "API", "URL", "PDF"}

# Mid-sentence, Whisper's capitalised first word is lowercased only when it is
# a common word. Anything else (Google, Monday, India, Parth) is probably a
# proper noun and keeps its capital.
COMMON = set("""
a about above after again against all almost also although always am among an and another any anyone
anything anyway are around as ask at away back be because been before being below best better between
both but by call can can't cannot could couldn't did didn't do does doesn't doing don't done down during
each either else enough even ever every everyone everything few find first for from get gets getting give
go goes going gone good got great had hadn't has hasn't have haven't having he he's her here here's hers
herself him himself his how however i'd if in instead into is isn't it it's its itself just keep know last
least less let let's like look lot make many maybe me might mine more most much must my myself need
never new next no nobody none nor not nothing now of off often oh ok okay on once one only or other others
otherwise our ours ourselves out over own perhaps please probably put quite rather really right said same
say see seems she she's should shouldn't since so some someone something sometimes soon still such sure
take than thank thanks that that's the their theirs them themselves then there there's therefore these they
they're think this those though through thus to today together tomorrow too try under unless until up upon
us use very want was wasn't way we we'll we're we've well were weren't what what's whatever when where
whether which while who who's whoever whole whom whose why will with within without won't would wouldn't
yeah yes yesterday yet you you'd you'll you're you've your yours yourself
add check create delete fix make move open remove run send set show start stop update write
hi hey hello sorry great nice cool
""".split())


def _norm_spaces(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text.strip(" ")


def remove_fillers(text: str) -> str:
    # "should, uh, ship" -> "should ship": Whisper brackets fillers in commas.
    out = re.sub(r"(?i),\s*(?:um+|uh+|uhm+|erm+|hmm+|ah+)\s*,\s*", " ", text)
    out = FILLERS.sub("", out)
    out = re.sub(r"^\s*[,.]\s*", "", out)
    out = _norm_spaces(out)
    if out and text[:1].isupper() and out[:1].islower():
        out = out[0].upper() + out[1:]
    return out


def apply_voice_commands(text: str) -> str:
    # Absorb a comma before the command (Whisper's pause marker) but keep a
    # real sentence-ending period.
    text = re.sub(r"(?i),?\s*\bnew paragraph\b[,.!]?\s*", "\n\n", text)
    text = re.sub(r"(?i),?\s*\b(?:new line|next line)\b[,.!]?\s*", "\n", text)
    # Capitalise the first letter after a line break we just inserted.
    text = re.sub(r"\n([a-z])", lambda m: "\n" + m.group(1).upper(), text)
    return text.strip(" ")


def apply_replacements(text: str, replacements: dict[str, str]) -> str:
    for spoken, written in sorted(replacements.items(), key=lambda kv: -len(kv[0])):
        if not spoken.strip():
            continue
        pattern = r"(?i)(?<!\w)" + re.escape(spoken.strip()) + r"(?!\w)"
        text = re.sub(pattern, lambda _m, w=written: w, text)
    return text


def pre_clean(text: str, cfg: dict) -> str:
    text = _norm_spaces(text)
    if cfg.get("remove_fillers", True):
        text = remove_fillers(text)
    if cfg.get("voice_commands", True):
        text = apply_voice_commands(text)
    text = apply_replacements(text, cfg.get("replacements") or {})
    return text


def _edit_distance_le1(a: str, b: str) -> bool:
    """True if a and b differ by at most one insert, delete or substitution."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = j = diffs = 0
    while i < len(a) and j < len(b):
        if a[i] != b[j]:
            diffs += 1
            if diffs > 1:
                return False
            if len(a) == len(b):
                i += 1
            j += 1
        else:
            i += 1
            j += 1
    return diffs + (len(b) - j) + (len(a) - i) <= 1


def fix_dictionary_spellings(text: str, dictionary: list[str]) -> str:
    """Snap near-miss proper nouns to the user's dictionary ('Path' -> 'Parth').

    Only capitalised words that are not starting a sentence are considered
    (so the word is being used as a name), and only for dictionary entries of
    4+ letters, so ordinary lowercase words like 'path' are never touched."""
    entries = [w for w in dictionary if len(w) >= 4 and w.isalpha()]
    if not entries:
        return text

    def repl(m: re.Match) -> str:
        word = m.group(0)
        start = m.start()
        prefix = text[:start].rstrip(" \t")
        if not prefix or prefix[-1] in ".!?\n:":
            return word  # sentence start: capital letter says nothing
        for e in entries:
            if word != e and word.lower() != e.lower() and _edit_distance_le1(word.lower(), e.lower()):
                return e
        return word

    return re.sub(r"\b[A-Z][a-z]{3,}\b", repl, text)


def _key(s: str) -> str:
    return re.sub(r"[^\w\s]", "", s).strip().lower()


def apply_snippets(text: str, snippets: dict[str, str]) -> str:
    if not snippets:
        return text
    whole = _key(text)
    for trigger, expansion in snippets.items():
        if whole == _key(trigger):
            return expansion
    for trigger, expansion in sorted(snippets.items(), key=lambda kv: -len(kv[0])):
        if len(_key(trigger).split()) < 2:   # single words inline are too risky
            continue
        pattern = r"(?i)(?<!\w)" + re.escape(trigger.strip()) + r"(?!\w)[.]?"
        text = re.sub(pattern, lambda _m, e=expansion: e, text)
    return text


def _app_kind(ctx: FocusContext, cfg: dict) -> str:
    exe = ctx.exe.lower()
    if exe in {a.lower() for a in cfg.get("terminal_apps", [])}:
        return "terminal"
    if exe in {a.lower() for a in cfg.get("code_apps", [])}:
        return "code"
    if exe in {a.lower() for a in cfg.get("chat_apps", [])}:
        return "chat"
    if ctx.is_browser and any(k.lower() in ctx.title.lower() for k in cfg.get("chat_title_keywords", [])):
        return "chat"
    title = ctx.title.lower()
    if exe in ("outlook.exe", "olk.exe", "thunderbird.exe") or "gmail" in title or "outlook" in title:
        return "email"
    if exe in ("winword.exe", "notion.exe", "obsidian.exe", "onenote.exe") or "google docs" in title:
        return "document"
    return "general"


def app_kind(ctx: FocusContext, cfg: dict) -> str:
    return _app_kind(ctx, cfg)


def _first_word(text: str) -> str:
    m = re.match(r"[\w'’-]+", text)
    return m.group(0) if m else ""


def _quote_open(before_line: str) -> bool:
    return before_line.count('"') % 2 == 1


def fit_to_context(text: str, ctx: FocusContext, cfg: dict, proper_nouns: set[str] | None = None) -> str:
    if not text:
        return text
    proper = set(proper_nouns or ()) | set(ctx.words) | set(cfg.get("dictionary") or [])
    kind = _app_kind(ctx, cfg)
    before = ctx.before
    after = ctx.after
    # With a selection, the dictation replaces it: look at what surrounds it.
    stripped_before = before.rstrip(" \t")
    last_char = before[-1:] if before else ""
    last_sig = stripped_before[-1:] if stripped_before else ""
    have_context = ctx.source in ("textpattern", "value")

    # 1) Capitalisation of the first word.
    # Look through closing quotes/brackets: 'He said "hi."' ends a sentence.
    core = stripped_before.rstrip("\"'”’)]")
    core_last = core[-1:] if core else ""
    sentence_start = (not have_context) or (not stripped_before) or last_sig in SENTENCE_END \
        or (core_last in ".!?" and core != stripped_before)
    if stripped_before.endswith(("...", "…")):
        sentence_start = False  # trailing ellipsis usually continues the thought
    if have_context and last_sig == ":" and not before.endswith("\n"):
        sentence_start = False  # "Note: something" stays lower after a colon mid-line
    if "\n" in text.strip():
        sentence_start = True  # a multi-line block (a message, a list) starts fresh
    first = _first_word(text)
    if first:
        if sentence_start:
            text = text[0].upper() + text[1:]
        elif (first.lower().replace("’", "'") in COMMON and first not in KEEP_CAPS and first not in proper
              and not re.match(r"I['’]", first) and not any(c.isupper() for c in first[1:])):
            text = text[0].lower() + text[1:]

    # 2) Leading space (never in front of a line break we are inserting).
    if (have_context and before and last_char not in NO_SPACE_AFTER and not last_char.isspace()
            and not text.startswith("\n")):
        if not (last_char == '"' and _quote_open(before.split("\n")[-1])) and text[:1] not in ",.;:!?)'’":
            text = " " + text

    # 3) Trailing punctuation / space when inserting before existing text.
    after_first = after[:1]
    if (have_context and ctx.caret_known and after_first and not after_first.isspace()
            and not text.endswith("\n")):
        if after_first.isalnum():
            # Inserting into the middle of a sentence: no full stop, but keep a
            # deliberate "?" or "!".
            text = text.rstrip()
            if text.endswith(".") and not text.endswith(".."):
                text = text[:-1]
            text += " "
        elif after_first in ".,;:!?":
            text = text.rstrip(".,;:!?")

    # 4) App-specific endings.
    body = text.strip()
    single_sentence = len(re.findall(r"[.!?](?:\s|$)", body)) <= 1 and "\n" not in body
    if kind in ("chat", "terminal") and single_sentence and body.endswith(".") and not body.endswith(".."):
        text = text.rstrip()
        text = text[:-1] if text.endswith(".") else text
    if kind == "terminal":
        text = text.replace("\n", " ")
    return text
