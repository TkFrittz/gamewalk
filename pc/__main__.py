"""Entry point:  python -m pc  [--dry-run] [--pair] [--verbose]"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import config as cfgmod
from . import discovery
from .config import ConfigError
from .hotkey import PanicHotkey
from .keys import IS_WINDOWS, make_sink
from .server import Server

BAR_W = 28


def _bar(spm: float, top: float = 200.0) -> str:
    filled = max(0, min(BAR_W, round(BAR_W * spm / top)))
    return "#" * filled + "-" * (BAR_W - filled)


def render(server: Server, pairing_pin: str) -> str:
    st = server.machine.status
    if not st.armed:
        state = "disarmed"
    elif st.moving:
        state = st.tier.upper()
    else:
        state = "idle"

    held = "+".join(st.held) if st.held else "-"
    line = (
        f"\r {state:<9} [{_bar(st.spm)}] {st.spm:5.1f} spm   "
        f"keys {held:<12} steps {st.steps:<6}"
    )
    if pairing_pin:
        line += f"  PAIR PIN {pairing_pin}"
    return line[:118].ljust(118)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="pc", description="GameWalk PC helper")
    ap.add_argument("--dry-run", action="store_true",
                    help="log keystrokes instead of injecting them")
    ap.add_argument("--pair", action="store_true",
                    help="open a pairing window at startup")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="log every event instead of a status line")
    ap.add_argument("--record", metavar="FILE", type=Path,
                    help="write incoming Mode B samples to a trace file")
    ap.add_argument("--config", metavar="FILE", type=Path,
                    default=cfgmod.CONFIG_PATH)
    ap.add_argument("--gui", action="store_true",
                    help="open the desktop app instead of the console view")
    ap.add_argument("--page", default="DASHBOARD",
                    choices=["DASHBOARD", "SETTINGS", "ACTIVITY"],
                    help="which tab the window opens on")
    ap.add_argument("--selftest", action="store_true",
                    help="validate config and exit")
    args = ap.parse_args(argv)

    # Python block-buffers stdout when it isn't a terminal, so piping the
    # helper to a log file would show nothing until 8KB had accumulated --
    # exactly the situation where you're watching a log to find out why
    # something isn't working.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass

    try:
        cfg = cfgmod.load(args.config)
    except ConfigError as exc:
        print(f"config is invalid: {exc}", file=sys.stderr)
        print(f"delete {args.config} to start from the shipped default.",
              file=sys.stderr)
        return 2

    if not cfg.token:
        cfg.token = discovery.new_token()
        cfgmod.save(cfg, args.config)

    if args.selftest:
        p = cfg.profile
        print(f"config OK: profile {cfg.active_profile!r}, "
              f"{len(p.tiers)} tiers, hold={p.hold.mode}")
        for t in p.tiers:
            print(f"  {t.name:<8} {'+'.join(t.keys):<14} "
                  f"enter {t.enter_spm:g} / exit {t.exit_spm:g}")
        return 0

    dry = args.dry_run or not IS_WINDOWS
    if not IS_WINDOWS and not args.dry_run:
        print("not on Windows: forcing --dry-run (no key injection available)\n")

    sink = make_sink(dry_run=dry, echo=args.verbose)
    try:
        server = Server(cfg=cfg, sink=sink, config_path=args.config,
                        dry_run=dry, verbose=args.verbose,
                        record_path=args.record)
    except OSError as exc:
        print(f"could not listen on port {cfg.port}: {exc}", file=sys.stderr)
        print("Another copy of the helper is probably already running. "
              "Close it, or set a different \"port\" in config.json.",
              file=sys.stderr)
        return 3

    hotkey = PanicHotkey(cfg.panic_hotkey, server.panic)
    hotkey.start()
    time.sleep(0.05)  # let it register so the banner reports the truth

    ip = discovery.lan_ip()

    if args.gui:
        # Everything below this point is the console view. The window carries
        # the same information, so printing it too would only add noise behind
        # a UI nobody is meant to look past.
        from .gui import run as run_gui
        if cfg.require_pin:
            server.responder.pairing.open()
        print(f"GameWalk - window open on {ip}:{cfg.port}")
        return run_gui(server, dry_run=dry, hotkey=hotkey, page=args.page)

    print("GameWalk helper")
    print(f"  listening   {ip}:{cfg.port}  (discovery {cfg.discovery_port})")
    print(f"  profile     {cfg.active_profile}  "
          f"({', '.join(t.name for t in cfg.profile.tiers)})")
    if dry:
        print("  keys        DRY RUN - nothing is actually pressed")
    elif hotkey.ok:
        print(f"  panic key   {cfg.panic_hotkey.upper()}")
    if hotkey.error and not dry:
        print(f"  panic key   unavailable: {hotkey.error}")

    pin = ""
    if args.pair or cfg.require_pin:
        pin = server.responder.pairing.open()
        print(f"  pairing     PIN {pin}  (valid {int(discovery.PAIR_WINDOW_S)}s)")
    print("\n  Ctrl-C to quit\n")

    last_log = 0
    last_paint = 0.0

    def on_tick(srv: Server, now: float) -> None:
        nonlocal pin, last_log, last_paint
        if pin and not srv.responder.pairing.is_open:
            pin = ""
        if args.verbose:
            return
        # Print new log lines above the status line so neither is lost.
        if len(srv.log) > last_log:
            for line in srv.log[last_log:]:
                print("\r" + line.ljust(118))
            last_log = len(srv.log)
        # The main loop ticks every 10ms, and repainting the status line
        # that often flickers visibly and writes ~100 lines a second into
        # any log you redirect it to. 10Hz is smooth to read and cheap.
        if now - last_paint >= 0.1:
            last_paint = now
            sys.stdout.write(render(srv, pin))
            sys.stdout.flush()

    try:
        server.serve_forever(on_tick=on_tick)
    except KeyboardInterrupt:
        pass
    finally:
        hotkey.stop()
        print("\nstopped, all keys released.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
