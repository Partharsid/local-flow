import pytest

from localflow.cleanup import output_is_faithful
from localflow.config import DEFAULTS
from localflow.context import FocusContext, extract_vocabulary
from localflow.hotkeys import combo_name, parse_combo
from localflow.polish import (apply_replacements, apply_snippets, apply_voice_commands, fit_to_context,
                              pre_clean, remove_fillers)

CFG = dict(DEFAULTS)


def ctx(before="", after="", exe="notepad.exe", title="Untitled - Notepad", source="textpattern", caret=True, words=()):
    return FocusContext(exe=exe, title=title, before=before, after=after, source=source,
                        caret_known=caret, words=list(words))


# --- fillers / commands / replacements ---------------------------------------
@pytest.mark.parametrize("raw,expected", [
    ("Um, I think so.", "I think so."),
    ("I think, uh, we should go.", "I think we should go."),
    ("So I think we should, uh, ship it.", "So I think we should ship it."),
    ("Uhm we should erm go", "We should go"),
    ("The drum is loud.", "The drum is loud."),      # 'um' inside a word stays
    ("Hmm. Okay.", "Okay."),
])
def test_remove_fillers(raw, expected):
    assert remove_fillers(raw) == expected


def test_voice_commands():
    assert apply_voice_commands("Hello. New line. how are you") == "Hello.\nHow are you"
    assert apply_voice_commands("Para one. New paragraph. Para two.") == "Para one.\n\nPara two."


def test_replacements_case_insensitive_whole_words():
    r = {"party sid": "Partharsid", "k three s": "k3s"}
    assert apply_replacements("Ask Party Sid about k three s.", r) == "Ask Partharsid about k3s."
    assert apply_replacements("partysid", r) == "partysid"


def test_snippets_whole_and_inline():
    s = {"my email": "me@example.com", "sign off": "Best,\nParth"}
    assert apply_snippets("My email.", s) == "me@example.com"
    assert apply_snippets("Send it to my email please", s) == "Send it to me@example.com please"
    assert apply_snippets("Thanks. Sign off", s) == "Thanks. Best,\nParth"


def test_pre_clean_pipeline():
    cfg = dict(CFG, replacements={"party sid": "Partharsid"})
    # Capitalisation of the first word is fit_to_context's job, not pre_clean's.
    assert pre_clean("um  hi party sid ,  new line thanks", cfg) == "hi Partharsid\nThanks"


# --- fitting into existing text ----------------------------------------------
def test_empty_field_capitalises_and_keeps_period():
    assert fit_to_context("hello there.", ctx(), CFG) == "Hello there."


def test_mid_sentence_lowercases_and_adds_space():
    assert fit_to_context("The meeting is at noon.", ctx(before="I told her that"), CFG) == " the meeting is at noon."


def test_after_sentence_end_capitalises_with_space():
    assert fit_to_context("see you soon.", ctx(before="Thanks for coming."), CFG) == " See you soon."


def test_after_newline_no_leading_space():
    assert fit_to_context("next item", ctx(before="List:\n"), CFG) == "Next item"


def test_after_space_no_double_space():
    assert fit_to_context("There", ctx(before="Hello "), CFG) == "there"


def test_proper_nouns_keep_capitals_mid_sentence():
    # Not in the common-word list => probably a name: keep the capital.
    assert fit_to_context("Google is down.", ctx(before="I think that"), CFG) == " Google is down."
    assert fit_to_context("Monday works.", ctx(before="maybe"), CFG) == " Monday works."
    assert fit_to_context("India won.", ctx(before="and"), CFG) == " India won."


def test_newline_edges():
    # A dictation starting with a line break gets no stray space before it.
    assert fit_to_context("\nNext point", ctx(before="Done."), CFG) == "\nNext point"
    # A trailing line break survives inserting before existing text.
    assert fit_to_context("Intro\n", ctx(before="", after="Body"), CFG) == "Intro\n"


def test_insert_before_existing_word_drops_period_adds_space():
    out = fit_to_context("Really quickly.", ctx(before="I ", after="went home."), CFG)
    assert out == "really quickly "


def test_keeps_question_mark_mid_sentence():
    assert fit_to_context("Why not?", ctx(before="He said ", after="and left"), CFG) == "why not? "


def test_before_punctuation_strips_period():
    assert fit_to_context("the end.", ctx(before="This is ", after="."), CFG) == "the end"


def test_keeps_I_and_proper_nouns_and_acronyms():
    assert fit_to_context("I agree.", ctx(before="Well,"), CFG) == " I agree."
    assert fit_to_context("Parth agrees.", ctx(before="and", words=["Parth"]), CFG) == " Parth agrees."
    assert fit_to_context("NASA called.", ctx(before="and"), CFG) == " NASA called."
    assert fit_to_context("iPhone sales.", ctx(before="and"), CFG) == " iPhone sales."


def test_open_paren_and_quote_no_space():
    assert fit_to_context("See below.", ctx(before="Note ("), CFG) == "see below."
    assert fit_to_context("Hi there.", ctx(before='He said "'), CFG) == "hi there."


