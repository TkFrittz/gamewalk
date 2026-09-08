"""LAN discovery and PIN pairing.

"Just install it and it works" cannot include typing an IP address, so the
phone broadcasts and the PC answers. Pairing exists so a housemate's phone
can't quietly walk your character into a wall; the PIN is typed once, ever.
"""

from __future__ import annotations

import secrets
import socket
import time
from dataclasses import dataclass

from . import protocol

PAIR_WINDOW_S = 120.0


def lan_ip() -> str:
    """Best guess at the address the phone should send to.

    Opening a UDP socket toward a public address makes the OS pick the route
    it would really use; no packet is sent. hostname lookup is unreliable here
    because it happily returns 127.0.0.1 or a VPN/Hyper-V adapter.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def new_token() -> str:
    return secrets.token_hex(8)


def new_pin() -> str:
    return f"{secrets.randbelow(1000000):06d}"


@dataclass
class Pairing:
    """A time-boxed window during which the PC will hand out its token."""

    pin: str = ""
    opened_at: float = 0.0

    def open(self) -> str:
        self.pin = new_pin()
        self.opened_at = time.monotonic()
        return self.pin

    def close(self) -> None:
        self.pin = ""
        self.opened_at = 0.0

    @property
    def is_open(self) -> bool:
        return bool(self.pin) and (time.monotonic() - self.opened_at) < PAIR_WINDOW_S

    @property
    def seconds_left(self) -> float:
        if not self.is_open:
            return 0.0
        return PAIR_WINDOW_S - (time.monotonic() - self.opened_at)

    def check(self, pin: str) -> bool:
        if not self.is_open:
            return False
        # Constant-time compare: the window is short and the PIN space small,
        # so there is no reason to leak digits through timing.
        return secrets.compare_digest(pin, self.pin)


class DiscoveryResponder:
    """Answers GW-DISCOVER? broadcasts on its own port."""

    def __init__(self, port: int, service_port: int, token: str,
                 require_pin: bool) -> None:
        self.port = port
        self.service_port = service_port
        self.token = token
        self.require_pin = require_pin
        self.pairing = Pairing()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind(("", port))
        self.sock.setblocking(False)
        self.host = socket.gethostname()
        self.on_pair: list[str] = []

    def fileno(self) -> int:
        return self.sock.fileno()

    def handle(self) -> str | None:
        """Read one datagram. Returns a line to log, or None."""
        try:
            data, addr = self.sock.recvfrom(2048)
        except (BlockingIOError, ConnectionResetError):
            return None

        parts = protocol.parse_discovery(data)
        if not parts:
            return None
        verb = parts[0]

        if verb == protocol.DISCOVER:
            state = "open" if not self.require_pin else "paired"
            reply = f"{protocol.HERE} {self.host} {lan_ip()} {self.service_port} {state}"
            self.sock.sendto(reply.encode(), addr)
            return f"discovery: answered {addr[0]}"

        if verb == protocol.PAIR:
            pin = parts[1] if len(parts) > 1 else ""
            if not self.require_pin or self.pairing.check(pin):
                self.sock.sendto(
                    f"{protocol.PAIRED} {self.token}".encode(), addr)
                self.pairing.close()
                return f"paired with {addr[0]}"
            self.sock.sendto(protocol.PAIR_DENIED.encode(), addr)
            return f"pairing REFUSED for {addr[0]} (wrong or expired PIN)"

        return None

    def close(self) -> None:
        self.sock.close()
