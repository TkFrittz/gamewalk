"""SL1 wire protocol: one ASCII line per UDP datagram.

Deliberately text, not binary. Every packet is readable in a capture or a log,
which is what makes "why did it not step" answerable at 11pm without a debugger.

    SL1 <token> <verb> <seq> [args...]

Parsing never raises on malformed input -- a garbled datagram from something
else on the LAN must not take down the listener, so parse() returns None and
the caller drops it.
"""

from __future__ import annotations

from typing import NamedTuple, Sequence

MAGIC = "SL1"
DISCOVERY_MAGIC = "GW"

# Phone -> PC
STEP = "STEP"
ACC = "ACC"
HB = "HB"
STOP = "STOP"
HELLO = "HELLO"
CFG_GET = "CFG?"
CFG_SET = "CFG="
SUB = "SUB"
UNSUB = "UNSUB"
PING = "PING"

# PC -> phone
CFG_FULL = "CFG!"
CFG_OK = "CFGOK"
CFG_ERR = "CFGERR"
STAT = "STAT"
PONG = "PONG"
#: PC -> phone. Lets the desktop app drive settings that only the
#: phone can act on, such as which sensor to use.
CMD = "CMD"

#: Verbs that arrive at high rate and must never be logged per-packet.
HOT_VERBS = frozenset({STEP, ACC, HB})

#: Verbs carrying a trailing JSON blob, mapped to how many plain arguments
#: come before it. The blob contains spaces and must survive intact, but
#: CFG! also carries a version ahead of it -- treating the whole tail as one
#: opaque argument would glue the version onto the front of the JSON.
_JSON_TAIL: dict[str, int] = {CFG_SET: 0, CFG_FULL: 1}


class Packet(NamedTuple):
    token: str
    verb: str
    seq: int
    args: Sequence[str]

    def arg(self, i: int, default: str = "") -> str:
        return self.args[i] if i < len(self.args) else default

    def farg(self, i: int, default: float = 0.0) -> float:
        try:
            return float(self.args[i])
        except (IndexError, ValueError):
            return default


def parse(data: bytes) -> Packet | None:
    """Decode one datagram. Returns None for anything that isn't ours."""
    try:
        line = data.decode("utf-8", "strict").strip()
    except UnicodeDecodeError:
        return None
    if not line.startswith(MAGIC + " "):
        return None

    # Split off the fixed head; a JSON tail keeps its spaces intact.
    head = line.split(" ", 4)
    if len(head) < 4:
        return None
    _, token, verb, seq_s = head[:4]
    tail = head[4] if len(head) > 4 else ""

    try:
        seq = int(seq_s)
    except ValueError:
        return None

    if verb in _JSON_TAIL:
        # Split off exactly the plain args, leaving the JSON blob whole.
        args = tail.split(" ", _JSON_TAIL[verb]) if tail else []
    else:
        args = tail.split() if tail else []
    return Packet(token=token, verb=verb, seq=seq, args=args)


def build(token: str, verb: str, seq: int, *args: object) -> bytes:
    parts = [MAGIC, token, verb, str(seq), *(str(a) for a in args)]
    return " ".join(parts).encode("utf-8")


# --- Discovery, a separate tiny protocol on its own port -------------------

DISCOVER = "GW-DISCOVER?"
HERE = "GW-HERE"
PAIR = "GW-PAIR"
PAIRED = "GW-PAIRED"
PAIR_DENIED = "GW-DENIED"


def parse_discovery(data: bytes) -> list[str] | None:
    try:
        line = data.decode("utf-8", "strict").strip()
    except UnicodeDecodeError:
        return None
    if not line.startswith(DISCOVERY_MAGIC + "-"):
        return None
    return line.split()
