"""The helper: UDP listener, config service, and the main loop.

Single-threaded around select(), apart from the hotkey's message loop. There
is no shared mutable state to race over, and a step arriving while a config
patch is half-applied is exactly the kind of bug that would be miserable to
reproduce.
"""

from __future__ import annotations

import json
import sys
import socket
import time
from dataclasses import dataclass, field
from pathlib import Path
from select import select

from . import config as cfgmod
from . import discovery, protocol
from .config import Config, ConfigError
from .detector import StepDetector
from .keys import Held, Sink
from .machine import Machine

TICK_S = 0.010          # 10 ms: fine enough that hold expiry isn't visibly late
STAT_MIN_INTERVAL_S = 0.05


@dataclass
class Client:
    addr: tuple[str, int]
    device: str = "?"
    mode: str = "?"
    version: str = "?"
    last_seen: float = 0.0
    sub_hz: float = 0.0
    last_stat: float = 0.0


@dataclass
class Server:
    cfg: Config
    sink: Sink
    config_path: Path = cfgmod.CONFIG_PATH
    dry_run: bool = False
    verbose: bool = False
    record_path: Path | None = None

    machine: Machine = field(init=False)
    detector: StepDetector = field(init=False)
    clients: dict[tuple[str, int], Client] = field(default_factory=dict)
    log: list[str] = field(default_factory=list)
    running: bool = True
    _seq: int = 0
    _mtime: float = 0.0
    _recorder: object = None

    def __post_init__(self) -> None:
        self.machine = Machine(cfg=self.cfg, held=Held(self.sink))
        self.detector = StepDetector(cfg=self.cfg.profile.detector)

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # No SO_REUSEADDR on Windows: for UDP it lets a *second* helper bind
        # the same port, after which the OS quietly hands each datagram to
        # only one of them. A forgotten helper from an earlier session then
        # eats the phone's packets while the new one shows an idle screen --
        # baffling to debug. Better that the second start fails loudly.
        if sys.platform != "win32":
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("", self.cfg.port))
        self.sock.setblocking(False)

        self.responder = discovery.DiscoveryResponder(
            self.cfg.discovery_port, self.cfg.port,
            self.cfg.token, self.cfg.require_pin,
        )
        if self.config_path.exists():
            self._mtime = self.config_path.stat().st_mtime
        if self.record_path:
            self._recorder = self.record_path.open("w", encoding="utf-8")

    # --- logging ------------------------------------------------------------

    def note(self, line: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log.append(f"{stamp}  {line}")
        del self.log[:-200]
        if self.verbose:
            print(f"{stamp}  {line}", flush=True)

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def send(self, addr: tuple[str, int], verb: str, *args: object) -> None:
        try:
            self.sock.sendto(
                protocol.build(self.cfg.token, verb, self._next_seq(), *args), addr)
        except OSError as exc:
            self.note(f"send to {addr[0]} failed: {exc}")

    # --- packet handling ----------------------------------------------------

    def handle_packet(self, data: bytes, addr: tuple[str, int], now_ms: float) -> None:
        pkt = protocol.parse(data)
        if pkt is None:
            return

        if self.cfg.token and pkt.token != self.cfg.token:
            # Rate-limited: a misconfigured device would otherwise fill the log.
            if not self.log or "wrong token" not in self.log[-1]:
                self.note(f"dropped packet from {addr[0]}: wrong token")
            return

        client = self.clients.get(addr)
        if client is None:
            client = self.clients[addr] = Client(addr=addr)
        client.last_seen = now_ms

        self.machine.on_packet(now_ms)
        verb = pkt.verb

        if verb == protocol.STEP:
            # arg 0 is the phone's own clock for when the step happened.
            # Cadence comes from that, not from when this datagram landed.
            self.machine.on_step(now_ms, pkt.farg(0, 0.0) or None)

        elif verb == protocol.ACC:
            t = pkt.farg(0, now_ms)
            x, y, z = pkt.farg(1), pkt.farg(2), pkt.farg(3)
            if self._recorder:
                self._recorder.write(
                    json.dumps({"t": t, "a": [x, y, z]}) + "\n")
            if self.detector.feed(t, x, y, z):
                self.machine.on_step(now_ms, t)
            self.machine.status.signal = self.detector.smoothed
            self.machine.status.threshold = self.detector.threshold

        elif verb == protocol.HELLO:
            client.device = pkt.arg(0, "?")
            client.mode = pkt.arg(1, "?")
            client.version = pkt.arg(2, "?")
            self.machine.arm(now_ms)
            self.detector.reset()
            self.note(
                f"connected: {client.device} ({addr[0]}) mode {client.mode} "
                f"v{client.version}")

        elif verb == protocol.STOP:
            self.note(f"stopped by {client.device}")
            self.machine.disarm()

        elif verb == protocol.HB:
            if not self.machine.status.armed:
                # Recover from a helper restart without needing the user to
                # toggle the app off and on.
                self.machine.arm(now_ms)
                self.note(f"re-armed from heartbeat ({addr[0]})")

        elif verb == protocol.CFG_GET:
            self.send_config(addr)

        elif verb == protocol.CFG_SET:
            self.apply_patch(addr, pkt.arg(0, "{}"))

        elif verb == protocol.SUB:
            client.sub_hz = max(1.0, min(20.0, pkt.farg(0, 5.0)))

        elif verb == protocol.UNSUB:
            client.sub_hz = 0.0

        elif verb == protocol.PING:
            self.send(addr, protocol.PONG, pkt.seq)

    # --- config service -----------------------------------------------------

    def send_config(self, addr: tuple[str, int]) -> None:
        blob = json.dumps(cfgmod.wire_dict(self.cfg), separators=(",", ":"))
        self.send(addr, protocol.CFG_FULL, self.cfg.version, blob)

    def apply_patch(self, addr: tuple[str, int], raw: str) -> None:
        try:
            patch = json.loads(raw)
            if not isinstance(patch, dict):
                raise ConfigError("patch must be an object")
            merged = cfgmod.deep_merge(cfgmod.to_dict(self.cfg), patch)
            merged["version"] = self.cfg.version + 1
            new_cfg = cfgmod.validate(merged)
        except (ValueError, ConfigError) as exc:
            # Rejected patches leave the running config untouched, so a bad
            # slider drag can't take the helper down mid-session.
            self.note(f"config rejected: {exc}")
            self.send(addr, protocol.CFG_ERR, str(exc).replace(" ", "_"))
            return

        self.adopt(new_cfg, save=True)
        self.send(addr, protocol.CFG_OK, new_cfg.version)
        self.note(f"config updated to v{new_cfg.version} by {addr[0]}")
        self.broadcast_config(exclude=addr)

    def apply_patch_local(self, patch: dict) -> str | None:
        """Apply a config patch from the GUI. Returns an error, or None.

        Same validation path as a patch from the phone -- a rejected edit
        leaves the running config untouched -- but the caller is in-process,
        so the reason comes back directly instead of over the wire.
        """
        try:
            merged = cfgmod.deep_merge(cfgmod.to_dict(self.cfg), patch)
            merged["version"] = self.cfg.version + 1
            new_cfg = cfgmod.validate(merged)
        except (ValueError, ConfigError) as exc:
            self.note(f"config rejected: {exc}")
            return str(exc)
        self.adopt(new_cfg, save=True)
        self.note(f"config updated to v{new_cfg.version}")
        self.broadcast_config()
        return None

    def set_dry_run(self, dry: bool) -> None:
        """Swap the key sink at runtime.

        Releases anything currently held first: flipping the switch while W is
        down would otherwise strand that key on the old sink with nothing left
        holding a reference to release it.
        """
        from .keys import Held, make_sink
        if dry == self.dry_run:
            return
        self.machine.held.release_all()
        self.dry_run = dry
        self.sink = make_sink(dry_run=dry, echo=self.verbose)
        self.machine.held = Held(self.sink)
        self.note("dry run ON - keys are not pressed" if dry
                  else "dry run OFF - keys are live")

    def adopt(self, new_cfg: Config, save: bool) -> None:
        new_cfg.token = self.cfg.token or new_cfg.token
        self.cfg = new_cfg
        self.machine.replace_config(new_cfg)
        self.detector.cfg = new_cfg.profile.detector
        self.responder.require_pin = new_cfg.require_pin
        if save:
            cfgmod.save(new_cfg, self.config_path)
            self._mtime = self.config_path.stat().st_mtime

    def broadcast_config(self, exclude: tuple[str, int] | None = None) -> None:
        for addr in self.clients:
            if addr != exclude:
                self.send_config(addr)

    def check_file_reload(self) -> None:
        """Hand edits to config.json push out to the app too -- live updating
        runs in both directions, so the JSON stays a first-class way to work."""
        try:
            mtime = self.config_path.stat().st_mtime
        except OSError:
            return
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
            raw["version"] = self.cfg.version + 1
            new_cfg = cfgmod.validate(raw)
        except (OSError, ValueError, ConfigError) as exc:
            self.note(f"config.json is invalid, keeping the old one: {exc}")
            return
        self.adopt(new_cfg, save=False)
        self.note(f"config.json reloaded (v{new_cfg.version})")
        self.broadcast_config()

    # --- telemetry ----------------------------------------------------------

    def push_stats(self, now: float) -> None:
        st = self.machine.status
        for client in self.clients.values():
            if client.sub_hz <= 0:
                continue
            interval = max(STAT_MIN_INTERVAL_S, 1.0 / client.sub_hz)
            if now - client.last_stat < interval:
                continue
            client.last_stat = now
            self.send(
                client.addr, protocol.STAT,
                f"{st.spm:.1f}", st.tier, f"{st.signal:.3f}",
                f"{st.threshold:.3f}", int(st.armed),
            )

    # --- panic --------------------------------------------------------------

    def panic(self) -> None:
        self.machine.disarm()
        self.note("PANIC: all keys released, disarmed. Press again to re-arm.")

    # --- main loop ----------------------------------------------------------

    def serve_forever(self, on_tick=None) -> None:
        last_reload = 0.0
        try:
            while self.running:
                readable, _, _ = select(
                    [self.sock, self.responder.sock], [], [], TICK_S)
                now = time.monotonic()
                now_ms = now * 1000.0

                for sock in readable:
                    if sock is self.sock:
                        try:
                            data, addr = self.sock.recvfrom(2048)
                        except (BlockingIOError, ConnectionResetError):
                            continue
                        self.handle_packet(data, addr, now_ms)
                    else:
                        if (line := self.responder.handle()):
                            self.note(line)

                self.machine.tick(now_ms)
                self.push_stats(now)

                if now - last_reload > 1.0:
                    last_reload = now
                    self.check_file_reload()
                    self._prune(now_ms)

                if on_tick:
                    on_tick(self, now)
        finally:
            self.shutdown()

    def _prune(self, now_ms: float) -> None:
        stale = [a for a, c in self.clients.items() if now_ms - c.last_seen > 30_000]
        for addr in stale:
            del self.clients[addr]

    def shutdown(self) -> None:
        self.machine.disarm()
        self.sock.close()
        self.responder.close()
        if self._recorder:
            self._recorder.close()
