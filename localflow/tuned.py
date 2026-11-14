"""Prompt format for LocalFlow's own fine-tuned cleanup model.

The model was trained on exactly this format (see training/), so it needs
no long instructions or examples: a three-line header and the transcript.
That keeps every request tiny, which is most of what makes it fast.
"""
from __future__ import annotations

import re

END = "<|im_end|>"
MODES = ("neutral", "casual", "formal", "technical", "notes", "prompt")

# Files of our own model start with this name.
TUNED_PREFIX = "localflow-"


def is_tuned(model_file: str) -> bool:
    return (model_file or "").lower().startswith(TUNED_PREFIX)


def header(mode: str, app: str, vocab: list[str] | None) -> str:
    lines = [f"mode: {mode if mode in MODES else 'neutral'}", f"app: {(app or 'unknown').lower()}"]
    if vocab:
        lines.append("vocab: " + ", ".join(v.strip() for v in vocab[:40] if v.strip()))
    return "\n".join(lines)


def build_prompt(mode: str, app: str, vocab: list[str] | None, text: str) -> str:
    return (f"<|im_start|>system\n{header(mode, app, vocab)}{END}\n"
            f"<|im_start|>user\n{text.strip()}{END}\n<|im_start|>assistant\n")


# Added by code (not the model) in expert prompt mode: good prompting habits
# that are the same for every request, so the model never has to invent them.
EXPERT_FOOTER = ("Before you start:\n"
                 "- If anything is unclear or missing, ask me first instead of guessing.\n"
                 "- Keep changes focused on what I asked for.\n\n"
                 "When you finish:\n"
                 "- Check that it works (run it or test it where you can).\n"
                 "- Tell me briefly what you changed and anything I still need to do.")

_SECTION = re.compile(r"(?m)^(Context|Steps|Requirements|Constraints):$")


def expert(prompt: str) -> str:
    """Prompt mode, expert: add the fixed footer to a structured request.
    Short replies ("thanks, go ahead") stay as they are."""
    if not _SECTION.search(prompt) and len(prompt.split()) < 25:
        return prompt
    return prompt.rstrip() + "\n\n" + EXPERT_FOOTER
