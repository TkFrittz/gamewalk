"""End-to-end through the real Server: UDP bytes in, key events out.

Uses real sockets on ephemeral ports. The unit tests cover the logic; this
covers the wiring between it, which is where "each piece works but the whole
doesn't" lives.
"""

from __future__ import annotations

import json
import socket

import pytest

from pc import config as cfgmod
from pc import protocol
from pc.keys import DryRunSink
from pc.server import Server

TOKEN = "testtoken"


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def server(tmp_path):
    raw = json.loads(cfgmod.DEFAULT_PATH.read_text(encoding="utf-8"))
    raw["token"] = TOKEN
    raw["port"] = _free_port()
    raw["discovery_port"] = _free_port()
    cfg = cfgmod.validate(raw)

    path = tmp_path / "config.json"
    cfgmod.save(cfg, path)

    sink = DryRunSink(echo=False)
    srv = Server(cfg=cfg, sink=sink, config_path=path, dry_run=True)
    srv.sink = sink
    yield srv
    srv.shutdown()


ADDR = ("127.0.0.1", 40000)


def feed(srv, verb, seq, *args, now_ms=0.0, token=TOKEN):
    srv.handle_packet(protocol.build(token, verb, seq, *args), ADDR, now_ms)


def test_hello_arms_and_steps_press_keys(server):
    feed(server, protocol.HELLO, 1, "pixel", "A", "0.1.0")
    assert server.machine.status.armed

    for i, t in enumerate([0.0, 500.0, 1000.0]):
        feed(server, protocol.STEP, i + 2, int(t), now_ms=t)

    assert server.machine.status.moving
    assert server.machine.status.held == ("w",)
    assert server.clients[ADDR].device == "pixel"


def test_wrong_token_is_dropped(server):
    feed(server, protocol.HELLO, 1, "attacker", "A", "0.1.0", token="wrong")
    assert not server.machine.status.armed


def test_stop_releases_keys(server):
    feed(server, protocol.HELLO, 1, "pixel", "A", "0.1.0")
    for i, t in enumerate([0.0, 500.0]):
        feed(server, protocol.STEP, i + 2, int(t), now_ms=t)
    assert server.machine.status.held == ("w",)

    feed(server, protocol.STOP, 9, now_ms=600.0)
    assert server.machine.status.held == ()
    assert not server.machine.status.armed


def test_heartbeat_rearms_after_helper_restart(server):
    """The app should not have to be toggled off and on because the PC helper
    was restarted mid-session."""
    feed(server, protocol.HB, 1, now_ms=0.0)
    assert server.machine.status.armed


def test_accelerometer_mode_drives_the_machine(server):
    """Mode B: raw samples in, steps found on the PC, keys held."""
    import math
    feed(server, protocol.HELLO, 1, "pixel", "B", "0.1.0")

    t = 0.0
    seq = 2
    while t < 6000.0:
        since = t % 500.0
        bump = sum(6.0 * math.exp(-((since - s) ** 2) / (2 * 45.0 ** 2))
                   for s in (0.0, 500.0))
        feed(server, protocol.ACC, seq, t, 0.0, 0.0, 9.81 + bump, now_ms=t)
        seq += 1
        t += 20.0

    assert server.machine.status.steps >= 8
    assert server.machine.status.held == ("w",)


def test_config_request_returns_meta(server):
    """Captured as raw bytes and parsed back, not inspected as arguments.

    Reading server.send's arguments directly would skip the encode/decode step
    and miss anything the wire format mangles -- which is exactly where the
    CFG! version/blob split went wrong.
    """
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.bind(("127.0.0.1", 0))
    client.settimeout(2.0)
    try:
        server.handle_packet(
            protocol.build(TOKEN, protocol.CFG_GET, 1), client.getsockname(), 0.0)
        data, _ = client.recvfrom(65535)
    finally:
        client.close()

    pkt = protocol.parse(data)
    assert pkt.verb == protocol.CFG_FULL
    assert pkt.arg(0) == str(server.cfg.version)
    blob = json.loads(pkt.arg(1))
    assert "meta" in blob and "profiles" in blob


def test_live_patch_applies_and_persists(server):
    sent = []
    server.send = lambda addr, verb, *a: sent.append((verb, a))

    patch = json.dumps({"profiles": {"default": {"hold": {"multiplier": 2.4}}}})
    feed(server, protocol.CFG_SET, 1, patch)

    assert sent[0][0] == protocol.CFG_OK
    assert server.cfg.profile.hold.multiplier == 2.4
    # Survives a restart: the patch reached disk, not just memory.
    assert cfgmod.load(server.config_path).profile.hold.multiplier == 2.4


