"""Windows drawn over the view: the jump menu, its date prompt, and the key legend (mixed into App)."""
from __future__ import annotations

from .render import Canvas
from .ui import ACCENT, DIM, FG, KEY_LEGEND


class Overlays:
    """The jump menu and key legend for App."""

    def _draw_menu(self, c: Canvas) -> None:
        """The jump menu, or its date prompt, as an opaque box in the middle of the scene."""
        if self.menu == "date":
            title = "JUMP TO DATE"
            lines = ["A UTC date, or an offset from the time shown", "", f"> {self.prompt}_", "",
                     "e.g.  2061-07-28   1989-08-25 03:56   +30d   -2y", "units: h d w m y",
                     "", "Enter to jump, Esc to cancel"]
        else:
            title = "JUMP TO"
            items = self.menu_items()
            lines = [f"{'›' if i == self.menu_idx else ' '} {i + 1 if i < 9 else ' '} {label}" for i, label in enumerate(items)]
            lines += ["", "↑↓ Enter, a number, or click   Esc close"]
        w = min(c.cols, max(len(s) for s in lines) + 6)
        h = min(c.rows, len(lines) + 4)
        x0, y0 = self._box(c, title, w, h)
        self.menu_hits = {}
        n_items = len(self.menu_items()) if self.menu == "jump" else 0
        for i, s in enumerate(lines[: h - 4]):
            row = y0 + 2 + i
            sel = self.menu == "jump" and i == self.menu_idx
            dim = self.menu == "jump" and i >= n_items
            c.text(x0 + 3, row, s[: w - 5], (255, 255, 255) if sel else DIM if dim else FG, bold=sel, reverse=sel)
            if i < n_items:
                self.menu_hits[row + 1] = i  # +1: the scene starts on terminal row 1

    def _box(self, c: Canvas, title: str, w: int, h: int, centre: bool = True) -> tuple[int, int]:
        """An opaque bordered box, centred on the canvas or at its corner; returns its top-left cell."""
        x0, y0 = (max(0, (c.cols - w) // 2), max(0, (c.rows - h) // 2)) if centre else (0, 0)
        edge = (90, 96, 116)
        horiz, vert, corners = ("-", "|", "++++") if self.opts.ascii else ("─", "│", "╭╮╰╯")
        for r in range(h):  # cells hide the dots beneath them, so this blanks the box
            if r in (0, h - 1):
                left, right = (corners[0], corners[1]) if r == 0 else (corners[2], corners[3])
                c.text(x0, y0 + r, left + horiz * (w - 2) + right, edge)
            else:
                c.text(x0, y0 + r, vert + " " * (w - 2) + vert, edge)
        c.text(x0 + 2, y0, f" {title} ", ACCENT, bold=True)
        return x0, y0

    def _keys_rows(self, cols: int, rows: int) -> list[str]:
        """The key legend as its own small canvas, centred over the whole screen (panel included),
        so it isn't squeezed into the scene. Rendered last, it covers only the box itself."""
        blocks = [[(title, None)] + list(entries) for title, entries in KEY_LEGEND]
        room = max(4, rows - 2 - 4)  # rows for entries inside the box: less the borders and padding

        def layout(gap: int):
            columns: list[list] = [[]]
            for block in blocks:
                if columns[-1] and len(columns[-1]) + gap + len(block) > room:
                    columns.append([])
                if columns[-1] and gap:
                    columns[-1].append(("", None))  # a blank line between sections
                columns[-1].extend(block)
            # Each column sizes its key field to its own longest key, which keeps the box narrow.
            key_ws = [max(len(k) for k, d in col if d) + 2 for col in columns]
            col_w = [kw + max(len(d) for _, d in col if d) + 3 for kw, col in zip(key_ws, columns)]
            return columns, key_ws, col_w

        # Airy if it fits; on a small terminal, drop the gaps between sections to save a column.
        options = [layout(1), layout(0)]
        columns, key_ws, col_w = next((o for o in options if sum(o[2]) + 3 <= cols),
                                      min(options, key=lambda o: sum(o[2])))
        hint = "? or Esc to close"
        w = min(cols, sum(col_w) + 3)
        h = min(rows - 2, max(len(col) for col in columns) + 4)
        c = Canvas(w, h)
        x0, y0 = self._box(c, "KEYS", w, h, centre=False)
        x = x0 + 3
        for col, cw, key_w in zip(columns, col_w, key_ws):
            for i, (k, d) in enumerate(col):
                row = y0 + 2 + i
                if row >= y0 + h - 1 or x >= x0 + w - 2:
                    break
                limit = x0 + w - 2 - x  # clip to the box when the terminal is too narrow
                if d is None:
                    c.text(x, row, k[:limit], ACCENT, bold=True)
                else:
                    c.text(x, row, k[:limit], FG, bold=True)
                    c.text(x + key_w, row, d[: max(0, limit - key_w)], DIM)
            x += cw
        c.text(x0 + w - len(hint) - 4, y0 + h - 1, f" {hint} ", DIM)  # set into the bottom border
        # Centre the box on the area between header and footer (terminal rows 1 .. rows-2).
        return c.render_rows(1 + max(0, (rows - 2 - h) // 2), max(0, (cols - w) // 2))