def test_after_closing_quote_adds_space():
    assert fit_to_context("Then left.", ctx(before='He said "hi."'), CFG) == " Then left."


def test_chat_app_drops_single_trailing_period():
    c = ctx(exe="slack.exe", title="Slack")
    assert fit_to_context("Sounds good.", c, CFG) == "Sounds good"
    assert fit_to_context("Sounds good. See you.", c, CFG) == "Sounds good. See you."


def test_browser_whatsapp_title_is_chat():
    c = ctx(exe="chrome.exe", title="(3) WhatsApp - Google Chrome")
    assert fit_to_context("On my way.", c, CFG) == "On my way"


def test_terminal_single_line_no_period():
    c = ctx(exe="windowsterminal.exe", title="PowerShell")
    assert fit_to_context("git status.", c, CFG) == "Git status"


def test_no_context_is_plain():
    c = ctx(source="none", caret=False)
    assert fit_to_context("hello world.", c, CFG) == "Hello world."


def test_value_pattern_assumes_end():
    c = ctx(before="Dear team,", source="value", caret=False)
    assert fit_to_context("Please review.", c, CFG) == " please review."


# --- llm guard -------------------------------------------------------------------
def test_faithful_accepts_cleanup():
    raw = "um so I was thinking we could uh meet at like 5 no wait 6 tomorrow"
    assert output_is_faithful(raw, "I was thinking we could meet at 6 tomorrow.")


def test_faithful_rejects_answer():
    assert not output_is_faithful("what's the capital of France",
                                  "The capital of France is Paris, which is also its largest city.")


def test_faithful_rejects_short_answer():
    # Real failure from qwen2.5:1.5b: only two new words, but it answered.
    assert not output_is_faithful("What's the capital of France", "The capital of France is Paris.")
    assert output_is_faithful("What's the capital of France", "What's the capital of France?")
    # Commands that start like questions but are dictated as statements still pass.
    assert output_is_faithful("so I think we should go", "I think we should go.")


def test_faithful_allows_number_words():
    assert output_is_faithful("meet at five p.m. no wait at six p.m. tomorrow", "Let's meet at 6 p.m. tomorrow.")


def test_faithful_rejects_padding():
    assert not output_is_faithful("send the report", "Sure! Here is a polished version: Please send the report as soon as possible, thank you.")


def test_faithful_accepts_list():
    raw = "the things we need are first more testing second better docs third a new logo"
    assert output_is_faithful(raw, "The things we need are:\n1. More testing\n2. Better docs\n3. A new logo")


# --- hotkeys / vocab ---------------------------------------------------------------
def test_parse_combo():
    assert parse_combo("right ctrl") == [{0xA3}]
    combo = parse_combo("ctrl+alt+space")
    assert 0xA2 in combo[0] and 0xA3 in combo[0] and combo[2] == {0x20}
    with pytest.raises(ValueError):
        parse_combo("ctrl+banana")


def test_combo_name_roundtrip():
    assert combo_name([0xA3]) == "right ctrl"
    assert combo_name([0xA2, 0xA4, 0x20]) == "ctrl+alt+space"
    assert combo_name([0xA2, 0x5B]) == "ctrl+win"
    for spec in ("right ctrl", "ctrl+alt+space", "alt+shift+z", "f8"):
        parse_combo(spec)


def test_dictionary_snaps_near_miss_names():
    from localflow.polish import fix_dictionary_spellings
    d = ["Parth", "Coolify", "Supabase"]
    assert fix_dictionary_spellings("Regards Path.", d) == "Regards Parth."
    assert fix_dictionary_spellings("Thanks, Path", d) == "Thanks, Parth"
    # Ordinary words and sentence starts are left alone.
    assert fix_dictionary_spellings("Set the file path here.", d) == "Set the file path here."
    assert fix_dictionary_spellings("Path is set. Path works.", d) == "Path is set. Path works."
    assert fix_dictionary_spellings("Ask Parth.", d) == "Ask Parth."


def test_multiline_block_keeps_first_capital():
    out = fit_to_context("Hi Rahul\nThanks for your help.", ctx(before="some earlier text"), CFG)
    assert out == " Hi Rahul\nThanks for your help."


def test_whisper_prompt_uses_only_user_words():
    from localflow.app import App
    assert App._whisper_prompt([]) == ""
    p = App._whisper_prompt(["Partharsid", "Supabase"])
    assert "Partharsid" in p and "Supabase" in p and len(p) < 400


def test_vocabulary_skips_ui_labels():
    # The Claude app's buttons poisoned Whisper's prompt once: never again.
    v = extract_vocabulary("Copy Fork Pin Read Just Environment Reorder Edit Connectors with Coolify")
    assert "Coolify" in v
    assert not {"Copy", "Fork", "Pin", "Read", "Environment", "Reorder", "Edit", "Connectors"} & set(v)


def test_extract_vocabulary():
    v = extract_vocabulary("We deployed it with Coolify and fetchUserData on my_server. Then NASA called.")
    assert "Coolify" in v and "fetchUserData" in v and "my_server" in v and "NASA" in v
    assert "Then" not in v and "We" not in v
