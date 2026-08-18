"""The desktop palette and type scale - the counterpart of :root in app.css.

Monochrome on near-black; colour carries meaning. Amber and red mean warning and
critical and are used for nothing else (D-014). Chinese text never uses the
monospace stack, because it falls back to a different CJK face and splits a line
into two typefaces (D-017); SANS is for anything that may contain Chinese, MONO
only for pure digits and Latin.
"""

from __future__ import annotations

COLORS = {
    "background": "#0A0A0A",
    "surface": "#131312",
    "surface_2": "#1A1A19",
    "line": "#2A2A28",
    "text": "#D8D5CF",
    "muted": "#94918B",
    "control": "#2E2E2C",
    "control_hover": "#3B3B38",
    "reading": "#B9B5AD",
    "ok": "#94918B",
    "amber": "#D8973A",
    "red": "#D8474C",
    "red_hover": "#BE3D42",
    "warn_surface": "#241E12",
    "warn_line": "#5C4720",
    "warn_text": "#E8A33D",
    "alert_surface": "#2A1618",
    "alert_text": "#E5484D",
    "log_text": "#9A9A95",
}

# presenters.py returns a tone; this is the only place a tone becomes a colour.
TONE_COLORS = {
    "neutral": COLORS["reading"],
    "muted": COLORS["muted"],
    "ok": COLORS["ok"],
    "warn": COLORS["amber"],
    "alert": COLORS["red"],
}

# Badges carry a background as well as a foreground.
TONE_BADGE = {
    "neutral": (COLORS["surface_2"], COLORS["muted"]),
    "muted": (COLORS["surface_2"], COLORS["muted"]),
    "ok": (COLORS["surface_2"], COLORS["ok"]),
    "warn": (COLORS["surface_2"], COLORS["amber"]),
    "alert": (COLORS["alert_surface"], COLORS["alert_text"]),
}

SANS = "Microsoft YaHei UI"
MONO = "Cascadia Mono"

# Radii follow app.css --radius: 14px for cards, one step down for controls.
RADIUS_CARD = 14
RADIUS_CONTROL = 10
RADIUS_CHIP = 8


def tone_color(tone: str) -> str:
    return TONE_COLORS.get(tone, COLORS["muted"])


def tone_badge(tone: str) -> tuple[str, str]:
    return TONE_BADGE.get(tone, TONE_BADGE["neutral"])


def sans(size: int, weight: str | None = None) -> tuple:
    return (SANS, size, weight) if weight else (SANS, size)


def mono(size: int, weight: str | None = None) -> tuple:
    return (MONO, size, weight) if weight else (MONO, size)
