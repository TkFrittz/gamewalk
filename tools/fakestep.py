"""Pretend to be a phone walking, so the PC half can be tuned with no phone.

This is the go/no-go test for the whole project: if synthetic steps don't move
your character, SendInput scancodes aren't reaching the game, and that is worth
knowing before any Android work exists.

    python tools/fakestep.py --spm 120           steady walk
    python tools/fakestep.py --ramp 80:180       accelerate, then back down
    python tools/fakestep.py --spm 150 --stutter drop the odd step
"""

from __future__ import annotations

import argparse
import random
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pc import protocol  # noqa: E402
from pc import config as cfgmod  # noqa: E402

HB_INTERVAL = 0.25


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Synthetic step generator")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=0, help="default: from config")
    ap.add_argument("--token", default="", help="default: from config")
    ap.add_argument("--spm", type=float, default=120.0)
    ap.add_argument("--ramp", metavar="LO:HI",
                    help="sweep LO->HI->LO over --period seconds")
    ap.add_argument("--period", type=float, default=30.0)
    ap.add_argument("--jitter", type=float, default=0.04,
                    help="fractional timing noise; real gait is never exact")
    ap.add_argument("--stutter", action="store_true",
                    help="randomly drop 5%% of steps, like real packet loss")
    ap.add_argument("--duration", type=float, default=0.0, help="0 = forever")
    ap.add_argument("--no-stop", action="store_true",
                    help="exit without sending STOP, as if the phone dropped "
                         "off Wi-Fi; exercises the dead-man switch")
    args = ap.parse_args(argv)

    cfg = cfgmod.load()
    port = args.port or cfg.port
    token = args.token or cfg.token

    lo = hi = args.spm
    if args.ramp:
        lo, hi = (float(v) for v in args.ramp.split(":"))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    dest = (args.host, port)
    seq = 0

    def send(verb: str, *a: object) -> None:
        nonlocal seq
        seq += 1
        sock.sendto(protocol.build(token, verb, seq, *a), dest)

    send(protocol.HELLO, "fakestep", "A", "0.1.0")
    print(f"sending steps to {args.host}:{port}")
    print(f"  cadence  {lo:g}..{hi:g} spm" if args.ramp else f"  cadence  {lo:g} spm")
    print("  Ctrl-C to stop\n")

    start = time.monotonic()
    next_step = start
    next_hb = start
    steps = 0

    try:
        while True:
            now = time.monotonic()
            if args.duration and now - start >= args.duration:
                break

            if now >= next_hb:
                send(protocol.HB)
                next_hb = now + HB_INTERVAL

            if now >= next_step:
                if not (args.stutter and random.random() < 0.05):
                    send(protocol.STEP, round(now * 1000))
                    steps += 1

                if args.ramp:
                    # Triangle wave: up then back down, so hysteresis gets
                    # exercised in both directions.
                    phase = ((now - start) % args.period) / args.period
                    tri = 2 * phase if phase < 0.5 else 2 * (1 - phase)
                    spm = lo + (hi - lo) * tri
                else:
                    spm = args.spm

                interval = 60.0 / max(1.0, spm)
                interval *= 1.0 + random.uniform(-args.jitter, args.jitter)
                next_step = now + interval

                print(f"\r  {spm:6.1f} spm   {steps:5d} steps", end="", flush=True)

            time.sleep(0.002)
    except KeyboardInterrupt:
        pass
    finally:
        if args.no_stop:
            print("\nexiting WITHOUT sending STOP -- the helper should notice "
                  "the silence and release everything")
        else:
            send(protocol.STOP)
            print("\nsent STOP")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