def test_bad_patch_is_rejected_and_changes_nothing(server):
    sent = []
    server.send = lambda addr, verb, *a: sent.append((verb, a))
    before = cfgmod.to_dict(server.cfg)

    feed(server, protocol.CFG_SET, 1,
         json.dumps({"profiles": {"default": {"tiers": [
             {"name": "walk", "keys": ["nonsense"],
              "enter_spm": 0, "exit_spm": 0}]}}}))

    assert sent[0][0] == protocol.CFG_ERR
    assert cfgmod.to_dict(server.cfg) == before


def test_patch_bumps_version_so_clients_can_sync(server):
    server.send = lambda *a: None
    before = server.cfg.version
    feed(server, protocol.CFG_SET, 1, json.dumps({"deadman_ms": 1200}))
    assert server.cfg.version == before + 1


def test_live_patch_takes_effect_mid_walk(server):
    """Requirement B, through the real packet path."""
    feed(server, protocol.HELLO, 1, "pixel", "A", "0.1.0")
    for i, t in enumerate([0.0, 550.0, 1100.0, 1650.0]):   # ~109 spm: walk tier
        feed(server, protocol.STEP, i + 2, int(t), now_ms=t)
    assert server.machine.status.held == ("w",)

    server.send = lambda *a: None
    server.sink.events.clear()
    feed(server, protocol.CFG_SET, 20, json.dumps(
        {"profiles": {"default": {"tiers": [
            {"name": "walk", "keys": ["up"], "enter_spm": 0, "exit_spm": 0},
            {"name": "run", "keys": ["shift", "up"],
             "enter_spm": 145, "exit_spm": 130}]}}}))

    assert server.machine.status.held == ("up",)
    assert ("press", "up") in server.sink.events
    assert ("release", "w") in server.sink.events


def test_file_edit_reloads_and_pushes(server):
    """Hand-editing config.json updates the app too -- live config runs in
    both directions."""
    pushed = []
    server.send = lambda addr, verb, *a: pushed.append(verb)

    feed(server, protocol.HELLO, 1, "pixel", "A", "0.1.0")
    pushed.clear()

    raw = json.loads(server.config_path.read_text(encoding="utf-8"))
    raw["deadman_ms"] = 1500
    server.config_path.write_text(json.dumps(raw), encoding="utf-8")
    server._mtime = 0.0  # force the mtime check rather than sleeping

    server.check_file_reload()
    assert server.cfg.deadman_ms == 1500
    assert protocol.CFG_FULL in pushed


def test_invalid_file_edit_keeps_the_old_config(server):
    feed(server, protocol.HELLO, 1, "pixel", "A", "0.1.0")
    server.config_path.write_text("{ not json", encoding="utf-8")
    server._mtime = 0.0

    server.check_file_reload()
    assert server.cfg.deadman_ms == 1000
    assert any("invalid" in line for line in server.log)


def test_subscription_gates_telemetry(server):
    """Don't stream stats at a phone in a pocket."""
    sent = []
    server.send = lambda addr, verb, *a: sent.append(verb)
    feed(server, protocol.HELLO, 1, "pixel", "A", "0.1.0")

    server.push_stats(now=100.0)
    assert protocol.STAT not in sent

    feed(server, protocol.SUB, 2, 5.0)
    server.push_stats(now=200.0)
    assert protocol.STAT in sent

    sent.clear()
    feed(server, protocol.UNSUB, 3)
    server.push_stats(now=300.0)
    assert protocol.STAT not in sent


def test_panic_releases_everything(server):
    feed(server, protocol.HELLO, 1, "pixel", "A", "0.1.0")
    for i, t in enumerate([0.0, 500.0]):
        feed(server, protocol.STEP, i + 2, int(t), now_ms=t)
    assert server.machine.status.held == ("w",)

    server.panic()
    assert server.machine.status.held == ()
    assert not server.machine.status.armed


def test_recording_writes_a_replayable_trace(server, tmp_path):
    path = tmp_path / "trace.jsonl"
    server._recorder = path.open("w", encoding="utf-8")
    feed(server, protocol.HELLO, 1, "pixel", "B", "0.1.0")
    for i in range(5):
        feed(server, protocol.ACC, i + 2, i * 20.0, 0.1, 0.2, 9.8, now_ms=i * 20.0)
    server._recorder.close()
    server._recorder = None

    rows = [json.loads(l) for l in path.read_text().splitlines()]
    assert len(rows) == 5 and rows[0]["a"] == [0.1, 0.2, 9.8]
