"""Colours, fonts and the small drawing helpers the GUI is built from.

Black and dark greys carry the layout; dark green is structure; neon green and
neon blue are reserved for live values and the things you can act on. Keeping
the neons scarce is what makes them readable at a glance from across a room --
if everything glows, nothing stands out.
"""

from __future__ import annotations

# --- surfaces --------------------------------------------------------------
BG = "#06080A"          # window
PANEL = "#0D1114"       # cards
PANEL_HI = "#141A1E"    # raised rows, hover
LINE = "#1D272B"        # hairline borders
LINE_HI = "#2A383D"

# --- greens ----------------------------------------------------------------
GREEN_DEEP = "#062017"  # meter track, inactive fills
GREEN_DARK = "#0C3D2A"  # borders and zones
GREEN_MID = "#12694A"
NEON_GREEN = "#3BFF95"  # live values, armed state
NEON_GREEN_DIM = "#1E8F55"

# --- blues -----------------------------------------------------------------
BLUE_DEEP = "#04222B"
BLUE_DARK = "#0A3F4F"
NEON_BLUE = "#25E1FF"   # walking tier, secondary actions
NEON_BLUE_DIM = "#127C93"

# --- text and alerts -------------------------------------------------------
TEXT = "#DDF3E9"
TEXT_DIM = "#6F857D"
TEXT_FAINT = "#41524C"
RED = "#FF5F6E"
AMBER = "#FFC24B"

#: Tier colours cycle through these, so a third tier gets its own identity
#: without anyone having to pick one.
TIER_COLORS = [NEON_BLUE, NEON_GREEN, "#B78CFF", AMBER]

FONT = "Segoe UI"
FONT_BOLD = "Segoe UI Semibold"
MONO = "Consolas"


def font(size: int, bold: bool = False, mono: bool = False):
    return ((MONO if mono else (FONT_BOLD if bold else FONT)), size)


def round_rect(canvas, x1, y1, x2, y2, r=10, **kw):
    """A rounded rectangle. Tk has no such primitive, but a smoothed polygon
    with doubled corner points is indistinguishable from one."""
    r = min(r, abs(x2 - x1) / 2, abs(y2 - y1) / 2)
    pts = [
        x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
        x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
        x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
    ]
    return canvas.create_polygon(pts, smooth=True, splinesteps=16, **kw)


def mix(a: str, b: str, t: float) -> str:
    """Blend two hex colours. Used for glow falloff and hover states."""
    t = max(0.0, min(1.0, t))
    ar, ag, ab = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
    br, bg, bb = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
    return "#%02x%02x%02x" % (
        round(ar + (br - ar) * t),
        round(ag + (bg - ag) * t),
        round(ab + (bb - ab) * t),
    )
