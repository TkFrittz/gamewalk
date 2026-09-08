"""Config client: everything the app's settings screen will do, from a shell.

Requirement B ("configure it from the phone, live") is fully testable before
the app exists, because this speaks the same protocol the app will.

    python tools/remote.py get
    python tools/remote.py set profiles.default.hold.multiplier 2.2
    python tools/remote.py set profiles.default.tiers.1.enter_spm 130
    python tools/remote.py watch          live telemetry + config pushes
    python tools/remote.py discover
    python tools/remote.py pair 481920
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pc import config as cfgmod  # noqa: E402
from pc import protocol  # noqa: E402

TIMEOUT = 2.0
RETRIES = 3


def _coerce(text: str) -> Any:
    """Turn a CLI string into JSON, falling back to a bare string.

    Lets `set ... 2.2` and `set ... '["shift","w"]'` both do the obvious thing.
    """
    try:
        return json.loads(text)
    except ValueError:
        return text


def _nest(path: str, value: Any) -> dict[str, Any]:
    """'a.b.1.c' -> nested dicts, with list indices rebuilt as sparse lists."""
    parts = path.split(".")
    node: Any = value
    for part in reversed(parts):
        if part.isdigit():
            node = {"__index__": int(part), "__value__": node}
        else:
            node = {part: node}
    return node


def _resolve_indices(patch: Any, base: Any) -> Any:
    """Rewrite index markers against the current config.

    Lists replace wholesale on the PC (a partial list is ambiguous -- is index
    1 an edit or an insert?), so editing one tier means sending the *entire*
    list back with that one element changed, and the changed element must
    itself be complete. Merging against the current value is what makes
    `set ...tiers.1.enter_spm 130` mean "change that one number" rather than
    "replace tier 1 with an object that has only enter_spm".
    """
    if not isinstance(patch, dict):
        return patch
    if "__index__" in patch:
        idx = patch["__index__"]
        if not isinstance(base, list) or idx >= len(base):
            raise SystemExit(f"index {idx} out of range")
        out = list(base)
        edited = _resolve_indices(patch["__value__"], base[idx])
        if isinstance(base[idx], dict) and isinstance(edited, dict):
            edited = cfgmod.deep_merge(base[idx], edited)
        out[idx] = edited
        return out
    return {
        k: _resolve_indices(v, base.get(k) if isinstance(base, dict) else None)
        for k, v in patch.items()
    }


class Remote:
    def __init__(self, host: str, port: int, token: str) -> None:
        self.dest = (host, port)
        self.token = token
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(TIMEOUT)
        self.seq = 0

    def send(self, verb: str, *args: object) -> int:
        self.seq += 1
        self.sock.sendto(protocol.build(self.token, verb, self.seq, *args), self.dest)
        return self.seq

    def request(self, verb: str, *args: object, expect: set[str]) -> protocol.Packet:
        """Control messages are retried: sensor loss is self-healing, but a
        dropped config write is not."""
        for attempt in range(RETRIES):
            self.send(verb, *args)
            deadline = time.monotonic() + TIMEOUT
            while time.monotonic() < deadline:
                try:
                    data, _ = self.sock.recvfrom(65535)
                except socket.timeout:
                    break
                pkt = protocol.parse(data)
                if pkt and pkt.verb in expect:
                    return pkt
            print(f"  no reply, retry {attempt + 1}/{RETRIES}", file=sys.stderr)
        raise SystemExit("helper did not answer; is it running?")

    def get_config(self) -> dict[str, Any]:
        pkt = self.request(protocol.CFG_GET, expect={protocol.CFG_FULL})
        return json.loads(pkt.arg(1))


def cmd_get(r: Remote, args) -> int:
    cfg = r.get_config()
    if args.path:
        node: Any = cfg
        for part in args.path.split("."):
            node = node[int(part)] if part.isdigit() else node[part]
        print(json.dumps(node, indent=2))
    else:
        cfg.pop("meta", None)
        cfg.pop("keys", None)
        print(json.dumps(cfg, indent=2))
    return 0


def cmd_set(r: Remote, args) -> int:
    current = r.get_config()
    patch = _resolve_indices(_nest(args.path, _coerce(args.value)), current)
    blob = json.dumps(patch, separators=(",", ":"))
    pkt = r.request(protocol.CFG_SET, blob,
                    expect={protocol.CFG_OK, protocol.CFG_ERR})
    if pkt.verb == protocol.CFG_ERR:
        print(f"rejected: {pkt.arg(0).replace('_', ' ')}", file=sys.stderr)
        return 1
    print(f"applied, config now v{pkt.arg(0)}")
    return 0


def cmd_meta(r: Remote, args) -> int:
    meta = r.get_config().get("meta", {})
    groups: dict[str, list[str]] = {}
    for key, m in meta.items():
        groups.setdefault(m.get("group", "Other"), []).append(
            f"  {key:<34} {m.get('type','?'):<7} {m.get('label','')}")
    for group, rows in groups.items():
        print(f"\n{group}")
        print("\n".join(sorted(rows)))
    return 0


def cmd_watch(r: Remote, args) -> int:
    r.send(protocol.SUB, args.hz)
    r.sock.settimeout(1.0)
    print("watching (Ctrl-C to stop)\n")
    try:
        while True:
            try:
                data, _ = r.sock.recvfrom(65535)
            except socket.timeout:
                r.send(protocol.SUB, args.hz)  # keep the subscription alive
                continue
            pkt = protocol.parse(data)
            if not pkt:
                continue
            if pkt.verb == protocol.STAT:
                spm, tier, sig, thr, armed = (pkt.arg(i) for i in range(5))
                print(f"\r  {tier:<8} {float(spm):6.1f} spm   "
                      f"signal {float(sig):6.2f} / thr {float(thr):5.2f}   "
                      f"armed={armed}   ", end="", flush=True)
            elif pkt.verb == protocol.CFG_FULL:
                print(f"\n  << config pushed, now v{pkt.arg(0)}")
    except KeyboardInterrupt:
        r.send(protocol.UNSUB)
        print("\nunsubscribed")
    return 0


def cmd_discover(r: Remote, args) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(2.0)
    sock.sendto(f"{protocol.DISCOVER} 0.1.0".encode(),
                ("255.255.255.255", args.discovery_port))
    found = 0
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            data, addr = sock.recvfrom(2048)
        except socket.timeout:
            break
        parts = protocol.parse_discovery(data)
        if parts and parts[0] == protocol.HERE:
            found += 1
            print(f"  {parts[1]}  {parts[2]}:{parts[3]}  ({parts[4]})")
    if not found:
        print("  nothing answered. Same network? Router AP isolation?")
    return 0 if found else 1


def cmd_pair(r: Remote, args) -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(2.0)
    sock.sendto(f"{protocol.PAIR} {args.pin}".encode(),
                (args.host, args.discovery_port))
    try:
        data, _ = sock.recvfrom(2048)
    except socket.timeout:
        print("no reply from the helper", file=sys.stderr)
        return 1
    parts = protocol.parse_discovery(data) or []
    if parts and parts[0] == protocol.PAIRED:
        print(f"paired. token = {parts[1]}")
        return 0
    print("refused: wrong or expired PIN", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    cfg = cfgmod.load()
    ap = argparse.ArgumentParser(description="GameWalk config remote")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=cfg.port)
    ap.add_argument("--discovery-port", type=int, default=cfg.discovery_port)
    ap.add_argument("--token", default=cfg.token)
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("get", help="print config, or one path")
    g.add_argument("path", nargs="?")
    g.set_defaults(fn=cmd_get)

    s = sub.add_parser("set", help="change one value, live")
    s.add_argument("path")
    s.add_argument("value")
    s.set_defaults(fn=cmd_set)

    m = sub.add_parser("meta", help="list every editable field")
    m.set_defaults(fn=cmd_meta)

    w = sub.add_parser("watch", help="live telemetry")
    w.add_argument("--hz", type=float, default=5.0)
    w.set_defaults(fn=cmd_watch)

    d = sub.add_parser("discover", help="find helpers on the LAN")
    d.set_defaults(fn=cmd_discover)

    p = sub.add_parser("pair", help="exchange a PIN for the token")
    p.add_argument("pin")
    p.set_defaults(fn=cmd_pair)

    args = ap.parse_args(argv)
    return args.fn(Remote(args.host, args.port, args.token), args)


if __name__ == "__main__":
    raise SystemExit(main())
