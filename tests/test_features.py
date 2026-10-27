"""Tests for 1.1 features: per-app styles and learning from corrections."""
from localflow.config import DEFAULTS
from localflow.context import FocusContext
from localflow.learning import find_learnable
from localflow.polish import style_for

CFG = dict(DEFAULTS)


def test_style_by_app_kind_and_override():
    assert style_for(FocusContext(exe="slack.exe", title="Slack"), CFG) == "casual"
    assert style_for(FocusContext(exe="outlook.exe", title="Inbox"), CFG) == "formal"
    assert style_for(FocusContext(exe="code.exe", title="app.py"), CFG) == "technical"
    assert style_for(FocusContext(exe="windowsterminal.exe", title="pwsh"), CFG) == "verbatim"
    assert style_for(FocusContext(exe="notepad.exe", title="x"), CFG) == "neutral"
    cfg = dict(CFG, app_style_overrides={"Slack.exe": "formal"})
    assert style_for(FocusContext(exe="slack.exe", title="Slack"), cfg) == "formal"


def test_update_version_compare():
    from localflow.updater import is_newer
    assert is_newer("1.1.0", "1.0.0")
    assert is_newer("v1.10.0", "1.9.3")
    assert not is_newer("1.0.0", "1.0.0")
    assert not is_newer("0.9.9", "1.0.0")


def test_srt_and_paragraphs():
    from localflow.files import Segment, srt_time, to_paragraphs, to_srt
    assert srt_time(3725.5) == "01:02:05,500"
    segs = [Segment(0.0, 1.5, "Hello there."), Segment(1.6, 3.0, "How are you?"), Segment(7.0, 8.0, "New topic.")]
    assert to_paragraphs(segs) == "Hello there. How are you?\n\nNew topic.\n"
    assert to_srt(segs[:1]) == "1\n00:00:00,000 --> 00:00:01,500\nHello there.\n"


def test_meeting_echo_removal():
    from localflow.files import Segment
    from localflow.meeting import _echo
    others = Segment(10.0, 13.0, "Can everyone see my screen?", "Others")
    assert _echo(Segment(10.2, 13.1, "can everyone see my screen", "You"), others)
    assert not _echo(Segment(14.0, 15.0, "Yes I can see it", "You"), others)


def test_learns_misheard_terms():
    pairs = find_learnable("I deployed it on Qualify with Supabase.",
                           "I deployed it on Coolify with Supabase.", set())
    assert pairs == [("Qualify", "Coolify")]
    assert find_learnable("Regards Path.", "Regards Parth.", set()) == [("Path", "Parth")]


def test_ignores_rewording_and_known_words():
    # A different word, not a mishearing.
    assert find_learnable("That was a great talk.", "That was an excellent talk.", set()) == []
    # Ordinary words are never learned.
    assert find_learnable("Send it to them.", "Send it to then.", set()) == []
    # Already in the dictionary.
    assert find_learnable("Ask Path.", "Ask Parth.", {"Parth"}) == []
    # Typing extra text after the dictation isn't a correction.
    assert find_learnable("Hello there.", "Hello there. Kubernetes rocks.", set()) == []
