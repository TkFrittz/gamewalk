"""Replay a recorded accelerometer trace through the Mode B detector.

Record once (`python -m pc --record walk.jsonl`), then tune sitting down,
repeatably, against the same walk -- instead of standing up and marching every
time a constant changes.

    python tools/replay.py walk.jsonl
    python tools/replay.py walk.jsonl --factor 1.2 --floor 0.9
    python tools/replay.py walk.jsonl --sweep factor:1.0:2.0:0.1
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pc import config as cfgmod  # noqa: E402
from pc.detector import StepDetector  # noqa: E402


def load_trace(path: Path) -> list[tuple[float, float, float, float]]:
    samples = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            t, (x, y, z) = row["t"], row["a"]
            samples.append((float(t), float(x), float(y), float(z)))
    return samples


def run(samples, det_cfg) -> list[float]:
    det = StepDetector(cfg=det_cfg)
    return [t for (t, x, y, z) in samples if det.feed(t, x, y, z)]


def summarize(steps: list[float], span_s: float) -> str:
    if len(steps) < 2:
        return f"{len(steps)} steps -- nothing to measure"
    gaps = [b - a for a, b in zip(steps, steps[1:])]
    median = statistics.median(gaps)
    spm = 60000.0 / median if median else 0.0
    spread = statistics.pstdev(gaps) if len(gaps) > 1 else 0.0
    return (f"{len(steps):4d} steps   {spm:6.1f} spm median   "
            f"gap {median:6.1f} +/- {spread:5.1f} ms   "
            f"({len(steps) / span_s * 60:.0f}/min overall)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Replay a trace through the detector")
    ap.add_argument("trace", type=Path)
    ap.add_argument("--factor", type=float)
    ap.add_argument("--floor", type=float)
    ap.add_argument("--refractory", type=float)
    ap.add_argument("--smooth", type=float)
    ap.add_argument("--sweep", metavar="NAME:LO:HI:STEP",
                    help="try a range of one parameter and compare")
    ap.add_argument("--verbose", "-v", action="store_true",
                    help="print each detected step")
    args = ap.parse_args(argv)

    samples = load_trace(args.trace)
    if len(samples) < 10:
        print("trace too short", file=sys.stderr)
        return 1
    span_s = (samples[-1][0] - samples[0][0]) / 1000.0
    rate = len(samples) / span_s if span_s else 0.0
    print(f"{args.trace.name}: {len(samples)} samples, "
          f"{span_s:.1f}s, {rate:.0f} Hz\n")

    base = cfgmod.load().profile.detector
    overrides = {}
    if args.factor is not None:
        overrides["threshold_factor"] = args.factor
    if args.floor is not None:
        overrides["threshold_floor"] = args.floor
    if args.refractory is not None:
        overrides["refractory_ms"] = args.refractory
    if args.smooth is not None:
        overrides["smooth_tau_ms"] = args.smooth
    det_cfg = replace(base, **overrides)

    if args.sweep:
        name, lo, hi, step = args.sweep.split(":")
        lo, hi, step = float(lo), float(hi), float(step)
        field = {"factor": "threshold_factor", "floor": "threshold_floor",
                 "refractory": "refractory_ms", "smooth": "smooth_tau_ms"}.get(name, name)
        value = lo
        while value <= hi + 1e-9:
            steps = run(samples, replace(det_cfg, **{field: value}))
            print(f"  {field}={value:<6.2f} {summarize(steps, span_s)}")
            value += step
        print("\nPick the value where step count stops changing much -- that "
              "plateau is the setting that isn't sensitive to your exact gait.")
        return 0

    steps = run(samples, det_cfg)
    print(f"  {summarize(steps, span_s)}")
    if args.verbose:
        t0 = samples[0][0]
        for t in steps:
            print(f"    {(t - t0) / 1000:7.3f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
