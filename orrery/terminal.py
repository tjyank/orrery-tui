"""Minimal cross-platform terminal handling: ANSI output, non-blocking key and mouse input."""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass

# Normalised key names returned by Terminal.read_keys()
UP, DOWN, LEFT, RIGHT, TAB, BTAB, ESC, ENTER, BACKSPACE = (
    "UP", "DOWN", "LEFT", "RIGHT", "TAB", "BTAB", "ESC", "ENTER", "BACKSPACE")

_CSI_KEYS = {"A": UP, "B": DOWN, "D": LEFT, "C": RIGHT, "Z": BTAB}


@dataclass(frozen=True)
class Mouse:
    """A mouse report. `col`/`row` are 0-based terminal cells.

    kind: press | release | drag | scroll. button: 0 left, 1 middle, 2 right; for scroll, +1 up / -1 down.
    """
    kind: str
    button: int
    col: int
    row: int


# Button-event tracking (1002: clicks, and motion while a button is held) in SGR encoding (1006).
MOUSE_ON = "\x1b[?1002h\x1b[?1006h"
MOUSE_OFF = "\x1b[?1006l\x1b[?1002l"

if os.name == "nt":
    import msvcrt

    _WIN_KEYS = {"H": UP, "P": DOWN, "K": LEFT, "M": RIGHT, "\x0f": BTAB}
else:
    import select
    import termios
    import tty


def parse_input(data: str) -> tuple[list, str]:
    """Split raw terminal input into keys and Mouse events; returns them and any unfinished tail.

    Understands CSI sequences (arrows, Shift-Tab, SGR mouse "ESC [ < b ; x ; y M/m") and SS3
    arrows ("ESC O A"). A lone ESC at the very end is left in the tail, since it may be the start
    of a sequence still arriving; the caller decides when it's really the Esc key.
    """
    out: list = []
    i, n = 0, len(data)
    while i < n:
        ch = data[i]
        if ch != "\x1b":
            out.append(_plain(ch))
            i += 1
            continue
        if i + 1 >= n:
            return out, data[i:]
        nxt = data[i + 1]
        if nxt == "O":  # SS3: ESC O <final>
            if i + 2 >= n:
                return out, data[i:]
            out.append(_CSI_KEYS.get(data[i + 2], ESC))
            i += 3
        elif nxt == "[":  # CSI: ESC [ <params> <final byte in @..~>
            j = i + 2
            while j < n and not ("@" <= data[j] <= "~"):  # parameter bytes (digits ; < ?) come first
                j += 1
            if j >= n:
                return out, data[i:]
            params, final = data[i + 2:j], data[j]
            if params.startswith("<") and final in "Mm":
                ev = _sgr_mouse(params[1:], final)
                if ev:
                    out.append(ev)
            elif final in _CSI_KEYS:
                out.append(_CSI_KEYS[final])
            i = j + 1
        else:  # ESC followed by an ordinary key: Esc, then that key
            out.append(ESC)
            i += 1
    return out, ""


def _sgr_mouse(params: str, final: str) -> Mouse | None:
    try:
        b, x, y = (int(p) for p in params.split(";"))
    except ValueError:
        return None
    col, row = x - 1, y - 1
    if b & 64:  # wheel: 64 up, 65 down
        return Mouse("scroll", 1 if b & 1 == 0 else -1, col, row)
    button = b & 3
    if b & 32:
        return Mouse("drag", button, col, row)
    return Mouse("press" if final == "M" else "release", button, col, row)


def _plain(ch: str) -> str:
    return {"\t": TAB, "\r": ENTER, "\n": ENTER, "\x1b": ESC, "\x03": "q", "\x7f": BACKSPACE,
            "\x08": BACKSPACE}.get(ch, ch)


def _enable_windows_vt(mouse: bool) -> tuple[int, int] | None:
    """Turn on VT output (and VT input, for mouse reports); returns the modes to restore."""
    import ctypes

    kernel32 = ctypes.windll.kernel32
    out_h, in_h = kernel32.GetStdHandle(-11), kernel32.GetStdHandle(-10)
    out_mode, in_mode = ctypes.c_uint32(), ctypes.c_uint32()
    if not kernel32.GetConsoleMode(out_h, ctypes.byref(out_mode)):
        return None
    kernel32.SetConsoleMode(out_h, out_mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    if mouse and kernel32.GetConsoleMode(in_h, ctypes.byref(in_mode)):
        # VT input delivers mouse reports as escape sequences; quick-edit would grab clicks to
        # select text, so it's switched off (ENABLE_EXTENDED_FLAGS lets that stick).
        mode = (in_mode.value | 0x0200 | 0x0080) & ~0x0040
        kernel32.SetConsoleMode(in_h, mode)
    return out_mode.value, in_mode.value


def _restore_windows(modes: tuple[int, int]) -> None:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), modes[0])
    if modes[1]:
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-10), modes[1])


class Terminal:
    def __init__(self, mouse: bool = True):
        self.mouse = mouse
        self._pending = ""  # input received but not yet a complete key or sequence
        self._win_modes = None

    def __enter__(self) -> "Terminal":
        if os.name == "nt":
            self._win_modes = _enable_windows_vt(self.mouse)
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
        else:
            self._fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        # alternate screen, hide cursor, clear
        self.write("\x1b[?1049h\x1b[?25l\x1b[2J" + (MOUSE_ON if self.mouse else ""))
        return self

    def __exit__(self, *exc) -> None:
        self.write((MOUSE_OFF if self.mouse else "") + "\x1b[0m\x1b[?25h\x1b[?1049l")
        if os.name == "nt":
            if self._win_modes:
                _restore_windows(self._win_modes)
        else:
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)

    @staticmethod
    def size() -> tuple[int, int]:
        s = shutil.get_terminal_size((100, 32))
        return s.columns, s.lines

    @staticmethod
    def write(data: str) -> None:
        sys.stdout.write(data)
        sys.stdout.flush()

    def _read_raw(self) -> str:
        chunks: list[str] = []
        if os.name == "nt":
            while msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):  # legacy console arrow keys, when VT input is off
                    code = msvcrt.getwch()
                    if code in _WIN_KEYS:
                        chunks.append({UP: "\x1b[A", DOWN: "\x1b[B", LEFT: "\x1b[D", RIGHT: "\x1b[C",
                                       BTAB: "\x1b[Z"}[_WIN_KEYS[code]])
                else:
                    chunks.append(ch)
        else:
            while select.select([sys.stdin], [], [], 0)[0]:
                chunks.append(os.read(self._fd, 1024).decode(errors="ignore"))
        return "".join(chunks)

    def read_keys(self) -> list:
        """Keys (str) and Mouse events received since the last call."""
        data = self._pending + self._read_raw()
        events, self._pending = parse_input(data)
        if self._pending and not self._read_more():
            # Nothing else arrived: a lone ESC is the Esc key; drop any other broken sequence.
            if self._pending == "\x1b":
                events.append(ESC)
            self._pending = ""
        return events

    def _read_more(self) -> bool:
        """Is more input waiting (the rest of an escape sequence)? Waits briefly on Unix."""
        if os.name == "nt":
            return msvcrt.kbhit()
        return bool(select.select([sys.stdin], [], [], 0.02)[0])
