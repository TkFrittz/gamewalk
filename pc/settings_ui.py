"""The settings page.

Split out of gui.py once every phone setting moved onto it. Keeping settings
in two places -- some here, some only reachable on the phone -- meant having to
remember which was which, so the phone-only ones are sent as commands and the
whole surface lives on one screen.

Config edits are queued onto the server thread by the host class rather than
applied here, so a slider can never mutate config halfway through the loop
reading it.
"""

from __future__ import annotations

import tkinter as tk

from . import protocol
from .theme import (
    AMBER, BG, NEON_BLUE, NEON_GREEN, PANEL, PANEL_HI, TEXT, TEXT_DIM,
    TIER_COLORS, font, mix,
)
from .widgets import KeyPicker, NeonButton, ScrollColumn, Slider, card


class SettingsMixin:
    """Everything under the SETTINGS tab. Mixed into App."""

    # --- layout -------------------------------------------------------------

    def _build_settings(self, parent):
        self.scroll = ScrollColumn(parent, bg=BG)
        self.scroll.pack(fill="both", expand=True)
        host = self.scroll.body

        tiers = card(host, "Speed tiers")
        tiers.master.pack(fill="x", pady=(0, 12))
        tk.Label(tiers, text="Keys held at each pace. Drop-below must stay under "
                             "enter-above, or the tier flickers when your "
                             "cadence sits on the line.",
                 bg=PANEL, fg=TEXT_DIM, font=font(9), wraplength=760,
                 justify="left").pack(anchor="w", padx=14, pady=(6, 6))
        self.tier_host = tk.Frame(tiers, bg=PANEL)
        self.tier_host.pack(fill="x", padx=14)
        add = NeonButton(tiers, "+  ADD A FASTER TIER", self.add_tier,
                         accent=NEON_BLUE, width=200, height=34, small=True)
        add.set_background(PANEL)
        add.pack(anchor="w", padx=14, pady=(8, 12))

        self.feel_host = self._section(host, "Feel")
        self.phone_host = self._section(host, "Phone")
        self.sens_host = self._section(host, "Sensitivity  (mode B only)")
        self.safety_host = self._section(host, "Safety")

        self._build_feel()
        self._build_phone()
        self._build_sensitivity()
        self._build_safety()

    def _section(self, host, title: str) -> tk.Frame:
        c = card(host, title)
        c.master.pack(fill="x", pady=(0, 12))
        inner = tk.Frame(c, bg=PANEL)
        inner.pack(fill="x", padx=14, pady=(8, 12))
        return inner

    # --- tiers --------------------------------------------------------------

    def _refresh_tiers(self):
        tiers = self.server.cfg.profile.tiers
        sig = repr([(t.name, t.keys, t.enter_spm, t.exit_spm) for t in tiers])
        if sig == self._tier_rows_signature:
            return
        self._tier_rows_signature = sig

        for child in self.tier_host.winfo_children():
            child.destroy()

        for i, t in enumerate(tiers):
            col = TIER_COLORS[i % len(TIER_COLORS)]
            row = tk.Frame(self.tier_host, bg=PANEL_HI)
            row.pack(fill="x", pady=4)

            head = tk.Frame(row, bg=PANEL_HI)
            head.pack(fill="x", padx=12, pady=(9, 4))
            tk.Label(head, text=t.name.upper(), bg=PANEL_HI, fg=col,
                     font=font(11, bold=True)).pack(side="left")

            if i > 0:
                rm = NeonButton(head, "REMOVE",
                                (lambda idx: lambda: self.remove_tier(idx))(i),
                                accent=TEXT_DIM, width=78, height=26, small=True)
                rm.set_background(PANEL_HI)
                rm.pack(side="right", padx=(6, 0))

            keys = NeonButton(
                head, " + ".join(k.upper() for k in t.keys) or "none",
                (lambda idx: lambda: self.pick_keys(idx))(i),
                accent=col, width=150, height=26, small=True)
            keys.set_background(PANEL_HI)
            keys.pack(side="right")

            if i > 0:
                Slider(row, "Enter above", t.enter_spm, 0, 260, 5,
                       lambda v: f"{v:.0f} spm",
                       (lambda idx: lambda v: self.set_tier(idx, "enter_spm", v))(i),
                       accent=col, bg=PANEL_HI).pack(fill="x", padx=12)
                Slider(row, "Drop below", t.exit_spm, 0, 260, 5,
                       lambda v: f"{v:.0f} spm",
                       (lambda idx: lambda v: self.set_tier(idx, "exit_spm", v))(i),
                       accent=mix(col, PANEL, 0.35), bg=PANEL_HI
                       ).pack(fill="x", padx=12, pady=(2, 0))
                tk.Frame(row, bg=PANEL_HI, height=10).pack()
            else:
                tk.Label(row, text="Base tier - always active while walking",
                         bg=PANEL_HI, fg=TEXT_DIM, font=font(8)).pack(
                    anchor="w", padx=12, pady=(0, 10))

        if hasattr(self, "scroll"):
            self.scroll.bind_wheel_deep(self.tier_host)

    def pick_keys(self, index: int):
        from .keys import valid_names
        tier = self.server.cfg.profile.tiers[index]
        KeyPicker(self.root, f"Keys for {tier.name}", valid_names(),
                  list(tier.keys),
                  lambda picked: self.set_tier(index, "keys", picked))

    # --- feel ---------------------------------------------------------------

    def _build_feel(self):
        host = self.feel_host
        for child in host.winfo_children():
            child.destroy()
        p = self.server.cfg.profile

        mode_row = tk.Frame(host, bg=PANEL)
        mode_row.pack(fill="x", pady=(0, 12))
        tk.Label(mode_row, text="Hold mode", bg=PANEL, fg=TEXT,
                 font=font(10)).pack(side="left")
        for name in ("fixed", "adaptive"):
            b = NeonButton(mode_row, name.upper(),
                           (lambda n: lambda: self.set_hold("mode", n))(name),
                           accent=NEON_GREEN if name == p.hold.mode else NEON_BLUE,
                           width=112, height=30, small=True,
                           primary=(name == p.hold.mode))
            b.set_background(PANEL)
            b.pack(side="right", padx=(6, 0))

        if p.hold.mode == "adaptive":
            self._slider(host, "Hold multiplier", p.hold.multiplier, 1.0, 3.0,
                         0.05, lambda v: f"{v:.2f}x",
                         lambda v: self.set_hold("multiplier", v),
                         "How long a key stays held, as a multiple of your step "
                         "interval. Below about 1.3 it stutters; higher coasts "
                         "longer after you stop.")
            self._slider(host, "Hold floor", p.hold.min_ms, 100, 1000, 50,
                         lambda v: f"{v:.0f} ms",
                         lambda v: self.set_hold("min_ms", int(v)),
                         "Never hold for less than this, however fast you go.",
                         accent=NEON_BLUE)
            self._slider(host, "Hold ceiling", p.hold.max_ms, 500, 3000, 50,
                         lambda v: f"{v:.0f} ms",
                         lambda v: self.set_hold("max_ms", int(v)),
                         "And never longer than this, however slowly.",
                         accent=NEON_BLUE, last=True)
        else:
            self._slider(host, "Fixed hold", p.hold.fixed_ms, 100, 3000, 50,
                         lambda v: f"{v:.0f} ms",
                         lambda v: self.set_hold("fixed_ms", int(v)),
                         "Must be longer than the gap between your steps, or "
                         "the key releases between them.", last=True)

        self._slider(host, "Steps before moving", self.server.cfg.start_steps,
                     1, 5, 1, lambda v: f"{v:.0f}",
                     lambda v: self.set_root("start_steps", int(v)),
                     "2 stops a single jolt in your pocket from walking you off "
                     "a ledge.", accent=NEON_BLUE, last=True)

    # --- phone --------------------------------------------------------------

    def _build_phone(self):
        host = self.phone_host
        for child in host.winfo_children():
            child.destroy()

        phone = self._current_phone()
        mode = phone.mode if phone else self._last_phone_mode

        tk.Label(host, text="Which sensor the phone uses. Applies immediately - "
                            "you don't have to touch the phone.",
                 bg=PANEL, fg=TEXT_DIM, font=font(9), wraplength=760,
                 justify="left").pack(anchor="w", pady=(0, 8))

        row = tk.Frame(host, bg=PANEL)
        row.pack(fill="x")
        for code, title in (("A", "A   step detector"),
                            ("B", "B   accelerometer")):
            b = NeonButton(row, title,
                           (lambda c: lambda: self.set_phone_mode(c))(code),
                           accent=NEON_GREEN if code == mode else NEON_BLUE,
                           width=200, height=38, primary=(code == mode))
            b.set_background(PANEL)
            b.pack(side="left", padx=(0, 8))

        tk.Label(host, text="A is best for battery and latency. Switch to B if "
                            "walking in place doesn't register - detection then "
                            "happens here on the PC instead of on the phone.",
                 bg=PANEL, fg=TEXT_DIM, font=font(8), wraplength=760,
                 justify="left").pack(anchor="w", pady=(8, 0))
        if not phone:
            tk.Label(host, text="No phone connected - this applies when one is.",
                     bg=PANEL, fg=AMBER, font=font(8)).pack(anchor="w", pady=(6, 0))

        self._slider(host, "Sample rate (mode B)", self._acc_hz, 20, 100, 5,
                     lambda v: f"{v:.0f} Hz", self.set_acc_hz,
                     "Higher spots a step slightly sooner and costs battery. "
                     "50Hz is plenty: a footfall spike is about 100ms wide.",
                     accent=NEON_BLUE, last=True, top=12)

    # --- sensitivity --------------------------------------------------------

    def _build_sensitivity(self):
        host = self.sens_host
        for child in host.winfo_children():
            child.destroy()
        d = self.server.cfg.profile.detector

        tk.Label(host, text="Used only in mode B, where this PC finds the steps "
                            "in raw motion data.",
                 bg=PANEL, fg=TEXT_DIM, font=font(9), wraplength=760,
                 justify="left").pack(anchor="w", pady=(0, 10))

        rows = (
            ("Threshold factor", "threshold_factor", 0.8, 3.0, 0.05,
             lambda v: f"{v:.2f}",
             "Multiple of recent motion energy. Lower is more sensitive."),
            ("Noise floor", "threshold_floor", 0.2, 4.0, 0.1,
             lambda v: f"{v:.1f}",
             "Never drop below this, so it can't find steps in sensor noise "
             "while you stand still."),
            ("Minimum step gap", "refractory_ms", 80, 400, 10,
             lambda v: f"{v:.0f} ms",
             "One footfall rings afterwards; this stops the ring counting as "
             "more steps."),
            ("Re-arm ratio", "rearm_ratio", 0.1, 0.95, 0.05,
             lambda v: f"{v:.2f}",
             "The signal must fall this far under the threshold before another "
             "step can register."),
            ("Smoothing", "smooth_tau_ms", 10, 200, 5,
             lambda v: f"{v:.0f} ms",
             "Higher is steadier but slower to react."),
        )
        for i, (label, field, lo, hi, step, fmt, help_text) in enumerate(rows):
            self._slider(host, label, getattr(d, field), lo, hi, step, fmt,
                         (lambda f: lambda v: self.set_detector(f, v))(field),
                         help_text, accent=NEON_BLUE,
                         last=(i == len(rows) - 1))

    # --- safety -------------------------------------------------------------

    def _build_safety(self):
        host = self.safety_host
        for child in host.winfo_children():
            child.destroy()

        row = tk.Frame(host, bg=PANEL)
        row.pack(fill="x")
        tk.Label(row, text="Panic key", bg=PANEL, fg=TEXT,
                 font=font(10)).pack(side="left")
        tk.Label(row, text="releases everything and disarms", bg=PANEL,
                 fg=TEXT_DIM, font=font(8)).pack(side="left", padx=(8, 0))

        current = self.server.cfg.panic_hotkey.upper()
        for key in ("F10", "F9", "F8", "F7"):
            b = NeonButton(
                row, key,
                (lambda k: lambda: self.set_root("panic_hotkey", k.lower()))(key),
                accent=NEON_GREEN if key == current else NEON_BLUE,
                width=54, height=30, small=True, primary=(key == current))
            b.set_background(PANEL)
            b.pack(side="right", padx=(4, 0))

        tk.Label(host, text="A new panic key takes effect next time this app starts.",
                 bg=PANEL, fg=TEXT_DIM, font=font(8)).pack(anchor="w", pady=(6, 12))

        self._slider(host, "Dead-man timeout", self.server.cfg.deadman_ms,
                     400, 3000, 100, lambda v: f"{v:.0f} ms",
                     lambda v: self.set_root("deadman_ms", int(v)),
                     "Release everything if the phone goes quiet this long. It "
                     "heartbeats 4x a second, so this is really about lost Wi-Fi.",
                     accent=NEON_BLUE, last=True)

    # --- helpers ------------------------------------------------------------

    def _slider(self, host, label, value, lo, hi, step, fmt, command,
                help_text="", accent=NEON_GREEN, last=False, top=0):
        s = Slider(host, label, value, lo, hi, step, fmt, command,
                   accent=accent, help_text=help_text, bg=host["bg"])
        s.pack(fill="x", pady=(top, 0 if last else 14))
        return s

    # --- edits --------------------------------------------------------------

    def set_hold(self, field: str, value):
        self._patch({"profiles": {self.server.cfg.active_profile:
                                  {"hold": {field: value}}}})
        if field == "mode":
            self.root.after(120, self._build_feel)

    def set_detector(self, field: str, value: float):
        self._patch({"profiles": {self.server.cfg.active_profile:
                                  {"detector": {field: value}}}})

    def set_root(self, field: str, value):
        self._patch({field: value})
        if field == "panic_hotkey":
            self.root.after(120, self._build_safety)

    def _tier_dicts(self):
        return [{"name": t.name, "keys": list(t.keys),
                 "enter_spm": t.enter_spm, "exit_spm": t.exit_spm}
                for t in self.server.cfg.profile.tiers]

    def set_tier(self, index: int, field: str, value):
        # Send every field of every tier. The list replaces wholesale, and a
        # partial element would be filled in from defaults -- quietly renaming
        # the tier and zeroing its thresholds.
        tiers = self._tier_dicts()
        tiers[index][field] = value
        self._patch({"profiles": {self.server.cfg.active_profile:
                                  {"tiers": tiers}}})

    def add_tier(self):
        tiers = self._tier_dicts()
        top = tiers[-1]["enter_spm"] if len(tiers) > 1 else 120.0
        tiers.append({
            "name": f"tier{len(tiers)}",
            "keys": ["shift", "w"],
            # Above the current top tier, with a hysteresis gap, so it
            # validates on arrival rather than being rejected.
            "enter_spm": top + 40,
            "exit_spm": top + 25,
        })
        self._patch({"profiles": {self.server.cfg.active_profile:
                                  {"tiers": tiers}}})

    def remove_tier(self, index: int):
        tiers = self._tier_dicts()
        if len(tiers) <= 1:
            return
        del tiers[index]
        self._patch({"profiles": {self.server.cfg.active_profile:
                                  {"tiers": tiers}}})

    # --- phone commands -----------------------------------------------------

    def set_phone_mode(self, mode: str):
        self._last_phone_mode = mode
        self._command("mode", mode)
        self.root.after(120, self._build_phone)

    def set_acc_hz(self, hz: float):
        self._acc_hz = int(hz)
        self._command("acc_hz", int(hz))

    def _command(self, name: str, value):
        def send(s):
            phone = None
            now_ms = __import__("time").monotonic() * 1000
            for cl in s.clients.values():
                if now_ms - cl.last_seen < 8000 and cl.device != "self_test":
                    phone = cl
            if phone:
                s.send(phone.addr, protocol.CMD, name, value)
                s.note(f"asked {phone.device} to set {name} = {value}")
            else:
                s.note(f"no phone connected; {name} will apply when one is")
        self.actions.put(send)
