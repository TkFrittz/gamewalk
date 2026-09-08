"""Custom widgets.

Tk's stock buttons and sliders look like Windows 95 no matter how they're
configured, so the interactive pieces are drawn on canvases instead. That also
buys hover and disabled states that actually read against a black background.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from .theme import (
    BG, LINE, LINE_HI, NEON_BLUE, NEON_GREEN, NEON_GREEN_DIM, PANEL,
    PANEL_HI, TEXT, TEXT_DIM, TEXT_FAINT, font, mix, round_rect,
)


class NeonButton(tk.Canvas):
    """A flat button with a neon outline that fills in on hover."""

    def __init__(
        self, parent, text: str, command: Callable[[], None],
        accent: str = NEON_GREEN, width: int = 150, height: int = 40,
        primary: bool = False, small: bool = False,
    ):
        super().__init__(parent, width=width, height=height, bg=PANEL,
                         highlightthickness=0, bd=0)
        self.command = command
        self.accent = accent
        self.primary = primary
        self._text = text
        # NOT _w/_h: Misc._w holds the widget's own Tcl path, and
        # overwriting it makes every later Tk call address a widget that
        # does not exist ("invalid command name 190").
        self._bw, self._bh = width, height
        self._hover = False
        self._enabled = True
        self._font = font(10 if small else 11, bold=True)

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Button-1>", self._on_click)
        self.draw()

    def set_background(self, color: str) -> None:
        self.configure(bg=color)
        self.draw()

    def config_text(self, text: str, accent: str | None = None) -> None:
        self._text = text
        if accent:
            self.accent = accent
        self.draw()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        w, h = self._bw, self._bh
        bg = self["bg"]

        if not self._enabled:
            fill, outline, fg = bg, LINE, TEXT_FAINT
        elif self.primary:
            base = mix(self.accent, bg, 0.72 if not self._hover else 0.55)
            fill, outline = base, self.accent
            fg = self.accent
        else:
            fill = PANEL_HI if self._hover else bg
            outline = LINE_HI if not self._hover else self.accent
            fg = TEXT if not self._hover else self.accent

        round_rect(self, 1, 1, w - 1, h - 1, r=8, fill=fill, outline=outline)
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)

    def _on_enter(self, _=None):
        if self._enabled:
            self._hover = True
            self.draw()

    def _on_leave(self, _=None):
        self._hover = False
        self.draw()

    def _on_click(self, _=None):
        if self._enabled:
            self.command()


class Toggle(tk.Canvas):
    """An on/off switch. Reads faster than a checkbox at a glance."""

    def __init__(self, parent, text: str, value: bool,
                 command: Callable[[bool], None], accent: str = NEON_BLUE,
                 bg: str = PANEL):
        super().__init__(parent, width=210, height=30, bg=bg,
                         highlightthickness=0, bd=0)
        self.value = value
        self.command = command
        self.accent = accent
        self._text = text
        self.bind("<Button-1>", self._toggle)
        self.draw()

    def draw(self) -> None:
        self.delete("all")
        on = self.value
        track = mix(self.accent, self["bg"], 0.45) if on else "#1A2226"
        round_rect(self, 2, 8, 38, 26, r=9, fill=track,
                   outline=self.accent if on else LINE_HI)
        cx = 30 if on else 11
        self.create_oval(cx - 6, 11, cx + 6, 23,
                         fill=self.accent if on else TEXT_FAINT, outline="")
        self.create_text(50, 17, text=self._text, anchor="w",
                         fill=TEXT if on else TEXT_DIM, font=font(10, bold=on))

    def set(self, value: bool) -> None:
        self.value = value
        self.draw()

    def _toggle(self, _=None):
        self.value = not self.value
        self.draw()
        self.command(self.value)


class Slider(tk.Frame):
    """Labelled slider that reports on release.

    Committing on every pixel of travel would push a config write per frame
    for values you are only passing through on the way to the one you want.
    """

    def __init__(self, parent, label: str, value: float, lo: float, hi: float,
                 step: float, fmt: Callable[[float], str],
                 command: Callable[[float], None], accent: str = NEON_GREEN,
                 help_text: str = "", bg: str = PANEL):
        super().__init__(parent, bg=bg)
        self.fmt = fmt
        self.command = command
        self.lo, self.hi, self.step = lo, hi, step
        self.accent = accent
        self._value = value
        self._dragging = False

        head = tk.Frame(self, bg=bg)
        head.pack(fill="x")
        tk.Label(head, text=label, bg=bg, fg=TEXT, font=font(10)).pack(side="left")
        self.readout = tk.Label(head, text=fmt(value), bg=bg, fg=accent,
                                font=font(11, bold=True))
        self.readout.pack(side="right")

        self.canvas = tk.Canvas(self, height=22, bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack(fill="x")
        self.canvas.bind("<Configure>", lambda e: self.draw())
        self.canvas.bind("<Button-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)

        # A fixed wraplength is wrong at every width but one: too small and
        # the text wraps early, too large and the label grows wider than its
        # column and gets clipped. Follow the actual width instead.
        self.help = None
        if help_text:
            self.help = tk.Label(self, text=help_text, bg=bg, fg=TEXT_DIM,
                                 font=font(8), justify="left", anchor="w")
            self.help.pack(fill="x", pady=(1, 0))
            self.bind("<Configure>", self._rewrap)

    def _rewrap(self, event):
        if self.help is not None and event.width > 40:
            self.help.config(wraplength=event.width - 8)

    def draw(self) -> None:
        c = self.canvas
        c.delete("all")
        w = c.winfo_width() or 320
        y = 11
        frac = (self._value - self.lo) / max(1e-9, self.hi - self.lo)
        x = 8 + frac * (w - 16)

        c.create_line(8, y, w - 8, y, fill="#1A2226", width=4, capstyle="round")
        c.create_line(8, y, x, y, fill=self.accent, width=4, capstyle="round")
        r = 8 if self._dragging else 6
        c.create_oval(x - r, y - r, x + r, y + r, fill=self.accent, outline=BG, width=2)

    def _value_at(self, px: int) -> float:
        w = self.canvas.winfo_width() or 320
        frac = (px - 8) / max(1, w - 16)
        raw = self.lo + max(0.0, min(1.0, frac)) * (self.hi - self.lo)
        snapped = round(raw / self.step) * self.step
        return max(self.lo, min(self.hi, snapped))

    def _press(self, e):
        self._dragging = True
        self._value = self._value_at(e.x)
        self.readout.config(text=self.fmt(self._value))
        self.draw()

    def _drag(self, e):
        self._value = self._value_at(e.x)
        self.readout.config(text=self.fmt(self._value))
        self.draw()

    def _release(self, _):
        self._dragging = False
        self.draw()
        self.command(self._value)

    def set(self, value: float) -> None:
        if self._dragging:
            return  # never yank the handle out from under a finger
        self._value = value
        self.readout.config(text=self.fmt(value))
        self.draw()


def card(parent, title: str = "") -> tk.Frame:
    """A titled panel with a hairline border."""
    outer = tk.Frame(parent, bg=LINE)
    inner = tk.Frame(outer, bg=PANEL)
    inner.pack(fill="both", expand=True, padx=1, pady=1)
    if title:
        tk.Label(inner, text=title.upper(), bg=PANEL, fg=TEXT_DIM,
                 font=font(8, bold=True)).pack(anchor="w", padx=14, pady=(11, 0))
    return inner


class ScrollColumn(tk.Frame):
    """A vertically scrolling column of cards.

    The settings page outgrew the window once every phone setting moved onto
    it, and a page you can't reach the bottom of is worse than a crowded one.
    """

    def __init__(self, parent, bg: str = BG):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack(side="left", fill="both", expand=True)

        # Tk's own Scrollbar uses the native Windows theme and ignores every
        # colour option, so it renders as a bright white strip against a black
        # window. Drawing a thin one keeps the page looking like one thing.
        self.bar = tk.Canvas(self, width=8, bg=bg, highlightthickness=0, bd=0)
        self.bar.pack(side="right", fill="y")
        self._thumb = (0.0, 1.0)
        self._drag_from = None
        self.bar.bind("<Button-1>", self._bar_press)
        self.bar.bind("<B1-Motion>", self._bar_drag)
        self.bar.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag_from", None))
        self.bar.bind("<Configure>", lambda e: self._draw_bar())
        self.canvas.configure(yscrollcommand=self._on_scroll)

        self.body = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.body.bind("<Configure>", self._on_body)
        self.canvas.bind("<Configure>", self._on_canvas)
        for widget in (self, self.canvas, self.body):
            widget.bind("<MouseWheel>", self._on_wheel)

    def _on_body(self, _):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        # Hide the scrollbar when everything fits, so a short page doesn't
        # carry a permanently disabled control.
        self._draw_bar()

    def _on_canvas(self, e):
        self.canvas.itemconfigure(self._win, width=e.width)
        self._on_body(None)

    def _on_wheel(self, e):
        if self.body.winfo_reqheight() > self.canvas.winfo_height():
            self.canvas.yview_scroll(int(-e.delta / 120), "units")

    # --- the drawn scrollbar ------------------------------------------------

    def _on_scroll(self, first, last):
        self._thumb = (float(first), float(last))
        self._draw_bar()

    def _draw_bar(self):
        self.bar.delete("all")
        h = self.bar.winfo_height() or 1
        first, last = self._thumb
        if last - first >= 0.999:
            return
        y1, y2 = first * h, last * h
        self.bar.create_line(4, 2, 4, h - 2, fill=LINE, width=2,
                             capstyle="round")
        self.bar.create_line(4, y1 + 3, 4, y2 - 3, fill=NEON_GREEN_DIM, width=6,
                             capstyle="round")

    def _bar_press(self, e):
        h = self.bar.winfo_height() or 1
        first, last = self._thumb
        if first * h <= e.y <= last * h:
            self._drag_from = (e.y, first)
        else:
            # Clicking the track jumps the thumb's centre to the cursor.
            self.canvas.yview_moveto(max(0.0, e.y / h - (last - first) / 2))

    def _bar_drag(self, e):
        if self._drag_from is None:
            return
        h = self.bar.winfo_height() or 1
        start_y, start_first = self._drag_from
        self.canvas.yview_moveto(max(0.0, start_first + (e.y - start_y) / h))

    def bind_wheel_deep(self, widget) -> None:
        """Wheel events go to the widget under the cursor, so every child has
        to forward them or scrolling dies over a card."""
        widget.bind("<MouseWheel>", self._on_wheel)
        for child in widget.winfo_children():
            self.bind_wheel_deep(child)


class KeyPicker(tk.Toplevel):
    """Choose the keys a tier holds."""

    def __init__(self, parent, title: str, available: list[str],
                 chosen: list[str], on_save):
        super().__init__(parent)
        self.title(title)
        self.configure(bg=BG)
        self.transient(parent)
        self.resizable(False, False)
        self.on_save = on_save
        self.vars: dict[str, tk.BooleanVar] = {}

        tk.Label(self, text=title, bg=BG, fg=TEXT,
                 font=font(13, bold=True)).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(self, text="Held together for as long as you stay in this tier.",
                 bg=BG, fg=TEXT_DIM, font=font(9)).pack(anchor="w", padx=16)

        grid = tk.Frame(self, bg=BG)
        grid.pack(padx=12, pady=12)
        cols = 8
        for i, key in enumerate(available):
            var = tk.BooleanVar(value=key in chosen)
            self.vars[key] = var
            cb = tk.Checkbutton(
                grid, text=key, variable=var, bg=PANEL, fg=TEXT,
                selectcolor=PANEL, activebackground=PANEL_HI,
                activeforeground=NEON_GREEN, font=font(9), bd=0,
                highlightthickness=0, width=9, anchor="w", padx=4)
            cb.grid(row=i // cols, column=i % cols, sticky="w", padx=1, pady=1)

        row = tk.Frame(self, bg=BG)
        row.pack(fill="x", padx=16, pady=(0, 14))
        NeonButton(row, "SAVE", self._save, accent=NEON_GREEN,
                   width=110, height=34, primary=True, small=True).pack(side="right")
        NeonButton(row, "CANCEL", self.destroy, width=100, height=34,
                   small=True).pack(side="right", padx=(0, 8))
        for child in row.winfo_children():
            child.configure(bg=BG)
            child.draw()

        self.grab_set()

    def _save(self):
        picked = [k for k, v in self.vars.items() if v.get()]
        if not picked:
            return
        self.on_save(picked)
        self.destroy()
