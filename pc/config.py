"""Config: load, validate, patch, persist, hot-reload.

The PC owns config. The app is a remote control, so every mutation lands here
and there is exactly one authoritative copy on disk.

Validation is strict and total. A settings screen is a phone being poked at
arm's length; a bad patch must be rejected with the old config still live,
never half-applied.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import keys

HERE = Path(__file__).resolve().parent
DEFAULT_PATH = HERE / "config.default.json"
CONFIG_PATH = HERE / "config.json"


class ConfigError(ValueError):
    """A patch was rejected. The message goes back over the wire as CFGERR."""


# --- Field descriptors -----------------------------------------------------
# Shipped to the app inside CFG! so it can render a settings UI without a
# hardcoded screen per field. Adding a setting here makes it appear on the
# phone with no app release.

META: dict[str, dict[str, Any]] = {
    "deadman_ms": {
        "type": "int", "min": 400, "max": 5000, "step": 100,
        "group": "Safety", "label": "Dead-man timeout",
        "help": "Release all keys if the phone goes silent this long. "
                "The phone heartbeats 4x a second, so this is about lost Wi-Fi.",
    },
    "start_steps": {
        "type": "int", "min": 1, "max": 5, "step": 1,
        "group": "Safety", "label": "Steps to start",
        "help": "Steps needed before moving. 2 rejects a single jolt in a pocket.",
    },
    "panic_hotkey": {
        "type": "key", "group": "Safety", "label": "Panic hotkey",
        "help": "Pressed on the PC, releases everything and disarms.",
    },
    "hold.mode": {
        "type": "enum", "values": ["adaptive", "fixed"],
        "group": "Smoothness", "label": "Hold mode",
        "help": "Adaptive tracks your cadence. Fixed uses one value always.",
    },
    "hold.multiplier": {
        "type": "float", "min": 1.0, "max": 3.0, "step": 0.05,
        "group": "Smoothness", "label": "Hold multiplier",
        "help": "How long a key stays held, as a multiple of your step interval. "
                "Below ~1.3 it starts stuttering; higher coasts longer after you stop.",
    },
    "hold.min_ms": {
        "type": "int", "min": 100, "max": 1000, "step": 50,
        "group": "Smoothness", "label": "Hold floor",
    },
    "hold.max_ms": {
        "type": "int", "min": 500, "max": 3000, "step": 50,
        "group": "Smoothness", "label": "Hold ceiling",
    },
    "hold.fixed_ms": {
        "type": "int", "min": 100, "max": 3000, "step": 50,
        "group": "Smoothness", "label": "Fixed hold",
    },
    "tiers[].name": {"type": "text", "group": "Controls", "label": "Name"},
    "tiers[].keys": {
        "type": "keys", "group": "Controls", "label": "Keys held",
        "help": "Held continuously while in this tier.",
    },
    "tiers[].enter_spm": {
        "type": "int", "min": 0, "max": 260, "step": 5,
        "group": "Controls", "label": "Enter above (SPM)",
    },
    "tiers[].exit_spm": {
        "type": "int", "min": 0, "max": 260, "step": 5,
        "group": "Controls", "label": "Drop below (SPM)",
        "help": "Must be lower than the enter threshold. The gap is what stops "
                "the tier flickering when your cadence sits near the line.",
    },
    "detector.threshold_factor": {
        "type": "float", "min": 0.8, "max": 3.0, "step": 0.05,
        "group": "Sensitivity (Mode B)", "label": "Threshold factor",
        "help": "Multiple of recent signal energy. Lower = more sensitive.",
    },
    "detector.threshold_floor": {
        "type": "float", "min": 0.2, "max": 4.0, "step": 0.1,
        "group": "Sensitivity (Mode B)", "label": "Noise floor",
        "help": "Never go below this. Stops it finding steps in noise while you stand still.",
    },
    "detector.refractory_ms": {
        "type": "int", "min": 80, "max": 400, "step": 10,
        "group": "Sensitivity (Mode B)", "label": "Minimum step gap",
    },
    "detector.rearm_ratio": {
        "type": "float", "min": 0.1, "max": 0.95, "step": 0.05,
        "group": "Sensitivity (Mode B)", "label": "Re-arm ratio",
    },
    "detector.smooth_tau_ms": {
        "type": "int", "min": 10, "max": 200, "step": 5,
        "group": "Sensitivity (Mode B)", "label": "Smoothing",
    },
    "detector.dc_tau_ms": {
        "type": "int", "min": 200, "max": 4000, "step": 100,
        "group": "Sensitivity (Mode B)", "label": "Gravity removal",
    },
}


@dataclass
class Tier:
    name: str
    keys: list[str]
    enter_spm: float
    exit_spm: float


@dataclass
class Hold:
    mode: str = "adaptive"
    multiplier: float = 1.8
    min_ms: int = 350
    max_ms: int = 1400
    fixed_ms: int = 650

    def duration_ms(self, interval_ms: float | None) -> float:
        """How long to keep keys held after the most recent step.

        With no cadence estimate yet (the first step of a walk) adaptive mode
        holds for `max_ms`, not `fixed_ms`. The asymmetry is deliberate: the
        two errors are not equally bad. Coasting slightly too long is
        invisible, while releasing too early is a visible stutter on the very
        first step of every walk -- which is exactly when you're looking.
        """
        if self.mode == "fixed":
            return float(self.fixed_ms)
        if interval_ms is None:
            return float(self.max_ms)
        return max(self.min_ms, min(self.max_ms, self.multiplier * interval_ms))


@dataclass
class Detector:
    dc_tau_ms: float = 1000.0
    smooth_tau_ms: float = 60.0
    threshold_factor: float = 1.4
    threshold_floor: float = 1.1
    rearm_ratio: float = 0.6
    refractory_ms: float = 180.0


@dataclass
class Profile:
    tiers: list[Tier]
    hold: Hold = field(default_factory=Hold)
    detector: Detector = field(default_factory=Detector)


@dataclass
class Config:
    version: int = 1
    port: int = 5599
    discovery_port: int = 5598
    deadman_ms: int = 1000
    panic_hotkey: str = "f8"
    start_steps: int = 2
    require_pin: bool = True
    token: str = ""
    active_profile: str = "default"
    profiles: dict[str, Profile] = field(default_factory=dict)

    @property
    def profile(self) -> Profile:
        return self.profiles[self.active_profile]


# --- Validation ------------------------------------------------------------

def _num(raw: Any, path: str, lo: float, hi: float) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ConfigError(f"{path}: expected a number, got {type(raw).__name__}")
    if not lo <= raw <= hi:
        raise ConfigError(f"{path}: {raw} out of range {lo}..{hi}")
    return float(raw)


def _validate_tiers(raw: Any) -> list[Tier]:
    if not isinstance(raw, list) or not raw:
        raise ConfigError("tiers: need at least one tier")

    tiers: list[Tier] = []
    for i, t in enumerate(raw):
        if not isinstance(t, dict):
            raise ConfigError(f"tiers[{i}]: expected an object")

        # Every field is required, deliberately. Lists replace wholesale, so a
        # client that sends a partial tier ("just change enter_spm") would have
        # the missing fields quietly filled with defaults -- renaming the tier
        # and zeroing its thresholds while the patch reports success. Demanding
        # complete elements turns that silent corruption into a clear error.
        missing = [f for f in ("name", "keys", "enter_spm", "exit_spm")
                   if f not in t]
        if missing:
            raise ConfigError(
                f"tiers[{i}]: missing {', '.join(missing)} -- send the whole "
                f"tier, not just the fields you changed")
        name = str(t["name"])

        key_list = t.get("keys", [])
        if not isinstance(key_list, list) or not key_list:
            raise ConfigError(f"tiers[{i}].keys: need at least one key")
        norm: list[str] = []
        for k in key_list:
            if not isinstance(k, str) or not keys.is_valid(k):
                raise ConfigError(f"tiers[{i}].keys: unknown key {k!r}")
            norm.append(keys.normalize(k))

        enter = _num(t.get("enter_spm", 0), f"tiers[{i}].enter_spm", 0, 400)
        exit_ = _num(t.get("exit_spm", 0), f"tiers[{i}].exit_spm", 0, 400)

        # The base tier has no thresholds; every tier above it must have a
        # real hysteresis gap or it will chatter at the boundary.
        if i > 0 and exit_ >= enter:
            raise ConfigError(
                f"tiers[{i}] ({name}): exit_spm ({exit_:g}) must be below "
                f"enter_spm ({enter:g}), otherwise the tier flickers"
            )
        tiers.append(Tier(name, norm, enter, exit_))

    for i in range(1, len(tiers)):
        if tiers[i].enter_spm <= tiers[i - 1].enter_spm:
            raise ConfigError(
                f"tiers[{i}] ({tiers[i].name}): enter_spm must be above the "
                f"tier below it ({tiers[i - 1].name})"
            )
    return tiers


def _validate_hold(raw: Any) -> Hold:
    raw = raw if isinstance(raw, dict) else {}
    h = Hold(
        mode=str(raw.get("mode", "adaptive")),
        multiplier=_num(raw.get("multiplier", 1.8), "hold.multiplier", 1.0, 3.0),
        min_ms=int(_num(raw.get("min_ms", 350), "hold.min_ms", 100, 1000)),
        max_ms=int(_num(raw.get("max_ms", 1400), "hold.max_ms", 500, 3000)),
        fixed_ms=int(_num(raw.get("fixed_ms", 650), "hold.fixed_ms", 100, 3000)),
    )
    if h.mode not in ("adaptive", "fixed"):
        raise ConfigError(f"hold.mode: expected adaptive or fixed, got {h.mode!r}")
    if h.min_ms > h.max_ms:
        raise ConfigError(f"hold.min_ms ({h.min_ms}) must not exceed max_ms ({h.max_ms})")
    if h.mode == "fixed" and not h.min_ms <= h.fixed_ms <= h.max_ms:
        raise ConfigError(
            f"hold.fixed_ms ({h.fixed_ms}) must sit between min_ms and max_ms"
        )
    return h


def _validate_detector(raw: Any) -> Detector:
    raw = raw if isinstance(raw, dict) else {}
    return Detector(
        dc_tau_ms=_num(raw.get("dc_tau_ms", 1000), "detector.dc_tau_ms", 200, 4000),
        smooth_tau_ms=_num(raw.get("smooth_tau_ms", 60), "detector.smooth_tau_ms", 10, 200),
        threshold_factor=_num(
            raw.get("threshold_factor", 1.4), "detector.threshold_factor", 0.8, 3.0),
        threshold_floor=_num(
            raw.get("threshold_floor", 1.1), "detector.threshold_floor", 0.2, 4.0),
        rearm_ratio=_num(raw.get("rearm_ratio", 0.6), "detector.rearm_ratio", 0.1, 0.95),
        refractory_ms=_num(
            raw.get("refractory_ms", 180), "detector.refractory_ms", 80, 400),
    )


def validate(raw: dict[str, Any]) -> Config:
    """Turn untrusted JSON into a Config, or raise ConfigError."""
    if not isinstance(raw, dict):
        raise ConfigError("config must be an object")

    profiles_raw = raw.get("profiles")
    if not isinstance(profiles_raw, dict) or not profiles_raw:
        raise ConfigError("profiles: need at least one profile")

    profiles = {
        name: Profile(
            tiers=_validate_tiers(p.get("tiers") if isinstance(p, dict) else None),
            hold=_validate_hold(p.get("hold") if isinstance(p, dict) else None),
            detector=_validate_detector(p.get("detector") if isinstance(p, dict) else None),
        )
        for name, p in profiles_raw.items()
    }

    active = str(raw.get("active_profile", "default"))
    if active not in profiles:
        raise ConfigError(f"active_profile: no profile named {active!r}")

    hotkey = str(raw.get("panic_hotkey", "f8"))
    if not keys.is_valid(hotkey):
        raise ConfigError(f"panic_hotkey: unknown key {hotkey!r}")

    return Config(
        version=int(raw.get("version", 1)),
        port=int(_num(raw.get("port", 5599), "port", 1024, 65535)),
        discovery_port=int(_num(raw.get("discovery_port", 5598), "discovery_port", 1024, 65535)),
        deadman_ms=int(_num(raw.get("deadman_ms", 1000), "deadman_ms", 400, 5000)),
        panic_hotkey=keys.normalize(hotkey),
        start_steps=int(_num(raw.get("start_steps", 2), "start_steps", 1, 5)),
        require_pin=bool(raw.get("require_pin", True)),
        token=str(raw.get("token", "")),
        active_profile=active,
        profiles=profiles,
    )


# --- Serialization ---------------------------------------------------------

def to_dict(cfg: Config) -> dict[str, Any]:
    return {
        "version": cfg.version,
        "port": cfg.port,
        "discovery_port": cfg.discovery_port,
        "deadman_ms": cfg.deadman_ms,
        "panic_hotkey": cfg.panic_hotkey,
        "start_steps": cfg.start_steps,
        "require_pin": cfg.require_pin,
        "token": cfg.token,
        "active_profile": cfg.active_profile,
        "profiles": {
            name: {
                "tiers": [
                    {"name": t.name, "keys": list(t.keys),
                     "enter_spm": t.enter_spm, "exit_spm": t.exit_spm}
                    for t in p.tiers
                ],
                "hold": {
                    "mode": p.hold.mode, "multiplier": p.hold.multiplier,
                    "min_ms": p.hold.min_ms, "max_ms": p.hold.max_ms,
                    "fixed_ms": p.hold.fixed_ms,
                },
                "detector": {
                    "dc_tau_ms": p.detector.dc_tau_ms,
                    "smooth_tau_ms": p.detector.smooth_tau_ms,
                    "threshold_factor": p.detector.threshold_factor,
                    "threshold_floor": p.detector.threshold_floor,
                    "rearm_ratio": p.detector.rearm_ratio,
                    "refractory_ms": p.detector.refractory_ms,
                },
            }
            for name, p in cfg.profiles.items()
        },
    }


def wire_dict(cfg: Config) -> dict[str, Any]:
    """What goes to the app: config plus the descriptors to render it."""
    d = to_dict(cfg)
    d.pop("token", None)  # the app already holds it; no reason to echo it back
    d["meta"] = META
    d["keys"] = keys.valid_names()
    return d


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursive merge. Lists replace wholesale -- a partial tier list is
    ambiguous (is index 1 an edit or an insert?) and guessing would corrupt
    someone's keybinds."""
    out = dict(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# --- Disk ------------------------------------------------------------------

def load(path: Path = CONFIG_PATH) -> Config:
    """Read config, creating it from the shipped default on first run."""
    if not path.exists():
        shutil.copyfile(DEFAULT_PATH, path)
    with path.open("r", encoding="utf-8") as fh:
        return validate(json.load(fh))


def save(cfg: Config, path: Path = CONFIG_PATH) -> None:
    """Write atomically. A half-written config that fails to parse on next
    start would be a miserable way to lose your keybinds."""
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(to_dict(cfg), fh, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
