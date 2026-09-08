"""The desktop app.

The server keeps running its own select() loop on a background thread; this
only reads its status and posts actions back. Config edits are queued rather
than applied directly, so a slider can never mutate the config halfway through
the loop reading it.
"""

from __future__ import annotations

import queue
import sys
import random
import socket
import threading
import time
import tkinter as tk
from tkinter import messagebox

from . import config as cfgmod
from . import discovery, protocol
from .server import Server
from .theme import (
    AMBER, BG, GREEN_DARK, GREEN_DEEP, LINE, NEON_BLUE, NEON_GREEN,
    NEON_GREEN_DIM, PANEL, PANEL_HI, RED, TEXT, TEXT_DIM, TEXT_FAINT,
    TIER_COLORS, font, mix, round_rect,
)
from .settings_ui import SettingsMixin
from .widgets import NeonButton, Slider, Toggle, card

METER_TOP = 220.0   # SPM at the right-hand end of the meter
POLL_MS = 80


class TestWalker:
    """Fake a phone walking, from inside the app.

    Sends real STEP datagrams to the helper's own port rather than poking the
    state machine directly, so a test exercises the identical path a phone
    takes -- protocol, token check, cadence estimation and key injection.
    """

    def __init__(self, port: int, token: str, on_update, on_done):
        self.port = port
        self.token = token
        self.on_update = on_update
        self.on_done = on_done
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.spm = 0.0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, seconds: float = 24.0, lo: float = 90.0, hi: float = 185.0):
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, args=(seconds, lo, hi), daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self, seconds: float, lo: float, hi: float):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        dest = ("127.0.0.1", self.port)
        seq = 0

        def send(verb, *a):
            nonlocal seq
            seq += 1
            try:
                sock.sendto(protocol.build(self.token, verb, seq, *a), dest)
            except OSError:
                pass

        send(protocol.HELLO, "self_test", "A", "gui")
        start = time.monotonic()
        next_step = start
        next_hb = start

        try:
            while not self._stop.is_set():
                now = time.monotonic()
                elapsed = now - start
                if elapsed >= seconds:
                    break

                if now >= next_hb:
                    send(protocol.HB)
                    next_hb = now + 0.25

                if now >= next_step:
                    # Triangle ramp: accelerate to a jog and back down, so both
                    # directions of the tier hysteresis get exercised.
                    phase = elapsed / seconds
                    tri = 2 * phase if phase < 0.5 else 2 * (1 - phase)
                    self.spm = lo + (hi - lo) * tri
                    send(protocol.STEP, int(now * 1000))
                    gap = 60.0 / max(1.0, self.spm)
                    next_step = now + gap * random.uniform(0.96, 1.04)
                    self.on_update(self.spm, elapsed / seconds)

                time.sleep(0.004)
        finally:
            send(protocol.STOP)
            sock.close()
            self.spm = 0.0
            self.on_done()


