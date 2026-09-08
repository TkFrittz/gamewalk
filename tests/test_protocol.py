"""Wire protocol.

The listener sits on a UDP port that anything on the LAN can reach, so the
parser's contract is: never raise, never accept someone else's traffic.
"""

from __future__ import annotations

import json

from pc import protocol
from pc.keys import DryRunSink, Held


def test_roundtrip():
    pkt = protocol.parse(protocol.build("tok", protocol.STEP, 7, 12345))
    assert pkt and pkt.verb == protocol.STEP and pkt.seq == 7
    assert pkt.arg(0) == "12345"


def test_acc_args_parse_as_floats():
    pkt = protocol.parse(
        protocol.build("tok", protocol.ACC, 1, 1000, -0.5, 9.8, 0.25))
    assert pkt.farg(1) == -0.5 and pkt.farg(3) == 0.25


def test_json_tail_survives_spaces():
    """A config blob must not be shredded by the tokenizer."""
    blob = json.dumps({"hold": {"mode": "adaptive", "multiplier": 2.0}})
    pkt = protocol.parse(protocol.build("tok", protocol.CFG_SET, 1, blob))
    assert json.loads(pkt.arg(0))["hold"]["multiplier"] == 2.0


def test_cfg_full_separates_version_from_the_blob():
    """CFG! carries a version *and* JSON. Treating the whole tail as one
    opaque argument glues the version onto the front of the blob."""
    blob = json.dumps({"profiles": {"default": {"hold": {"mode": "adaptive"}}}})
    pkt = protocol.parse(protocol.build("tok", protocol.CFG_FULL, 1, 42, blob))
    assert pkt.arg(0) == "42"
    assert json.loads(pkt.arg(1))["profiles"]["default"]["hold"]["mode"] == "adaptive"


def test_blob_with_spaces_is_not_truncated():
    """json.dumps(indent=2) has spaces and newlines; nothing may be lost."""
    blob = json.dumps({"a": [1, 2, 3], "b": "two words"}, indent=2)
    pkt = protocol.parse(protocol.build("tok", protocol.CFG_FULL, 1, 9, blob))
    assert json.loads(pkt.arg(1))["b"] == "two words"


def test_rejects_foreign_traffic():
    """Something else on the LAN must not take down the listener."""
    for junk in [b"", b"hello", b"\xff\xfe\x00", b"SL2 tok STEP 1",
                 b"SL1 tok STEP notanumber", b"SL1 tok", b"SL1"]:
        assert protocol.parse(junk) is None


def test_missing_args_do_not_raise():
    pkt = protocol.parse(protocol.build("tok", protocol.STEP, 1))
    assert pkt.arg(5) == "" and pkt.farg(5, 1.5) == 1.5


def test_hot_verbs_are_marked():
    """Logging one line per 50Hz sample would bury everything useful."""
    assert protocol.ACC in protocol.HOT_VERBS
    assert protocol.CFG_SET not in protocol.HOT_VERBS


def test_discovery_parsing():
    assert protocol.parse_discovery(b"GW-DISCOVER? 0.1.0")[0] == protocol.DISCOVER
    assert protocol.parse_discovery(b"nonsense") is None


# --- key set diffing -------------------------------------------------------

def test_held_diffs_instead_of_reapplying():
    sink = DryRunSink(echo=False)
    held = Held(sink)
    held.apply(["w"])
    sink.events.clear()

    to_press, to_release = held.apply(["shift", "w"])
    assert to_press == {"shift"} and to_release == set()
    assert ("release", "w") not in sink.events


def test_held_releases_what_is_dropped():
    sink = DryRunSink(echo=False)
    held = Held(sink)
    held.apply(["shift", "w"])
    sink.events.clear()
    held.apply(["w"])
    assert sink.events == [("release", "shift")]


def test_held_release_all_is_idempotent():
    sink = DryRunSink(echo=False)
    held = Held(sink)
    held.apply(["shift", "w"])
    assert held.release_all() == {"shift", "w"}
    sink.events.clear()
    assert held.release_all() == set()
    assert sink.events == []


def test_held_normalizes_names():
    sink = DryRunSink(echo=False)
    held = Held(sink)
    held.apply(["W"])
    assert held.apply(["w"])[0] == set(), "case difference re-pressed the key"
