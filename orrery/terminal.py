"""Minimal cross-platform terminal handling: ANSI output and non-blocking key input."""
from __future__ import annotations

import os
import shutil
import sys

# Normalised key names returned by Terminal.read_keys()
UP, DOWN, LEFT, RIGHT, TAB, BTAB, ESC, ENTER = "UP", "DOWN", "LEFT", "RIGHT", "TAB", "BTAB", "ESC", "ENTER"

if os.name == "nt":
    import msvcrt

    _WIN_KEYS = {"H": UP, "P": DOWN, "K": LEFT, "M": RIGHT, "\x0f": BTAB}
else:
    import select
    import termios
    import tty

    _ANSI_KEYS = {"[A": UP, "[B": DOWN, "[D": LEFT, "[C": RIGHT, "[Z": BTAB, "OA": UP, "OB": DOWN, "OD": LEFT, "OC": RIGHT}


def _enable_windows_vt() -> None:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
    mode = ctypes.c_uint32()
    if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING


class Terminal:
    def __enter__(self) -> "Terminal":
        if os.name == "nt":
            _enable_windows_vt()
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
        else:
            self._fd = sys.stdin.fileno()
            self._saved = termios.tcgetattr(self._fd)
            tty.setcbreak(self._fd)
        # alternate screen, hide cursor, clear
        self.write("\x1b[?1049h\x1b[?25l\x1b[2J")
        return self

    def __exit__(self, *exc) -> None:
        self.write("\x1b[0m\x1b[?25h\x1b[?1049l")
        if os.name != "nt":
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._saved)

    @staticmethod
    def size() -> tuple[int, int]:
        s = shutil.get_terminal_size((100, 32))
        return s.columns, s.lines

    @staticmethod
    def write(data: str) -> None:
        sys.stdout.write(data)
        sys.stdout.flush()

    def read_keys(self) -> list[str]:
        keys: list[str] = []
        if os.name == "nt":
            while msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):
                    code = msvcrt.getwch()
                    if code in _WIN_KEYS:
                        keys.append(_WIN_KEYS[code])
                else:
                    keys.append(self._plain(ch))
            return keys
        while select.select([sys.stdin], [], [], 0)[0]:
            ch = os.read(self._fd, 1).decode(errors="ignore")
            if ch == "\x1b":
                seq = ""
                while len(seq) < 2 and select.select([sys.stdin], [], [], 0.01)[0]:
                    seq += os.read(self._fd, 1).decode(errors="ignore")
                keys.append(_ANSI_KEYS.get(seq, ESC))
            else:
                keys.append(self._plain(ch))
        return keys

    @staticmethod
    def _plain(ch: str) -> str:
        return {"\t": TAB, "\r": ENTER, "\n": ENTER, "\x1b": ESC, "\x03": "q"}.get(ch, ch)
