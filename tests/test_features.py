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