class App(SettingsMixin):
    def __init__(self, root: tk.Tk, server: Server, dry_run: bool):
        self.root = root
        self.server = server
        self.dry_run = dry_run
        self.actions: queue.Queue = queue.Queue()
        self.log_index = 0
        self._sliders: dict[str, Slider] = {}
        self._tier_rows_signature = ""
        # Phone-owned settings the PC can drive. Remembered so the page
        # still shows the last known state when no phone is connected.
        self._last_phone_mode = "A"
        self._acc_hz = 50

        self.test = TestWalker(
            server.cfg.port, server.cfg.token,
            on_update=lambda spm, frac: None,
            on_done=lambda: self.root.after(0, self._test_finished),
        )

        root.title("GameWalk")
        root.configure(bg=BG)
        root.geometry("900x740")
        root.minsize(760, 640)
        self._set_icon()

        self._build()
        self._poll()

    # --- chrome -------------------------------------------------------------

    def _set_icon(self):
        try:
            icon = tk.PhotoImage(width=32, height=32)
            icon.put(BG, to=(0, 0, 32, 32))
            for x, y in [(15, 6), (16, 6), (15, 7), (16, 7)]:
                icon.put(NEON_GREEN, to=(x, y, x + 2, y + 2))
            for y in range(9, 20):
                icon.put(NEON_GREEN, to=(15, y, 17, y + 1))
            for i in range(6):
                icon.put(NEON_GREEN, to=(15 - i, 20 + i, 17 - i, 21 + i))
                icon.put(NEON_BLUE, to=(16 + i, 20 + i, 18 + i, 21 + i))
            self.root.iconphoto(True, icon)
            self._icon = icon
        except Exception:
            pass

    def _build(self):
        self._build_header()

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        self.pages = {}
        self.page_host = tk.Frame(body, bg=BG)
        self.page_host.pack(fill="both", expand=True)

        for name in ("DASHBOARD", "SETTINGS", "ACTIVITY"):
            frame = tk.Frame(self.page_host, bg=BG)
            self.pages[name] = frame

        self._build_dashboard(self.pages["DASHBOARD"])
        self._build_settings(self.pages["SETTINGS"])
        self._build_activity(self.pages["ACTIVITY"])
        self.show_page("DASHBOARD")

    def _build_header(self):
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=16, pady=(14, 10))

        mark = tk.Frame(head, bg=BG)
        mark.pack(side="left")
        tk.Label(mark, text="GAME", bg=BG, fg=TEXT,
                 font=(font(17, bold=True)[0], 17)).pack(side="left")
        tk.Label(mark, text="WALK", bg=BG, fg=NEON_GREEN,
                 font=(font(17, bold=True)[0], 17)).pack(side="left")

        self.tabs = {}
        tabbar = tk.Frame(head, bg=BG)
        tabbar.pack(side="left", padx=(28, 0))
        for name in ("DASHBOARD", "SETTINGS", "ACTIVITY"):
            lbl = tk.Label(tabbar, text=name, bg=BG, fg=TEXT_DIM,
                           font=font(9, bold=True), padx=12, pady=6,
                           cursor="hand2")
            lbl.pack(side="left")
            lbl.bind("<Button-1>", lambda e, n=name: self.show_page(n))
            self.tabs[name] = lbl

        self.conn_pill = tk.Canvas(head, width=210, height=28, bg=BG,
                                   highlightthickness=0, bd=0)
        self.conn_pill.pack(side="right")

    def show_page(self, name: str):
        for frame in self.pages.values():
            frame.pack_forget()
        self.pages[name].pack(fill="both", expand=True)
        for tab, lbl in self.tabs.items():
            lbl.config(fg=NEON_GREEN if tab == name else TEXT_DIM)

    # --- dashboard ----------------------------------------------------------

    def _build_dashboard(self, parent):
        hero_wrap = tk.Frame(parent, bg=LINE)
        hero_wrap.pack(fill="x")
        hero = tk.Frame(hero_wrap, bg=PANEL)
        hero.pack(fill="both", padx=1, pady=1)

        self.hero = tk.Canvas(hero, height=250, bg=PANEL,
                              highlightthickness=0, bd=0)
        self.hero.pack(fill="x")
        self.hero.bind("<Configure>", lambda e: self._draw_hero())

        controls = tk.Frame(parent, bg=BG)
        controls.pack(fill="x", pady=(12, 0))

        self.btn_arm = NeonButton(
            controls, "ARM", self.toggle_arm, accent=NEON_GREEN,
            width=190, height=54, primary=True)
        self.btn_arm.set_background(BG)
        self.btn_arm.pack(side="left")

        self.btn_test = NeonButton(
            controls, "TEST WALK", self.toggle_test, accent=NEON_BLUE,
            width=150, height=54)
        self.btn_test.set_background(BG)
        self.btn_test.pack(side="left", padx=(10, 0))

        right = tk.Frame(controls, bg=BG)
        right.pack(side="right")
        self.tg_dry = Toggle(right, "Dry run - don't press keys", self.dry_run,
                             self.set_dry_run, accent=AMBER, bg=BG)
        self.tg_dry.pack(anchor="e")
        self.lbl_panic = tk.Label(
            right, text="", bg=BG, fg=TEXT_DIM, font=font(9))
        self.lbl_panic.pack(anchor="e", pady=(4, 0))

        grid = tk.Frame(parent, bg=BG)
        grid.pack(fill="both", expand=True, pady=(12, 0))
        grid.columnconfigure(0, weight=3, uniform="g")
        grid.columnconfigure(1, weight=2, uniform="g")
        # Row 1 takes the slack, so the window has no dead space at the bottom
        # at any height.
        grid.rowconfigure(1, weight=1)

        conn = card(grid, "Connection")
        conn.master.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.conn_rows = tk.Frame(conn, bg=PANEL)
        self.conn_rows.pack(fill="both", expand=True, padx=14, pady=(8, 12))

        pair = card(grid, "Pairing")
        pair.master.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        self.pin_label = tk.Label(pair, text="-", bg=PANEL, fg=NEON_BLUE,
                                  font=(font(22, mono=True)[0], 22, "bold"))
        self.pin_label.pack(anchor="w", padx=14, pady=(8, 0))
        self.pin_sub = tk.Label(pair, text="", bg=PANEL, fg=TEXT_DIM,
                                font=font(9))
        self.pin_sub.pack(anchor="w", padx=14)
        btn = NeonButton(pair, "NEW PIN", self.new_pin, accent=NEON_BLUE,
                         width=130, height=34, small=True)
        btn.pack(anchor="w", padx=14, pady=(10, 12))

        recent = card(grid, "Recent activity")
        recent.master.grid(row=1, column=0, columnspan=2, sticky="nsew",
                           pady=(12, 0))
        self.recent = tk.Text(recent, bg=PANEL, fg=TEXT_DIM, bd=0,
                              highlightthickness=0, font=font(9, mono=True),
                              wrap="none", padx=14, pady=8, state="disabled",
                              height=6)
        self.recent.pack(fill="both", expand=True, pady=(4, 10))
        self.recent.tag_config("good", foreground=NEON_GREEN)
        self.recent.tag_config("info", foreground=TEXT_DIM)
        self.recent.tag_config("warn", foreground=RED)

    def _draw_hero(self):
        c = self.hero
        c.delete("all")
        w = c.winfo_width() or 800
        st = self.server.machine.status
        tiers = self.server.cfg.profile.tiers

        armed = st.armed
        idx = st.tier_index
        accent = TIER_COLORS[idx % len(TIER_COLORS)] if (st.moving and idx >= 0) \
            else (NEON_GREEN_DIM if armed else TEXT_FAINT)

        # --- big cadence number ---
        spm_text = f"{st.spm:.0f}" if st.spm >= 1 else "0"
        c.create_text(34, 62, text=spm_text, anchor="w", fill=accent,
                      font=(font(1)[0], 72, "bold"))
        approx = c.bbox(c.find_all()[-1])
        x_after = (approx[2] if approx else 200) + 12
        c.create_text(x_after, 82, text="SPM", anchor="w", fill=TEXT_DIM,
                      font=font(13, bold=True))

        # --- state badge ---
        if not armed:
            badge, bcol = "DISARMED", TEXT_FAINT
        elif st.moving:
            badge, bcol = st.tier.upper(), accent
        else:
            badge, bcol = "READY", NEON_GREEN_DIM
        bw = max(110, 16 + len(badge) * 11)
        round_rect(c, w - bw - 34, 40, w - 34, 78, r=10,
                   fill=mix(bcol, PANEL, 0.82), outline=bcol)
        c.create_text(w - bw / 2 - 34, 59, text=badge, fill=bcol,
                      font=font(13, bold=True))

        # --- keys currently held ---
        keys = " + ".join(k.upper() for k in st.held) if st.held else "-"
        c.create_text(w - 34, 100, text=keys, anchor="e",
                      fill=accent if st.held else TEXT_FAINT,
                      font=(font(1, mono=True)[0], 15, "bold"))
        c.create_text(w - 34, 122, text="KEYS HELD", anchor="e", fill=TEXT_DIM,
                      font=font(8, bold=True))

        # --- meter ---
        mx0, mx1, my = 34, w - 34, 168
        c.create_line(mx0, my, mx1, my, fill=GREEN_DEEP, width=16,
                      capstyle="round")

        span = mx1 - mx0

        def px(spm: float) -> float:
            return mx0 + span * max(0.0, min(1.0, spm / METER_TOP))

        # Tier bands, so you can see the shape of your own config.
        for i, t in enumerate(tiers):
            if i == 0:
                continue
            col = TIER_COLORS[i % len(TIER_COLORS)]
            c.create_line(px(t.enter_spm), my, mx1, my,
                          fill=mix(col, PANEL, 0.86), width=16, capstyle="butt")

        if st.spm > 0:
            c.create_line(mx0, my, px(st.spm), my, fill=accent, width=16,
                          capstyle="round")

        # Threshold ticks: where the tier will change, and in which direction.
        for i, t in enumerate(tiers):
            if i == 0:
                continue
            col = TIER_COLORS[i % len(TIER_COLORS)]
            for spm, style in ((t.enter_spm, "up"), (t.exit_spm, "down")):
                x = px(spm)
                c.create_line(x, my - 14, x, my + 14,
                              fill=col if style == "up" else mix(col, PANEL, 0.5),
                              width=2)
                c.create_text(x, my + 26,
                              text=f"{'▲' if style == 'up' else '▼'}{spm:.0f}",
                              fill=col if style == "up" else TEXT_DIM,
                              font=font(8, bold=style == "up"))
            c.create_text(px(t.enter_spm), my - 24, text=t.name.upper(),
                          fill=col, font=font(8, bold=True))

        c.create_text(mx0, my + 46, text="0", anchor="w", fill=TEXT_FAINT,
                      font=font(8))
        c.create_text(mx1, my + 46, text=f"{METER_TOP:.0f} SPM", anchor="e",
                      fill=TEXT_FAINT, font=font(8))

        # --- counters ---
        c.create_text(34, 122, text=f"{st.steps:,} steps", anchor="w",
                      fill=TEXT_DIM, font=font(10))
        if self.test.running:
            c.create_text(34, 215, text="SELF-TEST RUNNING - simulated walking",
                          anchor="w", fill=NEON_BLUE, font=font(9, bold=True))

    # --- settings -----------------------------------------------------------

    # --- activity -----------------------------------------------------------

    def _build_activity(self, parent):
        holder = card(parent, "Activity")
        holder.master.pack(fill="both", expand=True)
        self.log = tk.Text(holder, bg=PANEL, fg=TEXT_DIM, bd=0,
                           highlightthickness=0, font=font(9, mono=True),
                           wrap="word", padx=14, pady=10, state="disabled",
                           insertbackground=NEON_GREEN)
        self.log.pack(fill="both", expand=True, padx=1, pady=(6, 12))
        self.log.tag_config("good", foreground=NEON_GREEN)
        self.log.tag_config("info", foreground=TEXT_DIM)
        self.log.tag_config("warn", foreground=RED)

    # --- actions ------------------------------------------------------------

    def toggle_arm(self):
        st = self.server.machine.status
        if st.armed:
            self.actions.put(lambda s: s.machine.disarm())
        else:
            self.actions.put(lambda s: s.machine.arm(time.monotonic() * 1000))

    def toggle_test(self):
        if self.test.running:
            self.test.stop()
            return
        if not self.server.machine.status.armed:
            self.actions.put(lambda s: s.machine.arm(time.monotonic() * 1000))
        if not self.dry_run:
            ok = messagebox.askokcancel(
                "Test walk",
                "This presses real keys for about 25 seconds.\n\n"
                "Click into your game first, or turn on Dry run to watch "
                "without anything being pressed.\n\nF8 stops everything.",
                parent=self.root)
            if not ok:
                return
        self.test.token = self.server.cfg.token
        self.test.port = self.server.cfg.port
        self.test.start()
        self.btn_test.config_text("STOP TEST", accent=RED)

    def _test_finished(self):
        self.btn_test.config_text("TEST WALK", accent=NEON_BLUE)

    def set_dry_run(self, value: bool):
        self.dry_run = value
        self.actions.put(lambda s: s.set_dry_run(value))

    def new_pin(self):
        pin = self.server.responder.pairing.open()
        self.pin_label.config(text=pin)

    def _patch(self, patch: dict):
        def apply(s: Server):
            err = s.apply_patch_local(patch)
            if err:
                self.root.after(0, lambda: self._reject(err))
        self.actions.put(apply)

    def _reject(self, err: str):
        messagebox.showwarning("Not applied", err, parent=self.root)
        # Rebuild every control from the config that is actually live, so a
        # rejected drag snaps back instead of showing a value the PC refused.
        self._tier_rows_signature = ""
        self._refresh_tiers()
        self._build_feel()
        self._build_sensitivity()
        self._build_safety()

    # --- polling ------------------------------------------------------------

    def _poll(self):
        try:
            self._draw_hero()
            self._draw_conn_pill()
            self._refresh_connection_rows()
            self._refresh_tiers()
            self._drain_log()
            self._sync_controls()
        except Exception:
            pass
        self.root.after(POLL_MS, self._poll)

    def _sync_controls(self):
        st = self.server.machine.status
        if st.armed:
            self.btn_arm.config_text("DISARM", accent=RED)
            self.btn_arm.primary = False
        else:
            self.btn_arm.config_text("ARM", accent=NEON_GREEN)
            self.btn_arm.primary = True
        self.btn_arm.draw()

        hk = getattr(self.root, "_hotkey", None)
        if hk is not None:
            self.lbl_panic.config(
                text=f"Panic key: {self.server.cfg.panic_hotkey.upper()}"
                if hk.ok else f"Panic key unavailable")

        pairing = self.server.responder.pairing
        if pairing.is_open:
            self.pin_label.config(text=pairing.pin, fg=NEON_BLUE)
            self.pin_sub.config(text=f"expires in {pairing.seconds_left:.0f}s")
        else:
            self.pin_label.config(text="- - - - - -", fg=TEXT_FAINT)
            self.pin_sub.config(text="Tap New PIN to add a phone")

    def _draw_conn_pill(self):
        c = self.conn_pill
        c.delete("all")
        phone = self._current_phone()
        if phone:
            col, text = NEON_GREEN, "PHONE CONNECTED"
        elif self.test.running:
            col, text = NEON_BLUE, "SELF-TEST"
        else:
            col, text = TEXT_FAINT, "WAITING FOR PHONE"
        round_rect(c, 1, 3, 208, 25, r=11, fill=mix(col, BG, 0.86), outline=col)
        c.create_oval(13, 11, 21, 19, fill=col, outline="")
        c.create_text(30, 15, text=text, anchor="w", fill=col,
                      font=font(9, bold=True))

    def _current_phone(self):
        now_ms = time.monotonic() * 1000
        best = None
        for cl in self.server.clients.values():
            if now_ms - cl.last_seen < 4000 and cl.device not in ("?", "self_test"):
                best = cl
        return best

    def _refresh_connection_rows(self):
        phone = self._current_phone()
        rows = [
            ("Helper", f"{self._ip}:{self.server.cfg.port}", TEXT),
            ("Phone", f"{phone.device.replace('_', ' ')}  ({phone.addr[0]})"
             if phone else "not connected",
             NEON_GREEN if phone else TEXT_FAINT),
            ("Mode", "hardware step detector" if not phone or phone.mode == "A"
             else "raw accelerometer", TEXT_DIM),
            ("Keys", "DRY RUN - nothing pressed" if self.dry_run
             else "live", AMBER if self.dry_run else NEON_GREEN),
        ]
        sig = repr(rows)
        if sig == getattr(self, "_conn_sig", None):
            return
        self._conn_sig = sig
        for child in self.conn_rows.winfo_children():
            child.destroy()
        for name, value, col in rows:
            r = tk.Frame(self.conn_rows, bg=PANEL)
            r.pack(fill="x", pady=3)
            tk.Label(r, text=name, bg=PANEL, fg=TEXT_FAINT, font=font(9),
                     width=8, anchor="w").pack(side="left")
            tk.Label(r, text=value, bg=PANEL, fg=col, font=font(10)).pack(
                side="left")

    def _drain_log(self):
        entries = self.server.log
        if len(entries) <= self.log_index:
            return
        new = entries[self.log_index:]
        self.log_index = len(entries)
        # The dashboard strip and the full Activity tab show the same stream,
        # so they advance together off one cursor rather than two that could
        # drift apart.
        for widget in (self.log, self.recent):
            widget.config(state="normal")
            for line in new:
                low = line.lower()
                tag = "warn" if ("refused" in low or "reject" in low or
                                 "failed" in low or "invalid" in low) else (
                    "good" if ("connected" in low or "paired" in low) else "info")
                widget.insert("end", line + "\n", tag)
            widget.see("end")
            widget.config(state="disabled")

    _ip = "?"


