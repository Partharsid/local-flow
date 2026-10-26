"""Hotkey state machine, driven with simulated (vk, down) events."""
import queue
import time

from localflow.hotkeys import RAWKEYBOARD, HotkeyEvent, HotkeyManager, _hotkey_args, _normalise_vk, parse_combo

RCTRL, LCTRL, C_KEY, ESC, SPACE, LALT = 0xA3, 0xA2, 0x43, 0x1B, 0x20, 0xA4


def manager(hold="right ctrl", toggle="ctrl+alt+space", paste="alt+shift+z"):
    q = queue.Queue()
    m = HotkeyManager(q)
    m.configure(hold, toggle, paste)
    return m, q


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def test_hold_starts_and_stops():
    m, q = manager()
    m._handle(RCTRL, True)
    m._handle(RCTRL, True)  # auto-repeat is ignored
    time.sleep(0.05)
    m._handle(RCTRL, False)
    assert drain(q) == [HotkeyEvent.START_HOLD, HotkeyEvent.STOP]
    assert m.last_hold_ms >= 40


def test_shortcut_while_holding_cancels():
    m, q = manager()
    m._handle(RCTRL, True)
    m._handle(C_KEY, True)    # Right Ctrl + C = copy, not dictation
    m._handle(C_KEY, False)
    m._handle(RCTRL, False)
    assert drain(q) == [HotkeyEvent.START_HOLD, HotkeyEvent.CANCEL]


def test_esc_cancels_only_while_recording():
    m, q = manager()
    m._handle(ESC, True)
    m._handle(ESC, False)
    assert drain(q) == []
    m.recording = True
    m._handle(ESC, True)
    assert drain(q) == [HotkeyEvent.CANCEL]


def test_modifier_only_toggle_uses_raw_input():
    m, q = manager(toggle="ctrl+win")
    m._handle(LCTRL, True)
    m._handle(0x5B, True)
    assert HotkeyEvent.TOGGLE in drain(q)


def test_capture_reports_combo_and_swallows_logic():
    m, q = manager()
    got = []
    m.capture_next(got.append)
    for vk, down in ((LCTRL, True), (LALT, True), (SPACE, True), (SPACE, False), (LALT, False), (LCTRL, False)):
        m._handle(vk, down)
    assert got == ["ctrl+alt+space"]
    assert drain(q) == []        # capture never triggers dictation


def test_capture_expires():
    m, q = manager()
    m.capture_next(lambda s: None, timeout=0.0)
    time.sleep(0.01)
    m._handle(RCTRL, True)
    assert drain(q) == [HotkeyEvent.START_HOLD]


def test_registerhotkey_args():
    assert _hotkey_args(parse_combo("ctrl+alt+space")) == (0x4000 | 0x2 | 0x1, 0x20)
    assert _hotkey_args(parse_combo("ctrl+win")) is None        # modifier-only


def test_raw_vk_left_right_split():
    assert _normalise_vk(RAWKEYBOARD(MakeCode=0x1D, Flags=2, VKey=0x11)) == RCTRL   # E0 = right
    assert _normalise_vk(RAWKEYBOARD(MakeCode=0x1D, Flags=0, VKey=0x11)) == LCTRL
    assert _normalise_vk(RAWKEYBOARD(MakeCode=0x36, Flags=0, VKey=0x10)) == 0xA1    # right shift