def run(server: Server, dry_run: bool, hotkey=None,
        page: str = "DASHBOARD") -> int:
    """Own the main thread with Tk; the server loop moves to a worker."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    # Launched via pythonw there is no console to print a traceback to, so an
    # unhandled error would just make the window vanish. Write it down and say
    # so instead.
    def report(exc_type, exc, tb):
        import traceback
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            log = cfgmod.HERE.parent / "gamewalk-error.log"
            log.write_text(
                time.strftime("%Y-%m-%d %H:%M:%S\n") + text, encoding="utf-8")
        except Exception:
            log = "(could not be written)"
        try:
            messagebox.showerror(
                "GameWalk stopped",
                f"{exc_type.__name__}: {exc}\n\nWritten to:\n{log}")
        except Exception:
            pass

    sys.excepthook = report

    root = tk.Tk()
    root._hotkey = hotkey
    root.report_callback_exception = lambda *a: report(*a)
    app = App(root, server, dry_run)
    app.show_page(page)
    App._ip = discovery.lan_ip()

    def on_tick(srv: Server, now: float) -> None:
        # Config edits run here, on the server's own thread, so a slider can
        # never mutate config halfway through the loop reading it.
        while True:
            try:
                action = app.actions.get_nowait()
            except queue.Empty:
                break
            try:
                action(srv)
            except Exception as exc:
                srv.note(f"action failed: {exc}")

    thread = threading.Thread(
        target=lambda: server.serve_forever(on_tick=on_tick),
        daemon=True, name="gw-server")
    thread.start()

    def on_close():
        app.test.stop()
        server.running = False
        root.after(120, root.destroy)

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()
    server.running = False
    return 0
